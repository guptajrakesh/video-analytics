"""YOLOX person detector wrapper.

Only the `person` class (COCO class index 0) is kept — this product counts
people, not the other 79 COCO categories, and filtering early keeps the
tracker's input clean.

Preprocessing follows the standard YOLOX letterbox resize (pad to a square
input size, preserving aspect ratio) because the pretrained weights were
trained with this exact preprocessing; using a different resize strategy
would silently hurt accuracy without raising an error.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import torch
from torchvision.ops import nms

from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead

from pipeline.tracker import Detection

PERSON_CLASS_ID = 0  # COCO class index for "person"

# (depth, width, depthwise) per variant — must match the exact architecture
# the released checkpoint was trained with, or load_state_dict will silently
# mismatch keys (see yolox/exp/default/yolox_<variant>.py upstream).
_VARIANTS = {
    "nano": dict(depth=0.33, width=0.25, depthwise=True),
    "tiny": dict(depth=0.33, width=0.375, depthwise=False),
    "s": dict(depth=0.33, width=0.50, depthwise=False),
}

_DEFAULT_WEIGHTS = os.path.join(os.path.dirname(__file__), "weights", "yolox_nano.pth")


def _build_model(variant: str) -> torch.nn.Module:
    cfg = _VARIANTS[variant]
    in_channels = [256, 512, 1024]
    backbone = YOLOPAFPN(cfg["depth"], cfg["width"], in_channels=in_channels, depthwise=cfg["depthwise"])
    head = YOLOXHead(80, cfg["width"], in_channels=in_channels, depthwise=cfg["depthwise"])
    return YOLOX(backbone, head)


def letterbox(frame: np.ndarray, target_size: tuple[int, int]) -> tuple[np.ndarray, float]:
    """Resize preserving aspect ratio, pad with 114 (YOLOX's convention) to
    reach target_size exactly. Returns the padded image and the scale ratio
    needed to map detections back to the original frame."""
    h, w = frame.shape[:2]
    target_h, target_w = target_size
    ratio = min(target_h / h, target_w / w)

    resized = cv2.resize(frame, (int(w * ratio), int(h * ratio)), interpolation=cv2.INTER_LINEAR)
    padded = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
    padded[: resized.shape[0], : resized.shape[1]] = resized
    return padded, ratio


class PersonDetector:
    def __init__(
        self,
        weights_path: str = _DEFAULT_WEIGHTS,
        variant: str = "nano",
        input_size: tuple[int, int] = (416, 416),
        conf_thresh: float = 0.3,
        nms_thresh: float = 0.45,
        device: str = "cpu",
    ):
        self.input_size = input_size
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh
        self.device = torch.device(device)

        self.model = _build_model(variant)
        checkpoint = torch.load(weights_path, map_location="cpu")
        state_dict = checkpoint.get("model", checkpoint)
        self.model.load_state_dict(state_dict)
        self.model.to(self.device).eval()

    @torch.no_grad()
    def detect(self, frame: np.ndarray) -> list[Detection]:
        padded, ratio = letterbox(frame, self.input_size)
        tensor = torch.from_numpy(padded).permute(2, 0, 1).float().unsqueeze(0).to(self.device)

        raw_output = self.model(tensor)[0]  # [num_anchors, 85], already decoded to padded-image pixel space

        boxes_cxcywh = raw_output[:, :4]
        obj_conf = raw_output[:, 4]
        class_scores = raw_output[:, 5:]

        person_score = obj_conf * class_scores[:, PERSON_CLASS_ID]
        keep = person_score >= self.conf_thresh
        if keep.sum() == 0:
            return []

        boxes_cxcywh = boxes_cxcywh[keep]
        scores = person_score[keep]

        x1 = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2
        y1 = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2
        x2 = boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] / 2
        y2 = boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] / 2
        boxes_xyxy = torch.stack([x1, y1, x2, y2], dim=1)

        nms_keep = nms(boxes_xyxy, scores, self.nms_thresh)
        boxes_xyxy = boxes_xyxy[nms_keep] / ratio  # map back to original frame coordinates
        scores = scores[nms_keep]

        return [
            Detection(box=tuple(box.tolist()), score=float(score))
            for box, score in zip(boxes_xyxy, scores)
        ]
