"""Isolated, resumable four-model experiment; never writes production resources."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import posixpath
import sys
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / ".local/robust-four-models-20261102"
BOOK = Path("C:/Users/playj/Desktop/2026외투리스트.xlsx")
EXPECTED_BOOK = "9823c52785dfc32ae3e07911d5b640434f4085d59e9ae903e826bc35f3ac221e"
PHOTOS = Path("C:/Users/playj/Desktop/Download")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def event(stage, **values):
    data = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage, **values}
    save(WORK / "status.json", data)
    with (WORK / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")
    print(json.dumps(data, ensure_ascii=False), flush=True)


def workbook_rows():
    if sha(BOOK) != EXPECTED_BOOK:
        raise ValueError("Source workbook changed: do not mix experiment versions")
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    records = []
    with ZipFile(BOOK) as z:
        strings = ["".join(x.itertext()) for x in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        relations = {x.get("Id"): x.get("Target") for x in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
            target = relations[sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            for row in ET.fromstring(z.read(member)).findall("m:sheetData/m:row", ns):
                values = {}
                for cell in row:
                    value, inline = cell.find("m:v", ns), cell.find("m:is", ns)
                    text = (
                        strings[int(value.text)]
                        if cell.get("t") == "s" and value is not None
                        else "".join(inline.itertext())
                        if inline is not None
                        else value.text
                        if value is not None
                        else ""
                    )
                    values["".join(filter(str.isalpha, cell.get("r")))] = text
                if int(row.get("r")) >= 3 and values.get("B", "").isdigit() and values.get("C"):
                    code = values["C"].zfill(4)
                    records.append(
                        {
                            "code": code,
                            "source": str(PHOTOS / ("0_" + code) / "img_0_0_0.jpg"),
                            "sheet": sheet.get("name"),
                            "row": int(row.get("r")),
                            **{
                                key: values[col]
                                for key, col in [("category", "E"), ("gender", "G"), ("age", "H"), ("size", "I")]
                            },
                        }
                    )
    if len(records) != 3596 or len({r["code"] for r in records}) != len(records):
        raise ValueError("Unexpected workbook rows")
    return sorted(records, key=lambda r: int(r["code"]))


def inspect_photo(row):
    from PIL import Image, ImageOps

    source = Path(row["source"])
    with Image.open(source) as image:
        original = image.size
        image.draft("RGB", (540, 960))
        image = ImageOps.exif_transpose(image).convert("RGB")
        w, h = image.size
        body = image.crop((int(w * 0.12), int(h * 0.50), int(w * 0.88), int(h * 0.85))).convert("L")
        pixels = list(body.resize((9, 8)).getdata())
        value = sum(int(pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x) for y in range(8) for x in range(8))
        body_sha = hashlib.sha256(body.resize((256, 256)).tobytes()).hexdigest()
    return {
        **row,
        "sha256": sha(source),
        "width": original[0],
        "height": original[1],
        "body_dhash": f"{value:016x}",
        "body_sha256": body_sha,
    }


def prepare():
    WORK.mkdir(parents=True, exist_ok=True)
    event("inventory_started")
    rows = workbook_rows()
    protected = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / "resources/ai").rglob("*") if p.is_file()}
    protected[str(BOOK)] = sha(BOOK)
    if (WORK / "protected.json").exists():
        if read(WORK / "protected.json") != protected:
            raise ValueError("Protected original changed")
    else:
        save(WORK / "protected.json", protected)
    inventory = read(WORK / "inventory.json") if (WORK / "inventory.json").exists() else []
    done = {r["code"]: r for r in inventory}
    pending = [r for r in rows if r["code"] not in done]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for row in pool.map(inspect_photo, pending):
            done[row["code"]] = row
            if len(done) % 100 == 0:
                save(WORK / "inventory.json", sorted(done.values(), key=lambda r: int(r["code"])))
                event("inventory", completed=len(done), total=len(rows))
    rows = sorted(done.values(), key=lambda r: int(r["code"]))
    save(WORK / "inventory.json", rows)
    known = read(ROOT / ".local/panel-detector-v2-20261026/training-labels.json") + read(
        ROOT / ".local/panel-detector-v2-20261026/regression-labels.json"
    )
    panels = {r["code"]: r for r in known}
    category = {
        r["code"]: r
        for r in read(ROOT / "model-results/20261026-category-label-review/approved-dataset-20261027-01/dataset.json")[
            "records"
        ]
    }
    annotations = {}
    for row in rows:
        code = row["code"]
        gold, cat = panels.get(code), category.get(code)
        if gold and gold["sha256"] != row["sha256"]:
            raise ValueError("Prior annotation photo mismatch")
        annotations[code] = {
            "box": gold["box"] if gold else None,
            "coordinate_size": gold.get("coordinate_size") if gold else None,
            "box_status": "prior_visually_reviewed" if gold else "missing",
            "raw_size": row["size"] if row["age"] == "성인" else gold["size"] if gold else None,
            "raw_size_status": "excel_adult" if row["age"] == "성인" else "prior_visual" if gold else "missing",
            "sleeves": cat["targets"]["sleeves_present"] if cat else None,
            "length": cat["targets"]["full_buttocks_coverage"] if cat else None,
            "attribute_status": "prior_confirmed" if cat else "missing",
            "old_category": cat["training_label"] if cat else None,
        }
    if not (WORK / "annotations.json").exists():
        save(WORK / "annotations.json", annotations)
    pairs = []
    hashes = [(r["code"], int(r["body_dhash"], 16)) for r in rows]
    for i, (code, value) in enumerate(hashes):
        distances = sorted(((value ^ other).bit_count(), previous) for previous, other in hashes[:i])
        if distances and distances[0][0] <= 6:
            pairs.append({"code": code, "previous": distances[0][1], "distance": distances[0][0]})
    save(WORK / "nearest-duplicates.json", pairs)
    save(
        WORK / "input-summary.json",
        {
            "count": len(rows),
            "categories": dict(Counter(r["category"] for r in rows)),
            "box_known": sum(a["box"] is not None for a in annotations.values()),
            "raw_missing": sum(a["raw_size"] is None for a in annotations.values()),
            "attribute_known": sum(a["attribute_status"] == "prior_confirmed" for a in annotations.values()),
            "unique_files": len({r["sha256"] for r in rows}),
            "unique_body_pixels": len({r["body_sha256"] for r in rows}),
            "nearest_distance_counts": dict(Counter(p["distance"] for p in pairs)),
        },
    )
    event("inventory_complete", records=len(rows), duplicate_candidates=len(pairs))


def contacts(kind="duplicates"):
    from PIL import Image, ImageDraw, ImageFont
    from coatdesk_ab.data import load_photo

    rows = {r["code"]: r for r in read(WORK / "inventory.json")}
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 15)
    if kind == "duplicates":
        items = read(WORK / "nearest-duplicates.json")
        cols, per_page, cw, ch = 6, 24, 280, 286
    else:
        raise ValueError(kind)
    folder = WORK / "review" / kind
    folder.mkdir(parents=True, exist_ok=True)
    for page in range((len(items) + per_page - 1) // per_page):
        target = folder / f"{page:03d}.jpg"
        if target.exists():
            continue
        canvas = Image.new("RGB", (cols * cw, ((per_page + cols - 1) // cols) * ch), "white")
        draw = ImageDraw.Draw(canvas)
        batch = items[page * per_page : (page + 1) * per_page]
        for i, pair in enumerate(batch):
            x, y = (i % cols) * cw, (i // cols) * ch
            draw.text(
                (x + 2, y + 2),
                f"{page * per_page + i}: {pair['previous']}/{pair['code']} d{pair['distance']}",
                fill="black",
                font=font,
            )
            for j, code in enumerate((pair["previous"], pair["code"])):
                photo = load_photo(rows[code]["source"], 360)
                photo.thumbnail((138, 258))
                canvas.paste(photo, (x + j * 140 + (138 - photo.width) // 2, y + 25))
            draw.line((x, y + ch - 1, x + cw, y + ch - 1), fill="#999999")
        canvas.save(target, quality=93)
        save(folder / f"{page:03d}.json", batch)
        if page % 10 == 0:
            event("duplicate_contact_sheets", completed=page + 1, total=(len(items) + per_page - 1) // per_page)
    event("duplicate_contacts_complete", pages=(len(items) + per_page - 1) // per_page)


def draft_boxes():
    import numpy as np
    import torch
    from PIL import Image
    from coatdesk_ab.data import load_photo
    from coatdesk_locator_v2.experiment import build_v2
    from coatdesk_locator.model import choose_detection

    torch.set_num_threads(4)
    rows = read(WORK / "inventory.json")
    annotations = read(WORK / "annotations.json")
    path = WORK / "box-drafts.json"
    drafts = read(path) if path.exists() else {}
    pending = [r for r in rows if annotations[r["code"]]["box"] is None and r["code"] not in drafts]
    model = build_v2().cuda().eval()
    model.load_state_dict(torch.load(ROOT / "resources/ai/detector.pt", map_location="cpu", weights_only=True))
    with torch.inference_mode():
        for start in range(0, len(pending), 24):
            batch = pending[start : start + 24]
            photos = [load_photo(r["source"]) for r in batch]
            inputs = [
                torch.from_numpy(np.asarray(p.resize((448, 448), Image.Resampling.LANCZOS)).transpose(2, 0, 1).copy())
                .float()
                .cuda()
                / 255
                for p in photos
            ]
            predictions = model(inputs)
            for row, photo, prediction in zip(batch, photos, predictions, strict=True):
                loc = choose_detection(prediction, 448, 448)
                box = loc["box"]
                if box is not None:
                    box = [round(v * (photo.width if i % 2 == 0 else photo.height) / 448) for i, v in enumerate(box)]
                drafts[row["code"]] = {
                    "box": box,
                    "coordinate_size": list(photo.size),
                    "score": loc["score"],
                    "status": "unreviewed_prediction",
                }
            save(path, drafts)
            event("box_drafts", completed=len(drafts), total=sum(a["box"] is None for a in annotations.values()))
    event("box_drafts_complete", total=len(drafts))


class UnionFind:
    def __init__(self, codes):
        self.parents = {c: c for c in codes}

    def root(self, code):
        while self.parents[code] != code:
            self.parents[code] = self.parents[self.parents[code]]
            code = self.parents[code]
        return code

    def join(self, a, b):
        a, b = self.root(a), self.root(b)
        self.parents[max(a, b)] = min(a, b)


def deduplicate():
    import cv2
    import numpy as np
    from coatdesk_ab.data import load_photo

    rows = read(WORK / "inventory.json")
    annotations = read(WORK / "annotations.json")
    decisions = read(WORK / "duplicate-review.json")
    pairs = read(WORK / "nearest-duplicates.json")
    if set(decisions) != {str(i) for i in range(len(pairs))}:
        raise ValueError("Every duplicate candidate needs a reviewed decision")
    uf = UnionFind(r["code"] for r in rows)
    for pair in decisions.values():
        if pair["decision"] == "same":
            uf.join(pair["code"], pair["previous"])
    if (WORK / "confirmed-series.json").exists():
        for series in read(WORK / "confirmed-series.json"):
            for code in series["codes"][1:]:
                uf.join(series["codes"][0], code)
    groups = defaultdict(list)
    for r in rows:
        groups[uf.root(r["code"])].append(r)
    # A reviewed whole-garment identity is not inferred from its panel label.
    panels, categories, excluded = [], [], []
    drafts = read(WORK / "box-drafts.json")
    for group, members in groups.items():
        if len(members) > 1:
            for r in members:
                a = annotations[r["code"]]
                loc = a if a["box"] else drafts[r["code"]]
                photo = load_photo(r["source"], 540)
                scale = photo.width / loc["coordinate_size"][0]
                box = [round(v * scale) for v in loc["box"]]
                crop = np.asarray(photo.crop(box).convert("L"))
                r["sharpness"] = float(cv2.Laplacian(crop, cv2.CV_64F).var())
        else:
            members[0]["sharpness"] = 0.0
        order = sorted(members, key=lambda r: (-r["sharpness"], -r["width"] * r["height"], int(r["code"])))
        categories.append(order[0]["code"])
        seen = set()
        for r in order:
            a = annotations[r["code"]]
            key = (r["age"], r["gender"] if r["age"] == "성인" else "child", a["raw_size"])
            # Missing numeric labels may not be treated as identical panels.
            if a["raw_size"] is None:
                key += (r["code"],)
            if key not in seen:
                panels.append(r["code"])
                seen.add(key)
            else:
                excluded.append(
                    {"code": r["code"], "group": group, "reason": "reviewed_repeated_garment_and_same_panel_label"}
                )
    result = {
        "method": "visually_confirmed_pairs_only; hashes_are_candidates_not_truth",
        "groups": {g: [r["code"] for r in members] for g, members in groups.items()},
        "panel_codes": sorted(panels, key=int),
        "category_codes": sorted(categories, key=int),
        "excluded": excluded,
    }
    save(WORK / "deduplication.json", result)
    event(
        "deduplication_complete",
        unique_garment_groups=len(groups),
        panel_records=len(panels),
        category_records=len(categories),
        excluded_repeated=len(excluded),
    )


def annotation_contacts():
    from PIL import Image, ImageDraw, ImageFont
    from coatdesk_ab.data import load_photo

    rows = {r["code"]: r for r in read(WORK / "inventory.json")}
    a = read(WORK / "annotations.json")
    drafts = read(WORK / "box-drafts.json")
    dedup = read(WORK / "deduplication.json")
    selected = sorted(set(dedup["panel_codes"]) | set(dedup["category_codes"]), key=int)
    # Review novel attributes and every new detector box on representative photos.
    items = [c for c in selected if a[c]["box"] is None or a[c]["attribute_status"] != "prior_confirmed"]
    folder = WORK / "review/annotations"
    folder.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    for page in range((len(items) + 39) // 40):
        target = folder / f"{page:03d}.jpg"
        if target.exists():
            continue
        canvas = Image.new("RGB", (8 * 200, 5 * 380), "white")
        draw = ImageDraw.Draw(canvas)
        codes = items[page * 40 : (page + 1) * 40]
        for i, code in enumerate(codes):
            x, y = i % 8 * 200, i // 8 * 380
            row = rows[code]
            loc = a[code] if a[code]["box"] else drafts[code]
            photo = load_photo(row["source"], 540)
            photo.thumbnail((198, 350))
            scale = photo.width / loc["coordinate_size"][0]
            box = [round(v * scale) for v in loc["box"]]
            ImageDraw.Draw(photo).rectangle(box, outline="#00bb33", width=2)
            canvas.paste(photo, (x + (198 - photo.width) // 2, y + 26))
            draw.text((x + 3, y + 3), code, font=font, fill="black")
            draw.line((x, y + 379, x + 199, y + 379), fill="#999999")
        canvas.save(target, quality=94)
        save(folder / f"{page:03d}.json", codes)
    # Independent large panel crops for transcription, no model size prediction.
    missing = [c for c in selected if a[c]["raw_size"] is None]
    folder = WORK / "review/raw-size"
    folder.mkdir(parents=True, exist_ok=True)
    for page in range((len(missing) + 39) // 40):
        canvas = Image.new("RGB", (8 * 200, 5 * 230), "white")
        draw = ImageDraw.Draw(canvas)
        codes = missing[page * 40 : (page + 1) * 40]
        for i, code in enumerate(codes):
            x, y = i % 8 * 200, i // 8 * 230
            loc = a[code] if a[code]["box"] else drafts[code]
            photo = load_photo(rows[code]["source"])
            box = loc["box"]
            dx = (box[2] - box[0]) * 0.12
            dy = (box[3] - box[1]) * 0.12
            crop = photo.crop((box[0] - dx, box[1] - dy, box[2] + dx, box[3] + dy))
            crop.thumbnail((196, 200))
            canvas.paste(crop, (x + (200 - crop.width) // 2, y + 26))
            draw.text((x + 3, y + 3), code, font=font, fill="black")
        canvas.save(folder / f"{page:03d}.jpg", quality=95)
        save(folder / f"{page:03d}.json", codes)
    event(
        "annotation_contacts_complete",
        annotation_records=len(items),
        annotation_pages=(len(items) + 39) // 40,
        raw_records=len(missing),
    )


def record_annotation_review(
    pages, *, long=(), vest=(), long_vest=(), boundary=(), excluded=(), unclear_sleeves=(), box_fixes=None
):
    """Apply explicit visual decisions supplied by the reviewing operator, never predictions."""
    annotations = read(WORK / "annotations.json")
    drafts = read(WORK / "box-drafts.json")
    path = WORK / "annotation-review.json"
    audit = read(path) if path.exists() else {}
    long, vest, long_vest, boundary, excluded = map(set, (long, vest, long_vest, boundary, excluded))
    unclear_sleeves = set(unclear_sleeves)
    box_fixes = box_fixes or {}
    codes = []
    for page in pages:
        for code in read(WORK / "review/annotations" / f"{page:03d}.json"):
            codes.append(code)
            targets = (
                [0, 1]
                if code in long_vest
                else [0, 0]
                if code in vest
                else [1, 1]
                if code in long
                else [1, None]
                if code in boundary
                else [1, 0]
            )
            if code in unclear_sleeves:
                targets[0] = None
            a = annotations[code]
            record = {
                "page": page,
                "reviewer": "codex_visual",
                "targets": targets,
                "box_checked": True,
                "excluded_category": code in excluded,
                "evidence": "Full front photo and overlaid head box; sleeves and hem visually inspected independently of category name.",
            }
            if a["box"] is None:
                draft = drafts[code]
                w, h = draft["coordinate_size"]
                box = box_fixes.get(code, draft["box"])
                if not box:
                    raise ValueError("No box to review")
                # Explicitly inspected enclosure plus a small annotation margin.
                a.update(
                    box=[max(0, box[0] - 5), max(0, box[1] - 5), min(w, box[2] + 5), min(h, box[3] + 5)],
                    coordinate_size=[w, h],
                    box_status="codex_visually_reviewed_draft_20261102",
                )
            if a["attribute_status"] != "prior_confirmed":
                a.update(sleeves=targets[0], length=targets[1], attribute_status="codex_visual_20261102")
            if code in excluded:
                a["category_excluded"] = "hem_clipped_or_uninterpretable"
            audit[code] = record
    if not (long | vest | long_vest | boundary | excluded | unclear_sleeves | set(box_fixes)) <= set(codes):
        raise ValueError("A supplied decision is not on the specified pages")
    save(WORK / "annotations.json", annotations)
    save(path, audit)
    event("annotation_review_saved", pages=list(pages), reviewed_total=len(audit))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "contacts", "draft-boxes", "deduplicate", "annotation-contacts"])
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "contacts":
        contacts()
    elif args.command == "draft-boxes":
        draft_boxes()
    elif args.command == "deduplicate":
        deduplicate()
    elif args.command == "annotation-contacts":
        annotation_contacts()


if __name__ == "__main__":
    main()
