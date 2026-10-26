"""Read-only integrity checks for a packaged, visually labelled size dataset."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def check_csv_records(rows, records):
    assert len(rows) == len(records)
    for row, expected in zip(rows, records, strict=True):
        assert row == {key: str(value) for key, value in expected.items()}


def check_genders(root, records, summary):
    if "gender_policy_id" not in summary:
        assert all("gender" not in row for row in records)
        return {}
    policy = json.loads((root / "성별기준.json").read_text(encoding="utf-8"))
    assert policy["policy_id"] == summary["gender_policy_id"]
    assert policy["adult_reference_mapping_confirmed_by_user"] is True
    assert policy["child_rule"] == {"gender": "male", "provisional": True, "confirmed_by_user": True}
    for row in records:
        assert row["gender"] in {"male", "female"}
        assert row["gender_policy_id"] == policy["policy_id"]
        assert row["gender_human_verified"] is False
        if row["age"] == "adult":
            assert row["gender_provisional"] is False
            assert row["gender_label_source"] == "confirmed_placard_visual"
            assert row["gender_reference"] == {"male": "A_0035", "female": "B_0001"}[row["gender"]]
            assert row["gender_ko"] == {"male": "남성", "female": "여성"}[row["gender"]]
        else:
            assert row["gender"] == "male" and row["gender_ko"] == "남아"
            assert row["gender_provisional"] is True
            assert row["gender_label_source"] == "user_provisional_rule" and row["gender_reference"] == "child_rule"
    expected = {f"{age}_{gender}": sum(r["age"] == age and r["gender"] == gender for r in records)
                for age in ("adult", "child") for gender in ("male", "female")}
    assert expected == summary["age_gender_counts"]
    assert Counter(r["gender"] for r in records) == summary["gender_counts"]
    assert sum(r["gender_provisional"] for r in records) == summary["gender_provisional_count"]
    for split, groups in summary["gender_split_counts"].items():
        for key, count in groups.items():
            assert sum(r["split"] == split and r["age"] + "_" + r["gender"] == key for r in records) == count
    distribution = csv_rows(root / "성별분포.csv")
    assert sum(int(d["selected_garments"]) for d in distribution) == len(records)
    assert len({(d["age"], d["gender"], d["size_label"]) for d in distribution}) == len(distribution)
    for group in distribution:
        chosen = [r for r in records if (r["age"], r["gender"], r["size_label"])
                  == (group["age"], group["gender"], group["size_label"])]
        assert len(chosen) == int(group["selected_garments"])
        assert sum(r["split"] == "train" for r in chosen) == int(group["train"])
        assert sum(r["split"] == "validation" for r in chosen) == int(group["validation"])
        assert sum(r["gender_provisional"] for r in chosen) == int(group["provisional_garments"])
    review = json.loads((root / "성별판독기록.json").read_text(encoding="utf-8"))
    assert review["labels"] == {r["code"]: r["gender"] for r in records if r["age"] == "adult"}
    assert review["all_selected_adults_reviewed"] is True
    return {"gender_labels_verified": True, "age_gender_counts": expected,
            "child_gender_is_provisional_user_rule": True, "gender_human_verified": False}


def verify(root: Path, check_sources: bool = True) -> dict:
    root = root.resolve(strict=True)
    records = [json.loads(line) for line in (root / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    mapping = json.loads((root / "사이즈기준.json").read_text(encoding="utf-8"))
    files = csv_rows(root / "files_sha256.csv")
    by_code = {r["code"]: r for r in records}
    assert len(by_code) == len(records) == summary["selected_garments"]
    assert len(files) == summary["copied_original_photos"]
    assert Counter(r["split"] for r in records) == summary["split_counts"]
    assert Counter(r["age"] for r in records) == summary["age_counts"]
    groups, hashes, file_counts, fronts = defaultdict(set), defaultdict(set), Counter(), Counter()
    for record in records:
        code, split = record["code"], record["split"]
        assert code.isascii() and code.isdecimal() and len(code) == 4
        groups[record["split_group"]].add(split)
        front = (root / record["front_path"]).resolve(strict=True)
        assert root in front.parents
        assert front.parent.name == "0_" + code
        assert front.parts[len(root.parts)] == {"train": "학습용", "validation": "검증용"}[split]
        assert json.loads((front.parent / "label.json").read_text(encoding="utf-8")) == record
        assert record["annotation_method"] == "codex_visual_transcription"
        assert record["human_verified"] is False
        if record["age"] == "adult":
            assert record["raw_text"] == record["size_label"] in mapping["adult_sizes"]
        else:
            matches = [r for r in mapping["child_correspondence"] if record["raw_text"] in r["visible_numbers"]]
            assert len(matches) == 1
            assert matches[0]["size_label"] == record["size_label"]
            assert matches[0]["child_size_group"] == record["child_size_group"]
    assert all(len(partitions) == 1 for partitions in groups.values())
    for row in files:
        record = by_code[row["code"]]
        target = (root / row["path"]).resolve(strict=True)
        assert root in target.parents
        assert target.parent == (root / record["front_path"]).parent
        assert row["split"] == record["split"]
        hashes[row["sha256"]].add(row["split"])
        file_counts[row["code"]] += 1
        if row["use_for_size_ocr"] == "True":
            assert row["path"] == record["front_path"]
            assert row["sha256"] == record["front_sha256"]
            fronts[row["code"]] += 1
    assert all(len(partitions) == 1 for partitions in hashes.values())
    assert all(fronts[c] == 1 and file_counts[c] == by_code[c]["photo_count"] for c in by_code)
    assert len({row["path"] for row in files}) == len(files)
    actual_photos = {p.relative_to(root).as_posix() for dirname in ("학습용", "검증용")
                     for p in (root / dirname).rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}}
    assert actual_photos == {row["path"] for row in files}
    check_csv_records(csv_rows(root / "labels.csv"), records)
    for split, dirname in (("train", "학습용"), ("validation", "검증용")):
        check_csv_records(csv_rows(root / dirname / "labels.csv"), [r for r in records if r["split"] == split])
    gender_checks = check_genders(root, records, summary)
    strata = defaultdict(set)
    for row in records:
        strata[(row["age"], row["raw_text"])].add(row["split"])
    assert all(len(splits) == 2 for splits in strata.values())

    def check_file(row):
        target = root / row["path"]
        assert target.stat().st_size == int(row["bytes"])
        assert digest(target) == row["sha256"], row["path"]
        if check_sources:
            assert digest(row["source_path"]) == row["sha256"], row["source_path"]
        with Image.open(target) as image:
            image.verify()
        return True

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(check_file, files))
    if check_sources:
        assert digest(mapping["workbook"]) == mapping["sha256"]
    for row in summary["distribution"]:
        chosen = [r for r in records if r["age"] == row["age"] and r["size_label"] == row["size_label"]]
        assert len(chosen) == row["selected_garments"]
        assert sum(r["split"] == "train" for r in chosen) == row["train"]
        assert sum(r["split"] == "validation" for r in chosen) == row["validation"]
    return {"ok": True, "root": str(root), "dataset_version": summary["dataset_version"],
            "garments": len(records), "original_photos": len(files),
            "original_bytes": sum(int(row["bytes"]) for row in files), "split_counts": summary["split_counts"],
            "all_copy_hashes_match": True, "source_hashes_rechecked": check_sources,
            "original_photographs_decode": True, "garment_overlap": 0, "duplicate_file_overlap": 0,
            "duplicate_group_overlap": 0, "labels_match_paths_and_mapping": True,
            "every_present_raw_label_has_both_splits": True, "model_training_performed": False, **gender_checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--skip-source-check", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.root, not args.skip_source_check)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True, indent=2))
