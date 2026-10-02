from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from models.schema import Vertical, ZoneType


def _check_points(points: list[list[float]], minimum: int, exact: int | None, what: str) -> list[list[float]]:
    if exact is not None and len(points) != exact:
        raise ValueError(f"{what} needs exactly {exact} points")
    if len(points) < minimum:
        raise ValueError(f"{what} needs at least {minimum} points")
    for point in points:
        if len(point) != 2:
            raise ValueError(f"{what} points must be [x, y] pairs")
    return points


class CameraCreate(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    vertical: Vertical = Vertical.GENERIC
    rtsp_url: str | None = None
    entrance_line: list[list[float]] | None = None  # [[x1,y1],[x2,y2]]

    @field_validator("entrance_line")
    @classmethod
    def _line(cls, v):
        return None if v is None else _check_points(v, 2, 2, "entrance_line")


class CameraUpdate(BaseModel):
    """All fields optional; only fields present in the request are changed
    (so entrance_line: null clears the line)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    vertical: Vertical | None = None
    rtsp_url: str | None = None
    entrance_line: list[list[float]] | None = None

    @field_validator("entrance_line")
    @classmethod
    def _line(cls, v):
        return None if v is None else _check_points(v, 2, 2, "entrance_line")


class CameraOut(BaseModel):
    id: str
    name: str
    vertical: Vertical
    rtsp_url: str | None
    entrance_line: list | None
    has_reference_frame: bool = False

    class Config:
        from_attributes = True


class ZoneTemplateOut(BaseModel):
    vertical: Vertical
    zone_types: list[ZoneType]


class ZoneCreate(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    camera_id: str
    name: str = Field(min_length=1, max_length=200)
    zone_type: ZoneType = ZoneType.GENERIC
    polygon: list[list[float]]
    wait_time_alert_seconds: float | None = Field(default=None, gt=0)
    max_occupancy_alert: int | None = Field(default=None, gt=0)
    alert_cooldown_seconds: float = Field(default=60.0, ge=0)

    @field_validator("polygon")
    @classmethod
    def _polygon(cls, v):
        return _check_points(v, 3, None, "polygon")


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    zone_type: ZoneType | None = None
    polygon: list[list[float]] | None = None
    wait_time_alert_seconds: float | None = Field(default=None, gt=0)
    max_occupancy_alert: int | None = Field(default=None, gt=0)
    alert_cooldown_seconds: float | None = Field(default=None, ge=0)

    @field_validator("polygon")
    @classmethod
    def _polygon(cls, v):
        return None if v is None else _check_points(v, 3, None, "polygon")


class ZoneOut(BaseModel):
    id: str
    camera_id: str
    name: str
    zone_type: ZoneType
    polygon: list
    wait_time_alert_seconds: float | None
    max_occupancy_alert: int | None
    alert_cooldown_seconds: float = 60.0

    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    video_path: str
    camera_id: str
    queued: bool


class FootfallSummary(BaseModel):
    camera_id: str
    total_in: int
    total_out: int


class FootfallBucket(BaseModel):
    bucket_start: datetime
    in_count: int
    out_count: int


class ZoneOccupancy(BaseModel):
    zone_id: str
    zone_name: str
    zone_type: ZoneType = ZoneType.GENERIC
    current_occupancy: int
    max_occupancy_alert: int | None = None


class AlertOut(BaseModel):
    id: str
    camera_id: str
    zone_id: str
    alert_type: str
    message: str
    value: float
    threshold: float
    occurred_at: datetime
    delivered: bool
    status: str = "open"
    acknowledged_at: datetime | None = None
    occurrences: int = 1
    last_occurred_at: datetime | None = None

    class Config:
        from_attributes = True


class AlertStatusUpdate(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]


class VideoOut(BaseModel):
    id: str
    camera_id: str
    source_type: str
    recorded_at: datetime
    processed_at: datetime | None
    status: str

    class Config:
        from_attributes = True


class QueueStatus(BaseModel):
    pending: int


class OverviewRow(BaseModel):
    camera_id: str
    camera_name: str
    vertical: Vertical
    zones: int
    total_in: int
    total_out: int
    visits: int
    avg_dwell_seconds: float
    open_alerts: int


class HeatmapOut(BaseModel):
    camera_id: str
    cols: int
    rows: int
    frame_width: float
    frame_height: float
    counts: list[list[int]]


class ZoneDwellSummary(BaseModel):
    zone_id: str
    zone_name: str
    visit_count: int
    avg_dwell_seconds: float
    max_dwell_seconds: float


# --- Edge -> central sync payload (see backend/sync/push.py) ---
# One batch = one fully-processed video's derived rows. Never includes the
# video file itself — only structured results already computed on-prem.


class SyncVideo(BaseModel):
    id: str
    camera_id: str
    source_type: str
    recorded_at: datetime
    processed_at: datetime | None


class SyncTrack(BaseModel):
    id: str
    video_id: str
    camera_id: str
    local_track_id: int
    first_seen: datetime
    last_seen: datetime


class SyncFootfallEvent(BaseModel):
    id: str
    camera_id: str
    track_id: str
    direction: str
    occurred_at: datetime


class SyncZoneEvent(BaseModel):
    id: str
    zone_id: str
    track_id: str
    event_type: str
    occurred_at: datetime


class SyncPositionSample(BaseModel):
    id: str
    camera_id: str
    track_id: str
    x: float
    y: float
    occurred_at: datetime


class SyncBatch(BaseModel):
    video: SyncVideo
    tracks: list[SyncTrack]
    footfall_events: list[SyncFootfallEvent]
    zone_events: list[SyncZoneEvent]
    position_samples: list[SyncPositionSample]
