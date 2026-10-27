"""Local full-photo experiment: separate sleeve and length supervision.

The original model and labels are never overwritten. Category-derived attribute
targets are proxies, not independently reviewed measurements of buttock coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path
import random
import shutil

import numpy as np

from coatdesk_ab.data import letterbox, load_photo
from scripts.category_first_model import mask_panel, metrics, read, save, sha

TARGETS = {"자켓": (0, 0), "코트": (0, 1), "조끼": (1, 0)}
CRITERION = "전체 사진 유지. 소매가 있는 옷은 디자인과 무관하게 기장으로만 분류: 맞는 체형에서 엉덩이를 완전히 덮으면 코트, 그보다 짧으면 자켓. 긴 민소매 또는 불확실하면 기타·확인 필요."


def attribute_targets(label, confirmed):
    if not confirmed or label not in TARGETS:
        raise ValueError("Only confirmed, supported category labels supply proxy targets")
    return TARGETS[label]


def decide(sleeve_probabilities, length_probabilities, threshold=.95):
    """Only the length output selects jacket versus coat; exceptions stay held."""
    for values in (sleeve_probabilities, length_probabilities):
        if (len(values) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                or not math.isclose(sum(values), 1, abs_tol=1e-5)):
            raise ValueError("Invalid binary probabilities")
    if not 0 < threshold <= 1:
        raise ValueError("Invalid review threshold")
    sleeve = int(sleeve_probabilities[1] > sleeve_probabilities[0])
    long = int(length_probabilities[1] > length_probabilities[0])
    candidate = "조끼" if sleeve else ("코트" if long else "자켓")
    score = min(sleeve_probabilities[sleeve], length_probabilities[long])
    reasons = []
    if sleeve and long:
        reasons.append("긴 민소매 예외")
    if sleeve_probabilities[sleeve] < threshold:
        reasons.append("소매 유무 점수 미달")
    if length_probabilities[long] < threshold:
        reasons.append("기장 점수 미달")
    return {"candidate": candidate, "score": float(score),
            "display_category": "기타" if reasons else candidate, "needs_review": bool(reasons),
            "review_reasons": reasons, "sleeve_probabilities_present_absent": list(sleeve_probabilities),
            "length_probabilities_not_full_full_cover": list(length_probabilities),
            "long_sleeveless_exception": bool(sleeve and long)}


def check_partition(fit, held):
    for key in ("code", "source_group", "source_sha256"):
        if {r[key] for r in fit} & {r[key] for r in held}:
            raise ValueError(f"Training/evaluation overlap: {key}")
    for rows in (fit, held):
        for key in ("code", "source_sha256"):
            if len({r[key] for r in rows}) != len(rows):
                raise ValueError(f"Duplicate {key}")


def prepare(args):
    if args.work.exists():
        raise FileExistsError("Use a new experiment directory")
    old = read(args.old_protocol)
    if sha(args.old_images) != old["images_sha256"]:
        raise ValueError("Original prepared inputs changed")
    review = read(args.validation_baseline)
    labels = read(args.validation_labels)
    if labels["dataset_id"] != review["dataset_id"] or "qa" in labels["dataset_id"].lower():
        raise ValueError("Wrong validation source")
    if sha(labels["source_file"]) != labels["source_sha256"]:
        raise ValueError("Original human review source changed")
    truth = {r["code"]: r for r in labels["records"]}
    if len(truth) != len(labels["records"]) or set(truth) != {r["code"] for r in review["records"]}:
        raise ValueError("Missing or duplicate validation labels")
    sources = {r["code"]: r for r in read(args.manifest)["rows"]}
    locations = {r["code"]: r for r in read(args.prepared)["records"]}
    images = list(np.load(args.old_images, allow_pickle=False))
    records = []
    for original in old["records"]:
        row = dict(original)
        if sha(row["source"]) != row["source_sha256"]:
            raise ValueError("Source photo changed")
        row["partition"] = "training" if row["eligible"] else ("unconfirmed" if row["human_label"] is None else "photo_excluded")
        if row["eligible"]:
            row["attribute_targets"] = attribute_targets(row["human_label"], row["review_status"] == "confirmed")
        records.append(row)
    for base in review["records"]:
        label = truth[base["code"]]
        if label["display_category"] != label["training_label"]:
            raise ValueError("Contradictory validation label")
        target = attribute_targets(label["training_label"], label["review_status"] == "confirmed")
        source, location = sources[base["code"]], locations[base["code"]]
        if (sha(source["source"]) != source["sha256"] or source["sha256"] != base["source_sha256"]
                or location["sha256"] != source["sha256"] or not location["location"]["valid"]
                or source["split_group"] != base["source_group"] or base["age_group"] != label["age_group"]):
            raise ValueError("Validation photo/metadata changed")
        image, _ = letterbox(mask_panel(load_photo(source["source"]), location["location"]["box"]), old["input_side"])
        records.append({"code": base["code"], "index": len(images), "source": source["source"],
                        "source_sha256": source["sha256"], "source_group": source["split_group"],
                        "age_group": base["age_group"], "human_label": label["training_label"],
                        "partition": "validation", "eligible": False, "attribute_targets": target})
        images.append(np.asarray(image).transpose(2, 0, 1))
    fit = [r for r in records if r["partition"] == "training"]
    held = [r for r in records if r["partition"] != "training"]
    check_partition(fit, held)
    if len(records) != len({r["code"] for r in records}):
        raise ValueError("Repeated garment")
    inputs = {str(getattr(args, key).resolve()): sha(getattr(args, key)) for key in
              ("old_protocol", "old_images", "validation_baseline", "validation_labels", "manifest", "prepared", "weights")}
    args.work.mkdir(parents=True)
    np.save(args.work / "images.npy", np.stack(images), allow_pickle=False)
    protocol = {"architecture": "efficientnet_b0_sleeve_length_2x2", "seed": old["seed"] + 100,
                "epochs": old["epochs"], "batch": old["batch"], "input_side": old["input_side"],
                "backbone_lr": old["backbone_lr"], "head_lr": old["head_lr"], "weight_decay": old["weight_decay"],
                "label_smoothing": old["label_smoothing"], "normalization_mean": old["normalization_mean"],
                "normalization_std": old["normalization_std"], "threshold": .95, "scores_calibrated": False,
                "crop": old["crop"], "panel_mask": old["panel_mask"], "criterion": CRITERION,
                "target_provenance": "derived_from_human_category_labels_not_independent_attribute_review",
                "category_to_attributes": TARGETS, "weights": str(args.weights.resolve()),
                "script_sha256": sha(__file__), "input_files": inputs,
                "images_sha256": sha(args.work / "images.npy"), "records": records,
                "class_counts_training": dict(Counter(r["human_label"] for r in fit)),
                "selection": "one_fixed_20_epoch_run_no_validation_checkpoint_or_threshold_selection",
                "validation_role": "known_development_regression_set_not_new_blind_test",
                "strict_length_feature_exclusivity_proven": False, "app_integration": False}
    save(args.work / "protocol.json", protocol)
    print({"records": len(records), "training": len(fit), "validation": sum(r["partition"] == "validation" for r in held)}, flush=True)


def build_model(weights=None):
    from torch import nn
    import torch
    from torchvision.models import efficientnet_b0

    model = efficientnet_b0(weights=None)
    if weights is not None:
        model.load_state_dict(torch.load(weights, weights_only=True, map_location="cpu"))
    model.classifier[1] = nn.Linear(model.classifier[1].in_features, 4)
    return model


def train_and_evaluate(args):
    import torch
    from torch import nn
    from torch.nn import functional as F

    p = read(args.work / "protocol.json")
    if sha(__file__) != p["script_sha256"] or sha(args.work / "images.npy") != p["images_sha256"]:
        raise ValueError("Frozen code or inputs changed")
    for path, digest in p["input_files"].items():
        if sha(path) != digest:
            raise ValueError(f"Frozen input changed: {path}")
    if args.output.exists() or (args.work / "training").exists():
        raise FileExistsError("Never overwrite an experiment")
    if not torch.cuda.is_available():
        raise RuntimeError("Use the existing local CUDA environment")
    torch.set_num_threads(4)
    random.seed(p["seed"])
    np.random.seed(p["seed"])
    torch.manual_seed(p["seed"])
    torch.cuda.manual_seed_all(p["seed"])
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    fit = [r for r in p["records"] if r["partition"] == "training"]
    check_partition(fit, [r for r in p["records"] if r["partition"] != "training"])
    run = args.work / "training"
    run.mkdir()
    data = torch.from_numpy(np.load(args.work / "images.npy", allow_pickle=False))
    model = build_model(p["weights"]).cuda()
    mean = torch.tensor(p["normalization_mean"], device="cuda")[None, :, None, None]
    std = torch.tensor(p["normalization_std"], device="cuda")[None, :, None, None]
    criteria = []
    for task in range(2):
        counts = Counter(r["attribute_targets"][task] for r in fit)
        if set(counts) != {0, 1}:
            raise ValueError("Both binary targets require confirmed training examples")
        weight = torch.tensor([math.sqrt(len(fit) / (2 * counts[i])) for i in range(2)], device="cuda")
        criteria.append(nn.CrossEntropyLoss(weight=weight / weight.mean(), label_smoothing=p["label_smoothing"]))
    optimizer = torch.optim.AdamW([{"params": model.features.parameters(), "lr": p["backbone_lr"]},
                                  {"params": model.classifier.parameters(), "lr": p["head_lr"]}], weight_decay=p["weight_decay"])
    scaler = torch.amp.GradScaler("cuda")

    def batch(rows, augment=False):
        x = data[[r["index"] for r in rows]].to("cuda").float() / 255
        if augment:
            side = p["input_side"]
            small = round(side * random.uniform(.85, 1))
            x = F.interpolate(x, size=(small, small), mode="bilinear", align_corners=False)
            left, top = random.randint(0, side - small), random.randint(0, side - small)
            x = F.pad(x, (left, side - small - left, top, side - small - top), value=1)
            if random.random() < .5:
                x = x.flip(-1)
            x = (x * torch.empty((len(rows), 1, 1, 1), device="cuda").uniform_(.9, 1.1)).clamp(0, 1)
        return (x - mean) / std

    epochs = []
    for epoch in range(1, p["epochs"] + 1):
        model.train()
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.eval()
        factor = .15 + .85 * (1 + math.cos(math.pi * (epoch - 1) / p["epochs"])) / 2
        for group, lr in zip(optimizer.param_groups, [p["backbone_lr"], p["head_lr"]], strict=True):
            group["lr"] = lr * factor
        order = fit.copy()
        random.shuffle(order)
        total = 0.
        for start in range(0, len(order), p["batch"]):
            rows = order[start:start + p["batch"]]
            target = torch.tensor([r["attribute_targets"] for r in rows], device="cuda")
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                logits = model(batch(rows, True)).float()
                loss = (criteria[0](logits[:, :2], target[:, 0]) + criteria[1](logits[:, 2:], target[:, 1])) / 2
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5)
            scaler.step(optimizer)
            scaler.update()
            total += float(loss.detach()) * len(rows)
        epochs.append({"epoch": epoch, "loss": total / len(fit)})
        if epoch == 1 or epoch % 5 == 0:
            print(epochs[-1], flush=True)
    checkpoint = {"architecture": p["architecture"], "state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
                  "input_side": p["input_side"], "protocol_sha256": sha(args.work / "protocol.json"),
                  "sleeve_classes": ["present", "absent"], "length_classes": ["not_full_cover", "full_cover"]}
    torch.save(checkpoint, run / "model.pt")
    # Evaluation uses the serialized model reloaded on CPU, never in-memory training weights.
    model = build_model()
    model.load_state_dict(torch.load(run / "model.pt", weights_only=True, map_location="cpu")["state_dict"], strict=True)
    model.eval()
    predictions = []
    cpu_mean, cpu_std = mean.cpu(), std.cpu()
    with torch.inference_mode():
        for start in range(0, len(p["records"]), 10):
            rows = p["records"][start:start + 10]
            x = data[[r["index"] for r in rows]].float() / 255
            logits = model((x - cpu_mean) / cpu_std)
            sleeves = logits[:, :2].softmax(1).tolist()
            lengths = logits[:, 2:].softmax(1).tolist()
            for row, sleeve, length in zip(rows, sleeves, lengths, strict=True):
                result = decide(sleeve, length, p["threshold"])
                if row["partition"] in ("unconfirmed", "photo_excluded"):
                    result["needs_review"] = True
                    result["display_category"] = "기타"
                    result["review_reasons"].append(row.get("exclusion_reason", "사용자 미확정"))
                predictions.append({**result, "code": row["code"], "partition": row["partition"],
                                    "truth": row["human_label"], "age_group": row["age_group"],
                                    "correct": None if row["human_label"] is None else result["candidate"] == row["human_label"]})
    summary = {"partitions": {}, "scores_calibrated": False, "independent_blind_test": False,
               "target_provenance": p["target_provenance"], "app_integration": False}
    for partition in ("training", "validation", "photo_excluded"):
        rows = [r for r in predictions if r["partition"] == partition]
        if rows:
            m = metrics(rows)
            accepted = [r for r in rows if not r["needs_review"]]
            m.pop("threshold_95")
            m["attribute_gate_95"] = {"accepted": len(accepted), "correct": sum(r["correct"] for r in accepted),
                                      "held": len(rows) - len(accepted)}
            summary["partitions"][partition] = m
    for path, digest in p["input_files"].items():
        if sha(path) != digest:
            raise ValueError("Protected input changed")
    for row in p["records"]:
        if sha(row["source"]) != row["source_sha256"]:
            raise ValueError("Protected photo changed")
    args.output.mkdir(parents=True)
    shutil.copyfile(run / "model.pt", args.output / "sleeve-length-efficientnet-b0.pt")
    shutil.copyfile(args.work / "protocol.json", args.output / "protocol.json")
    shutil.copyfile(__file__, args.output / "training-source.py")
    save(args.output / "predictions.json", predictions)
    save(args.output / "metrics.json", summary)
    save(args.output / "epochs.json", epochs)
    save(args.output / "integrity.json", {"protected_inputs": p["input_files"], "photos_verified": len(p["records"]),
                                         "checkpoint_sha256": sha(args.output / "sleeve-length-efficientnet-b0.pt"),
                                         "cpu_reload_evaluation": True})
    print(summary, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    prep = sub.add_parser("prepare")
    for name in ("old-protocol", "old-images", "validation-labels", "validation-baseline", "manifest", "prepared", "weights", "work"):
        prep.add_argument("--" + name, type=Path, required=True)
    train = sub.add_parser("train")
    train.add_argument("--work", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args) if args.stage == "prepare" else train_and_evaluate(args)


if __name__ == "__main__":
    main()
