from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .crops import VERSION, detect, load_front
from .training import CLASSES, hog, ocr


class TrainedRecognizer:
    """Explicitly loaded offline models; importing never changes the desktop app."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve(strict=True)
        self.card = json.loads((self.directory / "model-card.json").read_text(encoding="utf-8"))
        if self.card["detector_version"] != VERSION:
            raise ValueError("Model and crop detector versions differ")
        with np.load(self.directory / "head_shape.npz", allow_pickle=False) as model:
            self.weights = model["weights"].copy()
            self.bias = model["bias"].copy()
        self.mapping = json.loads((self.directory / "size-mapping.json").read_text(encoding="utf-8"))

    def predict(self, front: Path, scratch: Path) -> dict:
        """Read only the front photo; put ephemeral OCR inputs in caller-owned scratch."""
        value = detect(load_front(front))
        if value is None:
            return {"detected": False, "values": {}, "needs_review": True, "reason": "placard_not_found"}
        feature = hog(value.shape).reshape(1, -1)
        shape = CLASSES[int((feature @ self.weights + self.bias).argmax())]
        scratch.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", prefix="size-", dir=scratch, delete=False) as stream:
            path = Path(stream.name)
        try:
            value.text.save(path)
            raw = ocr(path, self.directory, "coat_size")
        finally:
            path.unlink()  # Only this call's own generated temporary crop.
        return self.decode(shape, raw)

    def decode(self, shape: str, raw: str) -> dict:
        """Keep the child's provisional business rule outside the learned head labels."""
        child = shape == "child"
        result = {"detected": True, "raw_text": raw, "shape_class": shape,
                  "values": {"age": "child" if child else "adult",
                             "gender": "male" if child or shape == "adult_male" else "female"},
                  "child_gender_provisional": child, "needs_review": True,
                  "reason": "model_suggestion_requires_review"}
        if raw not in self.card["validated_raw_labels"]:
            result["reason"] = "unvalidated_raw_label"
            return result
        if child:
            matches = [r for r in self.mapping["child_correspondence"] if raw in r["visible_numbers"]]
            size = matches[0]["size_label"] if len(matches) == 1 else ""
        else:
            size = raw if raw in self.mapping["adult_sizes"] else ""
        if size:
            result["values"]["size"] = size
        else:
            result["reason"] = "head_and_text_disagree"
        return result
