"""Pure-Python aggregation logic: footfall counting, zone dwell/queue time,
and heatmap bucketing.

Deliberately has zero dependency on cv2/torch/the detector — it only
consumes (track_id, point, timestamp) samples, however they were produced.
This is what makes it unit-testable without any ML stack installed, and is
the one piece of the pipeline worth testing thoroughly: the detector just
finds boxes, but this logic is what turns boxes into the business numbers
(footfall, dwell, queue wait-time) a retail/bank client actually pays for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Point = tuple[float, float]


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Standard ray-casting point-in-polygon test."""
    x, y = point
    n = len(polygon)
    inside = False
    x1, y1 = polygon[-1]
    for x2, y2 in polygon:
        if (y1 > y) != (y2 > y):
            x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_intersect:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _side_of_line(point: Point, line_p1: Point, line_p2: Point) -> float:
    """Signed area trick: >0 on one side of the line, <0 on the other."""
    (x1, y1), (x2, y2) = line_p1, line_p2
    x, y = point
    return (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)


@dataclass
class FootfallEvent:
    track_id: str
    direction: str  # "in" | "out"
    timestamp: float


class FootfallCounter:
    """Counts entrance-line crossings. A crossing's direction is determined
    by which side of the line the track was on before vs. after — so the
    line's "in" direction is defined by the sign convention of
    (line_p1 -> line_p2), consistently for every track."""

    def __init__(self, line_p1: Point, line_p2: Point):
        self.line_p1 = line_p1
        self.line_p2 = line_p2
        self._last_side: dict[str, float] = {}

    def update(self, track_id: str, point: Point, timestamp: float) -> FootfallEvent | None:
        side = _side_of_line(point, self.line_p1, self.line_p2)
        prev_side = self._last_side.get(track_id)
        self._last_side[track_id] = side

        if prev_side is None or prev_side == 0 or side == 0:
            return None
        if (prev_side > 0) == (side > 0):
            return None  # no crossing

        direction = "in" if side > 0 else "out"
        return FootfallEvent(track_id=track_id, direction=direction, timestamp=timestamp)


@dataclass
class ZoneEvent:
    track_id: str
    zone_id: str
    event_type: str  # "enter" | "exit"
    timestamp: float
    dwell_seconds: float | None = None  # set on "exit"


class ZoneTracker:
    """Tracks which tracks are currently inside a zone polygon and emits
    enter/exit events with dwell time. One instance per zone."""

    def __init__(self, zone_id: str, polygon: list[Point]):
        self.zone_id = zone_id
        self.polygon = polygon
        self._enter_time: dict[str, float] = {}

    def update(self, track_id: str, point: Point, timestamp: float) -> ZoneEvent | None:
        inside = point_in_polygon(point, self.polygon)
        was_inside = track_id in self._enter_time

        if inside and not was_inside:
            self._enter_time[track_id] = timestamp
            return ZoneEvent(track_id=track_id, zone_id=self.zone_id, event_type="enter", timestamp=timestamp)

        if not inside and was_inside:
            enter_time = self._enter_time.pop(track_id)
            return ZoneEvent(
                track_id=track_id,
                zone_id=self.zone_id,
                event_type="exit",
                timestamp=timestamp,
                dwell_seconds=timestamp - enter_time,
            )

        return None

    def finalize(self, timestamp: float) -> list[ZoneEvent]:
        """Call at end-of-video to close out dwell time for tracks still
        inside the zone when the video ends (otherwise their dwell would be
        silently lost rather than counted)."""
        events = []
        for track_id, enter_time in list(self._enter_time.items()):
            events.append(
                ZoneEvent(
                    track_id=track_id,
                    zone_id=self.zone_id,
                    event_type="exit",
                    timestamp=timestamp,
                    dwell_seconds=timestamp - enter_time,
                )
            )
        self._enter_time.clear()
        return events


@dataclass
class HeatmapGrid:
    """Bucketed position density over a camera's reference frame."""

    cols: int
    rows: int
    frame_width: float
    frame_height: float
    counts: list[list[int]] = field(init=False)

    def __post_init__(self) -> None:
        self.counts = [[0 for _ in range(self.cols)] for _ in range(self.rows)]

    def add(self, point: Point) -> None:
        x, y = point
        col = min(self.cols - 1, max(0, int(x / self.frame_width * self.cols)))
        row = min(self.rows - 1, max(0, int(y / self.frame_height * self.rows)))
        self.counts[row][col] += 1
