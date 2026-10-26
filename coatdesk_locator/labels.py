from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coatdesk_ab.data import load_photo, read, save, sha

WORK = Path(".local/panel-detector-20261025")
PREVIOUS = Path(".local/ab-work-20261024-r3")
PAGE_SIZE = 36


def face_refinement(photo, box):
    """Draft only: find bounded light-paper face near the template location."""
    gray = np.asarray(photo.convert("L"))
    binary = (gray > 175).astype(np.uint8)
    n, components, stats, centers = cv2.connectedComponentsWithStats(binary, 8)
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    candidates = []
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        px, py = centers[k]
        if not (80 <= w <= 330 and 60 <= h <= 300 and 3500 <= area <= 65000):
            continue
        if not (.60 <= w / h <= 2.1 and .20 < px / photo.width < .80 and .08 < py / photo.height < .65):
            continue
        distance = ((px - cx) / bw) ** 2 + ((py - cy) / bh) ** 2
        if distance < .8:
            candidates.append((distance, k))
    if not candidates:
        return list(box), None
    _, k = min(candidates)
    fx, fy, fw, fh, _ = stats[k]
    # Use connected dark outline around the bounded face, not printed glyphs.
    left, top = max(0, round(fx - .14 * fw)), max(0, round(fy - 1.0 * fw))
    right, bottom = min(photo.width, round(fx + 1.14 * fw)), min(photo.height, round(fy + fh + .025 * fw))
    face = (components[top:bottom, left:right] == k).astype(np.uint8)
    dark = (gray[top:bottom, left:right] < 145).astype(np.uint8)
    border = cv2.dilate(face, np.ones((7, 7), np.uint8)) - face
    dn, dc, ds, _ = cv2.connectedComponentsWithStats(dark, 8)
    choices = [(int(((dc == j) & (border > 0)).sum()), j) for j in range(1, dn)]
    if not choices or max(choices)[0] < 20:
        return list(box), [int(fx), int(fy), int(fx + fw), int(fy + fh)]
    _, j = max(choices)
    dx, dy, dw, dh, _ = ds[j]
    # Very small annotation margin includes anti-aliased outline pixels.
    refined = [max(0, left + int(dx) - 3), max(0, top + int(dy) - 3),
               min(photo.width, left + int(dx + dw) + 3), min(photo.height, top + int(dy + dh) + 3)]
    if refined[3] - refined[1] < 100 or refined[2] - refined[0] < 100:
        return list(box), None
    return refined, [int(fx), int(fy), int(fx + fw), int(fy + fh)]


def render_page(work, records, page):
    rows = records[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    canvas = Image.new("RGB", (1440, math.ceil(len(rows) / 6) * 272), "#edf2f6")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 15)
    for i, row in enumerate(rows):
        photo = load_photo(row["source"])
        box = row["box"]
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        # Substantial context exposes cut-off hair/bun and wrong-body crops.
        view = [max(0, round(x0 - .40 * w)), max(0, round(y0 - .35 * h)),
                min(photo.width, round(x1 + .40 * w)), min(photo.height, round(y1 + .30 * h))]
        image = photo.crop(view)
        ImageDraw.Draw(image).rectangle((x0 - view[0], y0 - view[1], x1 - view[0] - 1, y1 - view[1] - 1), outline="#e00025", width=3)
        image.thumbnail((234, 235))
        x, y = i % 6 * 240, i // 6 * 272
        draw.text((x + 3, y + 2), f'{row["code"]} {row["shape"].replace("adult_", "")} {row["role"].replace("outer_validation", "test").replace("additional_test", "extra").replace("inner_", "")}', fill="#172433", font=font)
        canvas.paste(image, (x + (240 - image.width) // 2, y + 28))
    path = work / "label-review" / f"page-{page:02}.jpg"
    canvas.save(path, quality=95)
    return path


def prepare(work):
    work.mkdir(parents=True)
    (work / "label-review").mkdir()
    old = read(PREVIOUS / "prepared.json")["records"]
    records = []
    for row in old:
        photo = load_photo(row["source"])
        if sha(row["source"]) != row["sha256"]:
            raise ValueError(f'Original changed: {row["code"]}')
        box, face = face_refinement(photo, row["location"]["box"])
        records.append(dict(row, draft_box=row["location"]["box"], box=box, face_box=face,
                            coordinate_size=list(photo.size), review_status="pending"))
    save(work / "draft-labels.json", {"records": records, "previous_prepared_sha256": sha(PREVIOUS / "prepared.json"),
                                     "coordinate_system": "EXIF-transposed, aspect-preserving 1080x1920 source preview pixels",
                                     "draft_method": "Existing template code, then bounded face/light paper and dark-outline draft; visual review required"})
    save(work / "source-lock.json", {"previous_code": {str(p): sha(p) for p in sorted(Path("coatdesk_ab").glob("*.py"))},
                                    "models": {str(p): sha(p) for p in sorted(Path("model-results/20261024-four-model-comparison/models").glob("*.pt"))},
                                    "labels": read(PREVIOUS / "manifest.json")["labels_sha256"]})
    for page in range(math.ceil(len(records) / PAGE_SIZE)):
        render_page(work, records, page)
    print(json.dumps({"drafts": len(records), "pages": math.ceil(len(records) / PAGE_SIZE),
                      "roles": dict(Counter(r["role"] for r in records)), "work": str(work)}))


def approve(work, pages, corrections):
    draft = read(work / "draft-labels.json")
    records = draft["records"]
    approved = read(work / "review-events.json") if (work / "review-events.json").exists() else []
    known = {p for event in approved for p in event["pages"]}
    if known & set(pages):
        raise ValueError("Page was already reviewed")
    selected = {r["code"] for p in pages for r in records[p * PAGE_SIZE:(p + 1) * PAGE_SIZE]}
    if set(corrections) - selected:
        raise ValueError("Correction outside selected review pages")
    event = {"pages": pages, "codes": sorted(selected), "page_sha256": {str(p): sha(work / "label-review" / f"page-{p:02}.jpg") for p in pages},
             "corrections": corrections, "reviewer": "Codex visual inspection; not independent operator verification"}
    approved.append(event)
    # These are new task-owned review metadata, not original labels.
    (work / "review-events.json").write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed_pages": len(known | set(pages)), "total": math.ceil(len(records) / PAGE_SIZE), "corrections": len(corrections)}))


def finalize(work):
    draft = read(work / "draft-labels.json")
    records = draft["records"]
    events = read(work / "review-events.json")
    pages = {p for event in events for p in event["pages"]}
    if pages != set(range(math.ceil(len(records) / PAGE_SIZE))):
        raise ValueError("Every label review page must be inspected first")
    corrections = {k: v for event in events for k, v in event["corrections"].items()}
    for row in records:
        if row["code"] in corrections:
            row["box"] = corrections[row["code"]]
        row["reviewed_box_before_margin"] = list(row["box"])
        x0, y0, x1, y1 = row["box"]
        dx, dy = (x1 - x0) * .02, (y1 - y0) * .02
        row["box"] = [max(0, math.floor(x0 - dx)), max(0, math.floor(y0 - dy)),
                      min(row["coordinate_size"][0], math.ceil(x1 + dx)),
                      min(row["coordinate_size"][1], math.ceil(y1 + dy))]
        x0, y0, x1, y1 = row["box"]
        if not (0 <= x0 < x1 <= row["coordinate_size"][0] and 0 <= y0 < y1 <= row["coordinate_size"][1]):
            raise ValueError("Invalid reviewed box")
        row["review_status"] = "codex_visually_reviewed"
    save(work / "box-labels.json", {**draft, "records": records, "review_events_sha256": sha(work / "review-events.json"),
                                   "corrections": corrections, "annotation_boundary_margin": .02,
                                   "independent_human_review": False})
    print(json.dumps({"frozen_box_labels": len(records), "manual_corrections": corrections}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "approve", "finalize"])
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--pages", default="")
    parser.add_argument("--corrections", default="{}")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.work)
    elif args.action == "approve":
        approve(args.work, [int(p) for p in args.pages.split(",")], json.loads(args.corrections))
    else:
        finalize(args.work)
