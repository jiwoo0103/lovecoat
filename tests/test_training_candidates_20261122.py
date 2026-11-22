"""Checks for source grouping and diversity selection without training a model."""
import numpy as np

from scripts.prepare_training_candidates_20261122 import CLASSES, Groups, diverse_pick, select


def row(code, group, label="자켓", raw="M", split="train", eligible=True):
    return {"code": code, "group": group, "category": label, "raw_size": raw, "split": split,
            "fit_eligible": eligible, "age": "성인", "gender": "여성", "size": raw, "capture_bin": int(code) // 500}


def test_new_size_contract_has_twenty_distinct_outputs():
    assert len(CLASSES) == len(set(CLASSES)) == 20
    assert CLASSES[:3] == ["60", "70", "80"]
    assert "2XS" in CLASSES


def test_transitive_repeats_keep_one_group():
    groups = Groups(["0001", "0002", "0003"])
    groups.join("0001", "0002")
    groups.join("0002", "0003")
    assert len({groups.root(c) for c in groups.parent}) == 1


def test_diversity_selection_does_not_inflate_repeated_garments():
    rows = [row("0001", "a"), row("0002", "a"), row("0003", "b"), row("0004", "c")]
    features = np.eye(4, dtype=np.float32)
    chosen = diverse_pick(list(range(4)), rows, features, 4, "category")
    assert len(chosen) == 3
    assert {rows[i]["group"] for i in chosen} == {"a", "b", "c"}


def test_unknown_child_raw_and_holdout_never_enter_size_training():
    rows = [row("0001", "a"), row("0002", "b", raw=None, eligible=False),
            row("0003", "c", split="test"), row("0004", "d", raw="60")]
    selected = select(rows, np.eye(4, dtype=np.float32), "size", "train", 10)
    assert {r["code"] for r in selected} == {"0001", "0004"}


def test_rare_other_class_survives_small_candidate_budget():
    rows = [row(str(i).zfill(4), str(i)) for i in range(1, 21)]
    rows.append(row("0021", "21", label="기타"))
    selected = select(rows, np.eye(21, dtype=np.float32), "category", "train", 4)
    assert any(r["category"] == "기타" for r in selected)


def test_distinct_size_signs_on_same_clothing_remain_in_training_only():
    rows = [row("0001", "a", raw="XS"), row("0002", "a", raw="3XL"),
            row("0003", "b", raw="70"), row("0004", "c", raw="60", split="test")]
    selected = select(rows, np.eye(4, dtype=np.float32), "size", "train", 10)
    assert {r["raw_size"] for r in selected} == {"XS", "3XL", "70"}
    assert all(r["split"] == "train" for r in selected)
