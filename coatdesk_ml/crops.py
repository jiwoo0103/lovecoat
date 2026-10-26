from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

VERSION = "placard-contours-glyphs-v5"


@dataclass
class Placard:
    text: Image.Image
    shape: Image.Image
    face_box: tuple[int, int, int, int]
    text_box: tuple[int, int, int, int]
    detection_score: float


def load_front(path: Path) -> Image.Image:
    with Image.open(path) as original:
        original.draft("RGB", (1080, 1920))
        return ImageOps.exif_transpose(original).convert("RGB")


def detect(image: Image.Image) -> Placard | None:
    """Label-independent face contour and glyph-row detection from a front photo."""
    image = image.copy()
    image.thumbnail((1080, 1920))
    width, height = image.size
    gray = np.array(image.convert("L"))
    x0, y0, x1, y1 = int(width * .26), int(height * .06), int(width * .76), int(height * .56)
    search = gray[y0:y1, x0:x1]
    candidates = []
    for threshold in (110, 150, 185, 210):
        binary = (search < threshold).astype(np.uint8) * 255
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, hierarchy = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        for index, contour in enumerate(contours):
            if hierarchy[0, index, 3] < 0:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            if not (.055 * width < w < .30 * width and .025 * height < h < .18 * height
                    and .60 < w / h < 1.9 and area > .40 * w * h):
                continue
            fx, fy = x + x0, y + y0
            face = gray[fy:fy + h, fx:fx + w]
            ink = (face < 150).astype(np.uint8)
            ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            count, _, stats, centers = cv2.connectedComponentsWithStats(ink, 8)
            glyphs = []
            for i in range(1, count):
                gx, gy, gw, gh, pixels = stats[i]
                cx, cy = centers[i]
                if (.17 * h < gh < .72 * h and .025 * w < gw < .92 * w
                        and .07 * w < cx < .93 * w and .24 * h < cy < .88 * h
                        and gx > 2 and gy > 2 and gx + gw < w - 2 and gy + gh < h - 2
                        and pixels > .13 * gw * gh):
                    glyphs.append((int(gx), int(gy), int(gw), int(gh)))
            if not glyphs:
                continue
            anchor = min(glyphs, key=lambda b: abs((b[1] + b[3] / 2) / h - .55))
            glyphs = [b for b in glyphs if abs((b[1] + b[3] / 2) - (anchor[1] + anchor[3] / 2)) < anchor[3] * .40
                      and .50 < b[3] / anchor[3] < 1.8]
            left, top = min(b[0] for b in glyphs), min(b[1] for b in glyphs)
            right, bottom = max(b[0] + b[2] for b in glyphs), max(b[1] + b[3] for b in glyphs)
            score = (right - left) * (bottom - top) * (1 - min(.8, abs((fx + w / 2) / width - .5)))
            candidates.append((score, (fx, fy, fx + w, fy + h),
                               (fx + left, fy + top, fx + right, fy + bottom)))
    if not candidates:
        # Thin/broken face outlines need not stop recognition. Opening removes
        # narrow outlines while preserving the thick printed glyphs.
        binary = (search < 150).astype(np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        count, _, stats, centers = cv2.connectedComponentsWithStats(binary, 8)
        glyphs = []
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            cx, cy = centers[i] + (x0, y0)
            if (.012 * height < h < .055 * height and .01 * width < w < .13 * width
                    and .15 < w / h < 1.5 and .32 * width < cx < .68 * width
                    and .12 * height < cy < .51 * height and area > .30 * w * h):
                glyphs.append((int(x + x0), int(y + y0), int(w), int(h)))
        for anchor in glyphs:
            line = [b for b in glyphs if abs((b[1] + b[3] / 2) - (anchor[1] + anchor[3] / 2)) < anchor[3] * .45
                    and .55 < b[3] / anchor[3] < 1.6 and abs(b[0] - anchor[0]) < .26 * width]
            left, top = min(b[0] for b in line), min(b[1] for b in line)
            right, bottom = max(b[0] + b[2] for b in line), max(b[1] + b[3] for b in line)
            th, center = bottom - top, (left + right) / 2
            above = gray[max(0, round(top - 2.5 * th)):round(top - .2 * th),
                         max(0, round(center - 1.8 * th)):min(width, round(center + 1.8 * th))]
            if above.size == 0 or np.mean(above < 150) < .035:
                continue
            score = th * len(line) ** .5 / (1 + abs(center / width - .5))
            face = (round(center - 1.8 * th), round(top - .7 * th), round(center + 1.8 * th), round(bottom + .7 * th))
            candidates.append((score, face, (left, top, right, bottom)))
        if not candidates:
            return None
    score, face_box, text_box = max(candidates, key=lambda c: c[0])
    left, top, right, bottom = text_box
    # A printed '1' can touch the lower face outline/collar, joining a component
    # that face-interior filtering correctly rejected as too large. Inspect a
    # narrow glyph row to recover that digit; side hair starts at the row's top
    # edge and is excluded. The original vertical glyph span stays unchanged.
    th = bottom - top
    rx, ry = max(0, left - round(1.7 * th)), max(0, top - round(.2 * th))
    ex, ey = min(width, right + round(1.7 * th)), min(height, bottom + round(.1 * th))
    line_ink = cv2.morphologyEx((gray[ry:ey, rx:ex] < 150).astype(np.uint8), cv2.MORPH_OPEN,
                              np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(line_ink, 8)
    for x, y, w, h, area in stats[1:n]:
        if (.55 * th < h < 1.6 * th and .15 < w / h < 1.5 and area > .30 * w * h
                and x > 0 and y > 0 and x + w < line_ink.shape[1]):
            left, right = min(left, int(rx + x)), max(right, int(rx + x + w))
    text_box = (left, top, right, bottom)
    text = image.crop(text_box).convert("L")
    scale = 64 / text.height
    text = text.resize((max(1, round(text.width * scale)), 64), Image.Resampling.LANCZOS)
    text = ImageOps.expand(text, 16, fill=255)
    glyph_height = bottom - top
    center = (left + right) / 2
    # End above the detected glyph row: no size characters or garment pixels in
    # the head classifier's input. Fill out-of-photo padding with white.
    box = (round(center - 2.1 * glyph_height), round(top - 2.8 * glyph_height),
           round(center + 2.1 * glyph_height), round(top - .18 * glyph_height))
    shape = Image.new("L", (box[2] - box[0], box[3] - box[1]), 255)
    clipped = (max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3]))
    shape.paste(image.crop(clipped).convert("L"), (clipped[0] - box[0], clipped[1] - box[1]))
    shape = shape.resize((64, 64), Image.Resampling.LANCZOS)
    return Placard(text, shape, face_box, text_box, score)
