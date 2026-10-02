from pipeline.alerts import Alert, group_alerts


def _wait(zone, at, value, threshold=5.0):
    return Alert(zone, "queue_wait_exceeded", f"Wait time {value:.1f}s exceeded threshold {threshold:.1f}s", value, threshold, at)


def test_group_alerts_collapses_repeats_within_cooldown():
    groups = group_alerts([_wait("z1", 0, 6), _wait("z1", 10, 9), _wait("z1", 25, 7)], cooldown_seconds=30)
    assert len(groups) == 1
    g = groups[0]
    assert g.occurrences == 3 and g.value == 9 and g.occurred_at == 0 and g.last_occurred_at == 25
    assert "3 times" in g.message and "peak 9.0s" in g.message


def test_group_alerts_window_slides_with_each_new_breach():
    # 0 -> 25 -> 50: each is within 30s of the previous, so still one spell.
    assert len(group_alerts([_wait("z1", 0, 6), _wait("z1", 25, 6), _wait("z1", 50, 6)], 30)) == 1


def test_group_alerts_starts_new_group_after_cooldown():
    groups = group_alerts([_wait("z1", 0, 6), _wait("z1", 100, 8)], cooldown_seconds=30)
    assert [(g.occurred_at, g.occurrences) for g in groups] == [(0, 1), (100, 1)]


def test_group_alerts_keeps_zones_and_types_separate():
    other_type = Alert("z1", "occupancy_exceeded", "m", 12, 10, 1)
    groups = group_alerts([_wait("z1", 0, 6), _wait("z2", 1, 6), other_type], 60)
    assert len(groups) == 3


def test_group_alerts_single_alert_keeps_original_message():
    (g,) = group_alerts([_wait("z1", 0, 6.4)], 60)
    assert g.occurrences == 1 and g.message == "Wait time 6.4s exceeded threshold 5.0s"


def test_group_alerts_zero_cooldown_never_merges():
    assert len(group_alerts([_wait("z1", 0, 6), _wait("z1", 5, 6)], 0)) == 2


def test_group_alerts_handles_unsorted_input():
    groups = group_alerts([_wait("z1", 20, 7), _wait("z1", 0, 6)], 30)
    assert len(groups) == 1 and groups[0].occurred_at == 0 and groups[0].last_occurred_at == 20
