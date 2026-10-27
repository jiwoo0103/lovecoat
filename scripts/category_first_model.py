"""Prepare and run an isolated first category experiment from human-confirmed labels.

No application integration, label promotion, or network access occurs in this tool.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import time

import numpy as np
from PIL import ImageDraw

from coatdesk_ab.data import letterbox, load_photo

CLASSES = ["자켓", "코트", "조끼"]
SEED = 20261027
SIDE = 320


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def confirmed_records(review, baseline, source_manifest, quality_exclusions):
    """Trust only matching, explicit human confirmations; keep every omission visible."""
    if review.get("schema_version") != "category-review-v1" or review.get("dataset_id") != baseline["dataset_id"]:
        raise ValueError("Review belongs to a different dataset")
    originals = {r["code"]: r for r in baseline["records"]}
    sources = {r["code"]: r for r in source_manifest["rows"]}
    rows = review["records"]
    codes = [r["code"] for r in rows]
    if len(set(codes)) != len(codes) or set(codes) != set(originals):
        raise ValueError("Review has missing, unknown or duplicate codes")
    if not set(quality_exclusions) <= set(originals):
        raise ValueError("Unknown quality exclusion")
    records, hashes = [], set()
    for row in sorted(rows, key=lambda r: int(r["code"])):
        code = row["code"]
        original, source = originals[code], sources[code]
        for key in ("source_sha256", "source_group", "age_group", "folder_id"):
            if row.get(key) != original[key]:
                raise ValueError(f"Immutable review metadata changed: {code}/{key}")
        if source["sha256"] != original["source_sha256"] or source["split_group"] != original["source_group"]:
            raise ValueError(f"Source metadata changed: {code}")
        if source["sha256"] in hashes:
            raise ValueError("Duplicate source photo must be reconciled before splitting")
        hashes.add(source["sha256"])
        status, label = row["review_status"], row.get("training_label")
        if status not in ("confirmed", "unreviewed"):
            raise ValueError("Invalid review status")
        if status == "unreviewed" and label is not None:
            raise ValueError("An unconfirmed opinion cannot become a training label")
        if status == "confirmed" and (label not in CLASSES or label != row["display_category"]):
            raise ValueError("Unsupported or contradictory confirmed label")
        edit = review.get("edits", {}).get(code)
        expected_category = edit["category"] if edit else original["display_category"]
        expected_confirmed = edit["confirmed"] if edit else original["review_status"] == "confirmed"
        if row["display_category"] != expected_category or (status == "confirmed") != expected_confirmed:
            raise ValueError("Export records and browser edits disagree")
        omission = quality_exclusions.get(code) or ("user_unconfirmed" if status != "confirmed" else "")
        records.append({
            "code": code, "index": len(records), "source": source["source"],
            "source_sha256": source["sha256"], "source_group": source["split_group"],
            "age_group": original["age_group"], "human_label": label,
            "label_index": CLASSES.index(label) if label in CLASSES else None,
            "eligible": not omission, "exclusion_reason": omission,
            "review_status": status, "previous_task_role": original["previous_task_role"],
        })
    if set(r["human_label"] for r in records if r["eligible"]) != set(CLASSES):
        raise ValueError("Each of the three classes requires confirmed examples")
    return records


def assign_folds(records, folds=3, seed=SEED):
    """Balance classes and age as far as possible without splitting garment groups."""
    eligible = [r for r in records if r["eligible"]]
    groups = defaultdict(list)
    for row in eligible:
        groups[row["source_group"]].append(row)
    rng, by_class = random.Random(seed), defaultdict(list)
    for group, rows in groups.items():
        if len({r["human_label"] for r in rows}) != 1:
            raise ValueError("A garment group has conflicting human labels")
        by_class[rows[0]["human_label"]].append((group, rows))
    counts, age_counts, totals, assignments = [Counter() for _ in range(folds)], [Counter() for _ in range(folds)], [0] * folds, {}
    for category in sorted(CLASSES, key=lambda c: len(by_class[c])):
        grouped = by_class[category]
        if len(grouped) < folds:
            raise ValueError("Not enough independent groups for every validation fold")
        rng.shuffle(grouped)
        grouped.sort(key=lambda g: -len(g[1]))
        for group, rows in grouped:
            order = list(range(folds))
            rng.shuffle(order)
            age = rows[0]["age_group"]
            fold = min(order, key=lambda f: (counts[f][category], age_counts[f][(category, age)], totals[f]))
            assignments[group] = fold
            counts[fold][category] += len(rows)
            age_counts[fold][(category, age)] += len(rows)
            totals[fold] += len(rows)
    return {r["code"]: assignments[r["source_group"]] for r in eligible}


def mask_panel(image, box):
    """Mask the existing placard box, without cropping any garment hem or sleeve."""
    x1, y1, x2, y2 = [int(v) for v in box]
    if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
        raise ValueError("Invalid cached panel box")
    masked = image.copy()
    ImageDraw.Draw(masked).rectangle((max(0, x1 - 8), max(0, y1 - 8),
                                     min(image.width - 1, x2 + 8), min(image.height - 1, y2 + 8)),
                                    fill=(255, 255, 255))
    return masked


def prepare(args):
    if args.work.exists():
        raise FileExistsError("Choose a new experiment folder")
    paths = {key: getattr(args, key).resolve(strict=True)
             for key in ("review", "baseline", "manifest", "prepared", "exclusions", "weights")}
    inputs = {str(p): sha(p) for p in paths.values()}
    records = confirmed_records(read(paths["review"]), read(paths["baseline"]),
                                read(paths["manifest"]), read(paths["exclusions"]))
    assignments = assign_folds(records)
    locations = {r["code"]: r for r in read(paths["prepared"])["records"]}
    images = []
    for row in records:
        if sha(row["source"]) != row["source_sha256"]:
            raise ValueError("Original source photo changed")
        cached = locations[row["code"]]
        if cached["sha256"] != row["source_sha256"] or not cached["location"]["valid"]:
            raise ValueError("Missing or mismatched panel location")
        row["panel_box_1080"] = cached["location"]["box"]
        row["fold"] = assignments.get(row["code"])
        image, _ = letterbox(mask_panel(load_photo(row["source"]), row["panel_box_1080"]), SIDE)
        images.append(np.asarray(image).transpose(2, 0, 1))
    args.work.mkdir(parents=True)
    np.save(args.work / "images.npy", np.stack(images), allow_pickle=False)
    shutil.copyfile(paths["review"], args.work / "human-review-source.json")
    protocol = {
        "architecture": "efficientnet_b0", "classes": CLASSES, "seed": SEED,
        "folds": 3, "epochs": 20, "selection": "fixed_epochs_no_oof_checkpoint_selection",
        "batch": 12, "input_side": SIDE, "backbone_lr": .0001, "head_lr": .001,
        "optimizer": "AdamW", "weight_decay": .0001, "label_smoothing": .02,
        "class_weights": "sqrt_inverse_frequency_fit_partition_only",
        "augmentation": "full_image_shrink_pad_translate_0.85_to_1.0_brightness_0.9_to_1.1_horizontal_flip",
        "normalization_mean": [.485, .456, .406], "normalization_std": [.229, .224, .225],
        "panel_mask": "cached_existing_detector_box_plus_8px_white_mask_in_1080_wide_photo",
        "crop": "none_letterbox_preserves_aspect_ratio_and_all_hem_pixels",
        "preliminary_review_threshold": .95, "scores_calibrated": False,
        "independent_new_capture_test": False, "app_integration": False,
        "criterion": "맞는 체형에서 엉덩이를 완전히 덮는 기장부터 코트. 불명확하면 기타·확인 필요.",
        "weights": str(paths["weights"]), "weights_sha256": sha(paths["weights"]),
        "script_sha256": sha(__file__), "input_files": inputs,
        "images_sha256": sha(args.work / "images.npy"),
        "records": records, "eligible_count": sum(r["eligible"] for r in records),
        "class_counts": dict(Counter(r["human_label"] for r in records if r["eligible"])),
        "omissions": [{"code": r["code"], "reason": r["exclusion_reason"]} for r in records if not r["eligible"]],
    }
    for path, digest in inputs.items():
        if sha(path) != digest:
            raise ValueError("Input metadata changed during preparation")
    save(args.work / "protocol.json", protocol)
    print(json.dumps({k: protocol[k] for k in ("eligible_count", "class_counts", "omissions")}, ensure_ascii=True), flush=True)


def metrics(predictions):
    matrix = [[0 for _ in CLASSES] for _ in CLASSES]
    for p in predictions:
        matrix[CLASSES.index(p["truth"])][CLASSES.index(p["candidate"]) ] += 1
    per_class = {}
    for i, category in enumerate(CLASSES):
        support = sum(matrix[i])
        predicted = sum(row[i] for row in matrix)
        correct = matrix[i][i]
        precision = correct / predicted if predicted else 0.
        recall = correct / support if support else 0.
        per_class[category] = {"support": support, "correct": correct, "predicted": predicted,
                               "precision": precision, "recall": recall,
                               "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.}
    accepted = [p for p in predictions if p["score"] >= .95]
    return {"count": len(predictions), "correct": sum(matrix[i][i] for i in range(len(CLASSES))),
            "accuracy": sum(p["truth"] == p["candidate"] for p in predictions) / len(predictions),
            "macro_f1": sum(p["f1"] for p in per_class.values()) / len(CLASSES),
            "confusion_rows_truth_columns_prediction": matrix, "per_class": per_class,
            "threshold_95": {"accepted": len(accepted), "correct": sum(p["truth"] == p["candidate"] for p in accepted),
                             "held": len(predictions) - len(accepted)}}


def train_stage(args):
    import torch
    from torch import nn
    from torch.nn import functional as F
    from torchvision.models import efficientnet_b0

    protocol = read(args.work / "protocol.json")
    if sha(__file__) != protocol["script_sha256"] or sha(protocol["weights"]) != protocol["weights_sha256"]:
        raise ValueError("Training code or initial weights changed after protocol freeze")
    if sha(args.work / "images.npy") != protocol["images_sha256"]:
        raise ValueError("Prepared images changed")
    folder = args.work / "training" / args.stage
    if folder.exists():
        raise FileExistsError("Never overwrite a completed or interrupted training stage")
    eligible = [r for r in protocol["records"] if r["eligible"]]
    final = args.stage == "final"
    fold = None if final else int(args.stage[-1])
    fit = eligible if final else [r for r in eligible if r["fold"] != fold]
    held = [r for r in protocol["records"] if r["review_status"] == "unreviewed"] if final else [r for r in eligible if r["fold"] == fold]
    if set(r["source_group"] for r in fit) & set(r["source_group"] for r in held):
        raise ValueError("A garment group crosses fit and held partitions")
    if not torch.cuda.is_available():
        raise RuntimeError("This bounded first experiment requires the existing CUDA environment")
    seed = protocol["seed"] + (100 if final else fold)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(4)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    folder.mkdir(parents=True)
    started = time.perf_counter()
    data = torch.from_numpy(np.load(args.work / "images.npy", allow_pickle=False))
    model = efficientnet_b0(weights=None)
    model.load_state_dict(torch.load(protocol["weights"], map_location="cpu", weights_only=True))
    model.classifier[1] = nn.Linear(model.classifier[1].in_features, len(CLASSES))
    model.cuda()
    counts = Counter(r["label_index"] for r in fit)
    weights = torch.tensor([math.sqrt(len(fit) / (len(CLASSES) * counts[i])) for i in range(len(CLASSES))], device="cuda")
    weights /= weights.mean()
    criterion = nn.CrossEntropyLoss(weight=weights, label_smoothing=protocol["label_smoothing"])
    optimizer = torch.optim.AdamW([{"params": model.features.parameters(), "lr": protocol["backbone_lr"]},
                                  {"params": model.classifier.parameters(), "lr": protocol["head_lr"]}],
                                 weight_decay=protocol["weight_decay"])
    scaler = torch.amp.GradScaler("cuda")
    mean = torch.tensor(protocol["normalization_mean"], device="cuda")[None, :, None, None]
    std = torch.tensor(protocol["normalization_std"], device="cuda")[None, :, None, None]

    def batch(rows, augment=False):
        x = data[[r["index"] for r in rows]].to("cuda").float() / 255.
        if augment:
            small = round(SIDE * random.uniform(.85, 1.))
            x = F.interpolate(x, size=(small, small), mode="bilinear", align_corners=False)
            left, top = random.randint(0, SIDE - small), random.randint(0, SIDE - small)
            x = F.pad(x, (left, SIDE - small - left, top, SIDE - small - top), value=1.)
            if random.random() < .5:
                x = x.flip(-1)
            x = (x * torch.empty((len(rows), 1, 1, 1), device="cuda").uniform_(.9, 1.1)).clamp_(0, 1)
        return (x - mean) / std

    for epoch in range(1, protocol["epochs"] + 1):
        model.train()
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.eval()
        factor = .15 + .85 * (1 + math.cos(math.pi * (epoch - 1) / protocol["epochs"])) / 2
        for group, lr in zip(optimizer.param_groups, [protocol["backbone_lr"], protocol["head_lr"]], strict=True):
            group["lr"] = lr * factor
        order = fit.copy()
        random.shuffle(order)
        loss_sum, correct = 0., 0
        for start in range(0, len(order), protocol["batch"]):
            rows = order[start:start + protocol["batch"]]
            y = torch.tensor([r["label_index"] for r in rows], device="cuda")
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                logits = model(batch(rows, True))
                loss = criterion(logits.float(), y)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5.)
            scaler.step(optimizer)
            scaler.update()
            loss_sum += float(loss.detach()) * len(rows)
            correct += int((logits.argmax(1) == y).sum())
        entry = {"stage": args.stage, "epoch": epoch, "fit_count": len(fit), "fit_correct": correct,
                 "fit_loss": loss_sum / len(fit), "seconds": time.perf_counter() - started}
        with (folder / "epochs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        if epoch == 1 or epoch % 5 == 0:
            print(json.dumps(entry), flush=True)
    model.eval()
    predictions = []
    with torch.inference_mode():
        for start in range(0, len(held), protocol["batch"]):
            rows = held[start:start + protocol["batch"]]
            with torch.autocast("cuda", dtype=torch.float16):
                probs = model(batch(rows)).float().softmax(1).cpu().tolist()
            for row, probability in zip(rows, probs, strict=True):
                index = max(range(len(CLASSES)), key=lambda i: probability[i])
                score = probability[index]
                predictions.append({"code": row["code"], "truth": row["human_label"],
                                    "candidate": CLASSES[index], "score": score, "probabilities": probability,
                                    "display_category": "기타" if final or score < .95 else CLASSES[index],
                                    "needs_review": final or score < .95, "fold": fold,
                                    "age_group": row["age_group"], "source_group": row["source_group"],
                                    "human_unconfirmed": final})
    torch.cuda.synchronize()
    torch.save({"architecture": protocol["architecture"], "classes": CLASSES,
                "state_dict": {key: value.cpu() for key, value in model.state_dict().items()},
                "input_side": SIDE, "protocol_sha256": sha(args.work / "protocol.json")}, folder / "model.pt")
    save(folder / "predictions.json", predictions)
    result = {"stage": args.stage, "fit_count": len(fit), "held_count": len(held),
              "fit_codes": [r["code"] for r in fit], "held_codes": [r["code"] for r in held],
              "epochs": protocol["epochs"], "seconds": time.perf_counter() - started,
              "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
              "model_sha256": sha(folder / "model.pt"), "protocol_sha256": sha(args.work / "protocol.json")}
    if not final:
        result["metrics"] = metrics(predictions)
    save(folder / "complete.json", result)
    print(json.dumps({key: result[key] for key in ("stage", "fit_count", "held_count", "seconds")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    prep = sub.add_parser("prepare")
    for key in ("review", "baseline", "manifest", "prepared", "exclusions", "weights", "work"):
        prep.add_argument("--" + key, type=Path, required=True)
    train = sub.add_parser("train")
    train.add_argument("--work", type=Path, required=True)
    train.add_argument("--stage", choices=["fold-0", "fold-1", "fold-2", "final"], required=True)
    args = parser.parse_args()
    prepare(args) if args.phase == "prepare" else train_stage(args)


if __name__ == "__main__":
    main()
