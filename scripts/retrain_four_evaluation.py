"""Paired evaluation for the isolated four-model candidates; no app changes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import shutil
import time

from scripts.retrain_four_models import ROOT, WORK, read, save, sha
from scripts.retrain_four_training import (
    KINDS,
    SHAPES,
    SIZES,
    build,
    category_input,
    change,
    configure,
    evaluate,
    protected_check,
    tensors,
)

EVAL = WORK / "evaluation"
OUTPUT = ROOT / "model-results/20261102-robust-four-models"
TASKS = ("detector", "shape", "size", "category")


def progress(stage, **values):
    EVAL.mkdir(parents=True, exist_ok=True)
    item = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage, **values}
    # Separate files from the live trainer: no shared temporary/status-file race.
    save(EVAL / "status.json", item)
    with (EVAL / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(json.dumps(item, ensure_ascii=False), flush=True)


def dataset():
    return read(WORK / "frozen-dataset.json")["records"]


def diagnostic_codes():
    return {
        r["code"]
        for r in dataset()
        if r["raw_size"] in ("70", "170", "3XL")
        or r["code"] in ("3598", "3599")
        or (r["sleeves"] == 0 and r["length"] == 1)
    }


def verify_inputs():
    protected_check()
    inventory = read(WORK / "inventory.json")

    def unchanged(row):
        return sha(row["source"]) == row["sha256"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        checks = list(pool.map(unchanged, inventory))
    changed = [r["code"] for r, ok in zip(inventory, checks) if not ok]
    if changed:
        raise ValueError("Original photos changed: " + ",".join(changed))
    save(
        EVAL / "input-integrity.json",
        {
            "photos_checked": len(checks),
            "all_originals_unchanged": True,
            "source_excel_and_production_weights_unchanged": True,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def prepare_masks():
    """Same frozen runtime locator, each full-photo illumination condition."""
    from coatdesk_ab.locator import WholePanelLocator
    from coatdesk_ab.data import load_photo

    protected_check()
    locator = WholePanelLocator(ROOT / "resources/ai/category")
    path = EVAL / "mask-locations.json"
    locations = read(path) if path.exists() else {}
    original_path = WORK / "category-mask-boxes.json"
    original = read(original_path) if original_path.exists() else {}
    rows = dataset()
    special = diagnostic_codes()
    jobs = []
    for r in rows:
        kinds = (
            KINDS
            if r["split"] in ("dev", "test") or r["code"] in special
            else ("original",)
            if r["category_eligible"]
            else ()
        )
        for kind in kinds:
            key = r["code"] + ":" + kind
            if key not in locations:
                jobs.append((r, kind))

    def one(job):
        r, kind = job
        if kind == "original" and r["code"] in original:
            result = original[r["code"]]
        else:
            if sha(r["source"]) != r["sha256"]:
                raise ValueError("Photo changed: " + r["code"])
            result = locator.locate(change(load_photo(r["source"]), kind))
        return r["code"], kind, result

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (code, kind, result) in enumerate(pool.map(one, jobs)):
            locations[code + ":" + kind] = result
            if kind == "original" and result["valid"]:
                original[code] = result
            if i % 50 == 0:
                save(path, locations)
                save(original_path, original)
                progress("mask_preparation", completed=i + 1, pending_at_start=len(jobs), cached=len(locations))
    save(path, locations)
    save(original_path, original)
    progress("masks_ready", total=len(locations), invalid=sum(not r["valid"] for r in locations.values()))


def candidate_hashes():
    result = {}
    for task in TASKS:
        folder = WORK / "training" / task
        completed = read(folder / "complete.json")
        if completed["dataset_hash"] != sha(WORK / "frozen-dataset.json"):
            raise ValueError("Candidate dataset mismatch: " + task)
        result[task] = sha(folder / "best.pt")
        if result[task] != completed["best_sha256"]:
            raise ValueError("Candidate changed after fitting: " + task)
    return result


def evaluation_identity(which):
    dependencies = read(WORK / "implementation-lock.json")
    current = {name: sha(ROOT / name) for name in dependencies}
    if current != dependencies:
        raise ValueError("Frozen training/runtime dependencies changed before comparison")
    return {
        "dataset_sha256": sha(WORK / "frozen-dataset.json"),
        "models": candidate_hashes() if which == "candidate" else read(WORK / "protected.json"),
        "evaluator_sha256": sha(Path(__file__)),
        "runtime_dependencies": current,
        "runtime_masks_sha256": sha(EVAL / "mask-locations.json"),
    }


def models(which, device):
    import torch

    loaded = {}
    for task in TASKS:
        model = build(task, baseline=which == "baseline")
        if which == "candidate":
            model.load_state_dict(
                torch.load(WORK / "training" / task / "best.pt", map_location="cpu", weights_only=True)["state_dict"]
            )
        loaded[task] = model.to(device).eval()
    return loaded


def normalize(value):
    return (value - value.new_tensor([0.485, 0.456, 0.406])[None, :, None, None]) / value.new_tensor(
        [0.229, 0.224, 0.225]
    )[None, :, None, None]


class PipelinePhotos:
    def __init__(self, items):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        from coatdesk_ab.data import load_photo
        from PIL import Image

        r, kind = self.items[index]
        photo = change(load_photo(r["source"]), kind)
        detector = tensors(photo.resize((448, 448), Image.Resampling.LANCZOS))
        return photo, detector, index


def pipeline_collate(batch):
    import torch

    photos, inputs, ids = zip(*batch)
    return list(photos), torch.stack(inputs), ids


def pipeline(which, split, device="cuda", observations=None, suffix=""):
    """Infer candidates at all scores, then apply review thresholds separately."""
    import torch
    from coatdesk_ab.data import SIZES as OLD_SIZES
    from coatdesk_locator.model import choose_detection, overlap
    from coatdesk_v2.category import decode

    special = diagnostic_codes() if split == "diagnostic" else set()
    rows = [r for r in dataset() if (r["split"] == split or r["code"] in special) and r["panel_eligible"]]
    wanted = set(observations) if observations is not None else None
    items = [(r, k) for r in rows for k in KINDS if wanted is None or (r["code"], k) in wanted]
    folder = EVAL / f"pipeline-{split}-{which}{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "predictions.json"
    protocol = {
        **evaluation_identity(which),
        "which": which,
        "device": device,
        "items": [(r["code"], k) for r, k in items],
    }
    if (folder / "protocol.json").exists():
        if read(folder / "protocol.json") != json.loads(json.dumps(protocol)):
            raise ValueError("Cached evaluation protocol changed")
    else:
        save(folder / "protocol.json", protocol)
    existing = read(path) if path.exists() else []
    known = {(r["code"], r["kind"]) for r in existing}
    remaining = [(r, k) for r, k in items if (r["code"], k) not in known]
    if not remaining:
        return existing
    loaded = models(which, device)
    masks = read(EVAL / "mask-locations.json")
    mapping = read(ROOT / "resources/ai/size_mapping.json")
    child_sizes = {n: r["size_label"] for r in mapping["child_correspondence"] for n in r["visible_numbers"]}
    classes = OLD_SIZES if which == "baseline" else SIZES
    iterator = torch.utils.data.DataLoader(
        PipelinePhotos(remaining),
        batch_size=12,
        collate_fn=pipeline_collate,
        num_workers=4,
        prefetch_factor=1,
        pin_memory=device == "cuda",
        generator=torch.Generator().manual_seed(20261102),
    )
    with torch.inference_mode():
        for photos, inputs, ids in iterator:
            predictions = loaded["detector"](list(inputs.to(device)))
            batch = []
            panels = []
            panel_indices = []
            categories = []
            category_indices = []
            for n, (photo, index) in enumerate(zip(photos, ids)):
                r, kind = remaining[index]
                predicted = predictions[n]
                predicted["boxes"] *= predicted["boxes"].new_tensor([photo.width / 448, photo.height / 448] * 2)
                loc = choose_detection(predicted, photo.width, photo.height)
                iou, _ = overlap(loc["box"], r["box"])
                _, coverage = overlap(loc["crop_box"], r["box"])
                p = {
                    "code": r["code"],
                    "group": r["group"],
                    "kind": kind,
                    "split": split,
                    "source_split": r["split"],
                    "which": which,
                    "truth_shape": r["shape"],
                    "truth_raw": r["raw_size"],
                    "truth_size": r["size"],
                    "truth_category": r["category"],
                    "category_primary_eligible": r["category_eligible"],
                    "long_sleeveless_truth": r["sleeves"] == 0 and r["length"] == 1,
                    "panel_score": loc["score"],
                    "box": loc["box"],
                    "crop_box": loc["crop_box"],
                    "iou": iou,
                    "crop_coverage": coverage,
                    "panel_correct": bool(loc["valid"] and coverage >= 0.99),
                    "predicted_shape": None,
                    "predicted_raw": None,
                    "predicted_size": None,
                    "raw_category": None,
                    "shape_score": 0.0,
                    "size_score": 0.0,
                    "sleeve_score": 0.0,
                    "length_score": 0.0,
                    "long_sleeveless": False,
                    "category_failure": True,
                    "consistent": False,
                }
                if loc["crop_box"]:
                    from coatdesk_ab.data import letterbox

                    # choose_detection already applied the runtime margin once.
                    panels.append(tensors(letterbox(photo.crop(loc["crop_box"]), 512)[0]))
                    panel_indices.append(n)
                mask = masks[r["code"] + ":" + kind]
                if mask["valid"]:
                    categories.append(tensors(category_input(photo, mask["box"])))
                    category_indices.append(n)
                p["mask_location"] = mask
                batch.append(p)
            if panels:
                x = normalize(torch.stack(panels).to(device))
                shape = loaded["shape"](x).softmax(1).float().tolist()
                size = loaded["size"](x).softmax(1).float().tolist()
                for i, sp, zp in zip(panel_indices, shape, size):
                    p = batch[i]
                    s = SHAPES[sp.index(max(sp))]
                    z = classes[zp.index(max(zp))]
                    p.update(
                        predicted_shape=s,
                        predicted_raw=z,
                        predicted_size=child_sizes.get(z, z),
                        shape_score=max(sp),
                        size_score=max(zp),
                        shape_probabilities=sp,
                        size_probabilities=dict(zip(classes, zp)),
                        consistent=z.isdigit() == (s == "child"),
                    )
            if categories:
                logits = loaded["category"](normalize(torch.stack(categories).to(device))).float()
                values = torch.cat((logits[:, :2].softmax(1), logits[:, 2:].softmax(1)), 1).tolist()
                for i, v in zip(category_indices, values):
                    decoded = decode(v)
                    p = batch[i]
                    p.update(
                        raw_category=decoded["raw_category"],
                        category_candidate=decoded["candidate"],
                        sleeve_score=decoded["sleeve_score"],
                        length_score=decoded["length_score"],
                        category_probabilities=v,
                        long_sleeveless=v[1] > v[0] and v[3] > v[2],
                        category_failure=False,
                    )
            for p in batch:
                p["shape_correct"] = p["predicted_shape"] == p["truth_shape"]
                p["raw_correct"] = p["predicted_raw"] == p["truth_raw"]
                p["size_correct"] = p["predicted_size"] == p["truth_size"]
                p["category_correct"] = p["raw_category"] == p["truth_category"]
                p["workflow_correct"] = p["shape_correct"] and p["size_correct"] and p["category_correct"]
                p["mandatory_review"] = (
                    p["predicted_shape"] == "child"
                    or p["long_sleeveless"]
                    or p["category_failure"]
                    or not p["consistent"]
                    or not p["crop_box"]
                )
                existing.append(p)
            save(path, existing)
            progress(
                "pipeline_evaluation",
                which=which,
                split=split,
                device=device,
                completed=len(existing),
                total=len(items),
            )
    return existing


def automatic(p, thresholds):
    return (
        not p["mandatory_review"]
        and p["panel_score"] >= thresholds["panel"]
        and p["shape_score"] >= thresholds["shape"]
        and p["size_score"] >= thresholds["size"]
        and p["sleeve_score"] >= 0.9
        and p["length_score"] >= 0.9
    )


def automatic_truth_correct(p):
    # Score the missed long-vest check even when "coat" matches Excel.
    # Truth never participates in the runtime abstention decision itself.
    return p["workflow_correct"] and not p.get("long_sleeveless_truth", False)


def review_summary(rows, thresholds):
    passed = [p for p in rows if automatic(p, thresholds)]
    return {
        "observations": len(rows),
        "unique_photo_codes": len({p["code"] for p in rows}),
        "independent_garment_groups": len({p.get("group", p["code"]) for p in rows}),
        "automatic": len(passed),
        "automatic_errors": sum(not automatic_truth_correct(p) for p in passed),
        "mandatory_long_sleeveless_misses": sum(p.get("long_sleeveless_truth", False) for p in passed),
        "review": len(rows) - len(passed),
        "thresholds": thresholds,
        "automatic_error_cases": [
            {
                "code": p["code"],
                "kind": p["kind"],
                "long_sleeveless_review_missed": p.get("long_sleeveless_truth", False),
            }
            for p in passed
            if not automatic_truth_correct(p)
        ],
    }


def sweep(rows):
    """Upper-tail histogram evaluates 125,000 score triplets without changing labels."""
    import numpy as np

    good = np.zeros((50, 50, 50), dtype=np.int64)
    bad = np.zeros_like(good)
    for p in rows:
        if p["mandatory_review"] or p["sleeve_score"] < 0.9 or p["length_score"] < 0.9:
            continue
        # The tiny tolerance only corrects representation of exact integer
        # percentages; the selected triplet is rechecked with actual float >=.
        indices = [min(99, math.floor(p[t + "_score"] * 100 + 1e-10)) - 50 for t in ("panel", "shape", "size")]
        if min(indices) < 0:
            continue
        (good if automatic_truth_correct(p) else bad)[tuple(indices)] += 1

    def upper(array):
        result = array[::-1, ::-1, ::-1].cumsum(0).cumsum(1).cumsum(2)
        return result[::-1, ::-1, ::-1]

    totals, errors = upper(good + bad), upper(bad)
    safe = np.argwhere(errors == 0)
    if not len(safe):
        return {"safe_triplet": False, "reason": "No zero-observed-error combination in the predeclared 50–99% grid."}
    best = max((int(totals[tuple(i)]), int(sum(i)), tuple(int(v) for v in i)) for i in safe)
    thresholds = dict(zip(("panel", "shape", "size"), [(v + 50) / 100 for v in best[2]]))
    checked = review_summary(rows, thresholds)
    if checked["automatic_errors"] or checked["automatic"] != best[0]:
        raise AssertionError("Threshold histogram/direct-decision mismatch")
    # Also answer how far thresholds can jointly decrease while preserving
    # exactly the selected dev automatic cohort. Keep this secondary proposal
    # fixed before seeing test predictions; do not combine independent minima.
    equivalent = np.argwhere((errors == 0) & (totals == best[0]))
    equivalent = equivalent[np.all(equivalent <= np.array(best[2])[None, :], axis=1)]
    lower = min((int(sum(i)), tuple(int(v) for v in i)) for i in equivalent)[1]
    lower_thresholds = dict(zip(("panel", "shape", "size"), [(v + 50) / 100 for v in lower]))
    lower_checked = review_summary(rows, lower_thresholds)
    base_cohort = {(p["code"], p["kind"]) for p in rows if automatic(p, thresholds)}
    lower_cohort = {(p["code"], p["kind"]) for p in rows if automatic(p, lower_thresholds)}
    if lower_cohort != base_cohort or lower_checked["automatic_errors"]:
        raise AssertionError("Lower-threshold proposal changed the frozen dev cohort")
    return {
        "safe_triplet": True,
        **checked,
        "joint_lower_same_dev_cohort": lower_checked,
        "joint_lower_selection": "Smallest threshold sum among componentwise-lower triplets with the identical maximum safe dev cohort; then lexicographically smaller. Search floor is 50%.",
        "search_observations": "all nine dev conditions; each photo contributes nine correlated observations",
        "tie_rule": "maximum automatic count, then largest threshold sum, then panel/shape/size lexicographically higher",
    }


def pipeline_summary(rows, thresholds=None):
    from scripts.category_expanded_model import class_metrics

    summary = {}
    for kind in KINDS:
        selected = [p for p in rows if p["kind"] == kind]
        if not selected:
            continue
        m = {
            "count": len(selected),
            "panel_coverage_correct": sum(p["panel_correct"] for p in selected),
            "mean_iou": sum(p["iou"] for p in selected) / len(selected),
            "mask_failures": sum(p["category_failure"] for p in selected),
        }
        for field in ("shape", "raw", "size", "category", "workflow"):
            subset = [p for p in selected if p["category_primary_eligible"]] if field == "category" else selected
            m[field] = {
                "count": len(subset),
                "correct": sum(p[field + "_correct"] for p in subset),
                "accuracy": sum(p[field + "_correct"] for p in subset) / len(subset) if subset else None,
            }
        m["class_metrics"] = {}
        for field, predicted_key, truth_key, classes in [
            ("shape", "predicted_shape", "truth_shape", SHAPES),
            ("raw", "predicted_raw", "truth_raw", SIZES),
            ("size", "predicted_size", "truth_size", ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL"]),
            ("category", "raw_category", "truth_category", ["자켓", "코트", "조끼", "기타"]),
        ]:
            subset = [p for p in selected if p["category_primary_eligible"]] if field == "category" else selected
            m["class_metrics"][field] = class_metrics(
                [p[truth_key] for p in subset], [p[predicted_key] or "failure" for p in subset], classes + ["failure"]
            )
        m["review_default90"] = review_summary(selected, dict(panel=0.9, shape=0.9, size=0.9))
        if thresholds:
            m["review_proposed"] = review_summary(selected, thresholds)
        summary[kind] = m
    for field in ("shape", "raw", "size", "category", "workflow"):
        codes = {p["code"] for p in rows if field != "category" or p["category_primary_eligible"]}
        summary[field + "_all_conditions_correct"] = {
            "codes": len(codes),
            "correct": sum(all(p[field + "_correct"] for p in rows if p["code"] == c) for c in codes),
        }
    summary["per_raw_class"] = {
        raw: {
            "observations": sum(p["truth_raw"] == raw for p in rows),
            "correct": sum(p["truth_raw"] == raw and p["raw_correct"] for p in rows),
            "photos": len({p["code"] for p in rows if p["truth_raw"] == raw}),
        }
        for raw in SIZES
    }
    return summary


def standalone(which, split, device="cuda"):
    import torch

    folder = EVAL / f"standalone-{split}-{which}"
    folder.mkdir(parents=True, exist_ok=True)
    protocol = {**evaluation_identity(which), "which": which, "split": split, "device": device}
    if (folder / "protocol.json").exists():
        if read(folder / "protocol.json") != protocol:
            raise ValueError("Cached standalone evaluation protocol changed")
    else:
        save(folder / "protocol.json", protocol)
    masks = read(EVAL / "mask-locations.json")
    result = {}
    for task in TASKS:
        path = folder / f"{task}.json"
        if path.exists():
            result[task] = read(path)["summary"]
            continue
        rows = [
            r
            for r in dataset()
            if r["split"] == split and r["category_eligible" if task == "category" else "panel_eligible"]
        ]
        if task == "category":
            # Standalone uses original locator masks for every condition, exactly
            # as the epoch selector. Full pipeline reruns masks for each variant.
            rows = [{**r, "mask_box": masks[r["code"] + ":original"]["box"]} for r in rows]
        model = build(task, baseline=which == "baseline")
        if which == "candidate":
            model.load_state_dict(
                torch.load(WORK / "training" / task / "best.pt", map_location="cpu", weights_only=True)["state_dict"]
            )
        predictions, summary = evaluate(model.to(device), rows, task, device=device, baseline=which == "baseline")
        save(path, {"summary": summary, "predictions": predictions, "gold_panel_crop": task in ("shape", "size")})
        result[task] = summary
        del model
        torch.cuda.empty_cache()
        progress("standalone_complete", which=which, split=split, task=task)
    return result


def compare():
    configure()
    verify_inputs()
    candidates = candidate_hashes()
    prepare_masks()
    lock = EVAL / "decision-before-test.json"
    if not lock.exists():
        dev = {}
        for which in ("baseline", "candidate"):
            single = standalone(which, "dev")
            predicted = pipeline(which, "dev")
            dev[which] = {
                "standalone": single,
                "pipeline": pipeline_summary(predicted),
                "threshold_search": sweep(predicted),
            }
        save(
            lock,
            {
                "dataset_sha256": sha(WORK / "frozen-dataset.json"),
                "candidate_sha256": candidates,
                "development": dev,
                "comparison": "baseline complete combination versus new complete combination; no mixed-model selection",
                "category_thresholds": {"sleeve": 0.9, "length": 0.9},
                "test_model_predictions_inspected_before_decision": False,
                "test_preprocessing_inspected_before_decision": True,
                "preprocessing_disclosure": "Labels and mask preparation were inspected; eight dark-condition mask failures were observed before comparison. Neither weights nor the frozen pipeline were changed using these held-out failures.",
            },
        )
    decision = read(lock)
    if decision["candidate_sha256"] != candidates or decision["dataset_sha256"] != sha(WORK / "frozen-dataset.json"):
        raise ValueError("Test decision was frozen for different inputs")
    results = {}
    for which in ("baseline", "candidate"):
        single = standalone(which, "test")
        predicted = pipeline(which, "test")
        proposed = decision["development"][which]["threshold_search"].get("thresholds")
        lower = (
            decision["development"][which]["threshold_search"].get("joint_lower_same_dev_cohort", {}).get("thresholds")
        )
        results[which] = {"standalone": single, "pipeline": pipeline_summary(predicted, proposed)}
        if proposed:
            results[which]["threshold_test"] = review_summary(predicted, proposed)
        if lower:
            results[which]["joint_lower_threshold_test"] = review_summary(predicted, lower)
            results[which]["joint_lower_original_test"] = review_summary(
                [p for p in predicted if p["kind"] == "original"], lower
            )
        proposals = [p for p in (proposed, lower) if p]
        # CPU coverage includes every original held-out test photograph, all
        # non-original field/coverage failures and proposed-threshold boundaries.
        verify = {
            (p["code"], p["kind"])
            for p in predicted
            if p["kind"] == "original"
            or not p["workflow_correct"]
            or not p["panel_correct"]
            or any(abs(p[t + "_score"] - 0.9) < 0.015 for t in ("panel", "shape", "size", "sleeve", "length"))
            or any(
                abs(p[t + "_score"] - proposal[t]) < 0.015 for proposal in proposals for t in ("panel", "shape", "size")
            )
        }
        cpu = pipeline(which, "test", "cpu", verify, suffix="-cpu-check")
        by = {(p["code"], p["kind"]): p for p in predicted}
        differences = []
        for p in cpu:
            g = by[p["code"], p["kind"]]
            fields = ["predicted_shape", "predicted_raw", "predicted_size", "raw_category", "mandatory_review"]
            differing = [field for field in fields if p[field] != g[field]]
            if proposed and automatic(p, proposed) != automatic(g, proposed):
                differing.append("proposed_review_decision")
            if lower and automatic(p, lower) != automatic(g, lower):
                differing.append("joint_lower_review_decision")
            if automatic(p, dict(panel=0.9, shape=0.9, size=0.9)) != automatic(g, dict(panel=0.9, shape=0.9, size=0.9)):
                differing.append("default90_review_decision")
            if differing:
                differences.append({"code": p["code"], "kind": p["kind"], "fields": differing})
        results[which]["cpu_check"] = {
            "observations": len(cpu),
            "original_photos": sum(p["kind"] == "original" for p in cpu),
            "scope": "all original test photos plus all GPU field/coverage failures and threshold boundaries",
            "differences": differences,
            "passed": not differences,
        }
    save(EVAL / "comparison.json", {"decision": decision, "test": results})
    special_results = {}
    annotations = {r["code"]: r for r in dataset()}
    for which in ("baseline", "candidate"):
        predicted = pipeline(which, "diagnostic")
        special_results[which] = {"raw_classes": {}, "long_sleeveless": {}}
        for raw in ("70", "170", "3XL"):
            subset = [p for p in predicted if p["truth_raw"] == raw]
            special_results[which]["raw_classes"][raw] = {
                "observations": len(subset),
                "photos": len({p["code"] for p in subset}),
                "correct_raw": sum(p["raw_correct"] for p in subset),
                "source_splits": sorted({p["source_split"] for p in subset}),
                "interpretation": "Mixed fitting/development/test diagnostic; not independent held-out accuracy",
            }
        vests = [
            p for p in predicted if annotations[p["code"]]["sleeves"] == 0 and annotations[p["code"]]["length"] == 1
        ]
        special_results[which]["long_sleeveless"] = {
            "observations": len(vests),
            "photos": len({p["code"] for p in vests}),
            "other_and_mandatory_review": sum(p["raw_category"] == "기타" and p["mandatory_review"] for p in vests),
            "default90_automatic": sum(automatic(p, dict(panel=0.9, shape=0.9, size=0.9)) for p in vests),
        }
        special_results[which]["3598_3599"] = [
            {
                k: p[k]
                for k in ("code", "kind", "predicted_raw", "predicted_size", "truth_raw", "truth_size", "source_split")
            }
            for p in predicted
            if p["code"] in ("3598", "3599")
        ]
    save(EVAL / "special-diagnostics.json", special_results)
    verify_inputs()
    progress("comparison_complete", cpu_checks_passed=all(r["cpu_check"]["passed"] for r in results.values()))


def export():
    verify_inputs()
    candidate_hashes()
    comparison = read(EVAL / "comparison.json")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    exported = {}
    for task in TASKS:
        source = WORK / "training" / task / "best.pt"
        dest = OUTPUT / (task + ".pt")
        if dest.exists() and sha(dest) != sha(source):
            raise ValueError("Never overwrite a different exported candidate")
        if not dest.exists():
            shutil.copyfile(source, dest)
        exported[dest.name] = sha(dest)
    training_evidence = audit_training()
    save(OUTPUT / "training-evidence.json", training_evidence)
    for name, source in [
        ("dataset-summary.json", WORK / "frozen-dataset.json"),
        ("comparison.json", EVAL / "comparison.json"),
        ("special-diagnostics.json", EVAL / "special-diagnostics.json"),
        ("size_mapping.json", ROOT / "resources/ai/size_mapping.json"),
    ]:
        if name == "dataset-summary.json":
            d = read(source)
            save(
                OUTPUT / name,
                {"summary": d["summary"], "limitations": d["limitations"], "frozen_dataset_sha256": sha(source)},
            )
        else:
            shutil.copyfile(source, OUTPUT / name)
    save(
        OUTPUT / "manifest.json",
        {
            "files": exported,
            "candidate_only": True,
            "production_replaced": False,
            "sizes": SIZES,
            "shapes": SHAPES,
            "size_output_count": 18,
            "loading": "Each .pt is a metadata wrapper containing state_dict. Existing app loader requires a separate approved integration change.",
            "comparison_file": "comparison.json",
            "source_excel_preserved": True,
            "evaluation_code_sha256": sha(Path(__file__)),
            "training_implementation": read(WORK / "implementation-lock.json"),
            "original_training_source_snapshot": str(WORK / "reproducibility-snapshot"),
            "training_evidence": "training-evidence.json",
        },
    )
    write_report(comparison)
    for task in TASKS:
        path = WORK / "training" / task / "complete.json"
        completed = read(path)
        completed.update(
            comparison_pending=False,
            comparison_sha256=sha(OUTPUT / "comparison.json"),
            report_sha256=sha(OUTPUT / "REPORT.md"),
        )
        save(path, completed)
    progress("candidates_exported", directory=str(OUTPUT), count=4)


def audit_training(tasks=TASKS):
    """Inspect actual optimizer checkpoints and changed tensors, not markers only."""
    import torch

    results = {}
    for task in tasks:
        run = WORK / "training" / task
        done, config = read(run / "complete.json"), read(run / "configuration.json")
        last = torch.load(run / "last.pt", map_location="cpu", weights_only=False)
        best = torch.load(run / "best.pt", map_location="cpu", weights_only=True)
        old_path = (
            ROOT
            / "resources/ai"
            / (
                "category/model.pt"
                if task == "category"
                else "detector.pt"
                if task == "detector"
                else f"panel-{task}.pt"
            )
        )
        old = torch.load(old_path, map_location="cpu", weights_only=True)
        old = old["state_dict"] if task == "category" else old
        changes = [
            k
            for k, value in best["state_dict"].items()
            if k in old and value.shape == old[k].shape and value.is_floating_point() and not torch.equal(value, old[k])
        ]
        steps = [int(s["step"]) for s in last["optimizer"]["state"].values() if "step" in s]
        expected_steps = done["epochs"] * math.ceil(config["fit"] / (16 if task == "category" else 12))
        if (
            not changes
            or not steps
            or max(steps) != expected_steps
            or last["epoch"] != done["epochs"]
            or last["cursor"] != 0
        ):
            raise AssertionError("Incomplete gradient-fit evidence: " + task)
        if any(not torch.isfinite(value).all() for value in best["state_dict"].values() if value.is_floating_point()):
            raise AssertionError("Non-finite candidate state: " + task)
        results[task] = {
            "epochs": done["epochs"],
            "best_epoch": best["epoch"],
            "optimizer_steps": max(steps),
            "expected_steps": expected_steps,
            "changed_same_shape_float_tensors": len(changes),
            "all_selected_weights_finite": True,
            "best_sha256": sha(run / "best.pt"),
            "last_sha256": sha(run / "last.pt"),
        }
        del last, best, old
    return results


def write_report(comparison):
    data = read(WORK / "frozen-dataset.json")
    test = comparison["test"]

    def percent(value):
        return f"{value:.2%}"

    labels = {
        "detector": "판넬 위치(기준 영역 99% 포함)",
        "shape": "성별·성인/아동",
        "size": "사이즈 원문",
        "category": "카테고리(엑셀 엄격 일치)",
    }
    lines = [
        "# 네 모델 재학습 및 비교 결과",
        "",
        "새 가중치 네 개를 별도 후보로 보존했다. 원본 사진·엑셀·앱 모델·설치본은 교체하지 않았다.",
        "",
        "## 자료와 분할",
        "",
        f"- 원본 엑셀 3,596건 → 반복 옷 2,517그룹. 분할 제약을 합친 그룹은 {data['summary']['garment_groups']:,}개다.",
        "- 판넬/성별/사이즈 학습 1,801·개발 367·최종 367. 카테고리 학습 1,776·개발 364·최종 362.",
        "- 같은 옷 및 불확실 중복은 분할을 넘지 않는다. 기존 가중치 학습 그룹 661개는 학습에만 배치했다.",
        "- 학습은 원본 25%와 한 종류의 완만한 조명/색온도/대비/음영/흐림 변형 75%를 매 방문 생성했다.",
        "- 평가는 원본+고정 8변형이다. 9조건은 같은 사진의 반복 관찰이며 9배의 독립 옷이 아니다.",
        "- 3598·3599는 원본 재확인에 따라 160→XL이다. 최초 150 오독과 정정 이력을 보존했다.",
        "",
        "## 학습 및 선택 회차",
        "",
        "| 모델 | 실행 회차 | 선택 회차 |",
        "|---|---:|---:|",
    ]
    for task in TASKS:
        done = read(WORK / "training" / task / "complete.json")
        lines.append(f"| {labels[task]} | {done['epochs']} | {done['best_epoch']} |")
    lines += [
        "",
        "## 최종 평가: 모델 단독",
        "",
        "성별/사이즈 단독은 정답 판넬 영역을 사용한다. 카테고리는 원본에서 찾은 고정 가림 영역을 사용한다. 실제 전체 경로는 다음 표로 구분한다.",
        "",
        "| 모델 | 기존 원본 | 신규 원본 | 기존 9조건 평균 | 신규 9조건 평균 |",
        "|---|---:|---:|---:|---:|",
    ]
    for task in TASKS:
        old, new = (test[w]["standalone"][task] for w in ("baseline", "candidate"))
        lines.append(
            f"| {labels[task]} | {percent(old['original']['accuracy'])} | {percent(new['original']['accuracy'])} | {percent(sum(old[k]['accuracy'] for k in KINDS) / 9)} | {percent(sum(new[k]['accuracy'] for k in KINDS) / 9)} |"
        )
    lines += [
        "",
        "## 최종 평가: 검출부터 실행한 전체 경로",
        "",
        "| 항목 | 기존 원본 | 신규 원본 | 기존 9조건 모두 일치 사진 | 신규 9조건 모두 일치 사진 |",
        "|---|---:|---:|---:|---:|",
    ]
    for field, label in [
        ("shape", "성별·성인/아동"),
        ("raw", "사이즈 원문"),
        ("size", "변환 사이즈"),
        ("category", "카테고리"),
        ("workflow", "전체 업무 정답"),
    ]:
        a, b = (test[w]["pipeline"] for w in ("baseline", "candidate"))
        x, y = a[field + "_all_conditions_correct"], b[field + "_all_conditions_correct"]
        lines.append(
            f"| {label} | {percent(a['original'][field]['accuracy'])} | {percent(b['original'][field]['accuracy'])} | {x['correct']}/{x['codes']} | {y['correct']}/{y['codes']} |"
        )
    lines += [
        "",
        "## 사람 확인 기준",
        "",
        "개발 9조건에서 판넬/성별/사이즈 50~99% 조합을 탐색한 뒤 최종 평가 전에 고정했다. 카테고리 기준 90%, 아동 성별 및 긴 민소매 필수 확인은 유지했다. 기준은 후보 값을 바꾸지 않고 확인 필요 여부만 바꾼다.",
        "",
    ]
    for which, label in [("baseline", "기존"), ("candidate", "신규")]:
        search = comparison["decision"]["development"][which]["threshold_search"]
        if not search["safe_triplet"]:
            lines.append(
                f"- {label}: 개발에서 자동 오답 0건인 기준 조합을 찾지 못했다. 이 탐색으로 기준 인하를 권하지 않는다."
            )
        else:
            t = search["thresholds"]
            result = test[which]["threshold_test"]
            lines.append(
                f"- {label}: 판넬 {t['panel']:.0%}·성별/연령 {t['shape']:.0%}·사이즈 {t['size']:.0%}. 최종 9조건 {result['observations']:,}회 관찰 중 자동 {result['automatic']:,}회, 자동 오답 {result['automatic_errors']}회, 확인 {result['review']:,}회. 독립 옷 {result['independent_garment_groups']}그룹이다."
            )
            if result["automatic_errors"]:
                lines.append(
                    "  최종 평가에서 자동 오답이 발생했으므로 이 기준은 채택 실패다. 같은 최종 평가로 기준을 다시 조정하지 않았다."
                )
            lower = search["joint_lower_same_dev_cohort"]["thresholds"]
            lower_test = test[which]["joint_lower_threshold_test"]
            lower_original = test[which]["joint_lower_original_test"]
            lines.append(
                f"- {label}의 개발 자동 처리 대상을 그대로 유지한 공동 인하 조합: 판넬 {lower['panel']:.0%}·성별/연령 {lower['shape']:.0%}·사이즈 {lower['size']:.0%}. 최종 원본 자동 {lower_original['automatic']}/{lower_original['observations']}장·오답/필수확인누락 {lower_original['automatic_errors']}건, 9조건 전체 자동 {lower_test['automatic']}회·오답/필수확인누락 {lower_test['automatic_errors']}회다. 검색 하한은 50%이며 그 아래까지 검증한 결과가 아니다."
            )
            if lower_test["automatic_errors"]:
                lines.append("  공동 인하 조합은 최종 평가를 통과하지 못했으므로 채택하지 않는다.")
        check = test[which]["cpu_check"]
        lines.append(
            f"- {label} CPU 대조: 원본 {check['original_photos']}장 포함 {check['observations']:,}조건. GPU와 후보/확인 판정 차이 {len(check['differences'])}건. 전체 원본과 오류·기준 경계를 검사했으며 모든 9조건의 CPU 전수 검사를 의미하지 않는다."
        )
    lines += [
        "",
        "## 해석과 제한",
        "",
        "- 개발/최종 사진은 과거 촬영 자료이고 일부는 이전 검토에 노출됐다. 내년 새 촬영에서의 정확도 보장이나 완전한 블라인드 시험이 아니다.",
        "- 아동 70은 학습 5·개발 1·최종 1이다. 최종 1벌의 9조건을 9벌로 해석하지 않는다.",
        "- 170과 3XL은 그룹/학습 이력 제약으로 독립 검증 자료가 없다. 아동 60·성인 2XS는 자료가 없어 학습/지원하지 않았다.",
        "- 새 위치·소매·기장 보조 라벨은 Codex 시각 검수이며 사용자 개별 승인과 구분한다. 경계 기장은 손실에서 제외했다. 업무 정답은 최신 엑셀로 엄격 평가했다.",
        "- 긴 민소매는 기타/확인 필요를 유지하므로 엑셀이 코트·조끼인 경우 엄격 일치에서는 다를 수 있다. 이 예외를 감추기 위해 엑셀을 바꾸지 않았다.",
        "- 원본 성적이 퇴행하거나 조명 안정성이 개선되지 않은 모델은 기존 모델 유지가 적절하다. 네 모델을 학습했다는 사실과 네 모델 모두의 교체 권고는 구분한다.",
        "- 현재 앱은 17개 사이즈 출력을 기대한다. 신규 18출력 후보의 앱 연결은 채택 결정 후 별도 구현이 필요하다.",
        "",
        "세부 클래스별 수·혼동표·조건별 결과·확인 기준 결과는 `comparison.json`, 재현용 고정 목록과 사진별 예측은 프로젝트의 `.local/robust-four-models-20261102/`에 보존한다.",
    ]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def smoke_baseline():
    """Cross-check this evaluator against the actual shipped CPU runtime."""
    from coatdesk_v2.ai_runtime import Models
    from coatdesk_v2.category import CategoryModel
    from coatdesk_ab.data import load_photo

    configure()
    known = read(EVAL / "mask-locations.json")
    rows = [r for r in dataset() if r["split"] == "dev" and r["panel_eligible"] and r["code"] + ":original" in known][
        :8
    ]
    wanted = {(r["code"], "original") for r in rows}
    predicted = pipeline("baseline", "dev", "cpu", wanted, suffix="-runtime-smoke-" + sha(Path(__file__))[:12])
    runtime = Models(ROOT / "resources/ai")
    category = CategoryModel(ROOT / "resources/ai/category")
    sources = {r["code"]: r["source"] for r in rows}
    differences = []
    for p in predicted:
        actual = runtime.analyze(sources[p["code"]], dict(panel=0.9, shape=0.9, size=0.9))
        cat = category.analyze(load_photo(sources[p["code"]]))
        sp = actual["probabilities"]["shape"]
        proposed_shape = max(sp, key=sp.get)
        values = {
            "predicted_shape": proposed_shape,
            "predicted_raw": actual["raw_size"],
            "predicted_size": actual["candidates"]["size"],
            "raw_category": cat["raw_category"],
        }
        for key, value in values.items():
            if p[key] != value:
                differences.append({"code": p["code"], "field": key, "evaluator": p[key], "runtime": value})
        actual_auto = not actual["held"] and proposed_shape != "child" and not cat["needs_review"]
        if automatic(p, dict(panel=0.9, shape=0.9, size=0.9)) != actual_auto:
            differences.append({"code": p["code"], "field": "automatic"})
    result = {
        "photos": len(predicted),
        "differences": differences,
        "passed": not differences,
        "scope": "Full baseline pipeline against shipped Models.analyze and CategoryModel.analyze on identical original photos",
    }
    save(EVAL / "runtime-smoke.json", result)
    progress("baseline_runtime_smoke", **result)
    if differences:
        raise AssertionError("Evaluation/runtime mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-masks", "smoke-baseline", "compare", "export"])
    args = parser.parse_args()
    if args.command == "prepare-masks":
        prepare_masks()
    elif args.command == "smoke-baseline":
        smoke_baseline()
    elif args.command == "compare":
        compare()
    else:
        export()


if __name__ == "__main__":
    main()
