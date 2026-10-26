from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

from .data import SEED, SHAPES, SIZES, letterbox, load_photo, read, save
from .evaluate import load_model
from .locator import WholePanelLocator
from .training import configure


def tensor(image, device):
    square, _ = letterbox(image)
    pixels = np.asarray(square).transpose(2, 0, 1).copy()
    value = torch.from_numpy(pixels).unsqueeze(0).to(device).float() / 255
    return (value - value.new_tensor([.485, .456, .406])[None, :, None, None]) / value.new_tensor([.229, .224, .225])[None, :, None, None]


@torch.inference_mode()
def pair(models, image, device):
    value = tensor(image, device)
    if device == "cuda":
        with torch.autocast("cuda", dtype=torch.float16):
            logits = [m(value).float() for m in models]
        torch.cuda.synchronize()
    else:
        logits = [m(value) for m in models]
    return {"shape": SHAPES[int(logits[0].argmax())], "size": SIZES[int(logits[1].argmax())]}


def chosen_rows(rows):
    rng = random.Random(SEED)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    chosen, sizes, shapes = [], set(), set()
    for row in shuffled:
        if row["size"] not in sizes or row["shape"] not in shapes:
            chosen.append(row)
            sizes.add(row["size"])
            shapes.add(row["shape"])
    for row in shuffled:
        if len(chosen) >= 32:
            break
        if row not in chosen:
            chosen.append(row)
    return sorted(chosen, key=lambda r: int(r["code"]))


def benchmark(work, output):
    configure()
    held = [r for r in read(work / "prepared.json")["records"] if r["role"] in ("outer_validation", "additional_test")]
    chosen = chosen_rows(held)
    locator = WholePanelLocator(work)
    cache_predictions = {mode: {r["code"]: r for r in read(output / (mode + "-predictions.json"))} for mode in ("panel", "whole")}
    measurements, diagnostics = {}, {}
    (output / "diagnostic-inputs").mkdir()
    for device in ("cuda", "cpu"):
        measurements[device] = {}
        for mode in ("panel", "whole"):
            models = [load_model(work, mode, task).to(device).eval() for task in ("shape", "size")]
            warm = Image.new("RGB", (512, 512), "white")
            pair(models, warm, device)
            pair(models, warm, device)
            times, outputs, mismatch = [], [], []
            diagnostic_rows = []
            for row in chosen:
                started = time.perf_counter()
                photo = load_photo(row["source"])
                location = locator.locate(photo) if mode == "panel" else None
                image = photo.crop(location["box"]) if mode == "panel" else photo
                prediction = pair(models, image, device)
                elapsed = time.perf_counter() - started
                times.append(elapsed)
                previous = cache_predictions[mode][row["code"]]
                if prediction["shape"] != previous["predicted_shape"] or prediction["size"] != previous["predicted_size"]:
                    mismatch.append(row["code"])
                outputs.append({"code": row["code"], "seconds": elapsed, "prediction": prediction})
                if device != "cuda":
                    continue
                location = location or row["location"]
                x0, y0, x1, y1 = location["box"]
                w, h = x1 - x0, y1 - y0
                relative = {"0039": (.13, .40, .87, .77), "0001": (.13, .49, .87, .82), "1185": (.13, .39, .87, .83)}[location["reference"]]
                face_mask = (round(x0 + relative[0] * w), round(y0 + relative[1] * h),
                             round(x0 + relative[2] * w), round(y0 + relative[3] * h))
                masked = photo.copy()
                ImageDraw.Draw(masked).rectangle(face_mask, fill="white")
                background_removed = Image.new("RGB", photo.size, "white")
                background_removed.paste(photo.crop(location["box"]), (x0, y0))
                plate_removed = photo.copy()
                ImageDraw.Draw(plate_removed).rectangle(location["box"], fill="white")
                variants = {}
                for name, modified in (("central_face_masked", masked), ("background_masked", background_removed), ("plate_masked", plate_removed)):
                    candidate = modified.crop(location["box"]) if mode == "panel" else modified
                    variants[name] = pair(models, candidate, "cuda")
                    if mode == "whole" and len(diagnostic_rows) < 6:
                        preview, _ = letterbox(modified)
                        preview.save(output / "diagnostic-inputs" / f'{row["code"]}-{name}.jpg', quality=93)
                diagnostic_rows.append({"code": row["code"], "truth_shape": row["shape"], "truth_size": row["size"],
                                        "original": prediction, "variants": variants, "mask_box": face_mask})
            measurements[device][mode] = {"count": len(times), "mean_seconds": float(np.mean(times)),
                                         "median_seconds": float(np.median(times)), "p95_seconds": float(np.percentile(times, 95)),
                                         "cached_argmax_differences": mismatch, "rows": outputs,
                                         "timing_includes": ["read/decode original JPEG", "whole-panel localization" if mode == "panel" else "no localization", "resize/letterbox", "two model forward passes"],
                                         "timing_excludes": ["model/runtime startup", "diagnostic interventions"],
                                         "precision": "float16 AMP" if device == "cuda" else "float32"}
            if device == "cuda":
                summaries = {}
                for variant in ("original", "central_face_masked", "background_masked", "plate_masked"):
                    def value(r):
                        return r["original"] if variant == "original" else r["variants"][variant]
                    summaries[variant] = {"count": len(diagnostic_rows),
                                          "shape_correct": sum(value(r)["shape"] == r["truth_shape"] for r in diagnostic_rows),
                                          "size_correct": sum(value(r)["size"] == r["truth_size"] for r in diagnostic_rows)}
                diagnostics[mode] = {"summary": summaries, "rows": diagnostic_rows}
            print(json.dumps({"benchmarked": device + "/" + mode, "mean_seconds": measurements[device][mode]["mean_seconds"],
                              "mismatches": mismatch}), flush=True)
            del models
            torch.cuda.empty_cache()
    save(output / "benchmark.json", measurements)
    save(output / "diagnostics.json", {"count": len(chosen), "modes": diagnostics,
               "purpose": "Post-training stress checks only; never used to change models or choose checkpoints",
               "mask_note": "Central face rectangle is a geometry-based intervention, not a verified pixel mask of every glyph; may also cover nearby outline pixels",
               "input_note": "Whole-mode diagnostic images retain the original frame; only specified pixels are painted white"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=Path(".local/ab-work-20261024-r2"))
    parser.add_argument("--output", type=Path, default=Path("model-results/20261024-four-model-comparison"))
    args = parser.parse_args()
    benchmark(args.work, args.output)
