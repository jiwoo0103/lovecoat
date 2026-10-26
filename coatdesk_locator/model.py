from __future__ import annotations

import math

import torch
from torchvision.models.detection import ssdlite320_mobilenet_v3_large
from torchvision.models.detection.anchor_utils import DefaultBoxGenerator

SCORE_THRESHOLD = .50
CROP_MARGIN = .08


def build_detector(base=None):
    model = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=2,
                                         score_thresh=.05, detections_per_img=10, topk_candidates=100)
    # Same anchor count, scales adapted in advance to a small placard in a full photo.
    model.anchor_generator = DefaultBoxGenerator([[2, 3] for _ in range(6)], min_ratio=.10, max_ratio=.70)
    if base is not None:
        original = torch.load(base, map_location="cpu", weights_only=True)
        target = model.state_dict()
        compatible = {k: v for k, v in original.items() if k in target and v.shape == target[k].shape}
        missing, unexpected = model.load_state_dict(compatible, strict=False)
        if unexpected or any("classification_head" not in k for k in missing):
            raise ValueError("Unexpected pretrained-model incompatibility")
    return model


def choose_detection(prediction, width, height):
    if len(prediction["scores"]) == 0:
        return {"valid": False, "score": 0., "box": None, "crop_box": None}
    index = int(prediction["scores"].argmax())
    score = float(prediction["scores"][index])
    box = [float(x) for x in prediction["boxes"][index]]
    x0, y0, x1, y1 = box
    valid = score >= SCORE_THRESHOLD and x1 > x0 and y1 > y0
    dx, dy = (x1 - x0) * CROP_MARGIN, (y1 - y0) * CROP_MARGIN
    crop = [max(0, math.floor(x0 - dx)), max(0, math.floor(y0 - dy)),
            min(width, math.ceil(x1 + dx)), min(height, math.ceil(y1 + dy))]
    return {"valid": valid, "score": score, "box": box, "crop_box": crop}


def overlap(predicted, truth):
    if predicted is None:
        return 0., 0.
    x0, y0 = max(predicted[0], truth[0]), max(predicted[1], truth[1])
    x1, y1 = min(predicted[2], truth[2]), min(predicted[3], truth[3])
    intersection = max(0., x1 - x0) * max(0., y1 - y0)
    actual = (truth[2] - truth[0]) * (truth[3] - truth[1])
    detected = (predicted[2] - predicted[0]) * (predicted[3] - predicted[1])
    return intersection / max(1e-8, actual + detected - intersection), intersection / max(1e-8, actual)
