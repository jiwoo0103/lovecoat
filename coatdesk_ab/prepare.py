from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .data import SIDE, letterbox, load_photo, read, save, sha
from .locator import WholePanelLocator


def prepare(work):
    manifest = read(work / "manifest.json")
    rows = manifest["rows"]
    locator = WholePanelLocator(work)
    for name in ("panel", "whole", "panel_photos", "contacts"):
        (work / name).mkdir()
    arrays = {mode: np.lib.format.open_memmap(work / mode / "images.npy", mode="w+", dtype=np.uint8,
                                             shape=(len(rows), 3, SIDE, SIDE)) for mode in ("panel", "whole")}

    def one(row):
        photo = load_photo(row["source"])
        started = time.perf_counter()
        location = locator.locate(photo)
        locate_seconds = time.perf_counter() - started
        panel = photo.crop(location["box"]) if location["box"] else Image.new("RGB", (256, 256), "white")
        panel.save(work / "panel_photos" / (row["code"] + ".png"))
        transforms = {}
        for mode, image in (("panel", panel), ("whole", photo)):
            square, transform = letterbox(image)
            arrays[mode][row["index"]] = np.asarray(square).transpose(2, 0, 1)
            transforms[mode] = transform
        return dict(row, location=location, locate_seconds=locate_seconds, transforms=transforms)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=5) as pool:
        prepared = []
        for count, record in enumerate(pool.map(one, rows), 1):
            prepared.append(record)
            if count % 100 == 0:
                print(json.dumps({"preparing": count, "total": len(rows), "seconds": round(time.perf_counter() - started, 1)}), flush=True)
    for array in arrays.values():
        array.flush()
    save(work / "prepared.json", {"records": prepared, "seconds": time.perf_counter() - started,
               "valid": sum(r["location"]["valid"] for r in prepared), "side": SIDE,
               "whole_image_operations": ["aspect-preserving resize", "white letterbox padding"],
               "whole_image_crop_operations": [], "classifier_predictions_generated": False})
    font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 17)
    # Inner-fit examples alone are used for pre-training detector QA. Held-out
    # contacts are available only for post-evaluation error inspection.
    fit = [r for r in prepared if r["role"] == "inner_fit"]
    chosen = sorted(fit, key=lambda r: r["location"]["score"])[:32]
    chosen += fit[::max(1, len(fit) // 32)][:32]
    for page in range(2):
        group = chosen[page * 32:page * 32 + 32]
        canvas = Image.new("RGB", (1280, 1120), "#e8eef4")
        draw = ImageDraw.Draw(canvas)
        for j, row in enumerate(group):
            x, y = j % 8 * 160, j // 8 * 280
            draw.text((x + 3, y + 3), f'{row["code"]} {row["location"]["score"]:.2f}', font=font, fill="black")
            with Image.open(work / "panel_photos" / (row["code"] + ".png")) as image:
                image.thumbnail((154, 245))
                canvas.paste(image, (x + (160 - image.width) // 2, y + 30))
        canvas.save(work / "contacts" / f"fit-locator-{page}.jpg", quality=94)
    print(json.dumps({"prepared": len(rows), "detector_valid": sum(r["location"]["valid"] for r in prepared),
                      "seconds": time.perf_counter() - started, "all_source_hashes_unchanged": all(sha(r["source"]) == r["sha256"] for r in rows)}), flush=True)
