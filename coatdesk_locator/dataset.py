from __future__ import annotations

import random

import cv2
import numpy as np
import torch
from PIL import Image, ImageEnhance
from torch.utils.data import Dataset

from coatdesk_ab.data import load_photo, read, save, sha

FRAME = (360, 640)


def box_affine(box, matrix):
    x0, y0, x1, y1 = box
    corners = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], dtype=np.float32)
    points = corners @ matrix.T
    return np.array([points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()], dtype=np.float32)


def prepare_cache(work):
    rows = read(work / "box-labels.json")["records"]
    images = np.lib.format.open_memmap(work / "photos.npy", mode="w+", dtype=np.uint8,
                                       shape=(len(rows), 3, FRAME[1], FRAME[0]))
    for row in rows:
        if row["review_status"] != "codex_visually_reviewed" or sha(row["source"]) != row["sha256"]:
            raise ValueError("Missing review or original photo changed")
        photo = load_photo(row["source"])
        if list(photo.size) != row["coordinate_size"]:
            raise ValueError("Label coordinate system changed")
        image = photo.resize(FRAME, Image.Resampling.LANCZOS)
        images[row["index"]] = np.asarray(image).transpose(2, 0, 1)
    images.flush()
    save(work / "cache.json", {"frame": FRAME, "count": len(rows), "array_sha256": sha(work / "photos.npy"),
                              "box_labels_sha256": sha(work / "box-labels.json"), "whole_photo_retained": True})


class Placards(Dataset):
    def __init__(self, work, roles, augment=False):
        self.rows = [r for r in read(work / "box-labels.json")["records"] if r["role"] in roles]
        self.images = np.load(work / "photos.npy", mmap_mode="r")
        self.augment = augment

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        pixels = self.images[row["index"]].transpose(1, 2, 0).copy()
        width, height = row["coordinate_size"]
        box = np.array(row["box"], dtype=np.float32) * np.array([FRAME[0] / width, FRAME[1] / height] * 2, dtype=np.float32)
        if self.augment:
            matrix = cv2.getRotationMatrix2D((FRAME[0] / 2, FRAME[1] / 2), random.uniform(-7, 7), random.uniform(.78, 1.22))
            matrix[:, 2] += [random.uniform(-.06, .06) * FRAME[0], random.uniform(-.08, .08) * FRAME[1]]
            moved = box_affine(box, matrix)
            # Only accept augmentations retaining the entire labelled head.
            if 0 <= moved[0] < moved[2] <= FRAME[0] and 0 <= moved[1] < moved[3] <= FRAME[1]:
                pixels = cv2.warpAffine(pixels, matrix, FRAME, flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
                box = moved
            if random.random() < .5:
                pixels = np.ascontiguousarray(pixels[:, ::-1])
                box = np.array([FRAME[0] - box[2], box[1], FRAME[0] - box[0], box[3]], dtype=np.float32)
            image = ImageEnhance.Brightness(Image.fromarray(pixels)).enhance(random.uniform(.82, 1.18))
            pixels = np.asarray(ImageEnhance.Contrast(image).enhance(random.uniform(.90, 1.10)))
        image = torch.from_numpy(pixels.transpose(2, 0, 1).copy()).float() / 255
        # Only a placard box and class 1; age, gender and size never enter targets.
        target = {"boxes": torch.from_numpy(box.copy()).reshape(1, 4), "labels": torch.ones(1, dtype=torch.int64),
                  "image_id": torch.tensor(row["index"])}
        return image, target


def collate(batch):
    return tuple(zip(*batch, strict=True))
