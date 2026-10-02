"""A clean-room, dependency-light reimplementation of ByteTrack
(Zhang et al., 2022 — MIT licensed algorithm) using only numpy + scipy.

The original reference implementation depends on `cython_bbox`, a compiled
C extension that needs a C compiler to build from source — an unnecessary
build-tool dependency for an MVP that should install with plain `pip
install` on any machine. This reimplements the same core algorithm
(Kalman-filter motion prediction + two-stage high/low confidence IoU
matching via the Hungarian algorithm) with pure numpy for IoU and
`scipy.optimize.linear_sum_assignment` for assignment, both of which ship
prebuilt wheels everywhere.

Only the parts needed for this product are implemented: a constant-velocity
Kalman filter over [center_x, center_y, aspect_ratio, height], and the
two-stage association that gives ByteTrack its name (match high-score boxes
first, then try to recover true detections from the low-score pool against
whatever tracks remain unmatched).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np
from scipy.optimize import linear_sum_assignment

Box = tuple[float, float, float, float]  # x1, y1, x2, y2


def _xyxy_to_cxcyah(box: Box) -> np.ndarray:
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    cx = x1 + w / 2
    cy = y1 + h / 2
    aspect = w / h if h > 0 else 0.0
    return np.array([cx, cy, aspect, h], dtype=float)


def _cxcyah_to_xyxy(state: np.ndarray) -> Box:
    cx, cy, aspect, h = state[:4]
    w = aspect * h
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def iou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    """Vectorized IoU between two sets of xyxy boxes. Pure numpy — this is
    the one piece cython_bbox normally accelerates; at MVP frame rates and
    typical per-frame detection counts (tens, not thousands), plain numpy is
    fast enough and needs no compiler."""
    if len(boxes_a) == 0 or len(boxes_b) == 0:
        return np.zeros((len(boxes_a), len(boxes_b)), dtype=float)

    a = boxes_a[:, None, :]
    b = boxes_b[None, :, :]

    inter_x1 = np.maximum(a[..., 0], b[..., 0])
    inter_y1 = np.maximum(a[..., 1], b[..., 1])
    inter_x2 = np.minimum(a[..., 2], b[..., 2])
    inter_y2 = np.minimum(a[..., 3], b[..., 3])

    inter_w = np.clip(inter_x2 - inter_x1, 0, None)
    inter_h = np.clip(inter_y2 - inter_y1, 0, None)
    inter_area = inter_w * inter_h

    area_a = np.clip(a[..., 2] - a[..., 0], 0, None) * np.clip(a[..., 3] - a[..., 1], 0, None)
    area_b = np.clip(b[..., 2] - b[..., 0], 0, None) * np.clip(b[..., 3] - b[..., 1], 0, None)

    union = area_a + area_b - inter_area
    return np.where(union > 0, inter_area / union, 0.0)


class _KalmanBoxTracker:
    """Constant-velocity Kalman filter over [cx, cy, aspect, h, vcx, vcy, va, vh]."""

    def __init__(self, box: Box):
        self.state = np.zeros(8, dtype=float)
        self.state[:4] = _xyxy_to_cxcyah(box)

        self._F = np.eye(8)
        for i in range(4):
            self._F[i, i + 4] = 1.0  # position += velocity

        self._H = np.zeros((4, 8))
        self._H[:4, :4] = np.eye(4)

        self.P = np.eye(8) * 10.0
        self._Q = np.eye(8) * 1.0  # process noise
        self._R = np.eye(4) * 1.0  # measurement noise

    def predict(self) -> Box:
        self.state = self._F @ self.state
        self.P = self._F @ self.P @ self._F.T + self._Q
        return _cxcyah_to_xyxy(self.state)

    def update(self, box: Box) -> None:
        z = _xyxy_to_cxcyah(box)
        y = z - self._H @ self.state
        S = self._H @ self.P @ self._H.T + self._R
        K = self.P @ self._H.T @ np.linalg.inv(S)
        self.state = self.state + K @ y
        self.P = (np.eye(8) - K @ self._H) @ self.P

    @property
    def box(self) -> Box:
        return _cxcyah_to_xyxy(self.state)


class TrackState(Enum):
    TRACKED = auto()
    LOST = auto()
    REMOVED = auto()


@dataclass
class Track:
    track_id: int
    kalman: _KalmanBoxTracker
    score: float
    state: TrackState = TrackState.TRACKED
    frames_lost: int = 0
    hits: int = 1

    @property
    def box(self) -> Box:
        return self.kalman.box

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) / 2, (y1 + y2) / 2)


@dataclass
class Detection:
    box: Box
    score: float


class ByteTracker:
    """Two-stage ByteTrack association.

    Stage 1: match high-score detections against predicted track boxes by
    IoU (Hungarian algorithm), for all currently tracked + recently-lost
    tracks.
    Stage 2: try to rescue true detections from the low-score pool by
    matching them against whatever tracks stage 1 left unmatched — this is
    ByteTrack's key idea: a low-confidence box is often a real, partially
    occluded person rather than noise, so give it a chance against tracks
    that just went unmatched, instead of discarding it outright.

    Defaults below (match_iou_thresh=0.15, max_frames_lost=45) were tuned
    against a real dense mall-corridor clip, not synthetic data — a tighter
    IoU threshold caused heavy track fragmentation at CPU inference speed
    (~10fps on YOLOX-Nano) because a person's box moves more between
    processed frames than it would at full 30fps. Dense cross-occlusion
    (people walking past each other) still causes some ID switches even
    with these defaults — that's a known limitation to revisit with
    per-camera calibration once real site footage is available, not
    something fully solved by hyperparameters alone.
    """

    def __init__(
        self,
        high_score_thresh: float = 0.6,
        low_score_thresh: float = 0.1,
        match_iou_thresh: float = 0.15,
        max_frames_lost: int = 45,
    ):
        self.high_score_thresh = high_score_thresh
        self.low_score_thresh = low_score_thresh
        self.match_iou_thresh = match_iou_thresh
        self.max_frames_lost = max_frames_lost

        self._tracks: list[Track] = []
        self._next_id = 1

    def update(self, detections: list[Detection]) -> list[Track]:
        for track in self._tracks:
            track.kalman.predict()

        high_dets = [d for d in detections if d.score >= self.high_score_thresh]
        low_dets = [d for d in detections if self.low_score_thresh <= d.score < self.high_score_thresh]

        active_tracks = [t for t in self._tracks if t.state != TrackState.REMOVED]

        matched_track_ids, matched_high_dets, unmatched_high_dets = self._match(active_tracks, high_dets)

        still_unmatched_tracks = [t for t in active_tracks if t.track_id not in matched_track_ids]
        matched_track_ids_2, matched_low_dets, _ = self._match(still_unmatched_tracks, low_dets)

        matched_ids = matched_track_ids | matched_track_ids_2
        id_to_track = {t.track_id: t for t in active_tracks}

        for track_id, det in {**matched_high_dets, **matched_low_dets}.items():
            track = id_to_track[track_id]
            track.kalman.update(det.box)
            track.score = det.score
            track.state = TrackState.TRACKED
            track.frames_lost = 0
            track.hits += 1

        for track in active_tracks:
            if track.track_id not in matched_ids:
                track.frames_lost += 1
                track.state = TrackState.LOST
                if track.frames_lost > self.max_frames_lost:
                    track.state = TrackState.REMOVED

        for det in unmatched_high_dets:
            new_track = Track(track_id=self._next_id, kalman=_KalmanBoxTracker(det.box), score=det.score)
            self._next_id += 1
            self._tracks.append(new_track)

        self._tracks = [t for t in self._tracks if t.state != TrackState.REMOVED]
        return [t for t in self._tracks if t.state == TrackState.TRACKED]

    def _match(
        self, tracks: list[Track], dets: list[Detection]
    ) -> tuple[set[int], dict[int, Detection], list[Detection]]:
        if not tracks or not dets:
            return set(), {}, list(dets)

        track_boxes = np.array([t.box for t in tracks])
        det_boxes = np.array([d.box for d in dets])
        ious = iou_matrix(track_boxes, det_boxes)

        cost = 1.0 - ious
        row_idx, col_idx = linear_sum_assignment(cost)

        matched_ids: set[int] = set()
        matched: dict[int, Detection] = {}
        matched_det_indices: set[int] = set()

        for r, c in zip(row_idx, col_idx):
            if ious[r, c] >= self.match_iou_thresh:
                matched_ids.add(tracks[r].track_id)
                matched[tracks[r].track_id] = dets[c]
                matched_det_indices.add(c)

        unmatched = [d for i, d in enumerate(dets) if i not in matched_det_indices]
        return matched_ids, matched, unmatched
