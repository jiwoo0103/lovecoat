"""Fresh controlled fits with +100 clear garments and +50/+50 validation photos.

Reuses the frozen previous trainer without modifying its experiment or weights.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import shutil
import time

import numpy as np

from scripts import category_rebuild_training as base

ROOT = base.ROOT
WORK = ROOT / ".local/category-rebuild-plus100-20261104-01"
OUTPUT = ROOT / "model-results/20261104-category-rebuild-plus100-01"
PREVIOUS = ROOT / ".local/category-rebuild-training-20261104-01"
LABELS = ROOT / "model-results/category-rebuild-label-audit-20261103-01/labels-approved-round3.json"
LABEL_SHA = "4e6d0e3ba3be519f8a1853314c228e0e1240b28468e27f7282beed9ecf2ff2b9"
ORIGINAL_IMPLEMENTATION = base.implementation


def implementation():
    result = ORIGINAL_IMPLEMENTATION()
    result[str(Path(__file__).relative_to(ROOT))] = base.sha(__file__)
    result["coatdesk_ab/network.py"] = base.sha(ROOT / "coatdesk_ab/network.py")
    return result


def bind(which):
    base.WORK = WORK / which
    base.OUTPUT = OUTPUT / which
    base.LABELS = LABELS
    base.LABEL_SHA = LABEL_SHA
    base.implementation = implementation


def validate_records(rows, train_count=800, evaluation_count=200):
    expected = {"train": train_count, "dev": evaluation_count, "test": evaluation_count}
    if Counter(r["dataset_split"] for r in rows) != expected:
        raise ValueError("Wrong partition counts")
    for key in ("code", "sha256", "body_sha256", "group"):
        if len({r[key] for r in rows}) != len(rows):
            raise ValueError("Duplicate garment identity: " + key)
    for row in rows:
        if row["dataset_split"] == "test" and (row["old_augmented_fit"] or row["old_program_prior_fit"]):
            raise ValueError("Test exposure to old gradient fit")
        if row.get("cohort") == "added_validation" and (row["old_augmented_split"] is not None or row["old_program_prior_fit"]):
            raise ValueError("Additional validation is not held out from old models")
    return expected


def additional_row(item, role):
    category = item["category"]
    if category not in ("자켓", "코트", "조끼") or item["visual_status"] != "clear":
        raise ValueError("Additional ambiguous labels are not silently hardened")
    photo = item["photo"]
    sleeve, length = int(category != "조끼"), int(category == "코트")
    old_record = item.get("old_record")
    if role == "train":
        if not old_record or old_record["dataset_split"] != "train" or old_record["training_label"] != category:
            raise ValueError("Training addition must be a rechecked old fitting label")
    return {
        "code": photo["code"], "source": photo["source"], "sha256": photo["sha256"],
        "body_sha256": photo["body_sha256"], "body_dhash": photo["body_dhash"],
        "group": photo.get("group", "new-" + photo["code"]), "dataset_split": role,
        "gender": photo.get("gender"), "raw_size": photo.get("raw_size", photo.get("size")),
        "user_category": category, "boundary": False,
        "attribute_targets": {"sleeves_present": sleeve, "at_least_reference_length": length},
        "target": [1 - sleeve, length], "evaluation_category": category,
        "acceptable_categories": [category], "attribute_correction": None,
        "label_source": "codex_visual_clear_4120_additions_20261104", "confirmed_by": None,
        "old_approval_kind": old_record.get("approval_kind") if old_record else None,
        "old_category": old_record.get("training_label") if old_record else None,
        "current_criterion_review": item["review_reason"],
        "cohort": "added_training" if role == "train" else "added_validation",
        "old_augmented_split": photo.get("old_augmented_split"),
        "old_augmented_fit": photo.get("old_augmented_split") == "train" if old_record else False,
        "old_program_prior_fit": bool(old_record),
    }


def prepare():
    from coatdesk_ab.locator import WholePanelLocator
    if (WORK / "fit800/protocol.json").exists():
        bind("fit800")
        base.check_protocol()
        print("Inputs already frozen", flush=True)
        return
    if base.sha(LABELS) != LABEL_SHA:
        raise ValueError("Latest approved labels changed")
    selection = base.read(WORK / "approved-additions.json")
    if selection["no_model_predictions_used_for_validation_labels"] is not True:
        raise ValueError("Validation labels cannot be chosen from model predictions")
    old = base.read(base.OLD_WORK / "frozen-dataset.json")
    old_index = {r["code"]: r for r in old["records"]}
    manifest = base.read(base.PACK / "manifest.json")
    rows = base.freeze_rows(manifest, base.read(LABELS), old["records"])
    prior = {r["code"]: r for r in base.read(PREVIOUS / "dataset.json")["records"]}
    for row in rows:
        previous = prior[row["code"]]
        if previous["sha256"] != row["sha256"]:
            raise ValueError("Old image cache refers to another source")
        row["locations"] = deepcopy(previous["locations"])
        row["cohort"] = "original"
    additions = [additional_row(item, item["role"]) for item in selection["records"]]
    legacy_path = ROOT / "model-results/20261026-category-label-review/approved-dataset-20261027-01/dataset.json"
    legacy = base.read(legacy_path)["records"]
    history_codes = {r["code"] for r in legacy + old["records"] + manifest["records"]}
    inventory = {r["code"]: r for r in base.read(WORK / "archive-inventory.json")}
    history = [inventory[code] for code in history_codes]
    for row in additions:
        if row["cohort"] != "added_validation":
            continue
        if row["code"] in history_codes or any(
            row["sha256"] == prior["sha256"] or row["body_sha256"] == prior["body_sha256"]
            or (int(row["body_dhash"], 16) ^ int(prior["body_dhash"], 16)).bit_count() <= 6
            for prior in history
        ):
            raise ValueError("Fresh validation overlaps historical data: " + row["code"])
    if Counter(r["user_category"] for r in additions if r["dataset_split"] == "train") != {"자켓": 50, "코트": 50}:
        raise ValueError("Expected exactly 50 jackets and 50 coats")
    rows += additions
    validate_records(rows)
    # Every newly added photo is conservatively distinct from the original pack
    # and from every other addition, not just a unique filename.
    originals = {r["code"]: r for r in manifest["records"]}
    hashes = {r["code"]: int(r["body_dhash"], 16) for r in manifest["records"]}
    hashes.update({r["code"]: int(r["body_dhash"], 16) for r in additions})
    for row in additions:
        if any((hashes[row["code"]] ^ h).bit_count() <= 6 for code, h in hashes.items() if code != row["code"]):
            raise ValueError("Additional source too similar to a known garment: " + row["code"])
    prior_files = [LABELS, base.PACK / "manifest.json", base.GENERIC, *base.OLD_MODELS.values(),
                   ROOT / "model-results/20261104-category-rebuild-1000-01/model.pt",
                   PREVIOUS / "dataset.json", PREVIOUS / "protocol.json",
                   WORK / "approved-additions.json", WORK / "selection-candidates.json",
                   WORK / "archive-inventory.json", WORK / "select_additions.py",
                   WORK / "visual-review.json", WORK / "finalize_selection.py", legacy_path,
                   base.OLD_WORK / "frozen-dataset.json", base.OLD_WORK / "category-mask-boxes.json",
                   ROOT / "resources/ai/category/policy.json"]
    protected = {str(path): base.sha(path) for path in prior_files}
    # Old locator/template bytes were verified by the previous frozen protocol.
    base.WORK = PREVIOUS
    base.check_protocol()
    location_cache = base.read(base.OLD_WORK / "category-mask-boxes.json")
    arrays = {split: np.lib.format.open_memmap(WORK / (split + "-images.npy"), mode="w+", dtype=np.uint8,
                  shape=(200 * 9, 3, 320, 320)) for split in ("dev", "test")}
    for split, array in arrays.items():
        array[:150 * 9] = np.load(PREVIOUS / (split + "-images.npy"), mmap_mode="r")
    counter = Counter()
    for row in rows:
        row["partition_index"] = counter[row["dataset_split"]]
        counter[row["dataset_split"]] += 1
    locator = WholePanelLocator(ROOT / "resources/ai/category")
    started = time.perf_counter()

    def one(row):
        if base.sha(row["source"]) != row["sha256"]:
            raise ValueError("Source changed: " + row["code"])
        photo = base.load_photo(row["source"])
        if row["code"] in originals:
            return row["code"], None
        locations = {}
        for k, kind in enumerate(("original",) if row["dataset_split"] == "train" else base.KINDS):
            changed = base.change(photo, kind)
            known = location_cache.get(row["code"]) if kind == "original" and row["code"] in old_index else None
            location = known or locator.locate(changed)
            if kind == "original" and not location["valid"]:
                raise ValueError("Original placard requires review: " + row["code"])
            locations[kind] = location
            if row["dataset_split"] != "train":
                image = base.category_input(changed, location["box"]) if location["valid"] else base.letterbox(changed, 320)[0]
                arrays[row["dataset_split"]][row["partition_index"] * 9 + k] = np.asarray(image).transpose(2, 0, 1)
        return row["code"], locations

    locations = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (code, found) in enumerate(pool.map(one, rows), 1):
            if found is not None:
                locations[code] = found
            if i % 100 == 0:
                print({"prepared": i, "total": len(rows), "seconds": round(time.perf_counter() - started)}, flush=True)
    for row in additions:
        row["locations"] = locations[row["code"]]
    for array in arrays.values():
        array.flush()
    old_protocol = base.read(PREVIOUS / "protocol.json")
    for which in ("fit700", "fit800"):
        folder = WORK / which
        folder.mkdir(parents=True, exist_ok=True)
        subset = [r for r in rows if which == "fit800" or r["cohort"] != "added_training"]
        validate_records(subset, train_count=800 if which == "fit800" else 700)
        base.save(folder / "dataset.json", {"records": subset, "dataset_id": "category-rebuild-plus100-20261104-01-" + which,
                  "criterion": base.read(LABELS)["criterion"]})
        for split in ("dev", "test"):
            # Separate files preserve each frozen run if the other is resumed.
            shutil.copyfile(WORK / (split + "-images.npy"), folder / (split + "-images.npy"))
        protocol = deepcopy(old_protocol)
        protocol.update(dataset_sha256=base.sha(folder / "dataset.json"), approved_label_sha256=LABEL_SHA,
                        implementation=implementation(), protected_inputs=protected,
                        cache_sha256={s: base.sha(folder / (s + "-images.npy")) for s in ("dev", "test")})
        protocol["summary"] = {split: {
            "count": len(part := [r for r in subset if r["dataset_split"] == split]),
            "user_categories": dict(Counter(r["user_category"] for r in part)),
            "evaluation_categories": dict(Counter(r["evaluation_category"] for r in part)),
            "cohorts": dict(Counter(r["cohort"] for r in part)),
            "user_reviewed": sum(r["confirmed_by"] is not None for r in part),
            "boundaries": sum(r["boundary"] for r in part),
            "old_program_prior_fit": sum(r["old_program_prior_fit"] for r in part),
            "old_augmented_gradient_fit": sum(r["old_augmented_fit"] for r in part)} for split in ("train", "dev", "test")}
        protocol["controlled_comparison"] = {
            "variant": which, "same_latest_round3_labels": True, "same_initial_generic_weights": True,
            "same_seed_optimizer_augmentation_epochs": True, "same_dev200_and_test200": True,
            "only_training_addition": 100 if which == "fit800" else 0,
            "new_validation_is_ai_visual_not_independent_human_truth": True,
            "old_test150_is_previously_examined": True, "new_test50_not_previously_model_evaluated": True}
        base.save(folder / "protocol.json", protocol)
    for name, value in protected.items():
        if base.sha(name) != value:
            raise ValueError("Protected input changed during freeze")
    base.save(WORK / "prepare-complete.json", {"records": len(rows), "counts": dict(counter),
        "added_labels_source": "AI current-criterion visual review, not newly user-confirmed",
        "all_sources_verified": True, "pairwise_additional_dhash_gt6": True,
        "new_original_locator_failures": 0,
        "condition_locator_failures": sum(not v["valid"] for r in rows for v in r["locations"].values()),
        "seconds": round(time.perf_counter() - started, 2)})
    print("Both frozen experiments ready", flush=True)


def compare():
    import torch
    from torchvision.models import efficientnet_b0
    bind("fit700")
    control_protocol = base.check_protocol()
    bind("fit800")
    protocol = base.check_protocol()
    for which in ("fit700", "fit800"):
        completed = base.read(WORK / which / "training-complete.json")
        if base.sha(WORK / which / "best.pt") != completed["best_sha256"]:
            raise ValueError("A selected checkpoint changed")
    rows = base.read(base.WORK / "dataset.json")["records"]
    dev = [r for r in rows if r["dataset_split"] == "dev"]
    test = [r for r in rows if r["dataset_split"] == "test"]
    models = {**base.OLD_MODELS,
        "previous_rebuild": ROOT / "model-results/20261104-category-rebuild-1000-01/model.pt",
        "control700": WORK / "fit700/best.pt", "expanded800": WORK / "fit800/best.pt"}
    base.configure()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, path in models.items():
        print({"comparing": name}, flush=True)
        model = efficientnet_b0(weights=None)
        model.classifier[1] = torch.nn.Linear(model.classifier[1].in_features, 4)
        saved = torch.load(path, map_location="cpu", weights_only=True)
        model.load_state_dict(saved["state_dict"])
        model.cuda().eval()
        predictions = base.infer(model, test, "test")
        base.save(OUTPUT / (name + "-test-predictions.json"), predictions)
        summary = base.summarize(predictions)
        for cohort, count in (("original", 150), ("added_validation", 50)):
            codes = {r["code"] for r in test if r["cohort"] == cohort}
            summary[cohort] = base.summarize([p for p in predictions if p["code"] in codes])
            if len(codes) != count:
                raise ValueError("Invalid evaluation cohort")
        summary["model_sha256"] = base.sha(path)
        summary["dev_diagnostics"] = base.summarize(base.infer(model, dev, "dev"))
        results[name] = summary
        print({"model": name, "originals": summary["per_condition"]["original"], "all9": summary["tolerant"],
               "old150": summary["original"]["tolerant"], "new50": summary["added_validation"]["tolerant"]}, flush=True)
        del model
        torch.cuda.empty_cache()
    base.check_protocol()
    with ThreadPoolExecutor(max_workers=4) as pool:
        source_hashes = list(pool.map(lambda r: base.sha(r["source"]), rows))
    if any(digest != row["sha256"] for row, digest in zip(rows, source_hashes)):
        raise ValueError("An original photo changed during the experiment")
    for split in ("dev", "test"):
        for which, frozen in (("fit700", control_protocol), ("fit800", protocol)):
            if base.sha(WORK / which / (split + "-images.npy")) != frozen["cache_sha256"][split]:
                raise ValueError("Evaluation cache changed")
    bind("fit700")
    base.check_protocol()
    bind("fit800")
    base.save(OUTPUT / "comparison.json", {"test_garments": 200, "observations": 1800,
        "results": results, "thresholds": protocol["thresholds"], "checkpoint_selection": "dev200 only, all frozen before test",
        "limitations": ["Old test150 has already been inspected in previous experiments; fresh50 reported separately.",
                        "Added labels are AI visual review under the current reference criterion, not new independent human ground truth.",
                        "Nine conditions are correlated variants, not1800 independent garments.",
                        "Control700 and expanded800 share round3 labels, initialization, augmentation, epochs and evaluation set."]})
    for which, name in (("fit700", "control700"), ("fit800", "expanded800")):
        folder = OUTPUT / name
        folder.mkdir(exist_ok=True)
        for file, output in (("best.pt", "model.pt"), ("protocol.json", "protocol.json"),
                             ("history.json", "history.json"), ("training-complete.json", "training-complete.json")):
            shutil.copyfile(WORK / which / file, folder / output)
    print("Comparison complete: " + str(OUTPUT), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "train700", "train800", "compare"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "compare":
        compare()
    else:
        bind("fit700" if args.command == "train700" else "fit800")
        base.train()
