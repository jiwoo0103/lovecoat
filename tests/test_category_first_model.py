import copy

import pytest
from PIL import Image

from scripts.category_first_model import assign_folds, confirmed_records, mask_panel, metrics


def fixtures():
    originals, sources, edited = [], [], []
    for index, label in enumerate(["자켓", "코트", "조끼", "코트"]):
        code = f"{index:04}"
        original = {"code": code, "source_sha256": str(index), "source_group": code,
                    "age_group": "성인", "folder_id": "0_" + code, "previous_task_role": "old_task",
                    "display_category": label, "review_status": "confirmed"}
        originals.append(original)
        sources.append({"code": code, "sha256": str(index), "split_group": code, "source": "/unused/photo"})
        edited.append({**original, "training_label": label})
    edited[-1].update(review_status="unreviewed", training_label=None)
    review = {"schema_version": "category-review-v1", "dataset_id": "actual", "records": edited,
              "edits": {"0003": {"category": "コート", "confirmed": False}}}
    review["edits"]["0003"]["category"] = "코트"
    return review, {"dataset_id": "actual", "records": originals}, {"rows": sources}


def test_unconfirmed_candidates_are_excluded_even_when_displayed_as_coats():
    review, baseline, manifest = fixtures()
    result = confirmed_records(review, baseline, manifest, {})
    assert len([r for r in result if r["eligible"]]) == 3
    assert result[-1]["exclusion_reason"] == "user_unconfirmed"
    assert result[-1]["human_label"] is None


@pytest.mark.parametrize("change", ["qa_dataset", "promoted_guess", "changed_hash", "duplicate", "edit_mismatch"])
def test_wrong_or_tampered_review_cannot_enter_training(change):
    review, baseline, manifest = fixtures()
    if change == "qa_dataset":
        review["dataset_id"] = "actual-qa-only"
    elif change == "promoted_guess":
        review["records"][-1]["training_label"] = "코트"
    elif change == "changed_hash":
        review["records"][0]["source_sha256"] = "other"
    elif change == "duplicate":
        review["records"].append(copy.deepcopy(review["records"][0]))
    else:
        review["edits"]["0003"]["confirmed"] = True
    with pytest.raises(ValueError):
        confirmed_records(review, baseline, manifest, {})


def test_group_folds_keep_same_garment_together_and_exclude_unconfirmed():
    rows = [{"code": str(i), "source_group": str(i // 2), "eligible": True,
             "human_label": ["자켓", "코트", "조끼"][i // 8], "age_group": ["성인", "아동"][i % 2]}
            for i in range(24)]
    rows.append({"code": "held", "eligible": False})
    folds = assign_folds(rows)
    assert folds == assign_folds(rows)
    assert "held" not in folds
    for i in range(0, 24, 2):
        assert folds[str(i)] == folds[str(i + 1)]
    for fold in range(3):
        assert {r["human_label"] for r in rows if r["eligible"] and folds[r["code"]] == fold} == {"자켓", "코트", "조끼"}


def test_mask_retains_hem_and_sleeves_and_metrics_count_rejected_mistakes():
    image = Image.new("RGB", (100, 180), "black")
    masked = mask_panel(image, [30, 20, 70, 50])
    assert masked.size == image.size and masked.getpixel((50, 30)) == (255, 255, 255)
    assert masked.getpixel((50, 179)) == (0, 0, 0)
    assert masked.getpixel((0, 100)) == (0, 0, 0)
    result = metrics([{"truth": "자켓", "candidate": "코트", "score": .6},
                      {"truth": "코트", "candidate": "코트", "score": .99},
                      {"truth": "조끼", "candidate": "조끼", "score": .96}])
    assert result["count"] == 3 and result["correct"] == 2
    assert result["threshold_95"] == {"accepted": 2, "correct": 2, "held": 1}
    assert result["per_class"]["자켓"]["recall"] == 0
