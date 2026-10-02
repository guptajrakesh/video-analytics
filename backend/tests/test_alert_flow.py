"""Alert persistence in the worker: grouping across consecutive videos.
Imports pipeline.run, which pulls in the detector's ML dependencies."""

import uuid
from datetime import datetime, timedelta, timezone

from ingestion.common import FrameJob, SourceType
from models.db import get_session, init_db
from models.schema import AlertEvent, Camera, Vertical, Zone, ZoneType
from pipeline.alerts import Alert
from pipeline.run import _persist_results

T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


def setup_zone(cooldown=60.0):
    init_db()
    cid, zid = f"cam-{uuid.uuid4().hex[:6]}", f"zone-{uuid.uuid4().hex[:6]}"
    with get_session() as s:
        s.add(Camera(id=cid, name=cid, vertical=Vertical.RETAIL_SHOWROOM))
        s.flush()
        s.add(Zone(id=zid, camera_id=cid, name="z", zone_type=ZoneType.GENERIC, polygon=[[0, 0], [1, 0], [1, 1]], alert_cooldown_seconds=cooldown))
    return cid, zid


def persist(cid, zid, recorded_at, alerts, cooldown=60.0):
    job = FrameJob(camera_id=cid, video_path="seg.mp4", source_type=SourceType.RTSP, recorded_at=recorded_at.isoformat())
    config = {"zones": [{"id": zid, "alert_cooldown_seconds": cooldown}]}
    return _persist_results(job, uuid.uuid4().hex, {}, [], [], [], alerts, config)


def wait_alert(zid, at, last=None, value=8.0, occurrences=1):
    return Alert(zid, "queue_wait_exceeded", "Wait time 8.0s exceeded threshold 5.0s", value, 5.0, at, occurrences, last if last is not None else at)


def rows(cid):
    with get_session() as s:
        return s.query(AlertEvent).filter(AlertEvent.camera_id == cid).order_by(AlertEvent.occurred_at).all()


def test_new_alert_is_created_open_and_returned_for_notification():
    cid, zid = setup_zone()
    new = persist(cid, zid, T0, [wait_alert(zid, 5.0)])
    assert len(new) == 1
    (row,) = rows(cid)
    assert (row.status, row.occurrences, row.delivered) == ("open", 1, False)


def test_next_segment_inside_cooldown_merges_instead_of_duplicating():
    cid, zid = setup_zone()
    persist(cid, zid, T0, [wait_alert(zid, 5.0, last=20.0, value=8.0, occurrences=2)])
    # The next 30s segment starts right after; its last breach is 10s into it.
    new = persist(cid, zid, T0 + timedelta(seconds=30), [wait_alert(zid, 2.0, last=10.0, value=11.0, occurrences=3)])

    assert new == []  # merged: nothing new to notify
    (row,) = rows(cid)
    assert row.occurrences == 5 and row.value == 11.0
    assert "5 times" in row.message and "peak 11.0s" in row.message
    assert row.last_occurred_at.replace(tzinfo=timezone.utc) == T0 + timedelta(seconds=40)


def test_breach_after_cooldown_is_a_new_alert():
    cid, zid = setup_zone()
    persist(cid, zid, T0, [wait_alert(zid, 5.0)])
    new = persist(cid, zid, T0 + timedelta(minutes=10), [wait_alert(zid, 1.0)])
    assert len(new) == 1 and len(rows(cid)) == 2


def test_acknowledged_alerts_are_not_merged_into():
    cid, zid = setup_zone()
    persist(cid, zid, T0, [wait_alert(zid, 5.0)])
    with get_session() as s:
        s.query(AlertEvent).filter(AlertEvent.camera_id == cid).update({"status": "acknowledged"})
    new = persist(cid, zid, T0 + timedelta(seconds=30), [wait_alert(zid, 1.0)])
    assert len(new) == 1 and len(rows(cid)) == 2  # a fresh alert for the continuing problem
