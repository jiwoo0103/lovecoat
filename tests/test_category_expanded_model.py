from copy import deepcopy

import pytest

from scripts.category_expanded_model import choose_thresholds, decode, summarize, validate_dataset


def sample(code, split, category="자켓", length="normal"):
    return {"code": code, "source_group": code, "source_sha256": code, "dataset_split": split,
            "review_status": "confirmed", "user_confirmed": True, "training_label": category,
            "display_category": category, "length": length, "acceptable_categories": [category],
            "targets": {"sleeves_present": 1, "full_buttocks_coverage": 0}}


def test_requires_approval_and_rejects_group_leakage():
    dataset = {"schema_version": "category-approved-split-v1", "records": [sample("a", "train"), sample("b", "dev"), sample("c", "test")]}
    assert len(validate_dataset(dataset)["train"]) == 1
    changed = deepcopy(dataset)
    changed["records"][0]["review_status"] = "unreviewed"
    with pytest.raises(ValueError, match="Unapproved"):
        validate_dataset(changed)
    changed = deepcopy(dataset)
    changed["records"][2]["source_group"] = "a"
    with pytest.raises(ValueError, match="overlap"):
        validate_dataset(changed)


def test_boundaries_cannot_supply_hard_length_targets():
    dataset = {"schema_version": "category-approved-split-v1", "records": [sample("a", "train", length="borderline"), sample("b", "dev"), sample("c", "test")]}
    with pytest.raises(ValueError, match="hard target"):
        validate_dataset(dataset)
    dataset["records"][0]["targets"]["full_buttocks_coverage"] = None
    dataset["records"][0]["acceptable_categories"] = ["자켓", "코트"]
    validate_dataset(dataset)


def test_length_head_controls_sleeved_class_and_long_vest_stays_review():
    assert decode([.99, .01, .99, .01])["raw_category"] == "자켓"
    assert decode([.99, .01, .01, .99])["raw_category"] == "코트"
    assert decode([.01, .99, .99, .01])["raw_category"] == "조끼"
    result = decode([.01, .99, .01, .99])
    assert result["raw_category"] == result["display_category"] == "기타"
    assert result["candidate"] == "조끼" and result["needs_review"]
    assert decode([.7, .3, .99, .01], .9, .6)["needs_review"]


def test_tolerant_metrics_do_not_remove_held_errors():
    r = {"truth": "자켓", "acceptable_categories": ["자켓", "코트"], "length": "borderline",
         "targets": {"sleeves_present": 1, "full_buttocks_coverage": None}, **decode([.99, .01, .01, .99], 1., 1.)}
    m = summarize([r])
    assert m["strict"]["correct"] == 0 and m["tolerant"]["correct"] == 1
    assert m["automatic"]["count"] == 0 and m["automatic"]["review_count"] == 1
    r["acceptable_categories"] = ["자켓"]
    assert summarize([r])["tolerant"]["correct"] == 0


def test_gate_is_selected_from_given_dev_rows_and_has_safe_no_evidence_case():
    rows = [{"raw_category": "자켓", "acceptable_categories": ["자켓"], "sleeve_score": .99, "length_score": .9} for _ in range(30)]
    rows += [{"raw_category": "조끼", "acceptable_categories": ["자켓"], "sleeve_score": .6, "length_score": .99}]
    chosen = choose_thresholds(rows)
    assert chosen["accepted"] == chosen["correct"] == 30
    assert chosen["sleeve"] > .6
    assert choose_thresholds(rows[:2])["selection"] == "no_supported_threshold_all_review"


def test_masked_loss_handles_absent_length_targets_without_nan():
    import torch
    from scripts.category_expanded_model import masked_attribute_loss

    logits = torch.tensor([[1., 0., 20., -20.], [0., 1., -20., 20.]], requires_grad=True)
    targets = torch.tensor([[0, -1], [1, -1]])
    criteria = [torch.nn.CrossEntropyLoss(), torch.nn.CrossEntropyLoss()]
    loss = masked_attribute_loss(logits, targets, criteria)
    loss.backward()
    assert torch.isfinite(loss) and torch.equal(logits.grad[:, 2:], torch.zeros(2, 2))
    empty = masked_attribute_loss(logits, torch.full((2, 2), -1), criteria)
    assert torch.isfinite(empty) and empty.item() == 0
