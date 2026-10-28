"""Frozen category inference contract, independent of the training tools."""
from __future__ import annotations

import json
import math

SLEEVE_THRESHOLD = .90
LENGTH_THRESHOLD = .90
POLICY_VERSION = "category-v241-configurable-s90-l90"
DEFAULT_CATEGORY_THRESHOLDS = {"sleeve": SLEEVE_THRESHOLD, "length": LENGTH_THRESHOLD}


def decode(probabilities, sleeve_threshold=SLEEVE_THRESHOLD, length_threshold=LENGTH_THRESHOLD):
    if not all(.01 <= value <= 1 for value in (sleeve_threshold, length_threshold)):
        raise ValueError("카테고리 기준은 1~100%로 입력하세요.")
    if len(probabilities) != 4 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("카테고리 출력이 올바르지 않습니다.")
    if any(not math.isclose(sum(probabilities[i:i+2]), 1, abs_tol=1e-5) for i in (0, 2)):
        raise ValueError("카테고리 출력 확률 합이 올바르지 않습니다.")
    absent, long = probabilities[1] > probabilities[0], probabilities[3] > probabilities[2]
    sleeve, length = probabilities[int(absent)], probabilities[2+int(long)]
    candidate = "조끼" if absent else ("코트" if long else "자켓")
    reasons = []
    if absent and long:
        reasons.append("긴 민소매 예외")
    if sleeve < sleeve_threshold:
        reasons.append("소매 유무 점수 미달")
    if length < length_threshold:
        reasons.append("기장 점수 미달")
    return {"candidate": candidate, "raw_category": "기타" if absent and long else candidate,
            "display_category": "기타" if reasons else candidate, "needs_review": bool(reasons),
            "review_reasons": reasons, "score": min(sleeve, length), "sleeve_score": sleeve,
            "length_score": length, "probabilities": list(probabilities), "policy_version": POLICY_VERSION,
            "thresholds": {"sleeve": sleeve_threshold, "length": length_threshold}}


class CategoryModel:
    def __init__(self, directory):
        import torch
        from torchvision.models import efficientnet_b0
        from coatdesk_ab.locator import WholePanelLocator
        self.policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
        if self.policy["version"] != POLICY_VERSION or self.policy["thresholds"] != {"sleeve": SLEEVE_THRESHOLD, "length": LENGTH_THRESHOLD}:
            raise ValueError("카테고리 정책 파일을 확인하세요.")
        self.locator = WholePanelLocator(directory)
        self.model = efficientnet_b0(weights=None)
        self.model.classifier[1] = torch.nn.Linear(self.model.classifier[1].in_features, 4)
        checkpoint = torch.load(directory / "model.pt", map_location="cpu", weights_only=True)
        if checkpoint["input_side"] != 320 or checkpoint["architecture"] != "efficientnet_b0_sleeve_length_2x2":
            raise ValueError("지원하지 않는 카테고리 모델입니다.")
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.cpu().eval()

    def analyze(self, photo):
        import numpy as np
        import torch
        from PIL import ImageDraw
        from coatdesk_ab.data import letterbox
        location = self.locator.locate(photo)
        if not location["valid"]:
            raise ValueError("카테고리 안내판 가림 영역을 찾지 못했습니다.")
        x1, y1, x2, y2 = [int(v) for v in location["box"]]
        masked = photo.copy()
        ImageDraw.Draw(masked).rectangle((max(0, x1-8), max(0, y1-8), min(photo.width-1, x2+8), min(photo.height-1, y2+8)), fill="white")
        image, _ = letterbox(masked, 320)
        value = torch.from_numpy(np.asarray(image).transpose(2, 0, 1).copy()).float().unsqueeze(0)/255
        value = (value-value.new_tensor([.485, .456, .406])[None, :, None, None])/value.new_tensor([.229, .224, .225])[None, :, None, None]
        with torch.inference_mode():
            logits = self.model(value)[0]
            probabilities = torch.cat((logits[:2].softmax(0), logits[2:].softmax(0))).tolist()
        return {**decode(probabilities), "mask_location": location}
