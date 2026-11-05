"""Check added label provenance and held-out split protections."""
from collections import Counter
from copy import deepcopy

import pytest

from scripts import category_rebuild_expand as expand


def test_additions_have_rechecked_labels_without_promoting_ai_to_human():
    selection = expand.base.read(expand.WORK / "approved-additions.json")
    rows = [expand.additional_row(item, item["role"]) for item in selection["records"]]
    assert Counter(r["dataset_split"] for r in rows) == {"train": 100, "dev": 50, "test": 50}
    assert Counter(r["user_category"] for r in rows if r["dataset_split"] == "train") == {"자켓": 50, "코트": 50}
    assert all(r["confirmed_by"] is None for r in rows)
    assert all(not r["old_program_prior_fit"] and not r["old_augmented_fit"] for r in rows if r["dataset_split"] != "train")
    ambiguous = deepcopy(selection["records"][0])
    ambiguous["visual_status"] = "uncertain"
    with pytest.raises(ValueError, match="ambiguous"):
        expand.additional_row(ambiguous, "train")


def test_partition_validation_rejects_test_leakage_and_duplicate_identity():
    rows = [dict(code=str(i), sha256=str(i), body_sha256=str(i), group=str(i),
                 dataset_split=role, old_augmented_fit=False, old_program_prior_fit=False,
                 old_augmented_split=None, cohort="added_validation")
            for i, role in enumerate(("train", "dev", "test"))]
    expand.validate_records(rows, train_count=1, evaluation_count=1)
    leaked = deepcopy(rows)
    leaked[2]["old_program_prior_fit"] = True
    with pytest.raises(ValueError, match="Test exposure"):
        expand.validate_records(leaked, train_count=1, evaluation_count=1)
    leaked = deepcopy(rows)
    leaked[1]["old_augmented_split"] = "dev"
    with pytest.raises(ValueError, match="held out"):
        expand.validate_records(leaked, train_count=1, evaluation_count=1)
    leaked = deepcopy(rows)
    leaked[1]["body_sha256"] = leaked[0]["body_sha256"]
    with pytest.raises(ValueError, match="Duplicate"):
        expand.validate_records(leaked, train_count=1, evaluation_count=1)


def test_latest_user_corrections_and_long_vests_are_retained():
    base = expand.base
    rows = base.freeze_rows(base.read(base.PACK / "manifest.json"), base.read(expand.LABELS),
                           base.read(base.OLD_WORK / "frozen-dataset.json")["records"])
    index = {r["code"]: r for r in rows}
    for code in ("1205", "3212", "3581"):
        assert index[code]["user_category"] == "코트"
        assert index[code]["target"] == [0, 1]
    for code in ("1358", "1538"):
        assert index[code]["user_category"] == "자켓"
        assert index[code]["target"] == [0, 0]
    assert sum(r["target"][1] == -1 for r in rows) == 10
    assert all(index[code]["target"] == [1, 1] for code in base.LONG_VESTS)
