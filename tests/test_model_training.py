import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("cv2", reason="Install requirements-training.txt for offline model tests")

from coatdesk_ml.crops import detect
from coatdesk_ml.inference import TrainedRecognizer
from coatdesk_ml.training import edit_distance, hog, inner_split, measure, shape_label


def test_inner_split_keeps_outer_holdout_and_duplicate_groups_and_rare_size():
    records = []
    for i in range(1, 31):
        records.append({"code": f"{i:04}", "split_group": str(i // 2), "split": "train",
                        "age": "adult", "gender": "male", "raw_text": "L"})
    records.append({"code": "1000", "split_group": "rare", "split": "train", "age": "child", "raw_text": "170"})
    records.append({"code": "2000", "split_group": "holdout", "split": "validation", "age": "child", "raw_text": "170"})
    result = inner_split(records)
    assert result["1000"] == "inner_fit"
    assert result["2000"] == "outer_validation"
    assert result["0002"] == result["0003"]
    assert "inner_dev" in result.values()
    assert result == inner_split(records)


def test_outer_validation_cannot_share_a_group_with_training():
    records = [{"code": "0001", "split_group": "same", "split": "train", "age": "adult", "gender": "female", "raw_text": "S"},
               {"code": "0002", "split_group": "same", "split": "validation", "age": "adult", "gender": "female", "raw_text": "S"}]
    with pytest.raises(ValueError, match="crosses"):
        inner_split(records)


def test_child_model_class_never_learns_provisional_gender_as_ground_truth():
    assert shape_label({"age": "child", "gender": "male", "gender_provisional": True}) == "child"
    assert shape_label({"age": "child", "gender": "female"}) == "child"
    assert shape_label({"age": "adult", "gender": "female"}) == "adult_female"


def test_blank_photo_is_rejected_and_hog_is_finite_and_shape_sensitive():
    assert detect(Image.new("RGB", (1080, 1920), "white")) is None
    blank = Image.new("L", (64, 64), 255)
    shaped = blank.copy()
    ImageDraw.Draw(shaped).ellipse((18, 10, 46, 50), fill=0)
    assert hog(blank).shape == hog(shaped).shape == (1764,)
    assert np.isfinite(hog(shaped)).all()
    assert not np.array_equal(hog(blank), hog(shaped))


def test_report_counts_blank_predictions_as_failures_and_uses_character_edits():
    result = measure(["XS", "XS", "110"], ["XS", "", "110"])
    assert result["correct"] == 2 and result["count"] == 3
    assert result["per_class"]["XS"] == {"count": 2, "correct": 1}
    assert edit_distance("110", "11") == 1
    assert edit_distance("2XL", "3XL") == 1
    assert edit_distance("XS", "") == 2


def test_inference_preserves_raw_child_number_and_separate_provisional_rule():
    model = TrainedRecognizer.__new__(TrainedRecognizer)
    model.card = {"validated_raw_labels": ["110", "S"]}
    model.mapping = {"adult_sizes": ["S"], "child_correspondence": [
        {"visible_numbers": ["100", "110"], "size_label": "S"}]}
    result = model.decode("child", "110")
    assert result["raw_text"] == "110"
    assert result["values"] == {"age": "child", "gender": "male", "size": "S"}
    assert result["child_gender_provisional"] is True
    assert result["needs_review"] is True
    contradictory = model.decode("adult_male", "110")
    assert "size" not in contradictory["values"]
    assert contradictory["reason"] == "head_and_text_disagree"
    unseen = model.decode("child", "60")
    assert "size" not in unseen["values"] and unseen["reason"] == "unvalidated_raw_label"
