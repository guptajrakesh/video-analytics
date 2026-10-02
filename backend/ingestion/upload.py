"""Handles an uploaded video file: persist it, then enqueue a FrameJob.

Storage is a local directory here (STORAGE_ROOT). In docker-compose this
directory is a volume that can equally be backed by a mounted MinIO/S3
bucket later without changing this function's logic — the detection worker
only needs a filesystem path it can open with cv2.VideoCapture.
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime

import redis

from ingestion.common import FrameJob, SourceType
from workers.queue import enqueue

STORAGE_ROOT = os.environ.get("VIDEO_STORAGE_ROOT", "./data/uploads")


def save_and_enqueue(r: redis.Redis, camera_id: str, filename: str, file_obj) -> FrameJob:
    os.makedirs(STORAGE_ROOT, exist_ok=True)
    ext = os.path.splitext(filename)[1] or ".mp4"
    dest_path = os.path.join(STORAGE_ROOT, f"{uuid.uuid4().hex}{ext}")

    with open(dest_path, "wb") as dest:
        shutil.copyfileobj(file_obj, dest)

    job = FrameJob(
        camera_id=camera_id,
        video_path=dest_path,
        source_type=SourceType.UPLOAD,
        recorded_at=datetime.now(UTC).isoformat(),
    )
    enqueue(r, job)
    return job
