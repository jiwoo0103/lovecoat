from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .crops import VERSION, detect, load_front

SEED = 20261024
CLASSES = ["adult_male", "adult_female", "child"]
PROJECT = Path(__file__).resolve().parents[1]
TESS = PROJECT / "vendor/tesseract"
LSTM_DIR = "lstm-data-psm13"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def shape_label(row):
    return "child" if row["age"] == "child" else "adult_" + row["gender"]


def inner_split(records):
    """Use only original training groups for tuning; leave all outer validation out."""
    training = [r for r in records if r["split"] == "train"]
    groups = defaultdict(list)
    for row in training:
        groups[row["split_group"]].append(row)
    remaining = Counter(r["raw_text"] for r in training)
    shapes = Counter(shape_label(r) for r in training)
    ordered = sorted(groups)
    random.Random(SEED).shuffle(ordered)
    development = set()
    for group in ordered:
        chosen = groups[group]
        raw = Counter(r["raw_text"] for r in chosen)
        heads = Counter(shape_label(r) for r in chosen)
        if len(development) >= round(len(training) * .20):
            break
        if any(remaining[k] <= n for k, n in raw.items()) or any(shapes[k] <= n for k, n in heads.items()):
            continue
        development.update(r["code"] for r in chosen)
        remaining.subtract(raw)
        shapes.subtract(heads)
    result = {r["code"]: ("outer_validation" if r["split"] == "validation"
                           else "inner_dev" if r["code"] in development else "inner_fit") for r in records}
    group_roles = defaultdict(set)
    for r in records:
        group_roles[r["split_group"]].add(result[r["code"]])
    if any(len(v) > 1 for v in group_roles.values()):
        raise ValueError("A duplicate candidate group crosses fit/dev/validation")
    if set(remaining) != {r["raw_text"] for r in training} or min(remaining.values()) < 1:
        raise ValueError("Inner fitting lost a size class")
    return result


def run(command, log=None, timeout=120):
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([str(x) for x in command], capture_output=True, env=env, timeout=timeout,
                            cwd=PROJECT, creationflags=flags)
    if log:
        log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-3000:])
    return result


def prepare(dataset: Path, work: Path):
    records = rows(dataset / "labels.jsonl")
    roles = inner_split(records)
    output = work / "prepared-final"
    output.mkdir(exist_ok=False)
    for name in ("text", "shape"):
        (output / name).mkdir()

    def one(row):
        path = (dataset / row["front_path"]).resolve(strict=True)
        if dataset.resolve() not in path.parents or path.name != "img_0_0_0.jpg":
            raise ValueError("Only original front_path photos may enter training")
        if sha(path) != row["front_sha256"]:
            raise ValueError("Front photo changed")
        value = detect(load_front(path))
        record = {**row, "role": roles[row["code"]], "shape_class": shape_label(row),
                  "detected": value is not None, "detector_version": VERSION}
        if value:
            value.text.save(output / "text" / (row["code"] + ".png"))
            value.shape.save(output / "shape" / (row["code"] + ".png"))
            record.update({"face_box": value.face_box, "text_box": value.text_box})
        return record

    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared = list(pool.map(one, records))
    save_json(output / "manifest.json", prepared)
    save_json(output / "provenance.json", {"dataset": str(dataset.resolve()),
              "labels_sha256": sha(dataset / "labels.jsonl"), "photos_manifest_sha256": sha(dataset / "files_sha256.csv"),
              "roles": dict(Counter(roles.values())), "detector_version": VERSION,
              "originals_modified": False, "front_only": True, "outer_validation_used_for_tuning": False})
    print(json.dumps({"prepared": len(prepared), "detected": sum(r["detected"] for r in prepared),
                      "roles": dict(Counter(roles.values()))}), flush=True)


def prepared_rows(work):
    return json.loads((work / "prepared-final/manifest.json").read_text(encoding="utf-8"))


def make_lstmf(work):
    output = work / LSTM_DIR
    output.mkdir(exist_ok=False)
    (output / "logs").mkdir()
    tasks = []
    for row in prepared_rows(work):
        if not row["detected"]:
            continue
        angles = [-5, 0, 5] if row["split"] == "train" else [0]
        for angle in angles:
            tasks.append((row, angle))

    def one(task):
        row, angle = task
        base = output / f"{row['code']}_{angle + 5:02}"
        with Image.open(work / "prepared-final/text" / (row["code"] + ".png")) as image:
            image = image.rotate(angle, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC)
            w, h = image.size
            image.save(base.with_suffix(".tif"), dpi=(300, 300))
        base.with_suffix(".gt.txt").write_text(row["raw_text"] + "\n", encoding="utf-8")
        text = "".join(f"{char} 0 0 {w} {h} 0\n" for char in row["raw_text"]) + f"\t 0 0 {w} {h} 0\n"
        base.with_suffix(".box").write_text(text, encoding="utf-8")
        run([TESS / "tesseract.exe", base.with_suffix(".tif"), base, "--tessdata-dir", work / "base",
             "-l", "eng", "--psm", "13", TESS / "tessdata/configs/lstm.train"], output / "logs" / (base.name + ".txt"))
        if not base.with_suffix(".lstmf").is_file():
            raise ValueError(f"Training example was not generated: {base}")
        return {"code": row["code"], "role": row["role"], "angle": angle,
                "lstmf": str(base.with_suffix(".lstmf").resolve())}

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, item in enumerate(pool.map(one, tasks), 1):
            results.append(item)
            if index % 400 == 0:
                print(json.dumps({"lstmf_generated": index, "total": len(tasks)}), flush=True)
    for role in ("inner_fit", "inner_dev", "outer_validation"):
        selected = [r for r in results if r["role"] == role and (role == "inner_fit" or r["angle"] == 0)]
        (output / f"{role}.txt").write_text("\n".join(r["lstmf"] for r in selected) + "\n", encoding="utf-8", newline="\n")
    selected = [r for r in results if r["role"] != "outer_validation"]
    (output / "all_training.txt").write_text("\n".join(r["lstmf"] for r in selected) + "\n", encoding="utf-8", newline="\n")
    save_json(output / "examples.json", results)
    run([TESS / "combine_tessdata.exe", "-e", work / "base/eng.traineddata", work / "base/eng.lstm"],
        work / "base/extract.log")
    print(json.dumps({"lstmf_complete": len(results)}), flush=True)


def start_ocr_training(work, name, iterations, final=False):
    output = work / name
    output.mkdir(exist_ok=False)
    command = [TESS / "lstmtraining.exe", "--continue_from", work / "base/eng.lstm",
               "--traineddata", work / "base/eng.traineddata", "--model_output", output / "coat",
               "--train_listfile", work / LSTM_DIR / ("all_training.txt" if final else "inner_fit.txt"),
               "--max_iterations", str(iterations), "--target_error_rate", "-1" if final else "0.01",
               "--perfect_sample_delay", "0", "--debug_interval", "0", "--max_image_MB", "512"]
    if not final:
        command += ["--eval_listfile", work / LSTM_DIR / "inner_dev.txt"]
    save_json(output / "command.json", {"args": [str(x) for x in command], "max_iterations": iterations,
                                        "outer_validation_in_training": False})
    # Keep streaming logs on disk, so the desktop agent can report progress.
    with (output / "training.log").open("xb") as stream:
        result = subprocess.run([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT,
                                env={**os.environ, "OMP_THREAD_LIMIT": "2"}, cwd=PROJECT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"OCR training failed. Inspect {output / 'training.log'}")
    checkpoints = list(output.glob("*_checkpoint"))
    if not checkpoints:
        raise RuntimeError("Training completed without a checkpoint")
    target = output / "coat_size.traineddata"
    run([TESS / "lstmtraining.exe", "--stop_training", "--continue_from", output / "coat_checkpoint",
         "--traineddata", work / "base/eng.traineddata", "--model_output", target, "--convert_to_int"], output / "export.log")
    save_json(output / "complete.json", {"traineddata": str(target), "sha256": sha(target)})


def hog(image):
    image = np.asarray(image.convert("L").resize((64, 64)), dtype=np.float32) / 255
    gy, gx = np.gradient(image)
    magnitude = np.hypot(gx, gy)
    orientation = np.mod(np.arctan2(gy, gx), np.pi) * 9 / np.pi
    lower = np.floor(orientation).astype(int) % 9
    fraction = orientation - np.floor(orientation)
    yy, xx = np.indices((64, 64))
    hist = np.zeros((8, 8, 9), np.float32)
    np.add.at(hist, (yy // 8, xx // 8, lower), magnitude * (1 - fraction))
    np.add.at(hist, (yy // 8, xx // 8, (lower + 1) % 9), magnitude * fraction)
    blocks = []
    for y in range(7):
        for x in range(7):
            block = hist[y:y + 2, x:x + 2].ravel()
            block = np.minimum(block / np.sqrt(np.sum(block ** 2) + 1e-6), .2)
            blocks.append(block / np.sqrt(np.sum(block ** 2) + 1e-6))
    return np.concatenate(blocks)


def shape_examples(work, records, augment):
    features, targets = [], []
    for row in records:
        if not row["detected"]:
            continue
        with Image.open(work / "prepared-final/shape" / (row["code"] + ".png")) as image:
            angles = [-8, -4, 0, 4, 8] if augment else [0]
            for angle in angles:
                variant = image.rotate(angle, fillcolor=255, resample=Image.Resampling.BICUBIC)
                features.append(hog(variant))
                targets.append(CLASSES.index(row["shape_class"]))
    return np.array(features, dtype=np.float32), np.array(targets, dtype=np.int32)


def fit_shape(features, targets, regularization):
    rng = np.random.default_rng(SEED)
    weights = rng.normal(0, .005, (features.shape[1], 3)).astype(np.float32)
    bias = np.zeros(3, np.float32)
    counts = np.bincount(targets, minlength=3)
    sample_weights = (len(targets) / (3 * counts[targets])).astype(np.float32)
    truth = np.eye(3, dtype=np.float32)[targets] * .97 + .01
    moments = [np.zeros_like(weights), np.zeros_like(bias)]
    squares = [np.zeros_like(weights), np.zeros_like(bias)]
    for step in range(1, 401):
        scores = features @ weights + bias
        scores -= scores.max(axis=1, keepdims=True)
        probability = np.exp(scores)
        probability /= probability.sum(axis=1, keepdims=True)
        residual = (probability - truth) * sample_weights[:, None] / len(targets)
        gradients = [features.T @ residual + regularization * weights, residual.sum(axis=0)]
        for i, parameter in enumerate((weights, bias)):
            moments[i] = .9 * moments[i] + .1 * gradients[i]
            squares[i] = .999 * squares[i] + .001 * gradients[i] ** 2
            parameter -= (.03 * (1 - .8 * step / 400)) * (moments[i] / (1 - .9 ** step)) / (
                np.sqrt(squares[i] / (1 - .999 ** step)) + 1e-8)
    return weights, bias


def train_shape(work):
    cv2.setRNGSeed(SEED)
    cv2.setNumThreads(2)
    records = prepared_rows(work)
    output = work / "shape"
    output.mkdir(exist_ok=True)
    if (output / "head_shape.npz").exists():
        raise FileExistsError(output / "head_shape.npz")
    x, y = shape_examples(work, [r for r in records if r["role"] == "inner_fit"], True)
    dx, dy = shape_examples(work, [r for r in records if r["role"] == "inner_dev"], False)
    candidates = []
    for regularization in (.0001, .001, .01):
        weights, bias = fit_shape(x, y, regularization)
        prediction = (dx @ weights + bias).argmax(axis=1)
        candidates.append({"regularization": regularization, "inner_dev_correct": int(np.sum(prediction == dy)), "inner_dev_count": len(dy)})
    best = max(candidates, key=lambda r: (r["inner_dev_correct"], r["regularization"]))
    all_x, all_y = shape_examples(work, [r for r in records if r["split"] == "train"], True)
    started = time.perf_counter()
    weights, bias = fit_shape(all_x, all_y, best["regularization"])
    np.savez_compressed(output / "head_shape.npz", weights=weights, bias=bias)
    save_json(output / "training.json", {"algorithm": "HOG + class-balanced softmax regression with Adam", "classes": CLASSES,
              "regularization": best["regularization"], "tuning": candidates, "training_garments": len(all_y) // 5,
              "augmented_training_examples": len(all_y), "final_fit_seconds": time.perf_counter() - started,
              "features_exclude_size_text_and_clothing": True, "outer_validation_used_for_tuning": False,
              "child_gender": "provisional user rule; not a predicted model class", "opencv_version": cv2.__version__})
    print(json.dumps({"shape_model_complete": True, "internal_selection": best}), flush=True)


def ocr(path: Path, tessdata: Path, language: str):
    result = run([TESS / "tesseract.exe", path, "stdout", "--tessdata-dir", tessdata, "-l", language,
                  "--psm", "13", "--oem", "1", "-c", "tessedit_char_whitelist=0123456789XSML",
                  "-c", "load_system_dawg=0", "-c", "load_freq_dawg=0"], timeout=30)
    return "".join(result.stdout.decode("utf-8", errors="replace").split()).upper()


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def measure(truth, predicted):
    count = len(truth)
    correct = sum(a == b for a, b in zip(truth, predicted, strict=True))
    per_class = {label: {"count": truth.count(label),
                        "correct": sum(a == label and a == b for a, b in zip(truth, predicted, strict=True))}
                 for label in sorted(set(truth))}
    return {"count": count, "correct": correct, "accuracy": correct / count,
            "macro_recall": float(np.mean([r["correct"] / r["count"] for r in per_class.values()])),
            "per_class": per_class,
            "confusion": {label: dict(Counter(b for a, b in zip(truth, predicted, strict=True) if a == label))
                          for label in sorted(set(truth))}}


def evaluate(work, ocr_dir, role="outer_validation", name="evaluation"):
    output = work / name
    output.mkdir(exist_ok=False)
    records = [r for r in prepared_rows(work) if r["role"] == role]
    head = np.load(work / "shape/head_shape.npz", allow_pickle=False) if (work / "shape/head_shape.npz").exists() else None

    def one(row):
        values = {"code": row["code"], "expected_raw": row["raw_text"], "expected_shape": row["shape_class"],
                  "split": row["split"], "detected": row["detected"]}
        for label, directory, lang in [("existing", TESS / "tessdata", "eng"), ("base_float", work / "base", "eng"),
                                       ("trained", ocr_dir, "coat_size")]:
            values[label] = ocr(work / "prepared-final/text" / (row["code"] + ".png"), directory, lang) if row["detected"] else ""
        if head and row["detected"]:
            with Image.open(work / "prepared-final/shape" / (row["code"] + ".png")) as image:
                feature = hog(image).reshape(1, -1).astype(np.float32)
            values["predicted_shape"] = CLASSES[int((feature @ head["weights"] + head["bias"]).argmax())]
        else:
            values["predicted_shape"] = ""
        return values

    results = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i, row in enumerate(pool.map(one, records), 1):
            results.append(row)
            if i % 40 == 0:
                print(json.dumps({"evaluation": name, "processed": i, "total": len(records)}), flush=True)
    report = {"role": role, "count": len(records), "detected": sum(r["detected"] for r in records),
              "seconds": time.perf_counter() - started, "detector": VERSION, "predictions": results}
    truth = [r["expected_raw"] for r in results]
    for engine in ("existing", "base_float", "trained"):
        predicted = [r[engine] for r in results]
        report[engine] = measure(truth, predicted)
        report[engine]["character_error_rate"] = sum(edit_distance(a, b) for a, b in zip(truth, predicted, strict=True)) / sum(map(len, truth))
    report["shape"] = measure([r["expected_shape"] for r in results], [r["predicted_shape"] for r in results])
    report["joint_correct"] = sum(r["expected_raw"] == r["trained"] and r["expected_shape"] == r["predicted_shape"] for r in results)
    save_json(output / "report.json", report)
    with (output / "predictions.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps({"evaluation": name, "text_correct": report["trained"]["correct"],
                      "head_correct": report["shape"]["correct"], "count": len(records)}), flush=True)
    return report
