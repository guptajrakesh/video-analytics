from pipeline.aggregator import (
    FootfallCounter,
    HeatmapGrid,
    ZoneTracker,
    point_in_polygon,
)

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_point_in_polygon_inside():
    assert point_in_polygon((5, 5), SQUARE) is True


def test_point_in_polygon_outside():
    assert point_in_polygon((15, 5), SQUARE) is False


def test_point_in_polygon_edge_cases_do_not_crash():
    # Points exactly on vertices/edges shouldn't raise; result is allowed to
    # go either way for the boundary itself.
    point_in_polygon((0, 0), SQUARE)
    point_in_polygon((10, 10), SQUARE)


def test_footfall_counts_single_entry():
    # Horizontal entrance line at y=0, "in" side is y>0 by sign convention.
    counter = FootfallCounter(line_p1=(0, 0), line_p2=(10, 0))

    assert counter.update("t1", (5, -1), timestamp=0.0) is None  # first sample, no prior side
    event = counter.update("t1", (5, 1), timestamp=1.0)  # crosses to y>0

    assert event is not None
    assert event.direction == "in"
    assert event.track_id == "t1"


def test_footfall_counts_exit_after_entry():
    counter = FootfallCounter(line_p1=(0, 0), line_p2=(10, 0))
    counter.update("t1", (5, -1), timestamp=0.0)
    counter.update("t1", (5, 1), timestamp=1.0)  # in

    event = counter.update("t1", (5, -1), timestamp=2.0)  # back out
    assert event.direction == "out"


def test_footfall_no_event_when_staying_on_same_side():
    counter = FootfallCounter(line_p1=(0, 0), line_p2=(10, 0))
    counter.update("t1", (5, 1), timestamp=0.0)
    event = counter.update("t1", (6, 2), timestamp=1.0)
    assert event is None


def test_footfall_tracks_are_independent():
    counter = FootfallCounter(line_p1=(0, 0), line_p2=(10, 0))
    counter.update("t1", (5, -1), timestamp=0.0)
    counter.update("t2", (5, -1), timestamp=0.0)

    event1 = counter.update("t1", (5, 1), timestamp=1.0)
    event2 = counter.update("t2", (5, -2), timestamp=1.0)  # t2 didn't cross

    assert event1 is not None
    assert event2 is None


def test_zone_tracker_enter_and_exit_with_dwell():
    tracker = ZoneTracker(zone_id="z1", polygon=SQUARE)

    enter_event = tracker.update("t1", (5, 5), timestamp=10.0)
    assert enter_event.event_type == "enter"

    # still inside -> no event
    assert tracker.update("t1", (6, 6), timestamp=15.0) is None

    exit_event = tracker.update("t1", (20, 20), timestamp=22.0)
    assert exit_event.event_type == "exit"
    assert exit_event.dwell_seconds == 12.0  # 22.0 - 10.0


def test_zone_tracker_finalize_closes_open_dwell():
    tracker = ZoneTracker(zone_id="z1", polygon=SQUARE)
    tracker.update("t1", (5, 5), timestamp=10.0)

    events = tracker.finalize(timestamp=30.0)
    assert len(events) == 1
    assert events[0].event_type == "exit"
    assert events[0].dwell_seconds == 20.0


def test_zone_tracker_finalize_is_idempotent_when_nothing_open():
    tracker = ZoneTracker(zone_id="z1", polygon=SQUARE)
    assert tracker.finalize(timestamp=30.0) == []


def test_zone_tracker_handles_multiple_tracks_independently():
    tracker = ZoneTracker(zone_id="z1", polygon=SQUARE)
    tracker.update("t1", (5, 5), timestamp=0.0)
    tracker.update("t2", (6, 6), timestamp=1.0)

    exit1 = tracker.update("t1", (100, 100), timestamp=5.0)
    assert exit1.dwell_seconds == 5.0

    # t2 should still be open
    remaining = tracker.finalize(timestamp=20.0)
    assert len(remaining) == 1
    assert remaining[0].track_id == "t2"
    assert remaining[0].dwell_seconds == 19.0


def test_heatmap_bucketing_counts_points_into_grid_cells():
    grid = HeatmapGrid(cols=2, rows=2, frame_width=100, frame_height=100)
    grid.add((10, 10))  # top-left cell
    grid.add((90, 90))  # bottom-right cell
    grid.add((20, 20))  # also top-left cell

    assert grid.counts[0][0] == 2
    assert grid.counts[1][1] == 1
    assert grid.counts[0][1] == 0
    assert grid.counts[1][0] == 0


def test_heatmap_clamps_out_of_range_points():
    grid = HeatmapGrid(cols=2, rows=2, frame_width=100, frame_height=100)
    grid.add((-5, -5))
    grid.add((1000, 1000))

    assert grid.counts[0][0] == 1
    assert grid.counts[1][1] == 1
