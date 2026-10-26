from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from coatdesk_ab.data import read, save, sha
from .dataset import FRAME, Placards, collate
from .labels import WORK
from .model import CROP_MARGIN, SCORE_THRESHOLD, build_detector, choose_detection, overlap

SEED = 20261025
MAX_EPOCHS = 50
BATCH = 12
BASE = Path(".local/ab-assets/ssdlite320_mobilenet_v3_large_coco-a79551df.pth")


def configure():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def freeze_protocol(work):
    protocol = work / "train-protocol.json"
    if not protocol.exists():
        filenames = ["__init__.py", "labels.py", "dataset.py", "model.py", "train.py"]
        save(protocol, {"seed": SEED, "batch": BATCH, "max_inner_epochs": MAX_EPOCHS,
                        "architecture": "Torchvision SSDLite320 MobileNetV3-Large, 2 classes: background + whole placard",
                        "base_sha256": sha(BASE), "labels_sha256": sha(work / "box-labels.json"),
                        "cache": read(work / "cache.json"), "base": str(BASE),
                        "code_sha256": {str(Path("coatdesk_locator") / p): sha(Path("coatdesk_locator") / p) for p in filenames},
                        "fit_inner": 516, "dev_inner": 129, "fit_final": 645, "held_out": 445,
                        "classifier_weights_frozen": True, "classifier_input": "whole auto-detected placard crop only",
                        "detector_input": "whole photograph resized to 360x640, then model internal 320x320",
                        "anchor_ratios": [2, 3], "anchor_scale_min": .10, "anchor_scale_max": .70,
                        "crop_margin": CROP_MARGIN, "score_threshold": SCORE_THRESHOLD,
                        "optimizer": "AdamW; backbone LR 0.0002, detection head LR 0.001; weight decay 0.0001",
                        "augmentation": "Full-frame affine rotation +-7deg, scale .78..1.22, translation, horizontal flip, brightness/contrast; labelled head never clipped",
                        "batch_norm": "COCO running statistics fixed; affine parameters trainable",
                        "checkpoint_selection": "max dev valid crop coverage >=99%, then IoU>=.75 count, then mean IoU",
                        "final_fit": "fresh COCO initialization, all 645 photos for inner-selected epoch count",
                        "negative_images": "None; standard SSD hard negative anchor mining on positive images",
                        "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
                        "limitations": ["Same previously examined collection; not a new-day independent test", "Codex visual box review, no independent operator review", "Only one visible placard per photo evaluated"]})
    value = read(protocol)
    if sha(BASE) != value["base_sha256"] or sha(work / "box-labels.json") != value["labels_sha256"]:
        raise ValueError("Frozen initialization or box labels changed")
    if sha(work / "photos.npy") != value["cache"]["array_sha256"]:
        raise ValueError("Frozen input array changed")
    for path, fingerprint in value["code_sha256"].items():
        if sha(path) != fingerprint:
            raise ValueError(f"Frozen training code changed: {path}")
    return value


@torch.inference_mode()
def validate(model, batches):
    model.eval()
    values = []
    for images, targets in batches:
        predictions = model([image.cuda(non_blocking=True) for image in images])
        for prediction, target in zip(predictions, targets, strict=True):
            found = choose_detection(prediction, *FRAME)
            truth = target["boxes"][0].tolist()
            iou, _ = overlap(found["box"], truth)
            _, coverage = overlap(found["crop_box"], truth)
            values.append({"index": int(target["image_id"]), "valid": found["valid"], "iou": iou, "coverage": coverage})
    return {"count": len(values), "valid": sum(r["valid"] for r in values),
            "coverage_99": sum(r["valid"] and r["coverage"] >= .99 for r in values),
            "iou_75": sum(r["valid"] and r["iou"] >= .75 for r in values),
            "mean_iou": float(np.mean([r["iou"] for r in values]))}


def fit(work, stage):
    configure()
    freeze_protocol(work)
    folder = work / "training" / stage
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "complete.json").exists():
        print(json.dumps({"already_completed": stage}))
        return
    epochs = MAX_EPOCHS if stage == "inner" else read(work / "training/inner/complete.json")["best_epoch"]
    train = Placards(work, ["inner_fit"] + (["inner_dev"] if stage == "final" else []), augment=True)
    dev = Placards(work, ["inner_dev"])
    train_batches = DataLoader(train, batch_size=BATCH, shuffle=True, collate_fn=collate, num_workers=0, pin_memory=True)
    dev_batches = DataLoader(dev, batch_size=BATCH, collate_fn=collate, num_workers=0)
    model = build_detector(BASE).cuda()
    optimizer = torch.optim.AdamW([{"params": model.backbone.parameters(), "lr": .0002},
                                  {"params": model.head.parameters(), "lr": .001}], weight_decay=.0001)
    scaler = torch.amp.GradScaler("cuda")
    history, best, best_epoch, first_epoch, previous_seconds = [], (-1, -1, -1.), 0, 1, 0.
    if (folder / "last.pt").exists():
        checkpoint = torch.load(folder / "last.pt", map_location="cpu", weights_only=False)
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        scaler.load_state_dict(checkpoint["scaler"])
        random.setstate(checkpoint["python_rng"])
        np.random.set_state(checkpoint["numpy_rng"])
        torch.set_rng_state(checkpoint["torch_rng"])
        torch.cuda.set_rng_state_all(checkpoint["cuda_rng"])
        history, best, best_epoch = checkpoint["history"], checkpoint["best"], checkpoint["best_epoch"]
        first_epoch, previous_seconds = checkpoint["epoch"] + 1, checkpoint["seconds"]
        print(json.dumps({"resumed": stage, "next_epoch": first_epoch}), flush=True)
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    for epoch in range(first_epoch, epochs + 1):
        factor = .1 + .9 * (1 + math.cos(math.pi * (epoch - 1) / MAX_EPOCHS)) / 2
        for group, lr in zip(optimizer.param_groups, [.0002, .001], strict=True):
            group["lr"] = lr * factor
        model.train()
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.eval()
        losses, seen = {}, 0
        epoch_started = time.perf_counter()
        for images, targets in train_batches:
            images = [image.cuda(non_blocking=True) for image in images]
            targets = [{k: v.cuda(non_blocking=True) for k, v in target.items()} for target in targets]
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                values = model(images, targets)
                loss = sum(values.values())
            if not bool(torch.isfinite(loss)):
                raise ValueError("Non-finite detection loss; previous atomic checkpoint retained")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5.)
            scaler.step(optimizer)
            scaler.update()
            for name, value in values.items():
                losses[name] = losses.get(name, 0.) + float(value.detach()) * len(images)
            seen += len(images)
        torch.cuda.synchronize()
        entry = {"stage": stage, "epoch": epoch, "fit_count": seen, "seconds": time.perf_counter() - epoch_started,
                 "losses": {k: v / seen for k, v in losses.items()}}
        if stage == "inner":
            entry["dev"] = validate(model, dev_batches)
            score = tuple(entry["dev"][key] for key in ("coverage_99", "iou_75", "mean_iou"))
            if score > best:
                best, best_epoch = score, epoch
                torch.save(model.state_dict(), folder / "best.pt")
        history.append(entry)
        checkpoint = {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scaler": scaler.state_dict(),
                      "epoch": epoch, "history": history, "best": best, "best_epoch": best_epoch,
                      "python_rng": random.getstate(), "numpy_rng": np.random.get_state(), "torch_rng": torch.get_rng_state(),
                      "cuda_rng": torch.cuda.get_rng_state_all(), "seconds": previous_seconds + time.perf_counter() - started}
        torch.save(checkpoint, folder / "last.tmp")
        os.replace(folder / "last.tmp", folder / "last.pt")
        with (folder / "epochs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        print(json.dumps(entry), flush=True)
    if stage == "final":
        best_epoch = epochs
        torch.save(model.state_dict(), folder / "model.pt")
    save(folder / "complete.json", {"stage": stage, "fit_count": len(train), "dev_count": len(dev) if stage == "inner" else 0,
                                   "epochs": epochs, "best_epoch": best_epoch, "best_dev": best if stage == "inner" else None,
                                   "history": history, "seconds": previous_seconds + time.perf_counter() - started,
                                   "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--stage", choices=["inner", "final"], required=True)
    args = parser.parse_args()
    fit(args.work, args.stage)
