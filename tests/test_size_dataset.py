from collections import Counter

import pytest

from scripts.prepare_size_dataset import assign_splits, group_related, normalise_label, select_balanced


def test_child_mapping_keeps_distinct_raw_numbers_and_rejects_unknowns():
    mapping = {"adult_sizes": ["XS", "S"], "child_correspondence": [
        {"size_label": "XS", "child_size_group": "80/90", "visible_numbers": ["80", "90"]}]}
    assert normalise_label("child", "80", mapping) == ("XS", "80/90")
    assert normalise_label("child", "90", mapping) == ("XS", "80/90")
    with pytest.raises(ValueError):
        normalise_label("child", "85", mapping)
    with pytest.raises(ValueError):
        normalise_label("adult", "90", mapping)


def test_sampling_retains_rare_sizes_and_children_and_spreads_capture_blocks():
    labels = {f"{i:04}": ["adult", "S"] for i in range(1, 601)}
    labels.update({"0801": ["adult", "3XL"], "0901": ["child", "170"], "0902": ["child", "170"]})
    chosen = select_balanced(labels, per_size=40)
    assert len(chosen) == 43
    assert {"0801", "0901", "0902"} <= set(chosen)
    assert Counter((int(c) - 1) // 150 for c in chosen if int(c) <= 600) == {0: 10, 1: 10, 2: 10, 3: 10}
    assert chosen == select_balanced(labels, per_size=40)


def test_duplicate_body_and_side_photos_are_indivisible_across_splits():
    codes = [f"{i:04}" for i in range(1, 13)]
    labels = {code: ["adult", "S"] for code in codes}
    inventory = {code: {"body_dhash": f"{(i + 1) * 0x123456789abc:016x}"} for i, code in enumerate(codes)}
    inventory["0002"]["body_dhash"] = inventory["0001"]["body_dhash"]
    files = {code: [{"sha256": "unique-" + code}] for code in codes}
    files["0004"].append({"sha256": "same-back-photo"})
    files["0005"].append({"sha256": "same-back-photo"})
    groups = group_related(codes, inventory, files)
    assert groups["0001"] == groups["0002"]
    assert groups["0004"] == groups["0005"]
    splits = assign_splits(codes, labels, groups)
    assert splits["0001"] == splits["0002"]
    assert splits["0004"] == splits["0005"]
    assert set(splits.values()) == {"train", "validation"}


def test_two_rare_child_photographs_have_one_in_each_partition():
    labels = {"1001": ["child", "170"], "1002": ["child", "170"]}
    splits = assign_splits(list(labels), labels, {c: c for c in labels})
    assert Counter(splits.values()) == {"train": 1, "validation": 1}
    with pytest.raises(ValueError, match="Cannot represent"):
        assign_splits(list(labels), labels, {c: "same-garment" for c in labels})
