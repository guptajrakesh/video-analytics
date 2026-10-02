"""One still image per camera, used by the setup screen to draw zones and the
entrance line on, and by the heatmap to overlay density on the real scene.

Zone coordinates are stored in this frame's pixel space, so the frame must
come from the same camera/resolution the pipeline will process. It is taken
from the first video a camera receives (upload, watched folder or RTSP
segment) and can be replaced explicitly.
"""

from __future__ import annotations

import hashlib
import os
import re

import cv2

REFERENCE_FRAME_DIR = os.environ.get("REFERENCE_FRAME_DIR", "./data/reference_frames")


def reference_frame_path(camera_id: str) -> str:
    # Camera ids are free text; keep the filename safe and unique.
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", camera_id)[:60]
    digest = hashlib.sha1(camera_id.encode("utf-8")).hexdigest()[:8]
    return os.path.join(REFERENCE_FRAME_DIR, f"{safe}-{digest}.jpg")


def has_reference_frame(camera_id: str) -> bool:
    return os.path.exists(reference_frame_path(camera_id))


def save_reference_frame_from_video(camera_id: str, video_path: str, overwrite: bool = False) -> bool:
    """Returns True if a frame was written."""
    path = reference_frame_path(camera_id)
    if os.path.exists(path) and not overwrite:
        return False
    cap = cv2.VideoCapture(video_path)
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        return False
    os.makedirs(REFERENCE_FRAME_DIR, exist_ok=True)
    return bool(cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90]))


def save_reference_frame_from_image(camera_id: str, image_bytes: bytes) -> bool:
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return False
    os.makedirs(REFERENCE_FRAME_DIR, exist_ok=True)
    return bool(cv2.imwrite(reference_frame_path(camera_id), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]))


def reference_frame_size(camera_id: str) -> tuple[int, int] | None:
    """(width, height) of the stored frame, or None."""
    path = reference_frame_path(camera_id)
    if not os.path.exists(path):
        return None
    frame = cv2.imread(path)
    if frame is None:
        return None
    height, width = frame.shape[:2]
    return width, height


def delete_reference_frame(camera_id: str) -> None:
    path = reference_frame_path(camera_id)
    if os.path.exists(path):
        os.remove(path)
