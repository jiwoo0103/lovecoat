"""Create an isolated review snapshot from the existing 4120 visual review.

No training, browser storage access, model loading, or source-file writes.
The earlier AI review is retained with its original provenance, not relabeled
as a new inspection or human ground truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CRITERION = "category-reference-4120-v1-20261103"
DATASET_ID = "category-rebuild-1000-4120-20261103-01"
DECISIONS = {"s": "자켓", "l": "코트", "v": "조끼", "a": None, "o": "기타"}
KINDS = ["original", "dark", "bright", "warm", "cool", "contrast", "shadow_left", "shadow_right", "blur"]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def validate(manifest, annotations, baseline, expected_count=1000):
    identity = manifest["dataset_id"]
    if any(d["dataset_id"] != identity for d in (annotations, baseline)):
        raise ValueError("Dataset identity mismatch")
    if manifest["criterion"]["id"] != CRITERION or baseline["criterion"] != manifest["criterion"]:
        raise ValueError("Wrong or changed reference criterion")
    if annotations["criterion_id"] != CRITERION or baseline["edits"]:
        raise ValueError("Expected original AI baseline, not user edits")
    rows = manifest["records"]
    if len(rows) != expected_count or len(baseline["records"]) != expected_count:
        raise ValueError("Wrong record count")
    for key in ("code", "group", "sha256", "body_sha256"):
        if len({r[key] for r in rows}) != expected_count:
            raise ValueError(f"Duplicate {key}")
    codes = {r["code"] for r in rows}
    exports = {r["code"]: r for r in baseline["records"]}
    if set(exports) != codes or set(annotations["records"]) != codes:
        raise ValueError("Missing or duplicate annotation/export records")
    counts = Counter(r["dataset_split"] for r in rows)
    if expected_count == 1000 and counts != {"train": 700, "dev": 150, "test": 150}:
        raise ValueError("New dataset_split changed; never use legacy split")
    if any(r["dataset_split"] not in ("train", "dev", "test") for r in rows):
        raise ValueError("Invalid partition")
    anchor = next((r for r in rows if r["code"] == "4120"), None)
    if anchor is None or anchor["dataset_split"] != "train":
        raise ValueError("Reference must stay in train")
    if anchor["sha256"] != manifest["criterion"]["reference_source_sha256"]:
        raise ValueError("Reference image changed")
    for r in rows:
        a, e = annotations["records"][r["code"]], exports[r["code"]]
        if a != r["ai_annotation"] or a != e["ai_annotation"]:
            raise ValueError("AI annotation lineage mismatch")
        if a["decision"] not in DECISIONS or a["category"] != DECISIONS[a["decision"]]:
            raise ValueError("Unknown or inconsistent AI decision")
        pending = a["decision"] in ("a", "o")
        if a["requires_review"] is not pending or a["confirmed_by"] is not None:
            raise ValueError("AI must not become human confirmed")
        if any(e[k] != r[k] for k in ("sha256", "group", "dataset_split")):
            raise ValueError("Export changed photograph or split")
        if e["confirmed_by"] is not None or e["review_status"] != ("unreviewed" if pending else "ai_labeled"):
            raise ValueError("Incorrect label provenance")
        targets = None if pending else {
            "sleeves_present": 0 if a["decision"] == "v" else 1,
            "at_least_reference_length": 1 if a["decision"] == "l" else 0,
        }
        if (e["training_label"] != (None if pending else a["category"])
                or e["training_eligible"] is not (not pending) or e["attribute_targets"] != targets):
            raise ValueError("Ambiguous or long sleeveless label promoted to training truth")
    return counts


def training_plan(dataset_id):
    return {
        "dataset_id": dataset_id, "criterion_id": CRITERION,
        "status": "awaiting_user_review_then_freeze_and_implement_training",
        "initialization": {
            "resume_existing_garment_checkpoints": False,
            "generic_image_pretraining_allowed": True,
            "backbone_source_and_sha256": None,
        },
        "proposed_contract": {
            "architecture": "efficientnet_b0_sleeve_length_2x2", "input_side": 320,
            "heads": {"sleeve": ["sleeved", "sleeveless"], "length": ["below_reference", "at_least_reference"]},
            "targets": ["sleeves_present", "at_least_reference_length"],
            "sleeve_target_conversion": "sleeves_present 1 -> class 0; sleeves_present 0 -> class 1",
            "boundary_length_target": None,
            "normalization_mean": [0.485, 0.456, 0.406],
            "normalization_std": [0.229, 0.224, 0.225],
            "whole_photo": True, "preserve_aspect_ratio_letterbox": True,
            "panel_text_masking": "same as prior experiment; exact mask must be frozen per photo",
            "long_sleeveless": "other; mandatory user check even at high confidence",
            "app_integration_status": "proposal only; runtime/resources untouched",
        },
        "training": {
            "original_probability": 0.25, "one_random_transform_probability": 0.75,
            "kinds": KINDS[1:], "strength": [0.0, 1.0],
            "brightness": [0.8, 1.2], "contrast": [0.85, 1.0],
            "warm_rgb_at_max": [1.04, 1.0, 0.94], "cool_rgb_at_max": [0.94, 1.0, 1.04],
            "shadow_max_attenuation": 0.25, "blur_max_sigma_at_width1080": 0.65,
            "crop_flip_rotation": False,
            "augmentation_source": "scripts/retrain_four_training.py:change/Photos.__getitem__",
        },
        "evaluation": {
            "partitions": ["dev", "test"], "kinds": KINDS, "fixed_strength": 1.0,
            "expected_observations_per_partition": 150 * 9,
            "independent_garments_per_partition": 150,
            "selection_partition": "dev", "test_after_model_and_threshold_lock": True,
            "report": ["per-condition agreement", "all-nine agreement", "sleeve confusions",
                       "clearly short/long length errors", "long-sleeveless mandatory-review omissions",
                       "automatic versus review-needed", "AI versus human label provenance"],
            "ai_labels_are_independent_human_truth": False,
        },
        "must_complete_before_fit": ["user resolves or explicitly defers pending labels",
                                     "validate/freeze latest export, targets, source hashes and groups",
                                     "implement fresh loader that never reads either old garment checkpoint"],
    }


def build(source, output, dataset_id=DATASET_ID):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Use a new output directory; never replace reviews")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", dataset_id):
        raise ValueError("Unsafe dataset identifier")
    manifest = read(source / "manifest.json")
    annotations = read(source / "ai-annotations.json")
    baseline = read(source / "선입력라벨.json")
    validate(manifest, annotations, baseline)
    if dataset_id == manifest["dataset_id"]:
        raise ValueError("The new review needs a separate storage identity")
    page_path = source / "시작하기.html"
    page = page_path.read_text(encoding="utf-8")
    pattern = r'(<script type="application/json" id="data">)([\s\S]*?)(</script>)'
    match = re.search(pattern, page)
    if match is None:
        raise ValueError("Review page has no embedded dataset")
    data = json.loads(match[2])
    if (data["dataset_id"] != manifest["dataset_id"] or data["criterion"] != manifest["criterion"]
            or data["records"] != [{k: r[k] for k in (
                "code", "photo", "sha256", "group", "dataset_split", "ai_annotation"
            )} for r in manifest["records"]]):
        raise ValueError("Review page and manifest differ")
    # Check actual originals again before creating any output.
    for r in manifest["records"]:
        if sha(r["source"]) != r["sha256"]:
            raise ValueError(f"Original photograph changed: {r['code']}")
    rows = manifest["records"]
    for i, r in enumerate(rows):
        for other in rows[:i]:
            if (int(r["body_dhash"], 16) ^ int(other["body_dhash"], 16)).bit_count() <= 6:
                raise ValueError("Conservative image similarity conflict")
    now = datetime.now(timezone.utc).isoformat()
    lineage = {"source_dataset_id": manifest["dataset_id"],
               "source_files": {name: sha(source / name) for name in (
                   "manifest.json", "ai-annotations.json", "선입력라벨.json", "시작하기.html")},
               "selection_and_ai_labels_reused_without_change": True,
               "new_visual_review_claimed": False, "browser_storage_read_or_modified": False}
    manifest, annotations, baseline = deepcopy(manifest), deepcopy(annotations), deepcopy(baseline)
    for document in (manifest, annotations, baseline, data):
        document["dataset_id"] = dataset_id
    manifest["rebuild_lineage"] = lineage
    manifest["created_at"] = now
    baseline["exported_at"] = now
    data["rebuild_lineage"] = lineage
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    page = page[:match.start(2)] + embedded + page[match.end(2):]
    page = page.replace("LOVeCOAT · 남은 사진 확인", "LOVeCOAT · 새 카테고리 라벨링")
    page = page.replace("/ 남은 사진 확인", "/ 새 카테고리 라벨링")
    page = page.replace("'lovecoat-relabel-1000-4120-'+", "DATA.dataset_id+'-'+")
    output.mkdir(parents=True)
    for name, value in (("manifest.json", manifest), ("ai-annotations.json", annotations),
                        ("선입력라벨.json", baseline), ("training-plan.json", training_plan(dataset_id))):
        dump(output / name, value)
    (output / "시작하기.html").write_text(page, encoding="utf-8")
    report = {"dataset_id": dataset_id, "count": 1000, "source_hashes_checked": 1000,
              "split_counts": dict(Counter(r["dataset_split"] for r in rows)),
              "summary": annotations["summary"], "lineage": lineage,
              "source_files_unchanged": all(sha(source / n) == h for n, h in lineage["source_files"].items()),
              "pairwise_body_dhash_gt_6": True, "training_executed": False,
              "browser_visual_check": False,
              "artifact_sha256": {p.name: sha(p) for p in output.iterdir() if p.is_file()}}
    dump(output / "verification.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "model-results/20261103-category-relabel-1000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-id", default=DATASET_ID)
    args = parser.parse_args()
    result = build(args.source, args.output, args.dataset_id)
    print(json.dumps({k: result[k] for k in ("dataset_id", "count", "split_counts", "summary")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
