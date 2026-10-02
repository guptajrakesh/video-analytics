"""Edge-mode sync worker: pushes each fully-processed video's derived rows
(tracks, footfall/zone events, position samples) to the central API, never
the video file itself. See the plan's "Deployment Modes" section and
api/main.py's /sync/ingest endpoint.

Syncs at video granularity (one video = one atomic batch) rather than a
time-based cursor per table: it's the natural unit the worker already
produces results in, and marking Video.synced_at after a successful push
gives a simple, resumable "what's left to sync" query with no separate
cursor state to keep consistent across a crash.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from models.db import get_session
from models.schema import FootfallEvent, PositionSample, Track, Video, ZoneEvent

logger = logging.getLogger(__name__)


def _serialize_batch(video: Video, tracks, footfall_events, zone_events, position_samples) -> dict:
    return {
        "video": {
            "id": video.id,
            "camera_id": video.camera_id,
            "source_type": video.source_type.value,
            "recorded_at": video.recorded_at.isoformat(),
            "processed_at": video.processed_at.isoformat() if video.processed_at else None,
        },
        "tracks": [
            {
                "id": t.id,
                "video_id": t.video_id,
                "camera_id": t.camera_id,
                "local_track_id": t.local_track_id,
                "first_seen": t.first_seen.isoformat(),
                "last_seen": t.last_seen.isoformat(),
            }
            for t in tracks
        ],
        "footfall_events": [
            {
                "id": e.id,
                "camera_id": e.camera_id,
                "track_id": e.track_id,
                "direction": e.direction,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in footfall_events
        ],
        "zone_events": [
            {
                "id": e.id,
                "zone_id": e.zone_id,
                "track_id": e.track_id,
                "event_type": e.event_type,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in zone_events
        ],
        "position_samples": [
            {
                "id": p.id,
                "camera_id": p.camera_id,
                "track_id": p.track_id,
                "x": p.x,
                "y": p.y,
                "occurred_at": p.occurred_at.isoformat(),
            }
            for p in position_samples
        ],
    }


def sync_once(central_api_url: str, timeout_seconds: float = 30.0) -> int:
    """Pushes every not-yet-synced, fully-processed video. Returns how many
    were synced. Each video is committed independently so a mid-run failure
    (e.g. the central API becomes unreachable on video 5 of 20) doesn't lose
    the 4 that already succeeded."""
    synced_count = 0
    with get_session() as session:
        pending_video_ids = [
            v.id for v in session.query(Video.id).filter(Video.status == "done", Video.synced_at.is_(None)).all()
        ]

    for video_id in pending_video_ids:
        with get_session() as session:
            video = session.get(Video, video_id)
            if video is None or video.synced_at is not None:
                continue  # raced with something else, or already handled

            track_ids = [t.id for t in session.query(Track.id).filter(Track.video_id == video_id).all()]
            tracks = session.query(Track).filter(Track.video_id == video_id).all()
            footfall_events = (
                session.query(FootfallEvent).filter(FootfallEvent.track_id.in_(track_ids)).all() if track_ids else []
            )
            zone_events = session.query(ZoneEvent).filter(ZoneEvent.track_id.in_(track_ids)).all() if track_ids else []
            position_samples = (
                session.query(PositionSample).filter(PositionSample.track_id.in_(track_ids)).all() if track_ids else []
            )

            payload = _serialize_batch(video, tracks, footfall_events, zone_events, position_samples)

            response = requests.post(f"{central_api_url}/sync/ingest", json=payload, timeout=timeout_seconds)
            response.raise_for_status()

            video.synced_at = datetime.now(timezone.utc)
            synced_count += 1
            logger.info("synced video %s (%d tracks, %d footfall, %d zone events)", video_id, len(tracks), len(footfall_events), len(zone_events))

    return synced_count


def run_sync_loop(central_api_url: str, interval_seconds: float = 60.0) -> None:
    logger.info("sync worker started, pushing to %s every %.0fs", central_api_url, interval_seconds)
    while True:
        try:
            n = sync_once(central_api_url)
            if n:
                logger.info("sync cycle complete: %d video(s) synced", n)
        except requests.RequestException:
            logger.warning("central API unreachable, will retry next cycle", exc_info=True)
        except Exception:
            logger.exception("unexpected error in sync cycle")
        time.sleep(interval_seconds)
