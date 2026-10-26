"""Whole placard localization using training-only reference silhouettes."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from .data import load_photo, read, save, sha

VERSION = "whole-panel-template-v2"
# These three reference photographs belong to inner_fit, not any validation set.
REFERENCES = {"0039": (429, 326, 636, 535), "0001": (438, 445, 643, 680), "1185": (421, 705, 605, 885)}
SCALES = [.65, .75, .85, .95, 1.05, 1.15, 1.25, 1.35]
ANGLES = [-6, 0, 6]
THRESHOLD = .45


def reference_assets(work, rows):
    folder = work / "references"
    folder.mkdir()
    records = []
    for code, box in REFERENCES.items():
        row = next(r for r in rows if r["code"] == code)
        if row["role"] != "inner_fit":
            raise ValueError("Detector reference must come only from inner fitting data")
        photo = load_photo(row["source"])
        panel = photo.crop(box)
        panel.save(folder / (code + ".png"))
        mask = np.full((panel.height, panel.width), 255, np.uint8)
        # Ignore the printed size and inner face; locate with hair/ears/outer
        # silhouette and surrounding light pixels, independent of size labels.
        mask[round(panel.height * .35):round(panel.height * .94),
             round(panel.width * .18):round(panel.width * .82)] = 0
        Image.fromarray(mask).save(folder / (code + "-mask.png"))
        records.append({"code": code, "role": row["role"], "box": box, "source_sha256": row["sha256"],
                        "template_sha256": sha(folder / (code + ".png")), "mask_sha256": sha(folder / (code + "-mask.png"))})
    save(folder / "references.json", {"records": records, "version": VERSION, "angles": ANGLES,
                                      "scales": SCALES, "threshold": THRESHOLD})


class WholePanelLocator:
    def __init__(self, work):
        cv2.setNumThreads(1)
        self.templates = []
        folder = work / "references"
        for item in read(folder / "references.json")["records"]:
            with Image.open(folder / (item["code"] + ".png")) as p:
                original = p.convert("L")
            with Image.open(folder / (item["code"] + "-mask.png")) as p:
                mask = p.convert("L")
            for scale in SCALES:
                dimensions = (round(original.width / 3 * scale), round(original.height / 3 * scale))
                for angle in ANGLES:
                    p = original.resize(dimensions).rotate(angle, expand=True, fillcolor=255)
                    m = mask.resize(dimensions, Image.Resampling.NEAREST).rotate(angle, expand=True, fillcolor=0)
                    template = np.where(np.asarray(p) < 160, 0, 255).astype(np.uint8)
                    self.templates.append((item["code"], template, np.asarray(m, dtype=np.uint8)))

    def locate(self, photo):
        search_photo = photo.copy()
        search_photo.thumbnail((360, 640))
        width, height = search_photo.size
        gray = np.where(np.asarray(search_photo.convert("L")) < 160, 0, 255).astype(np.uint8)
        x0, y0, x1, y1 = round(width * .20), round(height * .04), round(width * .80), round(height * .60)
        search = gray[y0:y1, x0:x1]
        integral = cv2.integral((search > 0).astype(np.uint8))
        best = (-1., None, None)
        for code, template, mask in self.templates:
            if template.shape[0] > search.shape[0] or template.shape[1] > search.shape[1]:
                continue
            response = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED, mask=mask)
            response = np.nan_to_num(response, nan=-1, posinf=-1, neginf=-1)
            # A dark jacket can resemble a hair silhouette. The inner face
            # must contain substantial light paper, even with printed digits.
            th, tw = template.shape
            ax, ay, bx, by = round(tw * .18), round(th * .35), round(tw * .82), round(th * .94)
            rh, rw = response.shape
            paper = (integral[by:by + rh, bx:bx + rw] - integral[ay:ay + rh, bx:bx + rw]
                     - integral[by:by + rh, ax:ax + rw] + integral[ay:ay + rh, ax:ax + rw]) / ((bx - ax) * (by - ay))
            response[paper < .45] = -1
            _, value, _, point = cv2.minMaxLoc(response)
            if value > best[0]:
                best = (value, (point[0] + x0, point[1] + y0, template.shape[1], template.shape[0]), code)
        score, match, reference = best
        if match is None:
            return {"valid": False, "score": -1, "box": None, "reference": None}
        x, y, w, h = match
        # A margin retains bun, side hair, ears and chin; never derive this box
        # from OCR glyph positions or from the retired model's head crop.
        sx, sy = photo.width / width, photo.height / height
        box = (max(0, round((x - .05 * w) * sx)), max(0, round((y - .05 * h) * sy)),
               min(photo.width, round((x + 1.05 * w) * sx)), min(photo.height, round((y + 1.05 * h) * sy)))
        return {"valid": score >= THRESHOLD, "score": float(score), "box": box, "reference": reference}
