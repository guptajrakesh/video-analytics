"""Watches a local folder or mounted network share for new video files and
enqueues them exactly like an upload would.

This is what makes edge mode possible without touching the detection
pipeline: a client's NVR/server already writes recordings to some folder (or
exposes one via an SMB/NFS mount) — point this watcher at it, and the rest of
the pipeline is unaware that the video didn't arrive via the upload endpoint.

Polling rather than filesystem-event-watching (e.g. watchdog's inotify
backend) because network shares (SMB/NFS) don't reliably deliver filesystem
events across the network — polling the directory listing is the robust
option for that case, at the cost of up to `poll_interval` seconds of latency.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime

import redis

from ingestion.common import FrameJob, SourceType
from workers.queue import enqueue

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}


def _load_seen(state_path: str) -> set[str]:
    if not os.path.exists(state_path):
        return set()
    with open(state_path) as f:
        return set(json.load(f))


def _save_seen(state_path: str, seen: set[str]) -> None:
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(sorted(seen), f)


def scan_once(folder: str, seen: set[str]) -> list[str]:
    """Return new video file paths in `folder` not already in `seen`."""
    if not os.path.isdir(folder):
        logger.warning("watch folder does not exist: %s", folder)
        return []

    new_files = []
    for entry in sorted(os.listdir(folder)):
        full_path = os.path.join(folder, entry)
        if full_path in seen:
            continue
        if os.path.splitext(entry)[1].lower() not in VIDEO_EXTENSIONS:
            continue
        if not os.path.isfile(full_path):
            continue
        new_files.append(full_path)
    return new_files


def watch_folder(
    folder: str,
    camera_id: str,
    redis_url: str,
    source_type: SourceType = SourceType.LOCAL_FOLDER,
    poll_interval: float = 10.0,
    state_path: str | None = None,
    run_once: bool = False,
) -> None:
    state_path = state_path or os.path.join(folder, ".watcher_state.json")
    seen = _load_seen(state_path)
    r = redis.Redis.from_url(redis_url, decode_responses=True)

    while True:
        new_files = scan_once(folder, seen)
        for path in new_files:
            job = FrameJob(
                camera_id=camera_id,
                video_path=path,
                source_type=source_type,
                recorded_at=datetime.now(UTC).isoformat(),
            )
            enqueue(r, job)
            seen.add(path)
            logger.info("enqueued new video from %s: %s", source_type.value, path)

        if new_files:
            _save_seen(state_path, seen)

        if run_once:
            return
        time.sleep(poll_interval)
