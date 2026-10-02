"""Live RTSP ingestion: segments a continuous camera stream into fixed-length
video files and enqueues each one exactly like an upload would.

Why segment instead of streaming frames directly into the detector: the
whole pipeline (worker, aggregator, DB writes) is already built around "one
video file -> one FrameJob" (see pipeline/run.py). Segmenting keeps that
contract identical for RTSP as for uploads/folder-watched files — the
worker doesn't know or care that a segment came from a live camera a moment
ago rather than a file someone uploaded. True frame-level streaming would
cut latency further, but footfall/dwell/queue analytics don't need
sub-second freshness, and reusing the file-based pipeline avoids a second,
parallel processing path to maintain.

RTSP connections drop — routers reboot, cameras reboot, networks blip — so
this reconnects with backoff rather than crashing the whole ingestion
process on a transient failure.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import UTC, datetime

import cv2
import redis

from ingestion.common import FrameJob, SourceType
from workers.queue import enqueue

logger = logging.getLogger(__name__)


def _open_capture(rtsp_url: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(rtsp_url)
    # Without this, a VideoCapture that is read slower than the camera's
    # live framerate silently falls behind and serves stale buffered
    # frames — fine for file playback, wrong for a live feed where we want
    # the current moment, not whatever got buffered minutes ago.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def record_segments(
    rtsp_url: str,
    camera_id: str,
    redis_url: str,
    segment_seconds: float = 30.0,
    storage_root: str | None = None,
    reconnect_backoff_seconds: float = 5.0,
    max_reconnect_backoff_seconds: float = 60.0,
    run_once_segment: bool = False,
) -> None:
    storage_root = storage_root or os.environ.get("VIDEO_STORAGE_ROOT", "./data/uploads")
    os.makedirs(storage_root, exist_ok=True)
    r = redis.Redis.from_url(redis_url, decode_responses=True)

    backoff = reconnect_backoff_seconds
    while True:
        cap = _open_capture(rtsp_url)
        if not cap.isOpened():
            logger.warning("could not open RTSP stream %s, retrying in %.0fs", rtsp_url, backoff)
            cap.release()
            time.sleep(backoff)
            backoff = min(backoff * 2, max_reconnect_backoff_seconds)
            continue

        backoff = reconnect_backoff_seconds  # reset after a successful connect
        try:
            _record_until_disconnected(cap, camera_id, r, segment_seconds, storage_root)
        except Exception:
            logger.exception("RTSP capture loop failed for %s, reconnecting", rtsp_url)
        finally:
            cap.release()

        if run_once_segment:
            return
        logger.info("RTSP stream %s disconnected, reconnecting in %.0fs", rtsp_url, backoff)
        time.sleep(backoff)


def _record_until_disconnected(
    cap: cv2.VideoCapture,
    camera_id: str,
    r: redis.Redis,
    segment_seconds: float,
    storage_root: str,
) -> None:
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0  # many RTSP sources misreport fps; 15 is a safe floor
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames_per_segment = max(1, int(fps * segment_seconds))

    while True:
        # Read the first frame before creating the VideoWriter at all: if
        # the stream is mid-disconnect, cap.read() fails immediately, and a
        # VideoWriter that never received a single frame may not actually
        # create its output file on this platform/codec combination —
        # discovered by a real reconnect-cycle soak test, where the
        # resulting os.remove() on a nonexistent file crashed the grabber.
        ret, frame = cap.read()
        if not ret:
            return  # signals disconnect to the caller's reconnect loop

        segment_path = os.path.join(storage_root, f"{uuid.uuid4().hex}.mp4")
        writer = cv2.VideoWriter(segment_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        segment_start = datetime.now(UTC)
        writer.write(frame)
        frame_count = 1

        while frame_count < frames_per_segment:
            ret, frame = cap.read()
            if not ret:
                break
            writer.write(frame)
            frame_count += 1

        writer.release()

        job = FrameJob(
            camera_id=camera_id,
            video_path=segment_path,
            source_type=SourceType.RTSP,
            recorded_at=segment_start.isoformat(),
        )
        enqueue(r, job)
        logger.info("enqueued RTSP segment for %s: %s (%d frames)", camera_id, segment_path, frame_count)

        if frame_count < frames_per_segment:
            return  # stream ended mid-segment -> treat as disconnect, let caller reconnect
