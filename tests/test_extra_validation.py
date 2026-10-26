import json

import pytest

from scripts.extra_validation import freeze, minimum_hash_distance, read_blind_labels, rescore
from scripts.prepare_size_dataset import digest


def test_blind_labels_keep_gender_separate_from_size():
    rows = read_blind_labels(["1112", "1152"], {"predictions_seen": False, "pages": ["mS fXL"]})
    assert [(r["size_label"], r["shape_class"]) for r in rows] == [("S", "adult_male"), ("XL", "adult_female")]
    assert all(not r["use_for_training"] and not r["human_verified"] for r in rows)


@pytest.mark.parametrize("codes,annotation", [
    (["1112"], {"predictions_seen": True, "pages": ["mXL"]}),
    (["1112", "1113"], {"predictions_seen": False, "pages": ["mXL"]}),
    (["1112", "1112"], {"predictions_seen": False, "pages": ["mXL fS"]}),
    (["0035"], {"predictions_seen": False, "pages": ["mXL"]}),
    (["1112"], {"predictions_seen": False, "pages": ["?"]}),
])
def test_reject_unusable_ground_truth(codes, annotation):
    with pytest.raises(ValueError):
        read_blind_labels(codes, annotation)


def test_body_duplicate_distance_finds_near_duplicate():
    inventory = {"new": {"body_dhash": "000f"}, "old1": {"body_dhash": "0007"},
                 "old2": {"body_dhash": "ffff"}}
    assert minimum_hash_distance("new", ["old2", "old1"], inventory) == (1, "old1")


def test_frozen_evaluation_rejects_modified_model(tmp_path):
    for name in ("coat_size.traineddata", "head_shape.npz"):
        (tmp_path / name).write_bytes(b"frozen weight")
    source = tmp_path / "inference.py"
    source.write_bytes(b"frozen implementation")
    card = {"model_version": "test", "files_sha256": {
        name: digest(tmp_path / name) for name in ("coat_size.traineddata", "head_shape.npz")},
        "code_sha256": {str(source): digest(source)}}
    (tmp_path / "model-card.json").write_text(json.dumps(card), encoding="utf-8")
    assert freeze(tmp_path)["new_training_performed"] is False
    (tmp_path / "head_shape.npz").write_bytes(b"modified weight")
    with pytest.raises(ValueError, match="artifact changed"):
        freeze(tmp_path)


def test_label_adjudication_rescores_without_changing_model_outputs():
    predictions = [{"code": "1407", "expected_raw": "S", "expected_shape": "adult_female",
                    "predicted_raw": "S", "predicted_shape": "adult_male", "baseline_raw": "S",
                    "inference": {"raw_text": "S", "shape_class": "adult_male"}, "both_correct": False}]
    rows = [{"code": "1407", "raw_text": "S", "shape_class": "adult_male"}]
    after, metrics = rescore(rows, predictions)
    assert metrics["both_correct"] == 1
    assert predictions[0]["expected_shape"] == "adult_female"
    assert after[0]["inference"] == predictions[0]["inference"]
    assert after[0]["predicted_raw"] == predictions[0]["predicted_raw"]
    with pytest.raises(ValueError, match="code mismatch"):
        rescore([dict(rows[0], code="1408")], predictions)
