"""Train an isolated sleeve/coverage model on an approved, fixed three-way split."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import shutil
import time

from scripts.category_first_model import read, save, sha

CATEGORIES = ["자켓", "코트", "조끼", "기타"]
MEAN, STD = [.485, .456, .406], [.229, .224, .225]


def validate_dataset(dataset):
    if dataset.get("schema_version") != "category-approved-split-v1":
        raise ValueError("Use the approved split, not unconfirmed or QA annotations")
    rows = dataset["records"]
    if len({r["code"] for r in rows}) != len(rows):
        raise ValueError("Duplicate garment code")
    partitions = {s: [] for s in ("train", "dev", "test", "excluded")}
    for row in rows:
        if (row["review_status"] != "confirmed" or not row["user_confirmed"]
                or row["training_label"] != row["display_category"]
                or row["training_label"] not in CATEGORIES):
            raise ValueError("Unapproved or contradictory label")
        partitions[row["dataset_split"]].append(row)
        for task in ("sleeves_present", "full_buttocks_coverage"):
            if row["targets"][task] not in (0, 1, None):
                raise ValueError("Invalid attribute target")
        if row["length"] in ("borderline", "unclear") and row["targets"]["full_buttocks_coverage"] is not None:
            raise ValueError("Uncertain length cannot supply a hard target")
        allowed = row["acceptable_categories"]
        if not allowed or row["training_label"] not in allowed or any(c not in CATEGORIES for c in allowed):
            raise ValueError("Invalid acceptable categories")
        if len(allowed) > 1 and (set(allowed) != {"자켓", "코트"} or row["length"] != "borderline"):
            raise ValueError("Only explicit jacket/coat boundaries can have multiple accepted labels")
    for key in ("code", "source_sha256", "source_group"):
        seen = {}
        for split in ("train", "dev", "test"):
            for row in partitions[split]:
                if row[key] in seen and seen[row[key]] != split:
                    raise ValueError(f"Partition overlap: {key}")
                seen[row[key]] = split
    if not all(partitions[s] for s in ("train", "dev", "test")):
        raise ValueError("All three fixed partitions are required")
    return partitions


def decode(probabilities, sleeve_threshold=.5, length_threshold=.5):
    """Logit order is present/absent, then not-full/full coverage."""
    if len(probabilities) != 4:
        raise ValueError("Four binary-head probabilities are required")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("Invalid probabilities")
    if any(not math.isclose(sum(probabilities[i:i + 2]), 1, abs_tol=1e-5) for i in (0, 2)):
        raise ValueError("Invalid binary probability sum")
    if not all(.5 <= v <= 1 for v in (sleeve_threshold, length_threshold)):
        raise ValueError("Invalid threshold")
    absent = probabilities[1] > probabilities[0]
    long = probabilities[3] > probabilities[2]
    sleeve_score = probabilities[int(absent)]
    length_score = probabilities[2 + int(long)]
    candidate = "조끼" if absent else ("코트" if long else "자켓")
    raw = "기타" if absent and long else candidate
    reasons = []
    if absent and long:
        reasons.append("긴 민소매 예외")
    if sleeve_score < sleeve_threshold:
        reasons.append("소매 유무 점수 미달")
    if length_score < length_threshold:
        reasons.append("기장 점수 미달")
    return {
        "candidate": candidate, "raw_category": raw,
        "display_category": "기타" if reasons else raw,
        "needs_review": bool(reasons), "review_reasons": reasons,
        "score": min(sleeve_score, length_score),
        "sleeve_score": sleeve_score, "length_score": length_score,
        "predicted_sleeves_present": int(not absent), "predicted_full_coverage": int(long),
    }


def class_metrics(truths, predictions, classes=CATEGORIES):
    matrix = {c: {p: 0 for p in classes} for c in classes}
    for truth, prediction in zip(truths, predictions, strict=True):
        matrix[truth][prediction] += 1
    per_class = {}
    for c in classes:
        tp = matrix[c][c]
        support = sum(matrix[c].values())
        predicted = sum(matrix[t][c] for t in classes)
        precision, recall = tp / predicted if predicted else 0., tp / support if support else 0.
        per_class[c] = {"support": support, "correct": tp, "predicted": predicted,
                        "precision": precision, "recall": recall,
                        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.}
    present = [c for c in classes if per_class[c]["support"]]
    correct = sum(t == p for t, p in zip(truths, predictions, strict=True))
    return {"count": len(truths), "correct": correct, "accuracy": correct / len(truths) if truths else None,
            "macro_f1": sum(per_class[c]["f1"] for c in present) / len(present) if present else 0.,
            "per_class": per_class, "confusion": matrix}


def summarize(rows):
    truth = [r["truth"] for r in rows]
    raw = [r["raw_category"] for r in rows]
    tolerant = [r["truth"] if r["raw_category"] in r["acceptable_categories"] else r["raw_category"] for r in rows]
    core = [r for r in rows if r["truth"] != "기타"]
    accepted = [r for r in rows if not r["needs_review"]]
    attributes = {}
    for target, prediction in (("sleeves_present", "predicted_sleeves_present"), ("full_buttocks_coverage", "predicted_full_coverage")):
        available = [r for r in rows if r["targets"][target] is not None]
        correct = sum(r["targets"][target] == r[prediction] for r in available)
        recall = []
        for value in (0, 1):
            subset = [r for r in available if r["targets"][target] == value]
            if subset:
                recall.append(sum(r[prediction] == value for r in subset) / len(subset))
        attributes[target] = {"count": len(available), "correct": correct,
                              "accuracy": correct / len(available) if available else None,
                              "balanced_accuracy": sum(recall) / len(recall) if recall else None}
    accepted_correct = sum(r["raw_category"] in r["acceptable_categories"] for r in accepted)
    core_tolerant = [r["truth"] if r["raw_category"] in r["acceptable_categories"] else r["raw_category"] for r in core]
    return {
        "strict": class_metrics(truth, raw), "tolerant": class_metrics(truth, tolerant),
        "core_three_class_strict": class_metrics([r["truth"] for r in core], [r["raw_category"] for r in core]),
        "core_three_class_tolerant": class_metrics([r["truth"] for r in core], core_tolerant),
        "attributes": attributes,
        "automatic": {"count": len(accepted), "correct_tolerant": accepted_correct,
                      "correct_strict": sum(r["raw_category"] == r["truth"] for r in accepted),
                      "accuracy_tolerant": accepted_correct / len(accepted) if accepted else None,
                      "accuracy_strict": sum(r["raw_category"] == r["truth"] for r in accepted) / len(accepted) if accepted else None,
                      "coverage": len(accepted) / len(rows) if rows else None,
                      "review_count": len(rows) - len(accepted)},
        "review_policy": {"other_truth_count": sum(r["truth"] == "기타" for r in rows),
                          "other_truth_held": sum(r["truth"] == "기타" and r["needs_review"] for r in rows)},
        "gross_error_diagnostics": {
            "sleeve_errors": sum(r["targets"]["sleeves_present"] != r["predicted_sleeves_present"] for r in rows if r["targets"]["sleeves_present"] is not None),
            "clearly_short_to_coat": sum(r["length"] == "short" and r["raw_category"] == "코트" for r in rows),
            "very_long_sleeved_to_jacket": sum(r["length"] == "very_long" and r["truth"] == "코트" and r["raw_category"] == "자켓" for r in rows),
        },
    }


def selection_key(summary, dev_loss):
    return (summary["core_three_class_tolerant"]["macro_f1"],
            summary["attributes"]["sleeves_present"]["balanced_accuracy"],
            summary["tolerant"]["accuracy"], -dev_loss)


def choose_thresholds(rows, precision_target=.98):
    grid = [.5, .6, .7, .8, .85, .9, .95, .975, .99, 1.]
    candidates = []
    for sleeve in grid:
        for length in grid:
            accepted = [r for r in rows if r["raw_category"] != "기타" and r["sleeve_score"] >= sleeve and r["length_score"] >= length]
            correct = sum(r["raw_category"] in r["acceptable_categories"] for r in accepted)
            precision = correct / len(accepted) if accepted else None
            candidates.append({"sleeve": sleeve, "length": length, "accepted": len(accepted), "correct": correct,
                               "precision_tolerant": precision, "precision_target": precision_target})
    qualified = [c for c in candidates if c["accepted"] >= 20 and c["precision_tolerant"] >= precision_target]
    if not qualified:
        # No evidence for the requested automation quality: require review, rather than tune on test.
        return {"sleeve": 1., "length": 1., "accepted": 0, "correct": 0,
                "precision_tolerant": None, "precision_target": precision_target,
                "selection": "no_supported_threshold_all_review"}
    chosen = max(qualified, key=lambda c: (c["accepted"], c["precision_tolerant"], c["sleeve"] + c["length"]))
    return {**chosen, "selection": "max_dev_coverage_at_observed_98pct_tolerant_precision_min20"}


def prepare(args):
    import numpy as np
    from coatdesk_ab.data import letterbox, load_photo
    from coatdesk_ab.locator import WholePanelLocator
    from scripts.category_first_model import mask_panel

    if args.work.exists():
        raise FileExistsError("Use a fresh experiment directory")
    dataset = read(args.dataset)
    partitions = validate_dataset(dataset)
    rows = [dict(r) for r in dataset["records"] if r["dataset_split"] != "excluded"]
    cached = {r["code"]: r for r in read(args.cached / "prepared.json")["records"]}
    locator = WholePanelLocator(args.cached)
    args.work.mkdir(parents=True)
    arrays = {side: np.lib.format.open_memmap(args.work / f"images-{side}.npy", mode="w+", dtype=np.uint8,
                                            shape=(len(rows), 3, side, side)) for side in (320, 448)}
    locations, missing = [], 0
    started = time.perf_counter()
    for index, row in enumerate(rows):
        if sha(row["source"]) != row["source_sha256"]:
            raise ValueError("Photo changed")
        photo = load_photo(row["source"])
        location = cached.get(row["code"])
        if location:
            if location["sha256"] != row["source_sha256"]:
                raise ValueError("Cached box belongs to another photo")
            location = location["location"]
        else:
            location = locator.locate(photo)
            missing += 1
        if not location["valid"]:
            raise ValueError(f"Placard location needs checking: {row['code']}")
        masked = mask_panel(photo, location["box"])
        for side in arrays:
            image, transform = letterbox(masked, side)
            assert transform["no_crop"]
            arrays[side][index] = np.asarray(image).transpose(2, 0, 1)
        row["index"] = index
        row["attribute_targets"] = [1 - row["targets"]["sleeves_present"] if row["targets"]["sleeves_present"] is not None else -1,
                                    row["targets"]["full_buttocks_coverage"] if row["targets"]["full_buttocks_coverage"] is not None else -1]
        locations.append({"code": row["code"], "source_sha256": row["source_sha256"], "location": location})
        if (index + 1) % 150 == 0:
            print(json.dumps({"prepared": index + 1, "total": len(rows), "seconds": round(time.perf_counter() - started, 1)}), flush=True)
    for array in arrays.values():
        array.flush()
    protected = [args.dataset, args.cached / "prepared.json", args.weights, Path(__file__),
                 Path("scripts/category_attributes_model.py"), Path("scripts/category_first_model.py"),
                 Path("coatdesk_ab/data.py"), Path("coatdesk_ab/locator.py")]
    protected += list((args.cached / "references").glob("*"))
    protocol = {
        "architecture": "efficientnet_b0_sleeve_length_2x2", "dataset_id": dataset["dataset_id"],
        "records": rows, "split_counts": {s: len(rs) for s, rs in partitions.items()},
        "seed": 2026102801, "epochs": 20, "batch": 16, "evaluated_epochs": [5, 10, 15, 20],
        "input_sides": [320, 448], "backbone_lr": .0001, "head_lr": .001,
        "weight_decay": .0001, "label_smoothing": .02,
        "class_weights": "sqrt_inverse_frequency_per_head_train_only", "batch_norm": "frozen_running_statistics",
        "augmentation": "whole_photo_shrink_and_pad_0.85_1.0_brightness_0.9_1.1_horizontal_flip_no_crop",
        "normalization_mean": MEAN, "normalization_std": STD,
        "panel_mask": "cached_or_same_frozen_template_locator_box_plus_8px_white_mask_at_1080_width",
        "crop": "none_full_photo_letterbox_aspect_preserved", "new_locator_count": missing,
        "targets": "approved_explicit_attributes_null_and_boundary_length_masked_not_category_proxy",
        "sleeve_classes": ["present", "absent"], "length_classes": ["not_full_cover", "full_cover"],
        "selection": "dev_tolerant_core_macro_f1_then_sleeve_balanced_accuracy_then_overall_tolerant_accuracy_then_lower_valid_attribute_loss; ties_prefer_320",
        "gate_selection": "dev_only_max_coverage_observed_98pct_tolerant_precision_min20_100_fixed_threshold_pairs",
        "scores_calibrated": False, "final_test_use": "once_after_checkpoint_resolution_and_gate_freeze",
        "weights": str(args.weights.resolve()), "approval": dataset["approval"],
        "input_files": {str(p.resolve()): sha(p) for p in protected},
        "image_hashes": {str(side): sha(args.work / f"images-{side}.npy") for side in arrays},
        "app_integration": False, "external_requests": False,
    }
    save(args.work / "protocol.json", protocol)
    save(args.work / "panel-locations.json", locations)
    print(json.dumps({"prepared": len(rows), "seconds": round(time.perf_counter() - started, 1), "new_locations": missing}), flush=True)


def check_frozen(work):
    p = read(work / "protocol.json")
    for path, expected in p["input_files"].items():
        if sha(path) != expected:
            raise ValueError(f"Frozen experiment input changed: {path}")
    return p


def masked_attribute_loss(logits, targets, criteria):
    losses = []
    for head in range(2):
        valid = targets[:, head] >= 0
        if valid.any():
            losses.append(criteria[head](logits[valid, 2 * head:2 * head + 2], targets[valid, head]))
    return sum(losses) / len(losses) if losses else logits.sum() * 0


def tensor_inference(model, tensor, rows, side, device="cuda", thresholds=(.5, .5)):
    import torch

    mean = torch.tensor(MEAN, device=device)[None, :, None, None]
    std = torch.tensor(STD, device=device)[None, :, None, None]
    predictions, losses = [], []
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(rows), 16):
            selected = rows[start:start + 16]
            x = tensor[[r["index"] for r in selected]].to(device).float() / 255
            logits = model((x - mean) / std).float()
            probabilities = torch.cat([logits[:, :2].softmax(1), logits[:, 2:].softmax(1)], dim=1).tolist()
            for row, values in zip(selected, probabilities, strict=True):
                item = {"code": row["code"], "partition": row["dataset_split"], "truth": row["training_label"],
                        "acceptable_categories": row["acceptable_categories"], "age_group": row["age_group"],
                        "length": row["length"], "targets": row["targets"], "probabilities": values,
                        **decode(values, *thresholds)}
                item.update(correct_strict=item["raw_category"] == item["truth"],
                            correct_tolerant=item["raw_category"] in item["acceptable_categories"])
                predictions.append(item)
                for head, target in enumerate(row["attribute_targets"]):
                    if target >= 0:
                        losses.append(-math.log(max(values[head * 2 + target], 1e-10)))
    return predictions, sum(losses) / len(losses) if losses else 0.


def train(args):
    import numpy as np
    import torch
    from torch import nn
    from torch.nn import functional as F
    from scripts.category_attributes_model import build_model

    p = check_frozen(args.work)
    if sha(args.work / f"images-{args.side}.npy") != p["image_hashes"][str(args.side)]:
        raise ValueError("Prepared tensor changed")
    run = args.work / f"run-{args.side}"
    if run.exists():
        raise FileExistsError("Never overwrite a training run")
    if not torch.cuda.is_available():
        raise RuntimeError("Existing local CUDA device is required")
    run.mkdir()
    torch.set_num_threads(4)
    random.seed(p["seed"])
    np.random.seed(p["seed"])
    torch.manual_seed(p["seed"])
    torch.cuda.manual_seed_all(p["seed"])
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    fit = [r for r in p["records"] if r["dataset_split"] == "train"]
    dev = [r for r in p["records"] if r["dataset_split"] == "dev"]
    tensor = torch.from_numpy(np.load(args.work / f"images-{args.side}.npy", allow_pickle=False)).cuda()
    model = build_model(p["weights"]).cuda()
    mean = torch.tensor(MEAN, device="cuda")[None, :, None, None]
    std = torch.tensor(STD, device="cuda")[None, :, None, None]
    criteria = []
    for head in range(2):
        counts = Counter(r["attribute_targets"][head] for r in fit if r["attribute_targets"][head] >= 0)
        if set(counts) != {0, 1}:
            raise ValueError("Each binary head needs both approved target values")
        weights = torch.tensor([math.sqrt(sum(counts.values()) / (2 * counts[i])) for i in range(2)], device="cuda")
        criteria.append(nn.CrossEntropyLoss(weight=weights / weights.mean(), label_smoothing=p["label_smoothing"]))
    optimizer = torch.optim.AdamW([{"params": model.features.parameters(), "lr": p["backbone_lr"]},
                                  {"params": model.classifier.parameters(), "lr": p["head_lr"]}], weight_decay=p["weight_decay"])
    scaler = torch.amp.GradScaler("cuda")
    epochs, best, best_summary = [], None, None
    started = time.perf_counter()
    for epoch in range(1, p["epochs"] + 1):
        model.train()
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.eval()
        factor = .15 + .85 * (1 + math.cos(math.pi * (epoch - 1) / p["epochs"])) / 2
        for group, lr in zip(optimizer.param_groups, [p["backbone_lr"], p["head_lr"]], strict=True):
            group["lr"] = lr * factor
        order, total = fit.copy(), 0.
        random.shuffle(order)
        for start in range(0, len(order), p["batch"]):
            rows = order[start:start + p["batch"]]
            x = tensor[[r["index"] for r in rows]].float() / 255
            side = args.side
            small = round(side * random.uniform(.85, 1))
            x = F.interpolate(x, size=(small, small), mode="bilinear", align_corners=False)
            left, top = random.randint(0, side - small), random.randint(0, side - small)
            x = F.pad(x, (left, side - small - left, top, side - small - top), value=1)
            if random.random() < .5:
                x = x.flip(-1)
            x = (x * torch.empty((len(rows), 1, 1, 1), device="cuda").uniform_(.9, 1.1)).clamp(0, 1)
            targets = torch.tensor([r["attribute_targets"] for r in rows], device="cuda")
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                logits = model((x - mean) / std).float()
                loss = masked_attribute_loss(logits, targets, criteria)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5)
            scaler.step(optimizer)
            scaler.update()
            total += float(loss.detach()) * len(rows)
        record = {"epoch": epoch, "loss": total / len(fit), "seconds": round(time.perf_counter() - started, 1)}
        if epoch in p["evaluated_epochs"]:
            predictions, dev_loss = tensor_inference(model, tensor, dev, side)
            summary = summarize(predictions)
            key = selection_key(summary, dev_loss)
            record.update(dev_summary=summary, dev_attribute_nll=dev_loss, selection_key=key)
            if best is None or key > best:
                best, best_summary = key, record
                checkpoint = {"architecture": p["architecture"], "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()},
                              "input_side": side, "epoch": epoch, "dataset_id": p["dataset_id"],
                              "protocol_sha256": sha(args.work / "protocol.json"),
                              "sleeve_classes": p["sleeve_classes"], "length_classes": p["length_classes"]}
                torch.save(checkpoint, run / "best.pt")
        epochs.append(record)
        with (run / "progress.json").open("w", encoding="utf-8") as stream:
            json.dump({"side": side, "epochs": epochs}, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"side": side, "epoch": epoch, "loss": round(record["loss"], 4), "seconds": record["seconds"],
                          "dev_correct_tolerant": record.get("dev_summary", {}).get("tolerant", {}).get("correct"),
                          "dev_vest_correct": record.get("dev_summary", {}).get("strict", {}).get("per_class", {}).get("조끼", {}).get("correct")}), flush=True)
    selected = build_model().cuda()
    checkpoint = torch.load(run / "best.pt", weights_only=True, map_location="cpu")
    selected.load_state_dict(checkpoint["state_dict"], strict=True)
    predictions, dev_loss = tensor_inference(selected, tensor, dev, args.side)
    save(run / "dev-predictions.json", predictions)
    save(run / "dev-summary.json", {"side": args.side, "selected_epoch": checkpoint["epoch"], "selection_key": best,
                                     "metrics": summarize(predictions), "dev_attribute_nll": dev_loss,
                                     "best_epoch_record": best_summary, "seconds": time.perf_counter() - started})
    save(run / "epochs.json", epochs)
    print(json.dumps({"completed_side": args.side, "selected_epoch": checkpoint["epoch"], "checkpoint_sha256": sha(run / "best.pt")}), flush=True)


def select(args):
    p = check_frozen(args.work)
    if (args.work / "selection.json").exists():
        raise FileExistsError("Selection is already frozen")
    summaries = [read(args.work / f"run-{side}/dev-summary.json") for side in p["input_sides"]]
    chosen = max(summaries, key=lambda s: (*s["selection_key"], -s["side"]))
    side = chosen["side"]
    rows = read(args.work / f"run-{side}/dev-predictions.json")
    thresholds = choose_thresholds(rows)
    selection = {"dataset_id": p["dataset_id"], "input_side": side, "epoch": chosen["selected_epoch"],
                 "protocol_sha256": sha(args.work / "protocol.json"), "checkpoint_sha256": sha(args.work / f"run-{side}/best.pt"),
                 "model_selection_uses": "dev_only", "thresholds": thresholds,
                 "resolution_comparison": summaries, "test_predictions_generated_before_selection": False}
    save(args.work / "selection.json", selection)
    print(json.dumps({"input_side": side, "epoch": selection["epoch"], "thresholds": thresholds}), flush=True)


def evaluate(args):
    import numpy as np
    import torch
    from scripts.category_attributes_model import build_model

    p, choice = check_frozen(args.work), read(args.work / "selection.json")
    if args.output.exists():
        raise FileExistsError("Never overwrite final results")
    checkpoint_path = args.work / f"run-{choice['input_side']}/best.pt"
    if sha(checkpoint_path) != choice["checkpoint_sha256"] or sha(args.work / "protocol.json") != choice["protocol_sha256"]:
        raise ValueError("Selected weights or protocol changed")
    torch.set_num_threads(4)
    model = build_model().cuda()
    checkpoint = torch.load(checkpoint_path, weights_only=True, map_location="cpu")
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    side = choice["input_side"]
    if sha(args.work / f"images-{side}.npy") != p["image_hashes"][str(side)]:
        raise ValueError("Selected input tensor changed")
    tensor = torch.from_numpy(np.load(args.work / f"images-{side}.npy", allow_pickle=False)).cuda()
    thresholds = (choice["thresholds"]["sleeve"], choice["thresholds"]["length"])
    predictions, _ = tensor_inference(model, tensor, p["records"], side, thresholds=thresholds)
    partitions = {s: summarize([r for r in predictions if r["partition"] == s]) for s in ("train", "dev", "test")}
    # Independent CPU reload on the final partition verifies the serialized artifact.
    cpu_model = build_model()
    cpu_model.load_state_dict(checkpoint["state_dict"], strict=True)
    test_rows = [r for r in p["records"] if r["dataset_split"] == "test"]
    cpu_predictions, _ = tensor_inference(cpu_model, tensor.cpu(), test_rows, side, device="cpu", thresholds=thresholds)
    gpu_test = [r for r in predictions if r["partition"] == "test"]
    differences = [max(abs(a - b) for a, b in zip(g["probabilities"], c["probabilities"], strict=True)) for g, c in zip(gpu_test, cpu_predictions, strict=True)]
    for g, c in zip(gpu_test, cpu_predictions, strict=True):
        if (g["code"], g["raw_category"], g["display_category"]) != (c["code"], c["raw_category"], c["display_category"]):
            raise ValueError("CPU/GPU classification mismatch")
    check_frozen(args.work)
    if not all(sha(r["source"]) == r["source_sha256"] for r in p["records"]):
        raise ValueError("Original photo changed during training")
    args.output.mkdir(parents=True)
    shutil.copyfile(checkpoint_path, args.output / "sleeve-length-efficientnet-b0.pt")
    for name in ("protocol.json", "selection.json", "panel-locations.json"):
        shutil.copyfile(args.work / name, args.output / name)
    shutil.copyfile(__file__, args.output / "training-source.py")
    save(args.output / "predictions.json", predictions)
    save(args.output / "cpu-test-predictions.json", cpu_predictions)
    save(args.output / "metrics.json", {"partitions": partitions, "input_side": side, "selected_epoch": choice["epoch"],
                                        "thresholds": choice["thresholds"], "scores_calibrated": False,
                                        "label_quality": "AI-assisted labels batch approved, not independently adjudicated human gold",
                                        "final_test_role": "not_used_for_checkpoint_resolution_or_threshold_selection"})
    save(args.output / "integrity.json", {"checkpoint_sha256": sha(checkpoint_path), "original_photos_unchanged": len(p["records"]),
                                          "protected_input_files": p["input_files"], "cpu_reload_test_count": len(cpu_predictions),
                                          "cpu_gpu_max_probability_difference": max(differences),
                                          "cpu_gpu_decisions_identical": True, "excluded_1495_not_used": True,
                                          "app_integration": False, "external_requests": False})
    for input_side in p["input_sides"]:
        for filename in ("epochs.json", "dev-summary.json", "dev-predictions.json"):
            shutil.copyfile(args.work / f"run-{input_side}/{filename}", args.output / f"{input_side}-{filename}")
    write_report(args.output, predictions, partitions, p, choice)
    print(json.dumps({"final_test": partitions["test"], "output": str(args.output)}, ensure_ascii=True), flush=True)


def write_report(output, predictions, partitions, protocol, choice):
    photos = output / "photos"
    photos.mkdir()
    sources = {r["code"]: r for r in protocol["records"]}
    for row in predictions:
        shutil.copyfile(sources[row["code"]]["photo"], photos / f"{row['code']}.jpg")
    test = partitions["test"]
    def percent(value):
        return f"{value * 100:.1f}%" if value is not None else "—"
    table = ["| 자료 | 전체 | 엄격 일치 | 경계 허용 일치 | 자동 분류 | 자동 분류 일치 | 확인 필요 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for split, name in (("train", "학습"), ("dev", "개발 검증"), ("test", "최종 평가")):
        m = partitions[split]
        table.append(f"| {name} | {m['strict']['count']} | {m['strict']['correct']} ({percent(m['strict']['accuracy'])}) | {m['tolerant']['correct']} ({percent(m['tolerant']['accuracy'])}) | {m['automatic']['count']} | {m['automatic']['correct_tolerant']} ({percent(m['automatic']['accuracy_tolerant'])}) | {m['automatic']['review_count']} |")
    text = "# 확대 자료 학습 결과\n\nEfficientNet-B0에서 소매 유무와 엉덩이 전체 덮임을 별도로 학습했습니다. 전체 정면 사진을 유지하고 경계·불명확 기장 목표는 손실에서 제외했습니다.\n\n"
    text += f"학습 567·개발 검증 134·최종 평가 141벌. 320·448 입력을 각 20회 학습하고 개발 검증으로 선택한 결과 **{choice['input_side']} 입력, {choice['epoch']}회 체크포인트**입니다. 선택·확인 필요 임계값은 최종 평가 전에 고정했습니다.\n\n" + "\n".join(table)
    text += "\n\n## 최종 평가 분류별 후보 일치\n\n| 분류 | 정답 수 | 엄격 일치 | 정밀도 | 재현율 |\n|---|---:|---:|---:|---:|\n"
    for category, m in test["strict"]["per_class"].items():
        text += f"| {category} | {m['support']} | {m['correct']} | {percent(m['precision'])} | {percent(m['recall'])} |\n"
    text += f"\n소매 유무: {test['attributes']['sleeves_present']['correct']}/{test['attributes']['sleeves_present']['count']}. 명확한 기장: {test['attributes']['full_buttocks_coverage']['correct']}/{test['attributes']['full_buttocks_coverage']['count']}.\n"
    text += f"\n선택한 소매 점수 기준 {choice['thresholds']['sleeve'] * 100:g}점, 기장 점수 기준 {choice['thresholds']['length'] * 100:g}점. 점수는 보정된 정답 확률이 아닙니다. 개발 검증에서 관측한 정확도가 운영 정확도를 보장하지 않습니다.\n"
    text += "\n학습 자료 성적은 새 성능이 아닙니다. 엄격 일치는 원래 분류, 경계 허용 일치는 미리 표시된 자켓·코트 경계만 두 분류를 허용합니다. 기타는 긴 민소매·판독 보류 정책이며 보류 여부를 별도 집계합니다. 정답은 AI 보조 초안을 사용자 일괄 승인한 자료이므로 독립적인 사람 정답 검수와 구분합니다.\n\nCPU로 최종 평가 141벌을 재로딩해 GPU와 분류·보류 결정이 같은지 확인했습니다. 원본 사진·기존 모델·승인 라벨은 변경하지 않았고 앱에는 연결하지 않았습니다.\n"
    with (output / "학습결과.md").open("x", encoding="utf-8") as stream:
        stream.write(text)
    data = json.dumps(predictions, ensure_ascii=False).replace("</", "<\\/")
    summary_html = f"<p>최종 평가 141벌 · 엄격 {test['strict']['correct']}/141 · 경계 허용 {test['tolerant']['correct']}/141 · 자동 분류 {test['automatic']['count']}벌 · 확인 필요 {test['automatic']['review_count']}벌</p>"
    document = """<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>옷 분류 학습 결과</title><style>
body{margin:0;background:#f4f6f2;color:#18392e;font:15px/1.6 'Malgun Gothic',sans-serif}header,main{max-width:1400px;margin:auto;padding:24px}h1{margin:0;font-size:28px}.tools{position:sticky;top:0;background:#e5eee5;padding:12px 24px;display:flex;gap:14px;flex-wrap:wrap}select,input{padding:8px;border:1px solid #b1c4b9;border-radius:6px}#gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:18px}article{background:white;border:1px solid #cdd9cd;border-radius:10px;overflow:hidden}img{width:100%;height:410px;object-fit:contain;background:#e9eee8}section{padding:14px}.wrong{border-color:#bb7868}.badge{color:#a24f34;font-weight:bold}a{color:inherit}small{display:block}button{cursor:pointer}dialog{max-width:95vw;padding:0;border:0}dialog img{width:auto;height:86vh;max-width:93vw;object-fit:contain}dialog::backdrop{background:#001d18d9}</style>
<header><h1>옷 분류 학습 결과</h1>SUMMARY<p>전체 정면 사진 · 소매/기장 별도 학습 · 경계 허용은 기존 표시 사례만 적용</p><small>AI 보조 라벨을 일괄 승인한 자료 기준의 실험 결과입니다. 학습 자료의 일치율은 새 성능에 합산하지 않습니다.</small></header>
<div class="tools"><label>자료 <select id="partition"><option value="test">최종 평가 141벌</option><option value="dev">개발 검증 134벌</option><option value="train">학습 567벌</option><option value="all">전체 842벌</option></select></label><label>정답 분류 <select id="category"><option value="all">전체</option><option>자켓</option><option>코트</option><option>조끼</option><option>기타</option></select></label><label>보기 <select id="status"><option value="all">전체</option><option value="wrong">엄격 불일치</option><option value="tolerant_wrong">경계 허용 후 불일치</option><option value="review">확인 필요</option><option value="auto">자동 분류</option></select></label><label>번호 <input id="query" placeholder="옷 번호"></label></div><main><p id="count"></p><div id="gallery"></div></main><dialog id="zoom"><img alt="정면 확대"><button id="close">닫기</button></dialog><script>
const rows=DATA;const $=id=>document.getElementById(id);function make(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}function render(){const shown=rows.filter(r=>($('partition').value==='all'||r.partition===$('partition').value)&&($('category').value==='all'||r.truth===$('category').value)&&r.code.includes($('query').value)&&($('status').value==='all'||($('status').value==='wrong'&&!r.correct_strict)||($('status').value==='tolerant_wrong'&&!r.correct_tolerant)||($('status').value==='review'&&r.needs_review)||($('status').value==='auto'&&!r.needs_review)));$('count').textContent=shown.length+'벌';$('gallery').replaceChildren();for(const r of shown){const card=make('article');if(!r.correct_tolerant)card.className='wrong';const image=make('img');image.src='photos/'+r.code+'.jpg';image.alt=r.code+' 정면';image.loading='lazy';image.onclick=()=>{$('zoom').querySelector('img').src=image.src;$('zoom').showModal();};card.append(image);const body=make('section');body.append(make('b',r.code+' · 정답 '+r.truth),make('p','판단 '+r.raw_category+' · 표시 '+r.display_category));const badge=make('small',r.needs_review?'확인 필요':'자동 분류');badge.className='badge';body.append(badge,make('small','소매 '+(100*r.sleeve_score).toFixed(1)+'점 · 기장 '+(100*r.length_score).toFixed(1)+'점'));if(r.acceptable_categories.length>1)body.append(make('small','기장 경계 · 자켓/코트 모두 허용'));if(r.review_reasons.length)body.append(make('small',r.review_reasons.join(' / ')));card.append(body);$('gallery').append(card);}}for(const id of ['partition','category','status','query'])$(id).addEventListener(id==='query'?'input':'change',render);$('close').onclick=()=>$('zoom').close();render();</script></html>"""
    document = document.replace("SUMMARY", summary_html).replace("const rows=DATA;", "const rows=" + data + ";")
    with (output / "결과보기.html").open("x", encoding="utf-8") as stream:
        stream.write(document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    prep = sub.add_parser("prepare")
    for name in ("dataset", "cached", "weights", "work"):
        prep.add_argument("--" + name, type=Path, required=True)
    training = sub.add_parser("train")
    training.add_argument("--work", type=Path, required=True)
    training.add_argument("--side", type=int, choices=(320, 448), required=True)
    selection = sub.add_parser("select")
    selection.add_argument("--work", type=Path, required=True)
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("--work", type=Path, required=True)
    evaluation.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    {"prepare": prepare, "train": train, "select": select, "evaluate": evaluate}[args.stage](args)


if __name__ == "__main__":
    main()
