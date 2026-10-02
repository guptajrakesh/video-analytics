"""Shared ingestion contract.

Every input mode (upload, local-folder watcher, RTSP) must normalize into a
FrameJob before it reaches the detection queue. The pipeline/worker code never
needs to know whether a frame originated from an uploaded file, a client's
local folder, or a live camera — that's the whole point of this abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceType(str, Enum):
    UPLOAD = "upload"
    LOCAL_FOLDER = "local_folder"
    NETWORK_SHARE = "network_share"
    RTSP = "rtsp"


@dataclass(frozen=True, slots=True)
class FrameJob:
    """One unit of work for the detection worker: a single decoded video file
    (identified by a local path readable by the worker) tied to a camera and
    a source type. The worker decodes frames itself; we queue per-video jobs
    rather than per-frame jobs to keep the queue small and let the worker
    control its own frame sampling rate.
    """

    camera_id: str
    video_path: str
    source_type: SourceType
    recorded_at: str  # ISO 8601 timestamp of when this video/segment started
