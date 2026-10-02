from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.schemas import (
    AlertOut,
    AlertStatusUpdate,
    CameraCreate,
    CameraOut,
    CameraUpdate,
    FootfallBucket,
    FootfallSummary,
    HeatmapOut,
    OverviewRow,
    QueueStatus,
    SyncBatch,
    UploadResponse,
    VideoOut,
    ZoneCreate,
    ZoneDwellSummary,
    ZoneOccupancy,
    ZoneOut,
    ZoneTemplateOut,
    ZoneUpdate,
)
from ingestion.reference_frame import (
    delete_reference_frame,
    has_reference_frame,
    reference_frame_path,
    reference_frame_size,
    save_reference_frame_from_image,
    save_reference_frame_from_video,
)
from ingestion.upload import save_and_enqueue
from models.db import SessionLocal, init_db
from models.schema import (
    ZONE_TEMPLATES,
    AlertEvent,
    Camera,
    FootfallEvent,
    PositionSample,
    SourceTypeDB,
    Track,
    Vertical,
    Video,
    Zone,
    ZoneEvent,
)
from pipeline.aggregator import HeatmapGrid
from workers.queue import QUEUE_KEY, get_redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
# Dashboard origin(s) allowed to call this API from the browser. Comma-
# separated so a deployment can add its real dashboard URL via env var
# without a code change.
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

app = FastAPI(title="Video Analytics Platform API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------- helpers


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _in_range(query, column, start: datetime | None, end: datetime | None):
    if start is not None:
        query = query.filter(column >= start)
    if end is not None:
        query = query.filter(column <= end)
    return query


def _camera_out(camera: Camera) -> CameraOut:
    out = CameraOut.model_validate(camera)
    out.has_reference_frame = has_reference_frame(camera.id)
    return out


def _dwell_seconds(db: Session, zone_id: str, start: datetime | None, end: datetime | None) -> list[float]:
    """Pair each "enter" with the next "exit" for the same track to compute
    dwell — dwell isn't stored redundantly on the row (see models/schema.py)."""
    query = db.query(ZoneEvent).filter(ZoneEvent.zone_id == zone_id)
    events = _in_range(query, ZoneEvent.occurred_at, start, end).order_by(ZoneEvent.track_id, ZoneEvent.occurred_at).all()

    open_enters: dict[str, datetime] = {}
    dwell: list[float] = []
    for event in events:
        if event.event_type == "enter":
            open_enters[event.track_id] = event.occurred_at
        elif event.event_type == "exit" and event.track_id in open_enters:
            dwell.append((event.occurred_at - open_enters.pop(event.track_id)).total_seconds())
    return dwell


def _footfall_counts(db: Session, camera_id: str, start: datetime | None, end: datetime | None) -> tuple[int, int]:
    counts = {"in": 0, "out": 0}
    query = db.query(FootfallEvent.direction, func.count(FootfallEvent.id)).filter(FootfallEvent.camera_id == camera_id)
    for direction, n in _in_range(query, FootfallEvent.occurred_at, start, end).group_by(FootfallEvent.direction).all():
        counts[direction] = n
    return counts["in"], counts["out"]


# ---------------------------------------------------------------- cameras


@app.post("/cameras", response_model=CameraOut)
def create_camera(payload: CameraCreate, db: Session = Depends(get_db)):
    if db.get(Camera, payload.id) is not None:
        raise HTTPException(status_code=409, detail="camera already exists")
    camera = Camera(**payload.model_dump())
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return _camera_out(camera)


@app.get("/cameras", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db)):
    return [_camera_out(c) for c in db.query(Camera).order_by(Camera.created_at, Camera.id).all()]


@app.patch("/cameras/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: str, payload: CameraUpdate, db: Session = Depends(get_db)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is None and field in {"name", "vertical"}:
            continue  # required fields can't be cleared; rtsp_url / entrance_line can
        setattr(camera, field, value)
    db.commit()
    db.refresh(camera)
    return _camera_out(camera)


@app.delete("/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: str, db: Session = Depends(get_db)):
    """Removes the camera with its zones and every result recorded for it."""
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")

    zone_ids = [z.id for z in db.query(Zone.id).filter(Zone.camera_id == camera_id).all()]
    db.query(AlertEvent).filter(AlertEvent.camera_id == camera_id).delete(synchronize_session=False)
    if zone_ids:
        db.query(ZoneEvent).filter(ZoneEvent.zone_id.in_(zone_ids)).delete(synchronize_session=False)
    db.query(FootfallEvent).filter(FootfallEvent.camera_id == camera_id).delete(synchronize_session=False)
    db.query(PositionSample).filter(PositionSample.camera_id == camera_id).delete(synchronize_session=False)
    db.query(Track).filter(Track.camera_id == camera_id).delete(synchronize_session=False)
    db.query(Video).filter(Video.camera_id == camera_id).delete(synchronize_session=False)
    db.query(Zone).filter(Zone.camera_id == camera_id).delete(synchronize_session=False)
    db.delete(camera)
    db.commit()
    delete_reference_frame(camera_id)


@app.get("/cameras/{camera_id}/reference-frame")
def get_reference_frame(camera_id: str):
    if not has_reference_frame(camera_id):
        raise HTTPException(status_code=404, detail="no reference frame yet — upload a video for this camera first")
    return FileResponse(reference_frame_path(camera_id), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.post("/cameras/{camera_id}/reference-frame", response_model=CameraOut)
async def set_reference_frame(camera_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Replace the camera's reference frame from an image or a video file
    (the video's first frame is used). Zones are stored in this frame's pixel
    space, so replace it with a frame at the camera's real resolution."""
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in IMAGE_EXTENSIONS:
        ok = save_reference_frame_from_image(camera_id, await file.read())
    elif ext in VIDEO_EXTENSIONS:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
        try:
            ok = save_reference_frame_from_video(camera_id, tmp.name, overwrite=True)
        finally:
            os.remove(tmp.name)
    else:
        raise HTTPException(status_code=400, detail="upload an image (jpg/png) or a video (mp4/avi/mkv/mov)")
    if not ok:
        raise HTTPException(status_code=400, detail="could not read a frame from that file")
    return _camera_out(camera)


# ---------------------------------------------------------------- zones


@app.post("/zones", response_model=ZoneOut)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)):
    if db.get(Camera, payload.camera_id) is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if db.get(Zone, payload.id) is not None:
        raise HTTPException(status_code=409, detail="zone already exists")
    zone = Zone(**payload.model_dump())
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@app.get("/cameras/{camera_id}/zones", response_model=list[ZoneOut])
def list_zones(camera_id: str, db: Session = Depends(get_db)):
    return db.query(Zone).filter(Zone.camera_id == camera_id).order_by(Zone.id).all()


@app.patch("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: str, payload: ZoneUpdate, db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="zone not found")
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is None and field in {"name", "zone_type", "polygon", "alert_cooldown_seconds"}:
            continue  # these can't be cleared; the threshold fields can
        setattr(zone, field, value)
    db.commit()
    db.refresh(zone)
    return zone


@app.delete("/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: str, db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="zone not found")
    db.query(AlertEvent).filter(AlertEvent.zone_id == zone_id).delete(synchronize_session=False)
    db.query(ZoneEvent).filter(ZoneEvent.zone_id == zone_id).delete(synchronize_session=False)
    db.delete(zone)
    db.commit()


@app.get("/verticals/{vertical}/zone-template", response_model=ZoneTemplateOut)
def zone_template(vertical: Vertical):
    # Lets a setup UI pre-fill zone-type choices for a vertical (e.g. retail
    # showroom -> entrance/display_section/billing_counter) without any
    # pipeline code change — see models/schema.py's ZONE_TEMPLATES.
    if vertical not in ZONE_TEMPLATES:
        raise HTTPException(status_code=404, detail="no template for this vertical")
    return ZoneTemplateOut(vertical=vertical, zone_types=ZONE_TEMPLATES[vertical])


# ---------------------------------------------------------------- videos


@app.post("/videos/upload", response_model=UploadResponse)
async def upload_video(camera_id: str = Form(...), file: UploadFile = None, db: Session = Depends(get_db)):
    if db.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if file is None:
        raise HTTPException(status_code=400, detail="file is required")
    if os.path.splitext(file.filename or "")[1].lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="unsupported file type; use mp4, avi, mkv or mov")

    r = get_redis(REDIS_URL)
    job = save_and_enqueue(r, camera_id, file.filename, file.file)
    # So zones can be drawn right away, without waiting for processing.
    save_reference_frame_from_video(camera_id, job.video_path)
    return UploadResponse(video_path=job.video_path, camera_id=camera_id, queued=True)


@app.get("/videos", response_model=list[VideoOut])
def list_videos(camera_id: str | None = None, limit: int = 20, db: Session = Depends(get_db)):
    query = db.query(Video)
    if camera_id:
        query = query.filter(Video.camera_id == camera_id)
    return query.order_by(Video.recorded_at.desc()).limit(min(limit, 200)).all()


@app.get("/queue", response_model=QueueStatus)
def queue_status():
    """Videos uploaded but not yet picked up by a worker."""
    return QueueStatus(pending=get_redis(REDIS_URL).llen(QUEUE_KEY))


# ---------------------------------------------------------------- analytics


@app.get("/cameras/{camera_id}/footfall", response_model=FootfallSummary)
def footfall_summary(camera_id: str, start: datetime | None = None, end: datetime | None = None, db: Session = Depends(get_db)):
    total_in, total_out = _footfall_counts(db, camera_id, start, end)
    return FootfallSummary(camera_id=camera_id, total_in=total_in, total_out=total_out)


@app.get("/zones/{zone_id}/dwell", response_model=ZoneDwellSummary)
def zone_dwell_summary(zone_id: str, start: datetime | None = None, end: datetime | None = None, db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="zone not found")

    dwell = _dwell_seconds(db, zone_id, start, end)
    if not dwell:
        return ZoneDwellSummary(zone_id=zone_id, zone_name=zone.name, visit_count=0, avg_dwell_seconds=0.0, max_dwell_seconds=0.0)
    return ZoneDwellSummary(
        zone_id=zone_id,
        zone_name=zone.name,
        visit_count=len(dwell),
        avg_dwell_seconds=sum(dwell) / len(dwell),
        max_dwell_seconds=max(dwell),
    )


@app.get("/cameras/{camera_id}/footfall-timeseries", response_model=list[FootfallBucket])
def footfall_timeseries(
    camera_id: str,
    interval_minutes: int = 60,
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
):
    """Buckets footfall events into fixed-width time windows (aligned to the
    Unix epoch, so any interval from 1 minute to a day or more works) for a
    footfall-over-time chart. Bucketed in Python rather than a DB-specific
    date_trunc/time_bucket function, since the API is meant to run unchanged
    against whatever database a client has."""
    if interval_minutes < 1:
        raise HTTPException(status_code=400, detail="interval_minutes must be at least 1")
    query = db.query(FootfallEvent).filter(FootfallEvent.camera_id == camera_id)
    events = _in_range(query, FootfallEvent.occurred_at, start, end).order_by(FootfallEvent.occurred_at).all()

    width = interval_minutes * 60
    buckets: dict[datetime, dict[str, int]] = {}
    for event in events:
        bucket_start = datetime.fromtimestamp(int(_utc(event.occurred_at).timestamp()) // width * width, tz=timezone.utc)
        buckets.setdefault(bucket_start, {"in": 0, "out": 0})[event.direction] += 1

    return [
        FootfallBucket(bucket_start=bucket_start, in_count=counts["in"], out_count=counts["out"])
        for bucket_start, counts in sorted(buckets.items())
    ]


@app.get("/cameras/{camera_id}/zones/occupancy", response_model=list[ZoneOccupancy])
def zones_occupancy(camera_id: str, db: Session = Depends(get_db)):
    """Current occupancy per zone: a track is "currently inside" if its most
    recent event in that zone is an "enter" with no matching "exit" yet.
    For a fully batch-processed video this is always 0 once finalize() has
    closed every open dwell (see pipeline/aggregator.py) — this only shows
    nonzero occupancy for a zone actively being fed by a live RTSP stream
    whose latest segment hasn't finished yet."""
    zones = db.query(Zone).filter(Zone.camera_id == camera_id).order_by(Zone.id).all()
    results = []
    for zone in zones:
        events = (
            db.query(ZoneEvent)
            .filter(ZoneEvent.zone_id == zone.id)
            .order_by(ZoneEvent.track_id, ZoneEvent.occurred_at)
            .all()
        )
        latest_event_by_track: dict[str, str] = {}
        for event in events:
            latest_event_by_track[event.track_id] = event.event_type
        occupancy = sum(1 for event_type in latest_event_by_track.values() if event_type == "enter")
        results.append(
            ZoneOccupancy(
                zone_id=zone.id,
                zone_name=zone.name,
                zone_type=zone.zone_type,
                current_occupancy=occupancy,
                max_occupancy_alert=zone.max_occupancy_alert,
            )
        )
    return results


@app.get("/cameras/{camera_id}/heatmap", response_model=HeatmapOut)
def camera_heatmap(
    camera_id: str,
    cols: int = 20,
    rows: int = 15,
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")

    query = db.query(PositionSample).filter(PositionSample.camera_id == camera_id)
    samples = _in_range(query, PositionSample.occurred_at, start, end).all()

    # Prefer the real frame size, so the grid lines up with the reference
    # frame it is drawn over; fall back to the extent of the data.
    size = reference_frame_size(camera_id)
    if size is not None:
        frame_width, frame_height = float(size[0]), float(size[1])
    elif samples:
        frame_width = max(s.x for s in samples) or 1.0
        frame_height = max(s.y for s in samples) or 1.0
    else:
        frame_width, frame_height = 1.0, 1.0

    grid = HeatmapGrid(cols=cols, rows=rows, frame_width=frame_width, frame_height=frame_height)
    for s in samples:
        grid.add((s.x, s.y))
    return HeatmapOut(camera_id=camera_id, cols=cols, rows=rows, frame_width=frame_width, frame_height=frame_height, counts=grid.counts)


@app.get("/overview", response_model=list[OverviewRow])
def overview(start: datetime | None = None, end: datetime | None = None, db: Session = Depends(get_db)):
    """One row per camera, for comparing sites side by side."""
    rows = []
    for camera in db.query(Camera).order_by(Camera.created_at, Camera.id).all():
        zones = db.query(Zone).filter(Zone.camera_id == camera.id).all()
        total_in, total_out = _footfall_counts(db, camera.id, start, end)

        dwell: list[float] = []
        for zone in zones:
            dwell.extend(_dwell_seconds(db, zone.id, start, end))

        alert_query = db.query(func.count(AlertEvent.id)).filter(AlertEvent.camera_id == camera.id, AlertEvent.status == "open")
        open_alerts = _in_range(alert_query, AlertEvent.occurred_at, start, end).scalar() or 0

        rows.append(
            OverviewRow(
                camera_id=camera.id,
                camera_name=camera.name,
                vertical=camera.vertical,
                zones=len(zones),
                total_in=total_in,
                total_out=total_out,
                visits=len(dwell),
                avg_dwell_seconds=sum(dwell) / len(dwell) if dwell else 0.0,
                open_alerts=open_alerts,
            )
        )
    return rows


# ---------------------------------------------------------------- alerts


@app.get("/cameras/{camera_id}/alerts", response_model=list[AlertOut])
def list_alerts(
    camera_id: str,
    limit: int = 50,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AlertEvent).filter(AlertEvent.camera_id == camera_id)
    if status:
        query = query.filter(AlertEvent.status == status)
    query = _in_range(query, AlertEvent.occurred_at, start, end)
    return query.order_by(AlertEvent.occurred_at.desc()).limit(min(limit, 500)).all()


@app.patch("/alerts/{alert_id}", response_model=AlertOut)
def update_alert_status(alert_id: str, payload: AlertStatusUpdate, db: Session = Depends(get_db)):
    alert = db.get(AlertEvent, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.status = payload.status
    alert.acknowledged_at = None if payload.status == "open" else datetime.now(timezone.utc)
    db.commit()
    db.refresh(alert)
    return alert


# ---------------------------------------------------------------- edge sync


@app.post("/sync/ingest")
def sync_ingest(batch: SyncBatch, db: Session = Depends(get_db)):
    """Receives one edge deployment's fully-processed video as structured
    rows — never the video file itself (see sync/push.py and the plan's
    "Deployment Modes" section). Idempotent by primary key, since the sync
    worker retries a batch it isn't sure landed (e.g. after a network blip
    before it saw the response) rather than risk losing data.
    """
    if db.get(Camera, batch.video.camera_id) is None:
        # Normal setup registers the camera centrally first (same id used on
        # both sides); this placeholder just stops an edge site's very first
        # sync from failing outright if that step was missed, rather than
        # silently dropping results.
        db.add(Camera(id=batch.video.camera_id, name=batch.video.camera_id, vertical=Vertical.GENERIC))
        db.flush()

    if db.get(Video, batch.video.id) is None:
        db.add(
            Video(
                id=batch.video.id,
                camera_id=batch.video.camera_id,
                source_type=SourceTypeDB(batch.video.source_type),
                video_path="<on edge device, not synced>",
                recorded_at=batch.video.recorded_at,
                processed_at=batch.video.processed_at,
                status="done",
            )
        )
        db.flush()

    for t in batch.tracks:
        if db.get(Track, t.id) is None:
            db.add(Track(**t.model_dump()))
    db.flush()  # tracks must exist before rows below reference them

    skipped_zone_events = 0
    for fe in batch.footfall_events:
        if db.get(FootfallEvent, fe.id) is None:
            db.add(FootfallEvent(**fe.model_dump()))
    for ze in batch.zone_events:
        if db.get(Zone, ze.zone_id) is None:
            skipped_zone_events += 1  # zone not set up centrally yet — see docstring
            continue
        if db.get(ZoneEvent, ze.id) is None:
            db.add(ZoneEvent(**ze.model_dump()))
    for ps in batch.position_samples:
        if db.get(PositionSample, ps.id) is None:
            db.add(PositionSample(**ps.model_dump()))

    db.commit()
    return {"status": "ok", "skipped_zone_events": skipped_zone_events}
