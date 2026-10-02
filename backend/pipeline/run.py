"""Worker entrypoint: pulls FrameJobs off the Redis queue and runs the full
detect -> track -> aggregate -> persist pipeline against each video.

This is intentionally the only place that imports both the ML stack
(detector/tracker) and the DB layer — everything else (aggregator, schema)
stays independently testable without either.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import cv2

from ingestion.common import FrameJob
from ingestion.reference_frame import save_reference_frame_from_video
from models.db import get_session, init_db
from models.schema import AlertEvent as AlertEventDB
from models.schema import FootfallEvent as FootfallEventDB
from models.schema import PositionSample as PositionSampleDB
from models.schema import SourceTypeDB, Track as TrackDB, Video, ZoneEvent as ZoneEventDB
from pipeline.aggregator import FootfallCounter, ZoneTracker
from pipeline.alerts import Alert, check_occupancy_alerts, check_wait_time_alerts, group_alerts, grouped_message
from pipeline.notify import deliver_alert
from pipeline.detector import PersonDetector
from pipeline.tracker import ByteTracker
from workers.queue import dequeue, get_redis

logger = logging.getLogger(__name__)


# Process every Nth frame rather than all of them: at CPU inference speed
# (~10fps for YOLOX-Nano, see pipeline/tracker.py notes) there's no value in
# re-detecting every single frame of a 30fps source — sampling keeps the
# pipeline's processing time roughly proportional to video duration instead
# of video duration * decode-fps, without materially hurting footfall/dwell
# accuracy (a person crossing a line/zone is visible across many sampled
# frames, not just one).
FRAME_SAMPLE_INTERVAL = int(os.environ.get("FRAME_SAMPLE_INTERVAL", "3"))


def process_video(job: FrameJob, detector: PersonDetector, camera_config: dict) -> None:
    """camera_config carries the entrance line and zone polygons for this
    camera — fetched from the DB in the real worker loop, passed directly
    here so this function stays testable without a live DB."""

    cap = cv2.VideoCapture(job.video_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {job.video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    video_id = uuid.uuid4().hex
    save_reference_frame_from_video(job.camera_id, job.video_path)  # no-op if the camera already has one

    tracker = ByteTracker()
    footfall_counter = FootfallCounter(*camera_config["entrance_line"]) if camera_config.get("entrance_line") else None
    zone_trackers = [ZoneTracker(zone["id"], zone["polygon"]) for zone in camera_config.get("zones", [])]

    footfall_events = []
    zone_events = []
    position_samples = []
    track_lifespans: dict[int, dict] = {}  # local_track_id -> {first_seen, last_seen}

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % FRAME_SAMPLE_INTERVAL == 0:
            timestamp = frame_idx / fps
            detections = detector.detect(frame)
            tracks = tracker.update(detections)

            for track in tracks:
                lifespan = track_lifespans.setdefault(track.track_id, {"first_seen": timestamp})
                lifespan["last_seen"] = timestamp

                point = track.center
                position_samples.append((track.track_id, point, timestamp))

                if footfall_counter is not None:
                    event = footfall_counter.update(str(track.track_id), point, timestamp)
                    if event is not None:
                        footfall_events.append(event)

                for zone_tracker in zone_trackers:
                    event = zone_tracker.update(str(track.track_id), point, timestamp)
                    if event is not None:
                        zone_events.append(event)

        frame_idx += 1

    final_timestamp = frame_idx / fps
    for zone_tracker in zone_trackers:
        zone_events.extend(zone_tracker.finalize(final_timestamp))

    cap.release()

    alerts = _check_alerts(camera_config.get("zones", []), zone_events)
    new_alerts = _persist_results(job, video_id, track_lifespans, footfall_events, zone_events, position_samples, alerts, camera_config)
    _notify_new_alerts(job.camera_id, new_alerts)

    logger.info(
        "processed %s: %d frames, %d tracks, %d footfall events, %d zone events, %d alerts",
        job.video_path,
        frame_idx,
        len(track_lifespans),
        len(footfall_events),
        len(zone_events),
        len(alerts),
    )


def _check_alerts(zone_configs: list[dict], zone_events: list) -> list[Alert]:
    """zone_configs are the dicts load_camera_config() builds, each
    optionally carrying wait_time_alert_seconds / max_occupancy_alert and an
    alert_cooldown_seconds used to group repeated breaches into one alert."""
    alerts: list[Alert] = []
    for zone in zone_configs:
        this_zone_events = [e for e in zone_events if e.zone_id == zone["id"]]
        if not this_zone_events:
            continue
        zone_alerts: list[Alert] = []
        if zone.get("wait_time_alert_seconds") is not None:
            zone_alerts.extend(check_wait_time_alerts(zone["id"], this_zone_events, zone["wait_time_alert_seconds"]))
        if zone.get("max_occupancy_alert") is not None:
            zone_alerts.extend(check_occupancy_alerts(zone["id"], this_zone_events, zone["max_occupancy_alert"]))
        alerts.extend(group_alerts(zone_alerts, zone.get("alert_cooldown_seconds", 60.0)))
    return alerts


def _notify_new_alerts(camera_id: str, new_alerts: list[tuple[str, Alert]]) -> None:
    """Send each newly created alert once and record whether a channel
    accepted it. Alerts merged into an already-open one are not re-sent."""
    for alert_id, alert in new_alerts:
        delivered = deliver_alert(camera_id, alert)
        if delivered:
            with get_session() as session:
                row = session.get(AlertEventDB, alert_id)
                if row is not None:
                    row.delivered = True


def _persist_results(
    job,
    video_id,
    track_lifespans,
    footfall_events,
    zone_events,
    position_samples,
    alerts: list[Alert] | None = None,
    camera_config: dict | None = None,
) -> list[tuple[str, Alert]]:
    """Persist everything for one processed video. Returns the alerts that
    were newly created (not merged into an already-open alert), as
    (alert_id, alert) pairs, so the caller can notify exactly once each."""
    new_alerts: list[tuple[str, Alert]] = []
    cooldowns = {z["id"]: z.get("alert_cooldown_seconds", 60.0) for z in (camera_config or {}).get("zones", [])}
    with get_session() as session:
        session.add(
            Video(
                id=video_id,
                camera_id=job.camera_id,
                source_type=SourceTypeDB(job.source_type.value),
                video_path=job.video_path,
                recorded_at=datetime.fromisoformat(job.recorded_at),
                processed_at=datetime.now(timezone.utc),
                status="done",
            )
        )
        session.flush()  # Video must exist before Track rows reference its video_id FK.

        for local_id, lifespan in track_lifespans.items():
            session.add(
                TrackDB(
                    id=f"{video_id}:{local_id}",
                    video_id=video_id,
                    camera_id=job.camera_id,
                    local_track_id=local_id,
                    first_seen=_offset_time(job.recorded_at, lifespan["first_seen"]),
                    last_seen=_offset_time(job.recorded_at, lifespan["last_seen"]),
                )
            )

        # Flush tracks before adding rows that reference them by track_id.
        # Track/FootfallEvent/ZoneEvent/PositionSample aren't linked via ORM
        # relationship() (just plain FK columns), so SQLAlchemy's unit-of-work
        # has no object-graph signal to order the inserts correctly on its
        # own — without this flush, Postgres (which enforces FK constraints,
        # unlike SQLite) can insert a dependent row before its track exists.
        session.flush()

        for event in footfall_events:
            session.add(
                FootfallEventDB(
                    id=uuid.uuid4().hex,
                    camera_id=job.camera_id,
                    track_id=f"{video_id}:{event.track_id}",
                    direction=event.direction,
                    occurred_at=_offset_time(job.recorded_at, event.timestamp),
                )
            )

        for event in zone_events:
            session.add(
                ZoneEventDB(
                    id=uuid.uuid4().hex,
                    zone_id=event.zone_id,
                    track_id=f"{video_id}:{event.track_id}",
                    event_type=event.event_type,
                    occurred_at=_offset_time(job.recorded_at, event.timestamp),
                )
            )

        for local_id, point, timestamp in position_samples:
            session.add(
                PositionSampleDB(
                    id=uuid.uuid4().hex,
                    camera_id=job.camera_id,
                    track_id=f"{video_id}:{local_id}",
                    x=point[0],
                    y=point[1],
                    occurred_at=_offset_time(job.recorded_at, timestamp),
                )
            )

        for alert in alerts or []:
            first_at = _offset_time(job.recorded_at, alert.occurred_at)
            last_at = _offset_time(job.recorded_at, alert.last_occurred_at or alert.occurred_at)
            cooldown = timedelta(seconds=cooldowns.get(alert.zone_id, 60.0))

            # Merge into a still-open alert of the same kind (e.g. a live
            # camera's next 30s segment continuing the same crowding spell).
            existing = (
                session.query(AlertEventDB)
                .filter(
                    AlertEventDB.zone_id == alert.zone_id,
                    AlertEventDB.alert_type == alert.alert_type,
                    AlertEventDB.status == "open",
                    AlertEventDB.last_occurred_at >= first_at - cooldown,
                )
                .order_by(AlertEventDB.last_occurred_at.desc())
                .first()
            )
            if existing is not None:
                existing.occurrences += alert.occurrences
                existing.value = max(existing.value, alert.value)
                existing.last_occurred_at = max(_as_utc(existing.last_occurred_at), last_at)
                existing.message = _merged_message(existing)
                continue

            alert_id = uuid.uuid4().hex
            session.add(
                AlertEventDB(
                    id=alert_id,
                    camera_id=job.camera_id,
                    zone_id=alert.zone_id,
                    alert_type=alert.alert_type,
                    message=alert.message,
                    value=alert.value,
                    threshold=alert.threshold,
                    occurred_at=first_at,
                    delivered=False,
                    status="open",
                    occurrences=alert.occurrences,
                    last_occurred_at=last_at,
                )
            )
            new_alerts.append((alert_id, alert))

    return new_alerts


def _as_utc(dt: datetime) -> datetime:
    # SQLite hands datetimes back naive; Postgres hands them back aware.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _merged_message(row: AlertEventDB) -> str:
    return grouped_message(row.alert_type, row.threshold, row.occurrences, row.value)


def _offset_time(recorded_at_iso: str, seconds: float) -> datetime:
    base = datetime.fromisoformat(recorded_at_iso)
    return base + timedelta(seconds=seconds)


def load_camera_config(camera_id: str) -> dict:
    """Fetch a camera's entrance line + zone polygons from the DB and shape
    them into the dict process_video() expects. Separate from process_video
    itself so that function stays testable with a hand-built config and no
    DB dependency (see pipeline/run.py tests / scripts/e2e_smoke_test.py)."""
    from models.schema import Camera, Zone

    with get_session() as session:
        camera = session.get(Camera, camera_id)
        zones = session.query(Zone).filter(Zone.camera_id == camera_id).all()

        config: dict = {}
        if camera is not None and camera.entrance_line:
            p1, p2 = camera.entrance_line
            config["entrance_line"] = (tuple(p1), tuple(p2))
        config["zones"] = [
            {
                "id": zone.id,
                "polygon": [tuple(p) for p in zone.polygon],
                "wait_time_alert_seconds": zone.wait_time_alert_seconds,
                "max_occupancy_alert": zone.max_occupancy_alert,
                "alert_cooldown_seconds": zone.alert_cooldown_seconds if zone.alert_cooldown_seconds is not None else 60.0,
            }
            for zone in zones
        ]
        return config


def run_worker_loop(redis_url: str) -> None:
    """Long-running worker process: pulls jobs and processes them one at a
    time. A single detector instance is reused across jobs (model load is
    the expensive part)."""
    init_db()
    detector = PersonDetector()
    r = get_redis(redis_url)

    logger.info("worker started, waiting for frame jobs")
    while True:
        job = dequeue(r, timeout=5)
        if job is None:
            continue
        try:
            camera_config = load_camera_config(job.camera_id)
            process_video(job, detector, camera_config)
        except Exception:
            logger.exception("failed to process job: %s", job)
            time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker_loop(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
