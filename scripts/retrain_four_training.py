"""Local candidates only: frozen garment split, light augmentation, resumable fits.

Run freeze, then train. Never imports this module from the application runtime.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import math
import os
import random
import time

from scripts.retrain_four_models import ROOT, WORK, UnionFind, event, read, save, sha

SEED = 20261102
KINDS = ("original", "dark", "bright", "warm", "cool", "contrast", "shadow_left", "shadow_right", "blur")
SIZES = [str(n) for n in range(70, 171, 10)] + ["XS", "S", "M", "L", "XL", "2XL", "3XL"]
SHAPES = ["adult_male", "adult_female", "child"]
EPOCHS = {"size": 24, "shape": 24, "category": 20, "detector": 40}
CATEGORY_DATA = ROOT / "model-results/20261026-category-label-review/approved-dataset-20261027-01/dataset.json"


def protected_check():
    for name, digest in read(WORK / "protected.json").items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Protected original changed: {name}")


def implementation_check():
    paths = [
        ROOT / "scripts/retrain_four_training.py",
        ROOT / "scripts/retrain_four_models.py",
        ROOT / "coatdesk_ab/network.py",
        ROOT / "coatdesk_ab/data.py",
        ROOT / "coatdesk_ab/locator.py",
        ROOT / "coatdesk_locator/model.py",
        ROOT / "coatdesk_v2/category.py",
    ]
    current = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    path = WORK / "implementation-lock.json"
    if path.exists():
        if read(path) != current:
            raise ValueError("Experiment implementation changed since fitting began")
    else:
        save(path, current)


def freeze():
    """Do not let related garments or previous gradient-fit groups cross splits."""
    protected_check()
    target = WORK / "frozen-dataset.json"
    if target.exists():
        raise FileExistsError("Dataset already frozen; use its saved split")
    inventory = {r["code"]: r for r in read(WORK / "inventory.json")}
    annotations = read(WORK / "annotations.json")
    dedup = read(WORK / "deduplication.json")
    uf = UnionFind(inventory)
    for codes in dedup["groups"].values():
        for c in codes[1:]:
            uf.join(codes[0], c)
    # Uncertain duplicates are retained, but cannot leak across partitions.
    for pair in read(WORK / "duplicate-review.json").values():
        if pair["decision"] == "uncertain":
            uf.join(pair["code"], pair["previous"])
    panel_history = read(ROOT / ".local/ab-work-20261024-r3/manifest.json")["rows"]
    category_history = read(CATEGORY_DATA)["records"]
    previous_fit, previous_selection = set(), set()
    for history, key in [(panel_history, "split_group"), (category_history, "source_group")]:
        related = defaultdict(list)
        for r in history:
            if r["code"] not in inventory:
                continue
            related[r.get(key, r["code"])].append(r["code"])
            fit = r.get("role") in ("inner_fit", "inner_dev") or r.get("dataset_split") == "train"
            if fit:
                previous_fit.add(r["code"])
            elif r.get("dataset_split") == "dev":
                previous_selection.add(r["code"])
        for codes in related.values():
            for c in codes[1:]:
                uf.join(codes[0], c)
    same_file = defaultdict(list)
    for r in inventory.values():
        same_file[r["sha256"]].append(r["code"])
    for codes in same_file.values():
        for c in codes[1:]:
            uf.join(codes[0], c)
    pinned = {uf.root(c) for c in previous_fit}
    selection_groups = {uf.root(c) for c in previous_selection}
    selected = sorted(set(dedup["panel_codes"]) | set(dedup["category_codes"]), key=int)
    records = []
    prior_conflicts = []
    for code in selected:
        r, a = inventory[code], dict(annotations[code])
        if not a["box"] or a["attribute_status"] == "missing" or a["raw_size"] not in SIZES:
            raise ValueError(f"Unreviewed label: {code}")
        if a.get("size_label_conflict"):
            raise ValueError(f"Unresolved raw label: {code}")
        if a.get("old_category") and a["old_category"] != r["category"]:
            prior_conflicts.append(
                {
                    "code": code,
                    "previous": a["old_category"],
                    "excel": r["category"],
                    "sleeves": a["sleeves"],
                    "length": a["length"],
                }
            )
        # Five re-opened front photos have disputed jacket/coat boundaries.
        # Keep the user's latest category for strict evaluation; do not invent
        # a hard length target from a category name or conflicting old label.
        if code in {"1159", "2090", "2587", "2876", "2998"}:
            a["length"] = None
            a["length_status"] = "rechecked_boundary_latest_excel_conflict"
        excluded = bool(a.get("category_excluded")) or code == "1495"
        shape = "child" if r["age"] == "유아동" else "adult_female" if r["gender"] == "여성" else "adult_male"
        records.append(
            {
                **r,
                **a,
                "group": uf.root(code),
                "shape": shape,
                "panel_eligible": code in dedup["panel_codes"],
                "category_eligible": code in dedup["category_codes"] and not excluded,
                "previous_fit": code in previous_fit,
                "previous_selection": code in previous_selection,
            }
        )
    groups = defaultdict(list)
    for r in records:
        groups[r["group"]].append(r)
    partition = {g: "train" for g in groups}
    # Allocate at raw-class level first, guaranteeing 70's 5/1/1 when seven
    # independent groups exist. Additional shape/category/day balance is
    # recorded, rather than claiming exact proportions under group constraints.
    strata = defaultdict(list)
    for g, rows in groups.items():
        strata[rows[0]["raw_size"]].append(g)
    rng = random.Random(SEED)
    for raw, ids in sorted(strata.items()):
        available = [g for g in ids if g not in pinned]
        rng.shuffle(available)
        # Prefer groups never used for previous model selection for final test.
        available.sort(key=lambda g: g in selection_groups)
        n = max(1, round(len(ids) * 0.15)) if len(ids) >= 3 else 0
        if len(available) < 2 * n:
            n = len(available) // 2
        for g in available[:n]:
            partition[g] = "test"
        for g in available[n : 2 * n]:
            partition[g] = "dev"
    for r in records:
        r["split"] = partition[r["group"]]
    if any(partition[g] != "train" for g in pinned if g in partition):
        raise AssertionError("Previous training leakage")
    for raw in SIZES:
        counts = Counter(r["split"] for r in records if r["panel_eligible"] and r["raw_size"] == raw)
        if not counts["train"]:
            raise ValueError(f"No training examples for {raw}")
    summary = {
        "garment_groups": len(groups),
        "all_records": len(records),
        "split_groups": dict(Counter(partition.values())),
        "prior_gradient_fit_groups_pinned": len(pinned & set(groups)),
        "prior_category_conflicts": prior_conflicts,
    }
    for task in ("panel", "category"):
        rows = [r for r in records if r[task + "_eligible"]]
        summary[task] = {
            s: {
                "count": sum(r["split"] == s for r in rows),
                "raw": dict(Counter(r["raw_size"] for r in rows if r["split"] == s)),
                "shape": dict(Counter(r["shape"] for r in rows if r["split"] == s)),
                "category": dict(Counter(r["category"] for r in rows if r["split"] == s)),
                "day": dict(Counter(r["sheet"] for r in rows if r["split"] == s)),
            }
            for s in ("train", "dev", "test")
        }
    data = {
        "seed": SEED,
        "sizes": SIZES,
        "shapes": SHAPES,
        "records": records,
        "summary": summary,
        "limitations": [
            "Retrospective photographs previously inspected; not a new-year blind test.",
            "New auxiliary sleeve/length and boxes visually reviewed by Codex, not independently user confirmed.",
            "Raw 60 and adult 2XS have no examples; raw 170 is training-only when singleton.",
        ],
        "inputs": {
            p.name: sha(p)
            for p in [
                WORK / "inventory.json",
                WORK / "annotations.json",
                WORK / "deduplication.json",
                WORK / "raw-transcription.json",
            ]
        },
    }
    save(target, data)
    event("dataset_frozen", **summary)


def change(photo, kind, strength=1.0):
    """Single approved photometric transform on the full 1080px preview."""
    import numpy as np
    from PIL import Image, ImageEnhance, ImageFilter

    if kind == "original":
        return photo.copy()
    if kind in ("dark", "bright"):
        return ImageEnhance.Brightness(photo).enhance(1 + (-0.2 if kind == "dark" else 0.2) * strength)
    if kind == "contrast":
        return ImageEnhance.Contrast(photo).enhance(1 - 0.15 * strength)
    if kind == "blur":
        return photo.filter(ImageFilter.GaussianBlur(0.65 * strength * photo.width / 1080))
    a = np.asarray(photo, dtype=np.float32)
    if kind in ("warm", "cool"):
        goal = np.array([1.04, 1.0, 0.94] if kind == "warm" else [0.94, 1.0, 1.04])
        a *= 1 + (goal - 1) * strength
    elif kind in ("shadow_left", "shadow_right"):
        x = np.linspace(0, 1, photo.width)
        if kind == "shadow_right":
            x = 1 - x
        a *= (1 - 0.25 * strength * (1 + np.cos(x * math.pi)) / 2)[None, :, None]
    else:
        raise ValueError(f"Unknown transform: {kind}")
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))


def panel_input(photo, box):
    from coatdesk_ab.data import letterbox

    dx, dy = (box[2] - box[0]) * 0.08, (box[3] - box[1]) * 0.08
    crop = [
        max(0, math.floor(box[0] - dx)),
        max(0, math.floor(box[1] - dy)),
        min(photo.width, math.ceil(box[2] + dx)),
        min(photo.height, math.ceil(box[3] + dy)),
    ]
    return letterbox(photo.crop(crop), 512)[0]


def category_input(photo, box):
    from PIL import ImageDraw
    from coatdesk_ab.data import letterbox

    image = photo.copy()
    x1, y1, x2, y2 = map(int, box)
    ImageDraw.Draw(image).rectangle(
        (max(0, x1 - 8), max(0, y1 - 8), min(image.width - 1, x2 + 8), min(image.height - 1, y2 + 8)), fill="white"
    )
    return letterbox(image, 320)[0]


def tensors(image):
    import numpy as np
    import torch

    return torch.from_numpy(np.asarray(image).transpose(2, 0, 1).copy()).float() / 255


class Photos:
    def __init__(self, rows, task, epoch=0, train=False, kinds=("original",)):
        self.rows, self.task, self.epoch, self.train, self.kinds = rows, task, epoch, train, kinds

    def __len__(self):
        return len(self.rows) * len(self.kinds)

    def __getitem__(self, index):
        import torch
        from PIL import Image
        from coatdesk_ab.data import load_photo

        i, k = divmod(index, len(self.kinds))
        row = self.rows[i]
        kind, strength = self.kinds[k], 1.0
        if self.train:
            # Index/epoch deterministic, independent of loader scheduling; resume
            # regenerates exactly the same augmentation without 9x disk copies.
            rng = random.Random(SEED + self.epoch * 1000003 + int(row["code"]))
            if rng.random() >= 0.25:
                kind, strength = rng.choice(KINDS[1:]), rng.random()
        photo = change(load_photo(row["source"]), kind, strength)
        box = row["box"]
        if self.task == "detector":
            image = tensors(photo.resize((448, 448), Image.Resampling.LANCZOS))
            w, h = row["coordinate_size"]
            target = {
                "boxes": torch.tensor([[box[0] * 448 / w, box[1] * 448 / h, box[2] * 448 / w, box[3] * 448 / h]]),
                "labels": torch.ones(1, dtype=torch.int64),
            }
        else:
            image = tensors(
                category_input(photo, row["mask_box"]) if self.task == "category" else panel_input(photo, box)
            )
            image = (image - torch.tensor([0.485, 0.456, 0.406])[:, None, None]) / torch.tensor([0.229, 0.224, 0.225])[
                :, None, None
            ]
            target = (
                torch.tensor(
                    [
                        int(not row["sleeves"]) if row["sleeves"] is not None else -1,
                        row["length"] if row["length"] is not None else -1,
                    ]
                )
                if self.task == "category"
                else torch.tensor(
                    (SIZES if self.task == "size" else SHAPES).index(
                        row["raw_size"] if self.task == "size" else row["shape"]
                    )
                )
            )
        return image, target, i, k


def collate(batch):
    import torch

    images, targets, ids, kinds = zip(*batch)
    return torch.stack(images), list(targets) if isinstance(targets[0], dict) else torch.stack(targets), ids, kinds


def build(task, baseline=False):
    import torch
    from coatdesk_ab.data import SIZES as OLD_SIZES
    from coatdesk_ab.network import PhotoClassifier
    from coatdesk_locator_v2.experiment import build_v2
    from torchvision.models import efficientnet_b0

    if task == "detector":
        model = build_v2()
        model.load_state_dict(torch.load(ROOT / "resources/ai/detector.pt", map_location="cpu", weights_only=True))
    elif task == "category":
        model = efficientnet_b0(weights=None)
        model.classifier[1] = torch.nn.Linear(model.classifier[1].in_features, 4)
        model.load_state_dict(
            torch.load(ROOT / "resources/ai/category/model.pt", map_location="cpu", weights_only=True)["state_dict"]
        )
    else:
        classes = SHAPES if task == "shape" else OLD_SIZES if baseline else SIZES
        model = PhotoClassifier(len(classes))
        old = torch.load(ROOT / f"resources/ai/panel-{task}.pt", map_location="cpu", weights_only=True)
        if task == "size" and not baseline:
            state = model.state_dict()
            for key, value in old.items():
                if key in ("output.1.weight", "output.1.bias"):
                    for source, label in enumerate(OLD_SIZES):
                        state[key][SIZES.index(label)] = value[source]
                else:
                    state[key] = value
            model.load_state_dict(state)
        else:
            model.load_state_dict(old)
    return model


def configure():
    import torch
    import numpy as np

    torch.set_num_threads(4)
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def loader(rows, task, epoch=0, train=False, kinds=("original",)):
    import torch

    generator = torch.Generator().manual_seed(SEED + epoch)
    return torch.utils.data.DataLoader(
        Photos(rows, task, epoch, train, kinds),
        batch_size=16 if task == "category" else 12,
        shuffle=train,
        generator=generator,
        collate_fn=collate,
        num_workers=4,
        pin_memory=True,
        persistent_workers=False,
    )


def class_metrics(truth, predicted, classes):
    from scripts.category_expanded_model import class_metrics as metrics

    return metrics(truth, predicted, classes)


def evaluate(model, rows, task, device="cuda", kinds=KINDS, baseline=False):
    import torch
    from coatdesk_ab.data import SIZES as OLD_SIZES
    from coatdesk_locator.model import choose_detection, overlap
    from coatdesk_v2.category import decode

    model.eval()
    predictions = []
    start_time = time.monotonic()
    with torch.inference_mode():
        for x, y, ids, ks in loader(rows, task, kinds=kinds):
            x = x.to(device)
            outputs = model(list(x)) if task == "detector" else model(x).float()
            for n, (i, k) in enumerate(zip(ids, ks)):
                r = rows[i]
                p = {"code": r["code"], "group": r["group"], "kind": kinds[k], "split": r["split"]}
                if task == "detector":
                    d = choose_detection(outputs[n], 448, 448)
                    w, h = r["coordinate_size"]
                    truth = [r["box"][0] * 448 / w, r["box"][1] * 448 / h, r["box"][2] * 448 / w, r["box"][3] * 448 / h]
                    iou, coverage = overlap(d["box"], truth)
                    _, crop_coverage = overlap(d["crop_box"], truth)
                    p.update(
                        score=d["score"],
                        valid=d["valid"],
                        iou=iou,
                        box=d["box"],
                        crop_box=d["crop_box"],
                        coverage=coverage,
                        crop_coverage=crop_coverage,
                        correct=d["valid"] and crop_coverage >= 0.99,
                    )
                elif task == "category":
                    values = torch.cat([outputs[n, :2].softmax(0), outputs[n, 2:].softmax(0)]).tolist()
                    p.update(
                        decode(values),
                        truth=r["category"],
                        probabilities=values,
                        targets=[None if v is None else int(not v) for v in [r["sleeves"]]] + [r["length"]],
                    )
                    p["correct"] = p["raw_category"] == r["category"]
                    p["attribute_correct"] = [
                        None if t is None else values[h * 2 : h * 2 + 2].index(max(values[h * 2 : h * 2 + 2])) == t
                        for h, t in enumerate(p["targets"])
                    ]
                else:
                    classes = OLD_SIZES if baseline and task == "size" else SIZES if task == "size" else SHAPES
                    values = outputs[n].softmax(0).tolist()
                    predicted = classes[values.index(max(values))]
                    truth = r["raw_size"] if task == "size" else r["shape"]
                    p.update(
                        truth=truth,
                        predicted=predicted,
                        score=max(values),
                        probabilities=dict(zip(classes, values)),
                        correct=truth == predicted,
                    )
                predictions.append(p)
    summaries = {}
    for kind in kinds:
        subset = [p for p in predictions if p["kind"] == kind]
        m = {
            "count": len(subset),
            "correct": sum(p["correct"] for p in subset),
            "accuracy": sum(p["correct"] for p in subset) / len(subset),
        }
        if task == "detector":
            m.update(
                mean_iou=sum(p["iou"] for p in subset) / len(subset),
                mean_crop_coverage=sum(p["crop_coverage"] for p in subset) / len(subset),
            )
        elif task == "category":
            m["attributes"] = []
            for h in range(2):
                recalls = []
                for value in (0, 1):
                    valid = [p for p in subset if p["targets"][h] == value]
                    if valid:
                        recalls.append(sum(p["attribute_correct"][h] for p in valid) / len(valid))
                m["attributes"].append(sum(recalls) / len(recalls) if recalls else 0)
        else:
            m.update(
                class_metrics(
                    [p["truth"] for p in subset], [p["predicted"] for p in subset], SIZES if task == "size" else SHAPES
                )
            )
        summaries[kind] = m
    summaries["all_conditions_correct"] = sum(
        all(p["correct"] for p in predictions if p["code"] == r["code"]) for r in rows
    )
    summaries["elapsed_seconds"] = time.monotonic() - start_time
    return predictions, summaries


def selection_key(summary, task):
    original = summary["original"]
    stress = sum(summary[k]["accuracy"] for k in KINDS) / len(KINDS)
    if task == "category":
        return (sum(original["attributes"]) / 2, original["accuracy"], stress)
    if task == "detector":
        return (original["accuracy"], stress, original["mean_iou"])
    return (original["macro_f1"], original["accuracy"], stress)


def checkpoint(path, value):
    import torch

    temporary = path.with_suffix(".tmp")
    torch.save(value, temporary)
    temporary.replace(path)


def training_checkpoint(model, optimizer, scheduler, epoch, cursor, best_key, task, dataset_hash, order):
    import torch
    import numpy as np

    return {
        "state_dict": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "epoch": epoch,
        "cursor": cursor,
        "best_key": best_key,
        "task": task,
        "dataset_hash": dataset_hash,
        "order": order,
        "random": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all(),
        "sizes": SIZES,
        "shapes": SHAPES,
    }


def train_one(task):
    import torch
    import numpy as np
    from coatdesk_ab.network import freeze_batch_norm
    from scripts.category_expanded_model import masked_attribute_loss

    configure()
    protected_check()
    implementation_check()
    data = read(WORK / "frozen-dataset.json")
    digest = sha(WORK / "frozen-dataset.json")
    rows = [r for r in data["records"] if r["category_eligible" if task == "category" else "panel_eligible"]]
    if task == "category":
        masks = read(WORK / "category-mask-boxes.json")
        rows = [{**r, "mask_box": masks[r["code"]]["box"]} for r in rows]
    fit = [r for r in rows if r["split"] == "train"]
    dev = [r for r in rows if r["split"] == "dev"]
    run = WORK / "training" / task
    run.mkdir(parents=True, exist_ok=True)
    if (run / "complete.json").exists():
        if read(run / "complete.json")["dataset_hash"] != digest:
            raise ValueError("Completed candidate dataset mismatch")
        event("training_already_complete", task=task)
        return
    if not torch.cuda.is_available():
        raise RuntimeError("Local CUDA required")
    model = build(task).cuda()
    # Use existing backbone/head names; do not silently train at a single LR.
    if task == "detector":
        base = list(model.backbone.parameters())
        head = list(model.head.parameters())
    elif task == "category":
        base = list(model.features.parameters())
        head = list(model.classifier.parameters())
    else:
        base = list(model.features.parameters())
        head = list(model.attention.parameters()) + list(model.output.parameters())
    optimizer = torch.optim.AdamW(
        [{"params": base, "lr": 2e-4 if task == "detector" else 1e-4}, {"params": head, "lr": 1e-3}], weight_decay=1e-4
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, EPOCHS[task])
    criteria = []
    if task != "detector":
        heads = range(2) if task == "category" else range(1)
        for h in heads:
            targets = (
                [(int(not r["sleeves"]) if r["sleeves"] is not None else None) if h == 0 else r["length"] for r in fit]
                if task == "category"
                else [
                    (SIZES if task == "size" else SHAPES).index(r["raw_size"] if task == "size" else r["shape"])
                    for r in fit
                ]
            )
            counts = Counter(t for t in targets if t is not None)
            nc = 2 if task == "category" else len(SIZES) if task == "size" else len(SHAPES)
            weights = torch.tensor(
                [math.sqrt(sum(counts.values()) / (nc * counts[i])) if counts[i] else 0 for i in range(nc)],
                device="cuda",
            )
            criteria.append(torch.nn.CrossEntropyLoss(weight=weights / weights.mean(), label_smoothing=0.02))
    epoch, cursor, best_key, order = 0, 0, None, None
    last = run / "last.pt"
    if last.exists():
        saved = torch.load(last, map_location="cpu", weights_only=False)
        if saved["dataset_hash"] != digest or saved["task"] != task:
            raise ValueError("Resume input mismatch")
        model.load_state_dict(saved["state_dict"])
        optimizer.load_state_dict(saved["optimizer"])
        scheduler.load_state_dict(saved["scheduler"])
        epoch, cursor, best_key, order = saved["epoch"], saved["cursor"], saved["best_key"], saved["order"]
        random.setstate(saved["random"])
        np.random.set_state(saved["numpy"])
        torch.set_rng_state(saved["torch"])
        torch.cuda.set_rng_state_all(saved["cuda"])
        event("training_resumed", task=task, epoch=epoch, cursor=cursor)
    save(
        run / "configuration.json",
        {
            "task": task,
            "max_epochs": EPOCHS[task],
            "fit": len(fit),
            "dev": len(dev),
            "dataset_hash": digest,
            "input_side": 320 if task == "category" else 448 if task == "detector" else 512,
            "seed": SEED,
            "augmentation": "25% original, 75% one random full-photo photometric change",
            "gpu": torch.cuda.get_device_name(0),
            "torch": torch.__version__,
        },
    )
    while epoch < EPOCHS[task]:
        implementation_check()
        started = time.monotonic()
        if order is None:
            order = list(range(len(fit)))
            random.Random(SEED + epoch).shuffle(order)
        ordered = [fit[i] for i in order]
        # No shuffle here: saved cursor refers to a frozen order. Dataset epoch
        # controls augmentation; only dropout depends on restored torch RNG.
        batch = 16 if task == "category" else 12
        iterator = torch.utils.data.DataLoader(
            Photos(ordered[cursor:], task, epoch, True),
            batch_size=batch,
            collate_fn=collate,
            num_workers=4,
            pin_memory=True,
            generator=torch.Generator().manual_seed(SEED + epoch),
        )
        model.train()
        freeze_batch_norm(model)
        running = 0.0
        steps = 0
        last_saved = time.monotonic()
        for x, y, _, _ in iterator:
            optimizer.zero_grad(set_to_none=True)
            x = x.cuda(non_blocking=True)
            if task == "detector":
                output = model(list(x), [{k: v.cuda(non_blocking=True) for k, v in t.items()} for t in y])
                loss = sum(output.values())
            else:
                logits = model(x)
                y = y.cuda(non_blocking=True)
                loss = masked_attribute_loss(logits, y, criteria) if task == "category" else criteria[0](logits, y)
            if not torch.isfinite(loss):
                raise ValueError(f"Non-finite {task} loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            cursor += len(x)
            running += float(loss.detach())
            steps += 1
            if time.monotonic() - last_saved >= 45:
                checkpoint(
                    last, training_checkpoint(model, optimizer, scheduler, epoch, cursor, best_key, task, digest, order)
                )
                event(
                    "training_batch_checkpoint",
                    task=task,
                    epoch=epoch + 1,
                    cursor=cursor,
                    fit=len(fit),
                    loss=running / steps,
                )
                last_saved = time.monotonic()
        checkpoint(last, training_checkpoint(model, optimizer, scheduler, epoch, cursor, best_key, task, digest, order))
        predictions, summary = evaluate(model, dev, task)
        key = selection_key(summary, task)
        save(
            run / f"dev-epoch-{epoch + 1:03d}.json",
            {"summary": summary, "selection_key": key, "training_loss": running / max(1, steps)},
        )
        if best_key is None or tuple(key) > tuple(best_key):
            best_key = key
            checkpoint(
                run / "best.pt",
                {
                    "state_dict": model.state_dict(),
                    "epoch": epoch + 1,
                    "summary": summary,
                    "dataset_hash": digest,
                    "task": task,
                    "sizes": SIZES,
                    "shapes": SHAPES,
                    "input_side": 320 if task == "category" else 448 if task == "detector" else 512,
                    "architecture": "efficientnet_b0_sleeve_length_2x2"
                    if task == "category"
                    else "resnet18_stride16_attention"
                    if task in ("shape", "size")
                    else "ssdlite_mobilenet_v3_large_448",
                },
            )
            save(run / "best-dev-predictions.json", predictions)
        scheduler.step()
        epoch += 1
        cursor = 0
        order = None
        checkpoint(last, training_checkpoint(model, optimizer, scheduler, epoch, cursor, best_key, task, digest, order))
        event(
            "training_epoch_complete",
            task=task,
            epoch=epoch,
            max_epochs=EPOCHS[task],
            selection_key=key,
            best_key=best_key,
            elapsed_seconds=time.monotonic() - started,
        )
    saved = torch.load(run / "best.pt", map_location="cpu", weights_only=True)
    model.cpu().load_state_dict(saved["state_dict"])
    save(
        run / "complete.json",
        {
            "task": task,
            "epochs": epoch,
            "best_epoch": saved["epoch"],
            "dataset_hash": digest,
            "best_sha256": sha(run / "best.pt"),
            "cpu_reload": True,
            "comparison_pending": True,
        },
    )
    event("model_training_complete", task=task, epochs=epoch, best_epoch=saved["epoch"])


def masks():
    from coatdesk_ab.locator import WholePanelLocator
    from coatdesk_ab.data import load_photo

    locator = WholePanelLocator(ROOT / "resources/ai/category")
    path = WORK / "category-mask-boxes.json"
    saved = read(path) if path.exists() else {}
    rows = [r for r in read(WORK / "frozen-dataset.json")["records"] if r["category_eligible"]]
    for i, r in enumerate(rows):
        if r["code"] in saved:
            continue
        location = locator.locate(load_photo(r["source"]))
        if not location["valid"]:
            raise ValueError(f"Runtime category masking failed: {r['code']}")
        saved[r["code"]] = location
        if i % 25 == 0:
            save(path, saved)
            event("category_masks_prepared", completed=len(saved), total=len(rows))
    save(path, saved)
    event("category_masks_complete", completed=len(saved))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "masks", "train"])
    parser.add_argument("--task", choices=["size", "shape", "category", "detector", "all"], default="all")
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.command == "masks":
        masks()
    else:
        # Windows byte-range lock is automatically released if the worker dies.
        # Holding this open prevents two independent resumptions from racing.
        import msvcrt

        lock = (WORK / "training.lock").open("a+b")
        lock.seek(0)
        lock.write(b"1")
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        save(
            WORK / "worker.json", {"pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "task": args.task}
        )
        try:
            for task in ["size", "shape", "detector", "category"] if args.task == "all" else [args.task]:
                if task == "category":
                    masks()
                train_one(task)
            event("four_fits_complete_comparison_pending")
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            lock.close()


if __name__ == "__main__":
    main()
