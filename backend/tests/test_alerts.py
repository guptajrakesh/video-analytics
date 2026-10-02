from pipeline.aggregator import ZoneEvent
from pipeline.alerts import check_occupancy_alerts, check_wait_time_alerts


def test_wait_time_alert_fires_when_dwell_exceeds_threshold():
    events = [
        ZoneEvent(track_id="t1", zone_id="z1", event_type="enter", timestamp=0.0),
        ZoneEvent(track_id="t1", zone_id="z1", event_type="exit", timestamp=65.0, dwell_seconds=65.0),
    ]
    alerts = check_wait_time_alerts("z1", events, threshold_seconds=60.0)
    assert len(alerts) == 1
    assert alerts[0].alert_type == "queue_wait_exceeded"
    assert alerts[0].value == 65.0
    assert alerts[0].zone_id == "z1"


def test_wait_time_alert_does_not_fire_under_threshold():
    events = [
        ZoneEvent(track_id="t1", zone_id="z1", event_type="enter", timestamp=0.0),
        ZoneEvent(track_id="t1", zone_id="z1", event_type="exit", timestamp=30.0, dwell_seconds=30.0),
    ]
    alerts = check_wait_time_alerts("z1", events, threshold_seconds=60.0)
    assert alerts == []


def test_wait_time_alert_ignores_enter_events():
    events = [ZoneEvent(track_id="t1", zone_id="z1", event_type="enter", timestamp=0.0)]
    alerts = check_wait_time_alerts("z1", events, threshold_seconds=0.0)
    assert alerts == []


def test_occupancy_alert_fires_when_concurrent_count_exceeds_threshold():
    # 3 people enter and never leave within this window -> concurrency hits 3
    events = [
        ZoneEvent(track_id="t1", zone_id="z1", event_type="enter", timestamp=0.0),
        ZoneEvent(track_id="t2", zone_id="z1", event_type="enter", timestamp=1.0),
        ZoneEvent(track_id="t3", zone_id="z1", event_type="enter", timestamp=2.0),
    ]
    alerts = check_occupancy_alerts("z1", events, threshold_count=2)
    # Only the 3rd entry actually pushes concurrency (3) above the threshold (2)
    assert len(alerts) == 1
    assert alerts[0].alert_type == "occupancy_exceeded"
    assert alerts[0].value == 3
    assert alerts[0].occurred_at == 2.0


def test_occupancy_alert_does_not_fire_under_threshold():
    events = [
        ZoneEvent(track_id="t1", zone_id="z1", event_type="enter", timestamp=0.0),
        ZoneEvent(track_id="t2", zone_id="z1", event_type="enter", timestamp=1.0),
    ]
    alerts = check_occupancy_alerts("z1", events, threshold_count=2)
    assert alerts == []


def test_occupancy_alert_accounts_for_exits_reducing_concurrency():
    events = [
        ZoneEvent(track_id="t1", zone_id="z1", event_type="enter", timestamp=0.0),
        ZoneEvent(track_id="t1", zone_id="z1", event_type="exit", timestamp=1.0, dwell_seconds=1.0),
        ZoneEvent(track_id="t2", zone_id="z1", event_type="enter", timestamp=2.0),
        ZoneEvent(track_id="t3", zone_id="z1", event_type="enter", timestamp=3.0),
    ]
    # Max concurrency is 2 (t2 and t3 overlap), t1 already left by then.
    alerts = check_occupancy_alerts("z1", events, threshold_count=2)
    assert alerts == []

    alerts = check_occupancy_alerts("z1", events, threshold_count=1)
    assert len(alerts) == 1
    assert alerts[0].value == 2
