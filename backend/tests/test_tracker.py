from pipeline.tracker import ByteTracker, Detection, iou_matrix
import numpy as np


def test_iou_matrix_identical_boxes_is_one():
    boxes = np.array([[0, 0, 10, 10]], dtype=float)
    result = iou_matrix(boxes, boxes)
    assert result[0, 0] == 1.0


def test_iou_matrix_disjoint_boxes_is_zero():
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[100, 100, 110, 110]], dtype=float)
    result = iou_matrix(a, b)
    assert result[0, 0] == 0.0


def test_iou_matrix_partial_overlap():
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[5, 0, 15, 10]], dtype=float)  # half overlap
    result = iou_matrix(a, b)
    # intersection 5x10=50, union 100+100-50=150 -> 1/3
    assert abs(result[0, 0] - (1 / 3)) < 1e-6


def test_single_detection_keeps_same_id_across_frames():
    tracker = ByteTracker()
    box = (10, 10, 30, 30)

    tracks1 = tracker.update([Detection(box=box, score=0.9)])
    assert len(tracks1) == 1
    id1 = tracks1[0].track_id

    # Move slightly each frame, as a real person walking would.
    for i in range(1, 5):
        moved = (10 + i, 10 + i, 30 + i, 30 + i)
        tracks = tracker.update([Detection(box=moved, score=0.9)])
        assert len(tracks) == 1
        assert tracks[0].track_id == id1


def test_two_separated_detections_get_different_ids():
    tracker = ByteTracker()
    tracks = tracker.update(
        [
            Detection(box=(0, 0, 20, 20), score=0.9),
            Detection(box=(200, 200, 220, 220), score=0.9),
        ]
    )
    assert len(tracks) == 2
    assert tracks[0].track_id != tracks[1].track_id


def test_track_survives_brief_occlusion():
    tracker = ByteTracker(max_frames_lost=5)
    box = (10, 10, 30, 30)
    tracks = tracker.update([Detection(box=box, score=0.9)])
    original_id = tracks[0].track_id

    # Two frames with no detection at all (occluded).
    tracker.update([])
    tracker.update([])

    # Reappears close to where the Kalman filter would predict it.
    tracks = tracker.update([Detection(box=(13, 13, 33, 33), score=0.9)])
    assert len(tracks) == 1
    assert tracks[0].track_id == original_id


def test_low_score_detection_can_continue_existing_track():
    tracker = ByteTracker(high_score_thresh=0.6, low_score_thresh=0.1)
    tracks = tracker.update([Detection(box=(10, 10, 30, 30), score=0.9)])
    original_id = tracks[0].track_id

    # A partially-occluded, low-confidence detection in roughly the same spot
    # should still extend the track rather than being discarded.
    tracks = tracker.update([Detection(box=(11, 11, 31, 31), score=0.2)])
    assert len(tracks) == 1
    assert tracks[0].track_id == original_id


def test_low_score_detection_alone_does_not_spawn_new_track():
    tracker = ByteTracker(high_score_thresh=0.6, low_score_thresh=0.1)
    tracks = tracker.update([Detection(box=(10, 10, 30, 30), score=0.2)])
    assert len(tracks) == 0


def test_track_removed_after_exceeding_max_frames_lost():
    tracker = ByteTracker(max_frames_lost=2)
    tracker.update([Detection(box=(10, 10, 30, 30), score=0.9)])

    tracker.update([])
    tracker.update([])
    tracks = tracker.update([])  # 3rd consecutive miss, exceeds max_frames_lost=2

    # internal track list should have dropped it entirely
    assert tracker._tracks == [] or all(t.state.name == "REMOVED" for t in tracker._tracks)
