import io
import uuid
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from models.db import add_missing_columns, engine, get_session
from models.schema import AlertEvent, FootfallEvent, PositionSample, Track, Video, ZoneEvent, SourceTypeDB

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]
T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self):
        self.items = []

    def rpush(self, key, value):
        self.items.append(value)

    def llen(self, key):
        return len(self.items)


@pytest.fixture()
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(api_main, "get_redis", lambda url: fake)
    with TestClient(api_main.app) as c:
        c.fake_redis = fake
        yield c


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_camera(client, **extra) -> str:
    cid = new_id("cam")
    body = {"id": cid, "name": "Test cam", "vertical": "retail_showroom", **extra}
    assert client.post("/cameras", json=body).status_code == 200
    return cid


def make_zone(client, cid: str, **extra) -> str:
    zid = new_id("zone")
    body = {"id": zid, "camera_id": cid, "name": "Zone A", "zone_type": "display_section", "polygon": SQUARE, **extra}
    r = client.post("/zones", json=body)
    assert r.status_code == 200, r.text
    return zid


def png_bytes(width=64, height=48) -> bytes:
    ok, buf = cv2.imencode(".png", np.full((height, width, 3), 120, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def seed_video_and_track(cid: str) -> str:
    vid, tid = new_id("vid"), new_id("trk")
    with get_session() as s:
        s.add(Video(id=vid, camera_id=cid, source_type=SourceTypeDB.UPLOAD, video_path="x.mp4", recorded_at=T0, status="done"))
        s.flush()
        s.add(Track(id=tid, video_id=vid, camera_id=cid, local_track_id=1, first_seen=T0, last_seen=T0))
        s.flush()
    return tid


# ---------------------------------------------------------------- cameras


def test_camera_create_validates_entrance_line(client):
    bad = {"id": new_id("cam"), "name": "x", "entrance_line": [[0, 0], [1, 1], [2, 2]]}
    assert client.post("/cameras", json=bad).status_code == 422


def test_duplicate_camera_is_409(client):
    cid = make_camera(client)
    assert client.post("/cameras", json={"id": cid, "name": "again"}).status_code == 409


def test_patch_camera_changes_only_given_fields_and_can_clear_line(client):
    cid = make_camera(client, entrance_line=[[0, 10], [100, 10]])
    r = client.patch(f"/cameras/{cid}", json={"name": "Renamed"})
    assert r.json()["name"] == "Renamed" and r.json()["entrance_line"] == [[0, 10], [100, 10]]

    r = client.patch(f"/cameras/{cid}", json={"entrance_line": None})
    assert r.json()["entrance_line"] is None and r.json()["name"] == "Renamed"

    r = client.patch(f"/cameras/{cid}", json={"name": None})  # required field can't be cleared
    assert r.json()["name"] == "Renamed"


def test_patch_missing_camera_is_404(client):
    assert client.patch("/cameras/nope", json={"name": "x"}).status_code == 404


def test_delete_camera_removes_zones_and_results(client):
    cid = make_camera(client)
    zid = make_zone(client, cid)
    tid = seed_video_and_track(cid)
    with get_session() as s:
        s.add(FootfallEvent(id=new_id("f"), camera_id=cid, track_id=tid, direction="in", occurred_at=T0))
        s.add(ZoneEvent(id=new_id("z"), zone_id=zid, track_id=tid, event_type="enter", occurred_at=T0))
        s.add(AlertEvent(id=new_id("a"), camera_id=cid, zone_id=zid, alert_type="queue_wait_exceeded", message="m", value=1, threshold=1, occurred_at=T0))

    assert client.delete(f"/cameras/{cid}").status_code == 204
    assert client.get(f"/cameras/{cid}/zones").json() == []
    assert client.get(f"/cameras/{cid}/footfall").json() == {"camera_id": cid, "total_in": 0, "total_out": 0}
    assert all(c["id"] != cid for c in client.get("/cameras").json())
    assert client.delete(f"/cameras/{cid}").status_code == 404


# ---------------------------------------------------------------- zones


def test_zone_validation_and_duplicates(client):
    cid = make_camera(client)
    assert client.post("/zones", json={"id": new_id("z"), "camera_id": cid, "name": "tri", "polygon": [[0, 0], [1, 1]]}).status_code == 422
    assert client.post("/zones", json={"id": new_id("z"), "camera_id": "missing", "name": "n", "polygon": SQUARE}).status_code == 404
    zid = make_zone(client, cid)
    dup = {"id": zid, "camera_id": cid, "name": "n", "polygon": SQUARE}
    assert client.post("/zones", json=dup).status_code == 409


def test_patch_zone_updates_and_can_clear_thresholds(client):
    cid = make_camera(client)
    zid = make_zone(client, cid, wait_time_alert_seconds=30, max_occupancy_alert=5)

    r = client.patch(f"/zones/{zid}", json={"name": "Counter", "wait_time_alert_seconds": 45})
    body = r.json()
    assert body["name"] == "Counter" and body["wait_time_alert_seconds"] == 45 and body["max_occupancy_alert"] == 5

    r = client.patch(f"/zones/{zid}", json={"max_occupancy_alert": None})
    assert r.json()["max_occupancy_alert"] is None and r.json()["wait_time_alert_seconds"] == 45

    r = client.patch(f"/zones/{zid}", json={"polygon": [[0, 0], [5, 5], [0, 5]]})
    assert r.json()["polygon"] == [[0, 0], [5, 5], [0, 5]]
    assert client.patch(f"/zones/{zid}", json={"polygon": [[0, 0]]}).status_code == 422


def test_delete_zone_removes_its_alerts_and_events(client):
    cid = make_camera(client)
    zid = make_zone(client, cid)
    tid = seed_video_and_track(cid)
    with get_session() as s:
        s.add(ZoneEvent(id=new_id("z"), zone_id=zid, track_id=tid, event_type="enter", occurred_at=T0))
        s.add(AlertEvent(id=new_id("a"), camera_id=cid, zone_id=zid, alert_type="queue_wait_exceeded", message="m", value=1, threshold=1, occurred_at=T0))

    assert client.delete(f"/zones/{zid}").status_code == 204
    assert client.get(f"/cameras/{cid}/alerts").json() == []
    assert client.get(f"/cameras/{cid}/zones").json() == []


# ---------------------------------------------------------------- reference frame


def test_reference_frame_upload_and_heatmap_uses_its_size(client):
    cid = make_camera(client)
    assert client.get(f"/cameras/{cid}/reference-frame").status_code == 404
    assert client.get("/cameras").json()[-1]["has_reference_frame"] is False

    r = client.post(f"/cameras/{cid}/reference-frame", files={"file": ("frame.png", png_bytes(64, 48), "image/png")})
    assert r.status_code == 200 and r.json()["has_reference_frame"] is True

    r = client.get(f"/cameras/{cid}/reference-frame")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    heat = client.get(f"/cameras/{cid}/heatmap?cols=4&rows=3").json()
    assert (heat["frame_width"], heat["frame_height"]) == (64.0, 48.0)


def test_reference_frame_rejects_other_file_types(client):
    cid = make_camera(client)
    r = client.post(f"/cameras/{cid}/reference-frame", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


# ---------------------------------------------------------------- uploads


def test_upload_rejects_unsupported_types_and_unknown_cameras(client):
    cid = make_camera(client)
    assert client.post("/videos/upload", data={"camera_id": cid}, files={"file": ("a.exe", b"x")}).status_code == 400
    assert client.post("/videos/upload", data={"camera_id": "missing"}, files={"file": ("a.mp4", b"x")}).status_code == 404


def test_upload_queues_job_and_queue_status_reports_it(client):
    cid = make_camera(client)
    before = client.get("/queue").json()["pending"]
    r = client.post("/videos/upload", data={"camera_id": cid}, files={"file": ("clip.mp4", b"not really a video")})
    assert r.status_code == 200 and r.json()["queued"] is True
    assert client.get("/queue").json()["pending"] == before + 1


# ---------------------------------------------------------------- analytics


def test_footfall_range_and_timeseries_buckets(client):
    cid = make_camera(client)
    tid = seed_video_and_track(cid)
    events = [
        (T0 + timedelta(minutes=1), "in"),
        (T0 + timedelta(minutes=4), "in"),
        (T0 + timedelta(minutes=6), "out"),
        (T0 + timedelta(minutes=70), "in"),
    ]
    with get_session() as s:
        for at, direction in events:
            s.add(FootfallEvent(id=new_id("f"), camera_id=cid, track_id=tid, direction=direction, occurred_at=at))

    assert client.get(f"/cameras/{cid}/footfall").json()["total_in"] == 3

    five = client.get(f"/cameras/{cid}/footfall-timeseries?interval_minutes=5").json()
    assert [(b["in_count"], b["out_count"]) for b in five] == [(2, 0), (0, 1), (1, 0)]

    hourly = client.get(f"/cameras/{cid}/footfall-timeseries?interval_minutes=60").json()
    assert [(b["in_count"], b["out_count"]) for b in hourly] == [(2, 1), (1, 0)]

    start = (T0 + timedelta(minutes=5)).isoformat()
    ranged = client.get(f"/cameras/{cid}/footfall", params={"start": start}).json()
    assert (ranged["total_in"], ranged["total_out"]) == (1, 1)
    assert client.get(f"/cameras/{cid}/footfall-timeseries?interval_minutes=0").status_code == 400


def test_dwell_respects_range_and_overview_compares_cameras(client):
    cid = make_camera(client)
    zid = make_zone(client, cid)
    tid = seed_video_and_track(cid)
    with get_session() as s:
        s.add(ZoneEvent(id=new_id("z"), zone_id=zid, track_id=tid, event_type="enter", occurred_at=T0))
        s.add(ZoneEvent(id=new_id("z"), zone_id=zid, track_id=tid, event_type="exit", occurred_at=T0 + timedelta(seconds=10)))
        s.add(FootfallEvent(id=new_id("f"), camera_id=cid, track_id=tid, direction="in", occurred_at=T0))
        s.add(AlertEvent(id=new_id("a"), camera_id=cid, zone_id=zid, alert_type="queue_wait_exceeded", message="m", value=10, threshold=5, occurred_at=T0))

    dwell = client.get(f"/zones/{zid}/dwell").json()
    assert dwell["visit_count"] == 1 and dwell["avg_dwell_seconds"] == 10.0
    after = (T0 + timedelta(hours=1)).isoformat()
    assert client.get(f"/zones/{zid}/dwell", params={"start": after}).json()["visit_count"] == 0

    row = next(r for r in client.get("/overview").json() if r["camera_id"] == cid)
    assert (row["zones"], row["total_in"], row["visits"], row["open_alerts"]) == (1, 1, 1, 1)
    assert row["avg_dwell_seconds"] == 10.0
    empty = next(r for r in client.get("/overview", params={"start": after}).json() if r["camera_id"] == cid)
    assert (empty["total_in"], empty["visits"], empty["open_alerts"]) == (0, 0, 0)


def test_heatmap_range_filters_samples(client):
    cid = make_camera(client)
    tid = seed_video_and_track(cid)
    with get_session() as s:
        s.add(PositionSample(id=new_id("p"), camera_id=cid, track_id=tid, x=10, y=10, occurred_at=T0))
        s.add(PositionSample(id=new_id("p"), camera_id=cid, track_id=tid, x=90, y=90, occurred_at=T0 + timedelta(hours=2)))

    both = client.get(f"/cameras/{cid}/heatmap?cols=2&rows=2").json()["counts"]
    assert sum(map(sum, both)) == 2
    late = client.get(f"/cameras/{cid}/heatmap?cols=2&rows=2", params={"start": (T0 + timedelta(hours=1)).isoformat()}).json()["counts"]
    assert sum(map(sum, late)) == 1


# ---------------------------------------------------------------- alerts


def test_alert_status_workflow_and_filter(client):
    cid = make_camera(client)
    zid = make_zone(client, cid)
    aid = new_id("a")
    with get_session() as s:
        s.add(AlertEvent(id=aid, camera_id=cid, zone_id=zid, alert_type="queue_wait_exceeded", message="m", value=9, threshold=5, occurred_at=T0))

    alert = client.get(f"/cameras/{cid}/alerts").json()[0]
    assert alert["status"] == "open" and alert["occurrences"] == 1 and alert["acknowledged_at"] is None

    r = client.patch(f"/alerts/{aid}", json={"status": "acknowledged"}).json()
    assert r["status"] == "acknowledged" and r["acknowledged_at"] is not None
    assert client.get(f"/cameras/{cid}/alerts", params={"status": "open"}).json() == []
    assert len(client.get(f"/cameras/{cid}/alerts", params={"status": "acknowledged"}).json()) == 1

    assert client.patch(f"/alerts/{aid}", json={"status": "open"}).json()["acknowledged_at"] is None
    assert client.patch(f"/alerts/{aid}", json={"status": "bogus"}).status_code == 422
    assert client.patch("/alerts/missing", json={"status": "resolved"}).status_code == 404


# ---------------------------------------------------------------- schema evolution


def test_add_missing_columns_restores_a_dropped_column_with_its_default(client):
    cid = make_camera(client)
    zid = make_zone(client, cid)
    with get_session() as s:
        s.add(AlertEvent(id=new_id("a"), camera_id=cid, zone_id=zid, alert_type="queue_wait_exceeded", message="m", value=1, threshold=1, occurred_at=T0))

    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE alert_events DROP COLUMN occurrences"))

    assert "alert_events.occurrences" in add_missing_columns()
    assert client.get(f"/cameras/{cid}/alerts").json()[0]["occurrences"] == 1
    assert add_missing_columns() == []  # idempotent
