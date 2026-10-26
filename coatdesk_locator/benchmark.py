from __future__ import annotations

import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

import argparse
import json
import time
import winreg
from pathlib import Path

import numpy as np
import torch

from coatdesk_ab.benchmark import chosen_rows
from coatdesk_ab.data import load_photo, read, save
from coatdesk_ab.locator import WholePanelLocator
from .evaluate import CLASSIFIERS, OUTPUT, verify_sources
from .labels import PREVIOUS, WORK
from .pipeline import FrozenPanelClassifiers, PlacardLocator


def stats(values):
    return {"count": len(values), "mean_seconds": float(np.mean(values)),
            "median_seconds": float(np.median(values)), "p95_seconds": float(np.percentile(values, 95))}


def benchmark(work, output):
    torch.set_num_threads(4)
    assert not torch.cuda.is_available(), "CPU-only benchmark must not use CUDA"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
        cpu_name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    rows = [r for r in read(work / "box-labels.json")["records"] if r["role"] in ("outer_validation", "additional_test")]
    chosen = chosen_rows(rows)
    detector = PlacardLocator(output / "detector.pt", "cpu")
    old = WholePanelLocator(PREVIOUS)
    classifiers = FrozenPanelClassifiers(CLASSIFIERS, "cpu")
    gpu = {r["code"]: r for r in read(output / "predictions.json")}
    photo = load_photo(chosen[0]["source"])
    for _ in range(4):
        detector.locate(photo)
        classifiers.predict(photo.crop(chosen[0]["box"]))
    results = {"template": [], "ai": []}
    for index, row in enumerate(chosen):
        for mode in (("template", "ai") if index % 2 == 0 else ("ai", "template")):
            started = time.perf_counter()
            photo = load_photo(row["source"])
            decoded = time.perf_counter()
            if mode == "ai":
                location = detector.locate(photo)
            else:
                location = old.locate(photo)
                location["crop_box"] = location["box"]
            located = time.perf_counter()
            prediction = {"shape": "unresolved", "size": "unresolved"}
            if location["valid"]:
                prediction = classifiers.predict(photo.crop(location["crop_box"]))
            finished = time.perf_counter()
            results[mode].append({"code": row["code"], "decode_seconds": decoded - started,
                                  "locate_seconds": located - decoded, "classify_seconds": finished - located,
                                  "total_seconds": finished - started, "valid": location["valid"],
                                  "shape": prediction["shape"], "size": prediction["size"],
                                  "both_correct": prediction["shape"] == row["shape"] and prediction["size"] == row["size"],
                                  "matches_gpu": prediction["shape"] == gpu[row["code"]]["predicted_shape"] and prediction["size"] == gpu[row["code"]]["predicted_size"] if mode == "ai" else None})
        if (index + 1) % 8 == 0:
            print(json.dumps({"cpu_photos": index + 1, "total": len(chosen)}), flush=True)
    summaries = {mode: {"timings": {part: stats([r[part] for r in values]) for part in
                                   ("decode_seconds", "locate_seconds", "classify_seconds", "total_seconds")},
                       "valid": sum(r["valid"] for r in values), "both_correct": sum(r["both_correct"] for r in values),
                       "gpu_prediction_differences": [r["code"] for r in values if r["matches_gpu"] is False]}
                 for mode, values in results.items()}
    save(output / "cpu-benchmark.json", {"cpu": cpu_name, "gpu_used": False, "torch_threads": 4,
                                          "warmup": 4, "count": len(chosen), "summary": summaries, "rows": results,
                                          "includes": "JPEG read, localization, crop/resize, both frozen classifiers",
                                          "excludes": "Application/runtime/model startup; review-gallery writes",
                                          "scope": "Measured trained placard detector; not the earlier generic COCO timing estimate"})
    verify_sources(work)
    print(json.dumps({"cpu": cpu_name, "summary": summaries}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    benchmark(args.work, args.output)
