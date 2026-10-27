import pytest

from scripts.category_dataset_split import group_candidates, split_groups


def rows():
    return [{"code": f"{i:04}", "source_group": str(i), "source_sha256": str(i),
             "draft_category": ("자켓", "코트", "조끼")[i % 3], "age_group": ("성인", "아동")[i % 2]}
            for i in range(120)]


def test_hash_and_transitive_similarity_groups_never_cross_splits():
    records = rows()
    records[1]["source_sha256"] = records[0]["source_sha256"]
    groups = group_candidates(records, [("0001", "0002"), ("0002", "0003")])
    assignment = split_groups(records, groups)
    assert len({groups[f"{i:04}"] for i in range(4)}) == 1
    assert len({assignment[f"{i:04}"] for i in range(4)}) == 1
    assert assignment == split_groups(records, groups)
    for split in ("train", "dev", "test"):
        assert {r["draft_category"] for r in records if assignment[r["code"]] == split} == {"자켓", "코트", "조끼"}


def test_train_only_pin_applies_to_entire_similarity_group():
    records = rows()
    groups = group_candidates(records, [("0000", "0001")])
    assignment = split_groups(records, groups, train_only=["0000"])
    assert assignment["0000"] == assignment["0001"] == "train"


def test_unknown_similarity_pair_and_invalid_ratios_are_rejected():
    with pytest.raises(ValueError):
        group_candidates(rows(), [("0000", "unknown")])
    records = rows()
    with pytest.raises(ValueError):
        split_groups(records, group_candidates(records), ratios={"train": .9, "dev": .2, "test": .1})
