"""Isolated 4120-reference rebuild: generic ImageNet initialization, never old fits.

Prepare -> train (dev only) -> compare (locked test); no application integration.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from coatdesk_ab.data import letterbox, load_photo

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".local/category-rebuild-training-20261104-01"
OUTPUT = ROOT / "model-results/20261104-category-rebuild-1000-01"
PACK = ROOT / "model-results/category-rebuild-1000-4120-20261103-01"
LABELS = ROOT / "model-results/category-rebuild-label-audit-20261103-01/labels-approved-round2.json"
LABEL_SHA = "d6a5673956ce23f37f8a0f7fbed3b195a3540eecf4c5605a9dddb105e492e1be"
GENERIC = ROOT / ".local/category-first-20261027-01/assets/efficientnet_b0_rwightman-7f5810bc.pth"
GENERIC_SHA = "7f5810bc96def8f7552d5b7e68d53c4786f81167d28291b21c0d90e1fca14934"
OLD_WORK = ROOT / ".local/robust-four-models-20261102"
OLD_MODELS = {
    "existing_program": ROOT / "resources/ai/category/model.pt",
    "existing_augmented": ROOT / "model-results/20261102-robust-four-models/category.pt",
}
KINDS = ("original", "dark", "bright", "warm", "cool", "contrast", "shadow_left", "shadow_right", "blur")
LONG_VESTS = frozenset(("2483", "3115", "3120", "2548", "3351", "3396", "2378", "2588", "2496", "3394"))
SEED, SIDE, EPOCHS, BATCH = 20261104, 320, 20, 16
MEAN, STD = [.485, .456, .406], [.229, .224, .225]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def event(stage, **values):
    item = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage, **values}
    save(WORK / "status.json", item)
    with (WORK / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(json.dumps(item, ensure_ascii=False), flush=True)


def change(photo, kind, strength=1.):
    """Exact photometric recipe of the 20261102 second model, at original scale."""
    if kind == "original":
        return photo.copy()
    if kind in ("dark", "bright"):
        return ImageEnhance.Brightness(photo).enhance(1 + (-.2 if kind == "dark" else .2) * strength)
    if kind == "contrast":
        return ImageEnhance.Contrast(photo).enhance(1 - .15 * strength)
    if kind == "blur":
        return photo.filter(ImageFilter.GaussianBlur(.65 * strength * photo.width / 1080))
    a = np.asarray(photo, dtype=np.float32)
    if kind in ("warm", "cool"):
        goal = np.array([1.04, 1., .94] if kind == "warm" else [.94, 1., 1.04])
        a *= 1 + (goal - 1) * strength
    elif kind in ("shadow_left", "shadow_right"):
        x = np.linspace(0, 1, photo.width)
        if kind == "shadow_right":
            x = 1 - x
        a *= (1 - .25 * strength * (1 + np.cos(x * math.pi)) / 2)[None, :, None]
    else:
        raise ValueError(kind)
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))


def category_input(photo, box):
    image = photo.copy()
    x1, y1, x2, y2 = map(int, box)
    ImageDraw.Draw(image).rectangle(
        (max(0, x1 - 8), max(0, y1 - 8), min(image.width - 1, x2 + 8), min(image.height - 1, y2 + 8)), fill="white"
    )
    return letterbox(image, SIDE)[0]


def freeze_rows(manifest, labels, old_rows):
    """Only approved current labels; old labels are used for exposure accounting."""
    if manifest["dataset_id"] != labels["dataset_id"] or labels["criterion"] != manifest["criterion"]:
        raise ValueError("Dataset/reference identity mismatch")
    originals = {r["code"]: r for r in manifest["records"]}
    if len(originals) != len(manifest["records"]):
        raise ValueError("Duplicate source code")
    if {r["code"] for r in labels["records"]} != set(originals) or len(labels["records"]) != len(originals):
        raise ValueError("Missing or duplicate labels")
    old = {r["code"]: r for r in old_rows}
    records = []
    for label in labels["records"]:
        row = originals[label["code"]]
        for key in ("sha256", "group", "dataset_split", "ai_annotation"):
            if label[key] != row[key]:
                raise ValueError(f"Changed identity {row['code']}/{key}")
        if not label["training_eligible"] or label["requires_review"]:
            raise ValueError("Unresolved label")
        targets = deepcopy(label["attribute_targets"])
        correction = None
        if row["code"] in LONG_VESTS:
            if label["category"] != "코트" or label["boundary"] or targets["at_least_reference_length"] != 1:
                raise ValueError("Long vest agreement changed")
            targets["sleeves_present"] = 0
            correction = "user_approved_training_plan_20261104_long_sleeveless_attribute_only"
        sleeve, length = targets["sleeves_present"], targets["at_least_reference_length"]
        if sleeve not in (0, 1) or length not in (0, 1, None):
            raise ValueError("Invalid attribute targets")
        if bool(label["boundary"]) != (length is None):
            raise ValueError("Boundary must mask the length target")
        target_category = "기타" if sleeve == 0 and length == 1 else label["category"]
        acceptable = ["기타"] if target_category == "기타" else list(label["acceptable_categories"])
        prior = old.get(row["code"])
        if prior and prior["sha256"] != row["sha256"]:
            raise ValueError("Old cache refers to another photo")
        records.append({
            "code": row["code"], "source": row["source"], "sha256": row["sha256"],
            "group": row["group"], "body_sha256": row["body_sha256"],
            "dataset_split": row["dataset_split"], "gender": row.get("gender"), "shape": row.get("shape"),
            "raw_size": row.get("raw_size"), "user_category": label["category"], "boundary": label["boundary"],
            "label_source": label["label_source"], "confirmed_by": label["confirmed_by"],
            "attribute_targets": targets, "attribute_correction": correction,
            "target": [1 - sleeve, -1 if length is None else length],
            "evaluation_category": target_category, "acceptable_categories": acceptable,
            "old_augmented_split": prior["split"] if prior else None,
            "old_augmented_fit": bool(prior and prior["split"] == "train" and prior["category_eligible"]),
            "old_program_prior_fit": row.get("previous_fit", False),
        })
    for key in ("sha256", "group", "body_sha256"):
        if len({r[key] for r in records}) != len(records):
            raise ValueError("Duplicate identity/group across partitions")
    if any(r["dataset_split"] not in ("train", "dev", "test") for r in records):
        raise ValueError("Invalid dataset_split")
    return records


def implementation():
    paths = [Path(__file__), ROOT / "coatdesk_ab/data.py", ROOT / "coatdesk_ab/locator.py"]
    paths += list((ROOT / "resources/ai/category/references").glob("*"))
    return {str(p.relative_to(ROOT)): sha(p) for p in paths if p.is_file()}


def check_protocol():
    protocol = read(WORK / "protocol.json")
    if protocol["implementation"] != implementation():
        raise ValueError("Frozen implementation changed")
    for name, digest in protocol["protected_inputs"].items():
        if sha(name) != digest:
            raise ValueError("Protected source changed: " + name)
    if sha(WORK / "dataset.json") != protocol["dataset_sha256"]:
        raise ValueError("Frozen dataset changed")
    return protocol


def prepare():
    from coatdesk_ab.locator import WholePanelLocator
    if (WORK / "protocol.json").exists():
        check_protocol()
        event("prepared_already")
        return
    WORK.mkdir(parents=True, exist_ok=True)
    if sha(LABELS) != LABEL_SHA or sha(GENERIC) != GENERIC_SHA:
        raise ValueError("Approved labels or generic ImageNet weights changed")
    manifest, labels, old = read(PACK / "manifest.json"), read(LABELS), read(OLD_WORK / "frozen-dataset.json")
    rows = freeze_rows(manifest, labels, old["records"])
    if Counter(r["dataset_split"] for r in rows) != {"train": 700, "dev": 150, "test": 150}:
        raise ValueError("Approved 700/150/150 split changed")
    if {r["code"] for r in rows if r["attribute_correction"]} != LONG_VESTS:
        raise ValueError("Missing approved long vest correction")
    protected = [LABELS, PACK / "manifest.json", PACK / "training-plan.json", GENERIC, *OLD_MODELS.values(),
                 OLD_WORK / "frozen-dataset.json", OLD_WORK / "category-mask-boxes.json",
                 OLD_WORK / "evaluation/mask-locations.json", ROOT / "resources/ai/category/policy.json"]
    protected_inputs = {str(p): sha(p) for p in protected}
    old_index = {r["code"]: r for r in old["records"]}
    cached = read(OLD_WORK / "evaluation/mask-locations.json")
    original = read(OLD_WORK / "category-mask-boxes.json")
    # Reuse only caches whose locator and template bytes match the previous run.
    previous_impl = {k.replace("\\", "/"): v for k, v in read(OLD_WORK / "implementation-lock.json").items()}
    if any(previous_impl[name] != sha(ROOT / name) for name in ("coatdesk_ab/locator.py", "coatdesk_ab/data.py")):
        raise ValueError("Old locator cache implementation differs")
    previous_protected = {k.replace("\\", "/"): v for k, v in read(OLD_WORK / "protected.json").items()}
    for p in (ROOT / "resources/ai/category/references").glob("*"):
        key = str(p.relative_to(ROOT)).replace("\\", "/")
        if p.is_file() and sha(p) != previous_protected[key]:
            raise ValueError("Locator reference changed")
    locator = WholePanelLocator(ROOT / "resources/ai/category")
    arrays = {split: np.lib.format.open_memmap(WORK / (split + "-images.npy"), mode="w+", dtype=np.uint8,
               shape=(sum(r["dataset_split"] == split for r in rows) * 9, 3, SIDE, SIDE)) for split in ("dev", "test")}
    counts = Counter()
    for row in rows:
        row["partition_index"] = counts[row["dataset_split"]]
        counts[row["dataset_split"]] += 1

    def one(row):
        if sha(row["source"]) != row["sha256"]:
            raise ValueError("Source photo changed: " + row["code"])
        photo = load_photo(row["source"])
        kinds = ("original",) if row["dataset_split"] == "train" else KINDS
        locations = {}
        for k, kind in enumerate(kinds):
            variant = change(photo, kind)
            key = row["code"] + ":" + kind
            known = cached.get(key) or (original.get(row["code"]) if kind == "original" else None)
            # Only known original source hashes validate old locations.
            location = known if row["code"] in old_index and known else locator.locate(variant)
            locations[kind] = location
            if row["dataset_split"] != "train":
                # Invalid locator must force review, never disappear from evaluation.
                image = category_input(variant, location["box"]) if location["valid"] else letterbox(variant, SIDE)[0]
                arrays[row["dataset_split"]][row["partition_index"] * 9 + k] = np.asarray(image).transpose(2, 0, 1)
        if not locations["original"]["valid"]:
            raise ValueError("Invalid original training/evaluation locator: " + row["code"])
        return row["code"], locations

    locations = {}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (code, found) in enumerate(pool.map(one, rows)):
            locations[code] = found
            if (i + 1) % 25 == 0:
                event("preparing_inputs", completed=i + 1, total=len(rows), seconds=round(time.perf_counter() - started))
    for a in arrays.values():
        a.flush()
    for row in rows:
        row["locations"] = locations[row["code"]]
    save(WORK / "dataset.json", {"records": rows, "dataset_id": manifest["dataset_id"], "criterion": labels["criterion"]})
    summary = {split: {
        "count": len(part := [r for r in rows if r["dataset_split"] == split]),
        "user_categories": dict(Counter(r["user_category"] for r in part)),
        "evaluation_categories": dict(Counter(r["evaluation_category"] for r in part)),
        "boundaries": sum(r["boundary"] for r in part),
        "user_reviewed": sum(r["confirmed_by"] is not None for r in part),
        "old_augmented_gradient_fit": sum(r["old_augmented_fit"] for r in part),
        "old_program_prior_fit": sum(r["old_program_prior_fit"] for r in part),
        "old_augmented_partitions": dict(Counter(str(r["old_augmented_split"]) for r in part)),
    } for split in ("train", "dev", "test")}
    protocol = {
        "dataset_sha256": sha(WORK / "dataset.json"), "approved_label_sha256": LABEL_SHA,
        "initialization": {"source": str(GENERIC), "sha256": GENERIC_SHA, "name": "ImageNet1K_V1",
                           "old_garment_checkpoint_loaded_for_training": False, "new_head_outputs": 4},
        "architecture": "efficientnet_b0_sleeve_length_2x2", "input_side": SIDE,
        "output_order": ["sleeved", "sleeveless", "below_reference", "at_least_reference"],
        "normalization_mean": MEAN, "normalization_std": STD,
        "preprocessing": "whole-photo; runtime-locator panel white mask with 8px margin at1080; aspect-preserving letterbox320",
        "augmentation": {"source_recipe": "scripts/retrain_four_training.py:change", "source_sha256": sha(ROOT / "scripts/retrain_four_training.py"),
                         "original_probability": .25, "one_transform_probability": .75, "strength": [0., 1.], "kinds": list(KINDS[1:]),
                         "geometric_transforms": False, "training_mask": "frozen original runtime mask after photometric transform",
                         "evaluation_mask": "runtime locator rerun per fixed full-strength condition"},
        "seed": SEED, "epochs": EPOCHS, "batch_size": BATCH,
        "optimizer": "AdamW", "backbone_lr": .0001, "head_lr": .001, "weight_decay": .0001,
        "scheduler": "CosineAnnealingLR", "label_smoothing": .02,
        "class_weights": "fit-only sqrt inverse frequency, normalized per head", "batch_norm": "frozen running statistics",
        "selection": "dev 9-condition mean of masked sleeve/length balanced accuracy; then dev tolerant category accuracy; test never used",
        "thresholds": {"sleeve": .9, "length": .9, "tuned_on_test": False, "scores_calibrated": False},
        "long_vests": {"codes": sorted(LONG_VESTS), "user_category_preserved": "코트", "sleeves_present": 0,
                       "at_least_reference_length": 1, "evaluation_category": "기타", "mandatory_review": True},
        "summary": summary, "protected_inputs": protected_inputs, "implementation": implementation(),
        "cache_sha256": {s: sha(WORK / (s + "-images.npy")) for s in ("dev", "test")},
        "app_integration": False,
    }
    for name, digest in protected_inputs.items():
        if sha(name) != digest:
            raise ValueError("Input changed during freeze")
    save(WORK / "protocol.json", protocol)
    event("inputs_frozen", summary=summary,
          invalid_conditions=sum(not location["valid"] for found in locations.values() for location in found.values()))


def configure():
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(4)
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    random.seed(SEED)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def build_fresh():
    import torch
    from torchvision.models import efficientnet_b0
    if sha(GENERIC) != GENERIC_SHA:
        raise ValueError("Only the frozen generic ImageNet asset may initialize this model")
    model = efficientnet_b0(weights=None)
    model.load_state_dict(torch.load(GENERIC, map_location="cpu", weights_only=True))
    model.classifier[1] = torch.nn.Linear(model.classifier[1].in_features, 4)
    return model


def normalize(images, device):
    import torch
    images = images.to(device, non_blocking=True).float().div_(255.)
    return (images - torch.tensor(MEAN, device=device)[None, :, None, None]) / torch.tensor(STD, device=device)[None, :, None, None]


class TrainingPhotos:
    def __init__(self, rows, epoch):
        self.rows, self.epoch = rows, epoch

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        import torch
        row = self.rows[index]
        rng = random.Random(SEED + self.epoch * 1000003 + int(row["code"]))
        kind, strength = ("original", 1.) if rng.random() < .25 else (rng.choice(KINDS[1:]), rng.random())
        image = category_input(change(load_photo(row["source"]), kind, strength), row["locations"]["original"]["box"])
        return torch.from_numpy(np.asarray(image).transpose(2, 0, 1).copy()), torch.tensor(row["target"])


def attribute_loss(logits, targets, criteria):
    terms = []
    for head in range(2):
        keep = targets[:, head] >= 0
        if keep.any():
            terms.append(criteria[head](logits[keep, head * 2:head * 2 + 2], targets[keep, head]))
    return sum(terms) / len(terms) if terms else logits.sum() * 0


def prediction(row, kind, probabilities):
    sleeve, length = int(probabilities[1] > probabilities[0]), int(probabilities[3] > probabilities[2])
    long_vest = bool(sleeve and length)
    raw = "기타" if long_vest else "조끼" if sleeve else "코트" if length else "자켓"
    sleeve_score, length_score = max(probabilities[:2]), max(probabilities[2:])
    valid = row["locations"][kind]["valid"]
    return {"code": row["code"], "kind": kind, "raw_category": raw, "user_category": row["user_category"],
            "evaluation_category": row["evaluation_category"], "acceptable_categories": row["acceptable_categories"],
            "target": row["target"], "predicted": [sleeve, length], "probabilities": probabilities,
            "needs_review": not valid or long_vest or sleeve_score < .9 or length_score < .9,
            "long_vest_predicted": long_vest, "locator_valid": valid,
            "confirmed_by": row["confirmed_by"], "boundary": row["boundary"],
            "correct": valid and raw in row["acceptable_categories"],
            "strict_correct": valid and raw == row["evaluation_category"],
            "user_strict_correct": valid and raw == row["user_category"]}


def summarize(predictions):
    def score(items, field="correct"):
        correct = sum(r[field] for r in items)
        return {"count": len(items), "correct": correct, "accuracy": correct / len(items) if items else None}
    result = {"tolerant": score(predictions), "strict_runtime": score(predictions, "strict_correct"),
              "strict_user_category": score(predictions, "user_strict_correct")}
    attrs = {}
    for h, name in enumerate(("sleeve", "length")):
        known = [r for r in predictions if r["target"][h] >= 0]
        recalls = []
        matrix = [[0, 0], [0, 0]]
        for r in known:
            matrix[r["target"][h]][r["predicted"][h]] += 1
        for value in (0, 1):
            part = [r for r in known if r["target"][h] == value]
            if part:
                recalls.append(sum(r["predicted"][h] == value and r["locator_valid"] for r in part) / len(part))
        attrs[name] = {"count": len(known), "correct": sum(r["predicted"][h] == r["target"][h] and r["locator_valid"] for r in known),
                       "balanced_accuracy": sum(recalls) / len(recalls) if recalls else None, "confusion": matrix}
        attrs[name]["accuracy"] = attrs[name]["correct"] / len(known) if known else None
    result["attributes"] = attrs
    accepted = [r for r in predictions if not r["needs_review"]]
    result["automatic"] = {**score(accepted), "coverage": len(accepted) / len(predictions) if predictions else 0.}
    result["review_needed"] = len(predictions) - len(accepted)
    result["per_condition"] = {kind: score([r for r in predictions if r["kind"] == kind]) for kind in KINDS}
    grouped = defaultdict(list)
    for r in predictions:
        grouped[r["code"]].append(r)
    result["all_nine_correct"] = {"count": len(grouped), "correct": sum(len(rs) == 9 and all(r["correct"] for r in rs) for rs in grouped.values())}
    result["all_nine_same_prediction"] = sum(len({r["raw_category"] for r in rs}) == 1 for rs in grouped.values())
    long = [r for r in predictions if r["evaluation_category"] == "기타"]
    result["long_vest"] = {"observations": len(long), "detected": sum(r["long_vest_predicted"] for r in long),
                            "sent_to_review": sum(r["needs_review"] for r in long),
                            "unsafe_automatic": sum(not r["needs_review"] for r in long)}
    result["category_confusion"] = {c: dict(Counter(r["raw_category"] for r in predictions if r["evaluation_category"] == c))
                                    for c in ("자켓", "코트", "조끼", "기타")}
    result["user_reviewed"] = score([r for r in predictions if r["confirmed_by"] is not None])
    result["ai_draft"] = score([r for r in predictions if r["confirmed_by"] is None])
    result["boundary"] = score([r for r in predictions if r["boundary"]])
    result["clear_normal_categories"] = score([r for r in predictions if not r["boundary"] and r["evaluation_category"] != "기타"])
    return result


def infer(model, rows, split, device="cuda"):
    import torch
    images = np.load(WORK / (split + "-images.npy"), mmap_mode="r")
    predictions = []
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(images), BATCH * 2):
            logits = model(normalize(torch.from_numpy(np.array(images[start:start + BATCH * 2])), device))
            values = torch.cat([logits[:, :2].softmax(1), logits[:, 2:].softmax(1)], 1).cpu().tolist()
            for i, probabilities in enumerate(values, start):
                predictions.append(prediction(rows[i // 9], KINDS[i % 9], probabilities))
    return predictions


def selection(metrics):
    return (sum(metrics["attributes"][h]["balanced_accuracy"] for h in ("sleeve", "length")) / 2,
            metrics["tolerant"]["accuracy"], metrics["per_condition"]["original"]["accuracy"])


def train():
    import torch
    import torchvision
    from coatdesk_ab.network import freeze_batch_norm
    protocol = check_protocol()
    if not torch.cuda.is_available():
        raise RuntimeError("Local CUDA GPU required for the authorized training")
    if (WORK / "training-complete.json").exists():
        event("training_already_complete")
        return
    configure()
    rows = read(WORK / "dataset.json")["records"]
    fit = [r for r in rows if r["dataset_split"] == "train"]
    dev = [r for r in rows if r["dataset_split"] == "dev"]
    model = build_fresh().to("cuda")
    initial_head = model.classifier[1].weight.detach().cpu().numpy().tobytes()
    optimizer = torch.optim.AdamW([
        {"params": model.features.parameters(), "lr": protocol["backbone_lr"]},
        {"params": model.classifier.parameters(), "lr": protocol["head_lr"]}], weight_decay=protocol["weight_decay"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, EPOCHS)
    criteria = []
    counts = []
    for h in range(2):
        counter = Counter(r["target"][h] for r in fit if r["target"][h] >= 0)
        if set(counter) != {0, 1}:
            raise ValueError("Both attribute classes need fitting examples")
        weight = torch.tensor([math.sqrt(sum(counter.values()) / (2 * counter[c])) for c in (0, 1)], device="cuda")
        criteria.append(torch.nn.CrossEntropyLoss(weight=weight / weight.mean(), label_smoothing=.02))
        counts.append(dict(counter))
    best_key, best_epoch, first_epoch, history = (-1., -1., -1.), None, 0, []
    last_path = WORK / "last.pt"
    if last_path.exists():
        saved = torch.load(last_path, map_location="cuda", weights_only=False)
        if saved["dataset_sha256"] != protocol["dataset_sha256"] or saved["implementation"] != protocol["implementation"]:
            raise ValueError("Resume is allowed only within this identical new run")
        model.load_state_dict(saved["state_dict"])
        optimizer.load_state_dict(saved["optimizer"])
        scheduler.load_state_dict(saved["scheduler"])
        first_epoch, best_epoch, best_key, history = saved["epoch"] + 1, saved["best_epoch"], tuple(saved["best_key"]), saved["history"]
        torch.set_rng_state(saved["torch_rng"].cpu())
        torch.cuda.set_rng_state(saved["cuda_rng"].cpu())
        random.setstate(saved["python_rng"])
        np.random.set_state(saved["numpy_rng"])
    save(WORK / "training-configuration.json", {"torch": torch.__version__, "torchvision": torchvision.__version__,
        "device": torch.cuda.get_device_name(0), "new_head_initial_sha256": hashlib.sha256(initial_head).hexdigest(),
        "class_counts_fit_only": counts, "generic_sha256": GENERIC_SHA, "old_garment_initialization": False})
    event("training_started", first_epoch=first_epoch + 1, epochs=EPOCHS, device=torch.cuda.get_device_name(0))
    started = time.perf_counter()
    for epoch in range(first_epoch, EPOCHS):
        loader = torch.utils.data.DataLoader(TrainingPhotos(fit, epoch), batch_size=BATCH, shuffle=True,
                   num_workers=4, pin_memory=True, generator=torch.Generator().manual_seed(SEED + epoch))
        model.train()
        freeze_batch_norm(model)
        total, seen = 0., 0
        tick = time.perf_counter()
        for images, targets in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(normalize(images, "cuda"))
            loss = attribute_loss(logits, targets.to("cuda", non_blocking=True), criteria)
            if not torch.isfinite(loss):
                raise ValueError("Non-finite fitting loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
            total += loss.item() * len(images)
            seen += len(images)
        scheduler.step()
        metrics = summarize(infer(model, dev, "dev"))
        key = selection(metrics)
        item = {"epoch": epoch + 1, "loss": total / seen, "selection": list(key), "dev": metrics,
                "seconds": round(time.perf_counter() - tick, 2)}
        history.append(item)
        if key > best_key:
            best_key, best_epoch = key, epoch + 1
            torch.save({"state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
                        "architecture": protocol["architecture"], "input_side": SIDE,
                        "output_order": protocol["output_order"], "dataset_sha256": protocol["dataset_sha256"],
                        "epoch": best_epoch, "selection_key": best_key, "initialization": protocol["initialization"]}, WORK / "best.pt")
        torch.save({"state_dict": model.state_dict(), "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict(),
                    "epoch": epoch, "best_epoch": best_epoch, "best_key": best_key, "history": history,
                    "torch_rng": torch.get_rng_state(), "cuda_rng": torch.cuda.get_rng_state(),
                    "python_rng": random.getstate(), "numpy_rng": np.random.get_state(),
                    "dataset_sha256": protocol["dataset_sha256"], "implementation": protocol["implementation"]}, last_path)
        save(WORK / "history.json", history)
        event("epoch_complete", epoch=epoch + 1, loss=round(total / seen, 5), selection=list(key),
              dev_original=metrics["per_condition"]["original"], dev_all9=metrics["tolerant"],
              best_epoch=best_epoch, seconds=item["seconds"])
    check_protocol()
    save(WORK / "training-complete.json", {"best_epoch": best_epoch, "selection": best_key,
        "best_sha256": sha(WORK / "best.pt"), "dataset_sha256": protocol["dataset_sha256"],
        "thresholds": protocol["thresholds"], "test_evaluated": False, "seconds": round(time.perf_counter() - started, 2)})
    event("training_complete", best_epoch=best_epoch)


def compare():
    import shutil
    import torch
    from torchvision.models import efficientnet_b0
    protocol = check_protocol()
    complete = read(WORK / "training-complete.json")
    if sha(WORK / "best.pt") != complete["best_sha256"]:
        raise ValueError("Selected checkpoint changed before test")
    configure()
    rows = read(WORK / "dataset.json")["records"]
    test = [r for r in rows if r["dataset_split"] == "test"]
    dev = [r for r in rows if r["dataset_split"] == "dev"]
    if any(r["old_augmented_fit"] or r["old_program_prior_fit"] for r in test):
        raise ValueError("Test photos overlap a known old gradient fit")
    results, model_hashes = {}, {}
    for name, path in {**OLD_MODELS, "rebuild": WORK / "best.pt"}.items():
        event("comparing_model", model=name)
        model = efficientnet_b0(weights=None)
        model.classifier[1] = torch.nn.Linear(model.classifier[1].in_features, 4)
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        model.load_state_dict(checkpoint["state_dict"])
        model.to("cuda")
        predictions = infer(model, test, "test")
        save(WORK / (name + "-test-predictions.json"), predictions)
        results[name] = summarize(predictions)
        dev_predictions = infer(model, dev, "dev")
        save(WORK / (name + "-dev-predictions.json"), dev_predictions)
        results[name]["dev_diagnostics"] = summarize(dev_predictions)
        model_hashes[name] = sha(path)
        if name == "rebuild":
            first = np.load(WORK / "test-images.npy", mmap_mode="r")[:1].copy()
            model.eval()
            with torch.inference_mode():
                gpu = model(normalize(torch.from_numpy(first), "cuda")).cpu()
                model.to("cpu")
                cpu = model(normalize(torch.from_numpy(first), "cpu"))
            results[name]["cpu_smoke"] = {"finite": bool(torch.isfinite(cpu).all()), "max_logit_difference": (cpu - gpu).abs().max().item(),
                                        "same_attribute_predictions": bool(torch.equal(cpu.reshape(1, 2, 2).argmax(2), gpu.reshape(1, 2, 2).argmax(2)))}
        del model
        torch.cuda.empty_cache()
        event("model_compared", model=name, original=results[name]["per_condition"]["original"], all9=results[name]["tolerant"])
    comparison = {"dataset_sha256": protocol["dataset_sha256"], "test_garments": len(test),
        "observations_per_model": len(test) * 9, "checkpoint_chosen_on": "dev only before test",
        "models_sha256": model_hashes, "selected_epoch": complete["best_epoch"], "thresholds": protocol["thresholds"],
        "test_prior_exposure": protocol["summary"]["test"], "results": results,
        "limitations": ["Retrospective pictures visually inspected during label review; no newly captured blind sample.",
                        "Labels include AI drafts retained with provenance, not 1000 independently human-labeled truths.",
                        "Nine conditions are correlated variants of 150 garments, not 1350 independent garments.",
                        "Old models had a different length criterion; comparison measures agreement with the new approved 4120 criterion.",
                        "Long sleeveless user category is retained as coat, but runtime target is other / mandatory review; strict user-category score is separate.",
                        "Dev includes old-model fit exposure; final test has none in the available histories."]}
    save(WORK / "comparison.json", comparison)
    check_protocol()
    for split in ("dev", "test"):
        if sha(WORK / (split + "-images.npy")) != protocol["cache_sha256"][split]:
            raise ValueError("Frozen evaluation image cache changed")
    with ThreadPoolExecutor(max_workers=8) as pool:
        if not all(pool.map(lambda r: sha(r["source"]) == r["sha256"], rows)):
            raise ValueError("Original photo changed after training")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for source, name in [(WORK / "best.pt", "model.pt"), (WORK / "protocol.json", "protocol.json"),
                          (WORK / "comparison.json", "comparison.json"), (WORK / "history.json", "history.json"),
                          (WORK / "training-complete.json", "training-complete.json")]:
        shutil.copyfile(source, OUTPUT / name)
    save(OUTPUT / "manifest.json", {"architecture": protocol["architecture"], "input_side": SIDE,
        "output_order": protocol["output_order"], "model_sha256": model_hashes["rebuild"],
        "normalization_mean": MEAN, "normalization_std": STD, "preprocessing": protocol["preprocessing"],
        "policy": protocol["thresholds"], "long_sleeveless": "other; mandatory user review", "app_integrated": False,
        "criterion": read(WORK / "dataset.json")["criterion"], "best_epoch": complete["best_epoch"],
        "source_dataset_sha256": protocol["dataset_sha256"], "protected_inputs_unchanged": True,
        "source_photos_reverified": len(rows), "evaluation_caches_reverified": True})
    event("comparison_complete", output=str(OUTPUT), results={n: m["tolerant"] for n, m in results.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "train", "compare"))
    args = parser.parse_args()
    {"prepare": prepare, "train": train, "compare": compare}[args.command]()
