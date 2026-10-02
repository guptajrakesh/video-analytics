"""Unit tests for the RTSP segment-recording logic using a fake capture
object instead of a real camera/RTSP server.

A live-stream soak test was attempted against a local mediamtx + ffmpeg
RTSP rig during development; the reconnect/backoff logic was verified
correctly against genuine connection failures (confirmed exponential
backoff on an unreachable host), but full live-frame delivery hit a local
environment-specific RTSP interop issue (this machine's ffmpeg 9.0.1 build
not receiving RTP data from any RTSP server tested, including a
version-pinned one) unrelated to this module's code — see README's Known
Limitations. These tests instead verify the segment-splitting,
disconnect-detection, and the exact zero-frame bug found during that soak
test, deterministically and without any network dependency.
"""

import os

import cv2
import numpy as np
import pytest

from ingestion.rtsp_grabber import _record_until_disconnected
from workers.queue import QUEUE_KEY


class FakeCapture:
    """Stands in for cv2.VideoCapture: serves `frame_count` synthetic frames
    then reports disconnect (read() -> False, None), like a real camera
    stream ending or dropping."""

    def __init__(self, frame_count: int, fps: float = 10.0, width: int = 64, height: int = 48):
        self.fps = fps
        self.width = width
        self.height = height
        self._remaining = frame_count
        self._frame = np.zeros((height, width, 3), dtype=np.uint8)

    def get(self, prop_id):
        return {cv2.CAP_PROP_FPS: self.fps, cv2.CAP_PROP_FRAME_WIDTH: self.width, cv2.CAP_PROP_FRAME_HEIGHT: self.height}[prop_id]

    def read(self):
        if self._remaining <= 0:
            return False, None
        self._remaining -= 1
        return True, self._frame


class FakeRedis:
    """Matches the subset of the redis.Redis interface enqueue() uses."""

    def __init__(self):
        self.pushed: list[str] = []

    def rpush(self, key, value):
        assert key == QUEUE_KEY
        self.pushed.append(value)


def test_zero_frames_enqueues_nothing_and_creates_no_file(tmp_path):
    # This is exactly the case that crashed the grabber during the real
    # soak test: a VideoWriter is never created at all when the very first
    # read fails, so there's nothing to clean up and nothing to enqueue.
    cap = FakeCapture(frame_count=0)
    r = FakeRedis()

    _record_until_disconnected(cap, "cam1", r, segment_seconds=1.0, storage_root=str(tmp_path))

    assert r.pushed == []
    assert list(tmp_path.iterdir()) == []


def test_full_segment_then_second_segment_then_disconnect(tmp_path):
    fps = 10.0
    segment_seconds = 1.0  # -> 10 frames per segment
    cap = FakeCapture(frame_count=25)  # 2 full segments (20) + 5 leftover frames
    r = FakeRedis()

    _record_until_disconnected(cap, "cam1", r, segment_seconds=segment_seconds, storage_root=str(tmp_path))

    # 2 full segments enqueued, then the 5 leftover frames as a final
    # partial segment (treated as a disconnect once it falls short).
    assert len(r.pushed) == 3

    written_files = list(tmp_path.iterdir())
    assert len(written_files) == 3

    frame_counts = []
    for f in written_files:
        video_cap = cv2.VideoCapture(str(f))
        frame_counts.append(int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        video_cap.release()

    assert sorted(frame_counts) == [5, 10, 10]


def test_partial_segment_is_enqueued_not_discarded(tmp_path):
    # A stream that drops mid-segment should still enqueue whatever was
    # captured — partial footage is still useful for counting/tracking,
    # not just complete segments.
    cap = FakeCapture(frame_count=3)
    r = FakeRedis()

    _record_until_disconnected(cap, "cam1", r, segment_seconds=10.0, storage_root=str(tmp_path))  # frames_per_segment=100

    assert len(r.pushed) == 1
    written_files = list(tmp_path.iterdir())
    assert len(written_files) == 1
    video_cap = cv2.VideoCapture(str(written_files[0]))
    assert int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 3
    video_cap.release()


def test_enqueued_job_has_rtsp_source_type_and_correct_camera(tmp_path):
    import dataclasses
    import json

    from ingestion.common import SourceType

    cap = FakeCapture(frame_count=3)
    r = FakeRedis()

    _record_until_disconnected(cap, "cam-xyz", r, segment_seconds=10.0, storage_root=str(tmp_path))

    payload = json.loads(r.pushed[0])
    assert payload["camera_id"] == "cam-xyz"
    assert payload["source_type"] == SourceType.RTSP.value
