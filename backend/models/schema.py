"""ORM schema, shared verbatim by cloud mode and edge mode (edge just points
DATABASE_URL at a local Postgres instead of the central one).

Vertical differences (retail showroom vs. bank branch) are data, not schema:
a Zone's `zone_type` and a Camera's `vertical` are just labels picked from a
template at setup time — see ZONE_TEMPLATES below. No code branches on
vertical.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Vertical(str, enum.Enum):
    RETAIL_SHOWROOM = "retail_showroom"
    BANK_BRANCH = "bank_branch"
    GENERIC = "generic"


class ZoneType(str, enum.Enum):
    ENTRANCE = "entrance"
    DISPLAY_SECTION = "display_section"
    BILLING_COUNTER = "billing_counter"
    LOBBY = "lobby"
    TELLER_COUNTER = "teller_counter"
    GENERIC = "generic"


# Picking a template at camera setup time is just pre-filling zone_type
# choices for the UI — it does not change any pipeline/aggregation code.
ZONE_TEMPLATES: dict[Vertical, list[ZoneType]] = {
    Vertical.RETAIL_SHOWROOM: [ZoneType.ENTRANCE, ZoneType.DISPLAY_SECTION, ZoneType.BILLING_COUNTER],
    Vertical.BANK_BRANCH: [ZoneType.ENTRANCE, ZoneType.LOBBY, ZoneType.TELLER_COUNTER],
}

# Zone types where occupancy should be interpreted as a queue (triggers
# wait-time calculation in the aggregator) rather than plain dwell time.
QUEUE_ZONE_TYPES = {ZoneType.BILLING_COUNTER, ZoneType.TELLER_COUNTER}


class SourceTypeDB(str, enum.Enum):
    UPLOAD = "upload"
    LOCAL_FOLDER = "local_folder"
    NETWORK_SHARE = "network_share"
    RTSP = "rtsp"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    vertical: Mapped[Vertical] = mapped_column(SAEnum(Vertical), default=Vertical.GENERIC)
    rtsp_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Two points [[x1,y1],[x2,y2]] in reference-frame pixel space, defining
    # the line FootfallCounter treats as the camera's entrance/exit boundary.
    # Separate from Zone.polygon (a dwell/queue *area*, not a crossing line)
    # because the aggregator's FootfallCounter and ZoneTracker consume
    # genuinely different geometry — see pipeline/aggregator.py.
    entrance_line: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    zones: Mapped[list["Zone"]] = relationship(back_populates="camera")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    name: Mapped[str] = mapped_column(String)
    zone_type: Mapped[ZoneType] = mapped_column(SAEnum(ZoneType), default=ZoneType.GENERIC)
    # Polygon as a flat list of [x, y] points in reference-frame pixel space.
    polygon: Mapped[list] = mapped_column(JSON)
    # Only meaningful for queue zone types; occupancy dwell past this many
    # seconds triggers an over-threshold alert (M6).
    wait_time_alert_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Concurrent occupancy past this count triggers an over-threshold alert
    # (M6) — e.g. a fire-safety/crowding limit, independent of any single
    # visit's dwell time.
    max_occupancy_alert: Mapped[int | None] = mapped_column(nullable=True)
    # Repeated breaches of the same kind within this window collapse into one
    # alert (with an occurrence count) instead of one alert per visit.
    alert_cooldown_seconds: Mapped[float] = mapped_column(Float, default=60.0, server_default="60")

    camera: Mapped["Camera"] = relationship(back_populates="zones")


class AlertEvent(Base):
    """A threshold breach detected while processing a video — see
    pipeline/alerts.py for the pure-logic detection and sync/webhook.py (or
    the worker's direct call) for delivery."""

    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"))
    alert_type: Mapped[str] = mapped_column(String)  # "queue_wait_exceeded" | "occupancy_exceeded"
    message: Mapped[str] = mapped_column(String)
    value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered: Mapped[bool] = mapped_column(default=False)
    # open -> acknowledged -> resolved. Nullable in the DB so rows written
    # before this column existed read as "open".
    status: Mapped[str] = mapped_column(String, default="open", server_default="open")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How many threshold breaches this alert groups (see zone cooldown), and
    # when the latest one happened; `value` holds the peak.
    occurrences: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Video(Base):
    """One ingested video/segment, from any source type."""

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    source_type: Mapped[SourceTypeDB] = mapped_column(SAEnum(SourceTypeDB))
    video_path: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|processing|done|failed
    # Edge-mode only: set once this video's derived rows (tracks, footfall/
    # zone events, position samples — never the raw video file) have been
    # pushed to the central API. NULL in cloud-mode deployments, where
    # there's no sync step. See backend/sync/push.py.
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Track(Base):
    """A single tracked person within one video, from ByteTrack's local
    track ID (which is only unique within that video, not globally)."""

    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    local_track_id: Mapped[int] = mapped_column()
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FootfallEvent(Base):
    """A track crossing the camera's entrance line — the raw signal behind
    footfall counts."""

    __tablename__ = "footfall_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"))
    direction: Mapped[str] = mapped_column(String)  # "in" | "out"
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ZoneEvent(Base):
    """A track entering or leaving a zone. Dwell/wait time is computed as
    exit.occurred_at - matching enter.occurred_at, done in the aggregator,
    not stored redundantly here to avoid a derived-data bug class."""

    __tablename__ = "zone_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"))
    event_type: Mapped[str] = mapped_column(String)  # "enter" | "exit"
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionSample(Base):
    """Sparse per-track position samples (not every frame — every Nth frame
    is enough for heatmaps/dwell and keeps table size manageable)."""

    __tablename__ = "position_samples"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
