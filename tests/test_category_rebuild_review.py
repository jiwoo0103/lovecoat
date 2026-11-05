from copy import deepcopy

import pytest

from scripts.category_rebuild_review import CRITERION, DECISIONS, KINDS, build, training_plan, validate


def fixture_pack():
    criterion = {"id": CRITERION, "reference_source_sha256": "hash-4120"}
    rows, exports, annotations = [], [], {}
    for code, decision, split in zip(
        ["4120", "0002", "0003", "0004", "0005"], ["l", "s", "v", "a", "o"],
        ["train", "train", "dev", "test", "test"], strict=True,
    ):
        pending = decision in ("a", "o")
        a = {"decision": decision, "category": DECISIONS[decision], "requires_review": pending,
             "confirmed_by": None, "origin": "codex_visual_reference_4120_20261103"}
        r = {"code": code, "group": code, "sha256": "hash-" + code, "body_sha256": "body-" + code,
             "dataset_split": split, "split": "train", "ai_annotation": a}
        annotations[code] = a
        rows.append(r)
        exports.append({**r, "confirmed_by": None, "review_status": "unreviewed" if pending else "ai_labeled",
                        "training_label": None if pending else DECISIONS[decision],
                        "training_eligible": not pending,
                        "attribute_targets": None if pending else {
                            "sleeves_present": 0 if decision == "v" else 1,
                            "at_least_reference_length": 1 if decision == "l" else 0}})
    manifest = {"dataset_id": "old", "criterion": criterion, "records": rows}
    annotations = {"dataset_id": "old", "criterion_id": CRITERION, "records": annotations}
    baseline = {"dataset_id": "old", "criterion": deepcopy(criterion), "edits": {}, "records": exports}
    return manifest, annotations, baseline


def test_new_partition_and_clear_ai_provenance_are_preserved():
    m, a, b = fixture_pack()
    assert validate(m, a, b, expected_count=5) == {"train": 2, "dev": 1, "test": 2}
    assert b["records"][2]["attribute_targets"]["sleeves_present"] == 0
    assert all(r["confirmed_by"] is None for r in b["records"])


@pytest.mark.parametrize("index", [3, 4])
def test_ambiguous_and_long_sleeveless_cannot_become_training_truth(index):
    m, a, b = fixture_pack()
    b["records"][index].update(training_label="코트", training_eligible=True)
    with pytest.raises(ValueError, match="promoted"):
        validate(m, a, b, expected_count=5)


@pytest.mark.parametrize("mutation", ["reference", "human", "split", "duplicate", "user_edits", "old_target"])
def test_contract_and_provenance_mismatches_are_rejected(mutation):
    m, a, b = fixture_pack()
    if mutation == "reference":
        m["criterion"]["reference_source_sha256"] = "different"
    elif mutation == "human":
        b["records"][0]["confirmed_by"] = "user_review"
    elif mutation == "split":
        b["records"][1]["dataset_split"] = "test"
    elif mutation == "duplicate":
        m["records"][1]["group"] = "4120"
    elif mutation == "user_edits":
        b["edits"]["4120"] = {"category": "자켓"}
    else:
        b["records"][0]["attribute_targets"] = {"full_buttocks_coverage": 1}
    with pytest.raises(ValueError):
        validate(m, a, b, expected_count=5)


def test_existing_review_is_never_overwritten(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "user.json"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        build(tmp_path / "not-read", output)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_approved_augmentation_and_separate_nine_condition_evaluation():
    plan = training_plan("new")
    assert plan["initialization"]["resume_existing_garment_checkpoints"] is False
    assert plan["training"]["original_probability"] == 0.25
    assert plan["training"]["kinds"] == KINDS[1:]
    assert plan["evaluation"]["kinds"] == KINDS and len(KINDS) == 9
    assert plan["evaluation"]["expected_observations_per_partition"] == 1350
    assert plan["evaluation"]["test_after_model_and_threshold_lock"] is True
    assert plan["evaluation"]["ai_labels_are_independent_human_truth"] is False
