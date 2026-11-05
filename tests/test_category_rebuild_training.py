"""Regressions for fresh initialization, latest label lineage and masked targets."""
from copy import deepcopy

import numpy as np
from PIL import Image
import pytest
import torch

from scripts import category_rebuild_training as rebuild


def approved_rows():
    manifest = rebuild.read(rebuild.PACK / "manifest.json")
    labels = rebuild.read(rebuild.LABELS)
    old = rebuild.read(rebuild.OLD_WORK / "frozen-dataset.json")["records"]
    return manifest, labels, old


def test_latest_approved_labels_override_all_historical_category_attributes():
    manifest, labels, old = approved_rows()
    original_labels = deepcopy(labels)
    for row in manifest["records"]:
        row.update(category="WRONG", sleeves=99, length=99, split="OBSOLETE")
    rows = rebuild.freeze_rows(manifest, labels, old)
    index = {r["code"]: r for r in rows}
    assert len(rows) == 1000
    assert rebuild.Counter(r["dataset_split"] for r in rows) == {"train": 700, "dev": 150, "test": 150}
    for code in ("1145", "2236", "2440"):
        assert index[code]["target"] == [1, 0]
        assert index[code]["user_category"] == "조끼"
    for code in ("2808", "2946", "3166", "3355", "4957", "1689", "2020"):
        assert index[code]["target"] == [0, 0]
        assert index[code]["user_category"] == "자켓"
    assert sum(r["target"][1] == -1 for r in rows) == 10
    assert {r["code"] for r in rows if r["attribute_correction"]} == rebuild.LONG_VESTS
    for code in rebuild.LONG_VESTS:
        assert index[code]["target"] == [1, 1]
        assert index[code]["user_category"] == "코트"
        assert index[code]["evaluation_category"] == "기타"
    assert labels == original_labels  # Never rewrite the approved label snapshots.
    assert not any(r["old_augmented_fit"] or r["old_program_prior_fit"] for r in rows if r["dataset_split"] == "test")


@pytest.mark.parametrize("field,value", [("dataset_split", "train"), ("sha256", "changed"), ("group", "changed")])
def test_cannot_silently_change_identity_or_split(field, value):
    manifest, labels, old = approved_rows()
    labels["records"][0][field] = value
    with pytest.raises(ValueError, match="Changed identity"):
        rebuild.freeze_rows(manifest, labels, old)


def test_masked_length_has_no_gradient_but_sleeve_still_learns():
    logits = torch.tensor([[.3, -.3, 1., -1.], [.2, -.2, -2., 2.]], requires_grad=True)
    targets = torch.tensor([[0, -1], [1, 1]])
    loss = rebuild.attribute_loss(logits, targets, [torch.nn.CrossEntropyLoss(), torch.nn.CrossEntropyLoss()])
    loss.backward()
    assert torch.equal(logits.grad[0, 2:], torch.zeros(2))
    assert logits.grad[0, :2].abs().sum() > 0
    assert logits.grad[1, 2:].abs().sum() > 0


def test_augmentation_is_pixel_identical_to_existing_second_model():
    from scripts.retrain_four_training import change, category_input
    photo = Image.fromarray(np.random.default_rng(7).integers(0, 256, (192, 108, 3), dtype=np.uint8))
    for kind in rebuild.KINDS:
        for strength in (0., .37, 1.):
            expected = change(photo, kind, strength)
            actual = rebuild.change(photo, kind, strength)
            assert np.array_equal(actual, expected), (kind, strength)
            assert np.array_equal(rebuild.category_input(actual, [30, 20, 60, 80]), category_input(expected, [30, 20, 60, 80]))


def test_training_initialization_loads_only_generic_imagenet(monkeypatch):
    real_load = torch.load
    loaded = []

    def capture(path, **kwargs):
        loaded.append(path)
        return real_load(path, **kwargs)

    monkeypatch.setattr(torch, "load", capture)
    model = rebuild.build_fresh()
    assert loaded == [rebuild.GENERIC]
    assert model.classifier[1].out_features == 4
    source = real_load(rebuild.GENERIC, map_location="cpu", weights_only=True)
    assert torch.equal(model.features[0][0].weight, source["features.0.0.weight"])


def test_long_vest_requires_review_and_preserves_separate_user_category_score():
    row = {"code": "2483", "locations": {"original": {"valid": True}}, "target": [1, 1],
           "user_category": "코트", "evaluation_category": "기타", "acceptable_categories": ["기타"],
           "confirmed_by": "user_review", "boundary": False}
    p = rebuild.prediction(row, "original", [.001, .999, .001, .999])
    assert p["correct"] and p["strict_correct"] and not p["user_strict_correct"]
    assert p["needs_review"]
    metrics = rebuild.summarize([p])
    assert metrics["long_vest"]["unsafe_automatic"] == 0
    assert metrics["automatic"]["coverage"] == 0


def test_invalid_locator_is_counted_as_failure_and_review_required():
    row = {"code": "1", "locations": {"original": {"valid": False}}, "target": [0, 0],
           "user_category": "자켓", "evaluation_category": "자켓", "acceptable_categories": ["자켓"],
           "confirmed_by": None, "boundary": False}
    p = rebuild.prediction(row, "original", [.999, .001, .999, .001])
    assert p["needs_review"] and not p["correct"]
    assert rebuild.summarize([p])["tolerant"] == {"count": 1, "correct": 0, "accuracy": 0.}
