"""Manifest compatibility and decoding of the newly supported raw size classes."""
import json
from types import SimpleNamespace

import pytest
from PIL import Image

torch = pytest.importorskip("torch")

from coatdesk_ab.data import SIZES  # noqa: E402 - optional torch must be checked first
from coatdesk_v2 import ai_runtime  # noqa: E402


@pytest.mark.parametrize("classes,raw,shape,expected", [
    (SIZES, "XS", 1, "XS"),
    (["70", *SIZES], "70", 2, "2XS"),
    (ai_runtime.SIZE_CLASSES_20, "60", 2, "2XS"),
    (ai_runtime.SIZE_CLASSES_20, "2XS", 1, "2XS"),
    (ai_runtime.SIZE_CLASSES_20, "170", 2, "2XL"),
])
def test_supported_manifest_order_decodes_raw_and_business_size(tmp_path, monkeypatch, classes, raw, shape, expected):
    (tmp_path / "manifest.json").write_text(json.dumps({"size_classes": classes}))
    (tmp_path / "size_mapping.json").write_text(json.dumps({"child_correspondence": [
        {"size_label": "2XS", "visible_numbers": ["60", "70"]},
        {"size_label": "2XL", "visible_numbers": ["170"]},
    ]}))
    photo = tmp_path / "photo.png"
    Image.new("RGB", (64, 96), "white").save(photo)

    class FakeModel:
        transform = SimpleNamespace(fixed_size=None)

        def __init__(self, count=0):
            self.count = count

        def cpu(self):
            return self

        def eval(self):
            return self

        def load_state_dict(self, _):
            pass

        def __call__(self, _):
            logits = torch.full((1, self.count), -20.)
            logits[0, shape if self.count == 3 else classes.index(raw)] = 20.
            return logits

    monkeypatch.setattr(ai_runtime, "build_detector", FakeModel)
    monkeypatch.setattr(ai_runtime, "PhotoClassifier", FakeModel)
    monkeypatch.setattr(torch, "load", lambda *args, **kwargs: {})
    model = ai_runtime.Models(tmp_path)
    result = model.analyze(photo, {"panel": .9, "shape": .9, "size": .9}, manual_roi=[0, 0, 1, 1])
    assert result["raw_size"] == raw
    assert result["values"]["size"] == expected
    assert result["candidates"]["size"] == expected
    assert not result["held"]
    assert list(result["probabilities"]["size"]) == classes


def test_permuted_twenty_class_order_is_rejected_before_loading_weights(tmp_path):
    classes = list(ai_runtime.SIZE_CLASSES_20)
    classes[0], classes[1] = classes[1], classes[0]
    (tmp_path / "manifest.json").write_text(json.dumps({"size_classes": classes}))
    with pytest.raises(ValueError, match="출력 순서"):
        ai_runtime.Models(tmp_path)
