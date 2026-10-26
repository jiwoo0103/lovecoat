from __future__ import annotations

import hashlib
import json
import math
import random
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset

from .data import SEED, SHAPES, SIDE, SIZES, read, save, sha
from .network import PhotoClassifier, freeze_batch_norm

MAX_EPOCHS = 24
BATCH = 12


def configure():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    torch.set_num_threads(4)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    if not torch.cuda.is_available():
        raise RuntimeError("This run was configured for the available NVIDIA GPU")


def tensor_hash(model):
    fingerprint = hashlib.sha256()
    for name, value in sorted(model.state_dict().items()):
        fingerprint.update(name.encode())
        fingerprint.update(value.detach().cpu().contiguous().numpy().tobytes())
    return fingerprint.hexdigest()


class Photos(Dataset):
    def __init__(self, work, mode, rows, task):
        self.images = np.load(work / mode / "images.npy", mmap_mode="r")
        self.rows, self.task = rows, task

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        image = torch.from_numpy(self.images[row["index"]].copy())
        return image, row[self.task + "_index"], row["index"]


def normalize(images, augment=False):
    images = images.to("cuda", non_blocking=True).float() / 255
    if augment:
        # Shrink + pad retains the entire input; no crop, horizontal flip, or
        # rotation that could remove image corners or reverse printed text.
        scaled = round(SIDE * random.uniform(.90, 1.0))
        if scaled < SIDE:
            images = F.interpolate(images, size=(scaled, scaled), mode="bilinear", align_corners=False)
            left, top = random.randint(0, SIDE - scaled), random.randint(0, SIDE - scaled)
            images = F.pad(images, (left, SIDE - scaled - left, top, SIDE - scaled - top), value=1.)
        brightness = .90 + .20 * torch.rand(images.shape[0], 1, 1, 1, device=images.device)
        images = (images * brightness).clamp_(0, 1)
    mean = images.new_tensor([.485, .456, .406])[None, :, None, None]
    std = images.new_tensor([.229, .224, .225])[None, :, None, None]
    return (images - mean) / std


def loader(work, mode, rows, task, shuffle=False):
    return DataLoader(Photos(work, mode, rows, task), batch_size=BATCH, shuffle=shuffle,
                      num_workers=0, pin_memory=True, generator=torch.Generator().manual_seed(SEED))


@torch.inference_mode()
def predict(model, batches):
    model.eval()
    output, loss, count = [], 0., 0
    for images, labels, indices in batches:
        labels = labels.to("cuda")
        with torch.autocast("cuda", dtype=torch.float16):
            logits = model(normalize(images))
            loss += float(F.cross_entropy(logits, labels, reduction="sum"))
        probabilities = logits.float().softmax(1).cpu().numpy()
        for index, label, probability in zip(indices.tolist(), labels.cpu().tolist(), probabilities, strict=True):
            output.append({"index": index, "truth": label, "predicted": int(probability.argmax()),
                           "probabilities": probability.tolist()})
        count += len(labels)
    torch.cuda.synchronize()
    return output, loss / max(1, count)


def fit(work, mode, task, stage, epochs=MAX_EPOCHS):
    configure()
    folder = work / "training" / f"{mode}-{task}" / stage
    folder.mkdir(parents=True)
    if (folder / "complete.json").exists():
        return read(folder / "complete.json")
    rows = read(work / "prepared.json")["records"]
    fitting = [r for r in rows if r["role"] == "inner_fit" or (stage == "final" and r["role"] == "inner_dev")]
    dev = [r for r in rows if r["role"] == "inner_dev"] if stage == "inner" else []
    classes = SHAPES if task == "shape" else SIZES
    train_batches = loader(work, mode, fitting, task, shuffle=True)
    dev_batches = loader(work, mode, dev, task) if dev else None
    model = PhotoClassifier(len(classes), work.parent / "ab-assets" / "resnet18-f37072fd.pth").cuda()
    initial_sha = tensor_hash(model)
    counts = np.bincount([r[task + "_index"] for r in fitting], minlength=len(classes))
    if (counts == 0).any():
        raise ValueError("A supported class is absent from fitting data")
    weights = np.sqrt(len(fitting) / (len(classes) * counts))
    weights /= weights.mean()
    criterion = nn.CrossEntropyLoss(weight=torch.tensor(weights, device="cuda", dtype=torch.float32), label_smoothing=.02)
    optimizer = torch.optim.AdamW([{"params": model.features.parameters(), "lr": .0001},
                                  {"params": list(model.attention.parameters()) + list(model.output.parameters()), "lr": .001}], weight_decay=.0001)
    scaler = torch.amp.GradScaler("cuda")
    best, best_epoch, history = (-1, -float("inf")), 0, []
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    for epoch in range(1, epochs + 1):
        factor = .15 + .85 * (1 + math.cos(math.pi * (epoch - 1) / MAX_EPOCHS)) / 2
        for group, initial in zip(optimizer.param_groups, [.0001, .001], strict=True):
            group["lr"] = initial * factor
        model.train()
        freeze_batch_norm(model)
        total_loss, correct, seen = 0., 0, 0
        epoch_started = time.perf_counter()
        for images, labels, _ in train_batches:
            labels = labels.to("cuda")
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                logits = model(normalize(images, augment=True))
                loss = criterion(logits.float(), labels)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5.)
            scaler.step(optimizer)
            scaler.update()
            total_loss += float(loss.detach()) * len(labels)
            correct += int((logits.argmax(1) == labels).sum())
            seen += len(labels)
        torch.cuda.synchronize()
        entry = {"epoch": epoch, "train_count": seen, "train_correct": correct, "train_loss": total_loss / seen,
                 "seconds": time.perf_counter() - epoch_started}
        if dev_batches:
            predictions, dev_loss = predict(model, dev_batches)
            dev_correct = sum(r["truth"] == r["predicted"] for r in predictions)
            entry.update(dev_count=len(predictions), dev_correct=dev_correct, dev_loss=dev_loss)
            key = (dev_correct, -dev_loss)
            if key > best:
                best, best_epoch = key, epoch
                torch.save(model.state_dict(), folder / "best.pt")
        history.append(entry)
        with (folder / "epochs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        print(json.dumps({"mode": mode, "task": task, "stage": stage, **entry}), flush=True)
    if stage == "final":
        best_epoch = epochs
        torch.save(model.state_dict(), folder / "model.pt")
    result = {"mode": mode, "task": task, "stage": stage, "classes": classes, "epochs": epochs,
              "best_epoch": best_epoch, "initial_tensor_sha256": initial_sha, "fit_count": len(fitting),
              "dev_count": len(dev), "seconds": time.perf_counter() - started, "history": history,
              "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(), "training_seed": SEED,
              "base_sha256": sha(work.parent / "ab-assets" / "resnet18-f37072fd.pth")}
    save(folder / "complete.json", result)
    del model, optimizer, scaler
    torch.cuda.empty_cache()
    return result
