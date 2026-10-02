import uuid
from datetime import datetime, timedelta, timezone

from models.db import get_session, init_db
from models.schema import AlertEvent, Camera, Vertical, Zone, ZoneType
from pipeline.regroup_alerts import apply_groups, plan_groups

T0 = datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)


def make_zone(cooldown=60.0):
    init_db()
    cid, zid = f"cam-{uuid.uuid4().hex[:6]}", f"zone-{uuid.uuid4().hex[:6]}"
    with get_session() as s:
        s.add(Camera(id=cid, name=cid, vertical=Vertical.GENERIC))
        s.flush()
        s.add(Zone(id=zid, camera_id=cid, name="z", zone_type=ZoneType.GENERIC, polygon=[[0, 0], [1, 0], [1, 1]], alert_cooldown_seconds=cooldown))
    return cid, zid


def legacy(cid, zid, at, value, delivered=True, status="open", alert_type="queue_wait_exceeded"):
    return AlertEvent(
        id=uuid.uuid4().hex, camera_id=cid, zone_id=zid, alert_type=alert_type, message=f"Wait time {value:.1f}s exceeded threshold 5.0s",
        value=value, threshold=5.0, occurred_at=at, delivered=delivered, status=status, occurrences=1, last_occurred_at=None,
    )


def rows(zid):
    with get_session() as s:
        return s.query(AlertEvent).filter(AlertEvent.zone_id == zid).order_by(AlertEvent.occurred_at).all()


def run_regroup(zids):
    with get_session() as s:
        groups = [g for g in plan_groups(s) if g.keep.zone_id in zids]
        return apply_groups(s, groups)


def test_legacy_alerts_inside_cooldown_become_one():
    cid, zid = make_zone()
    with get_session() as s:
        for i, v in enumerate([6.0, 9.5, 7.0]):
            s.add(legacy(cid, zid, T0 + timedelta(seconds=i * 20), v))

    assert run_regroup({zid}) == (3, 1)
    (row,) = rows(zid)
    assert row.occurrences == 3 and row.value == 9.5
    assert row.occurred_at.replace(tzinfo=timezone.utc) == T0
    assert row.last_occurred_at.replace(tzinfo=timezone.utc) == T0 + timedelta(seconds=40)
    assert "3 times" in row.message and "peak 9.5s" in row.message


def test_gap_longer_than_cooldown_starts_a_new_group():
    cid, zid = make_zone(cooldown=30.0)
    with get_session() as s:
        s.add(legacy(cid, zid, T0, 6.0))
        s.add(legacy(cid, zid, T0 + timedelta(seconds=10), 7.0))
        s.add(legacy(cid, zid, T0 + timedelta(seconds=300), 8.0))

    assert run_regroup({zid}) == (3, 2)
    assert [r.occurrences for r in rows(zid)] == [2, 1]


def test_statuses_and_types_are_not_mixed():
    cid, zid = make_zone()
    with get_session() as s:
        s.add(legacy(cid, zid, T0, 6.0, status="open"))
        s.add(legacy(cid, zid, T0 + timedelta(seconds=5), 6.0, status="acknowledged"))
        s.add(legacy(cid, zid, T0 + timedelta(seconds=6), 6.0, alert_type="occupancy_exceeded"))

    assert run_regroup({zid}) == (3, 3)


def test_rerun_finds_nothing_and_new_style_alerts_are_untouched():
    cid, zid = make_zone()
    with get_session() as s:
        s.add(legacy(cid, zid, T0, 6.0))
        s.add(legacy(cid, zid, T0 + timedelta(seconds=5), 6.5))
        modern = legacy(cid, zid, T0 + timedelta(seconds=8), 7.0)
        modern.last_occurred_at = T0 + timedelta(seconds=8)
        modern.occurrences = 4
        s.add(modern)

    run_regroup({zid})
    with get_session() as s:
        assert [g for g in plan_groups(s) if g.keep.zone_id == zid] == []
    assert sorted(r.occurrences for r in rows(zid)) == [2, 4]


def test_delivered_if_any_member_was_delivered():
    cid, zid = make_zone()
    with get_session() as s:
        s.add(legacy(cid, zid, T0, 6.0, delivered=False))
        s.add(legacy(cid, zid, T0 + timedelta(seconds=5), 6.0, delivered=True))
    run_regroup({zid})
    assert rows(zid)[0].delivered is True
