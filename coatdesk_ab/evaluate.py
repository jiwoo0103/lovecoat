from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from .data import SHAPES, SIDE, SIZES, load_photo, read, save, sha
from .network import PhotoClassifier
from .training import configure, loader, predict


def measure(rows, truth, predicted):
    labels = sorted({r[truth] for r in rows})
    per_class = {label: {"count": sum(r[truth] == label for r in rows),
                         "correct": sum(r[truth] == label and r[predicted] == label for r in rows)} for label in labels}
    count = len(rows)
    correct = sum(r[truth] == r[predicted] for r in rows)
    return {"count": count, "correct": correct, "accuracy": correct / count,
            "macro_recall": float(np.mean([v["correct"] / v["count"] for v in per_class.values()])),
            "per_class": per_class,
            "confusion": {label: dict(Counter(r[predicted] for r in rows if r[truth] == label)) for label in labels}}


def summarize(rows):
    return {"shape": measure(rows, "truth_shape", "predicted_shape"), "size": measure(rows, "truth_size", "predicted_size"),
            "count": len(rows), "joint_correct": sum(r["shape_correct"] and r["size_correct"] for r in rows),
            "locator_valid": sum(r["locator_valid"] for r in rows),
            "errors": [r["code"] for r in rows if not r["shape_correct"] or not r["size_correct"]]}


def load_model(work, mode, task):
    model = PhotoClassifier(len(SHAPES if task == "shape" else SIZES)).cuda()
    model.load_state_dict(torch.load(work / "training" / f"{mode}-{task}" / "final/model.pt", map_location="cuda", weights_only=True))
    return model.eval()


def evaluate(work, output):
    configure()
    if not (work / "all-training-complete.json").is_file():
        raise ValueError("All four final models must be completed before opening held-out results")
    protocol = read(work / "train-protocol.json")
    for name, fingerprint in protocol["code_sha256"].items():
        if sha(name) != fingerprint:
            raise ValueError(f"Frozen training code changed: {name}")
    if sha(work / "manifest.json") != protocol["labels_manifest_sha256"] or sha(work / "prepared.json") != protocol["prepared_manifest_sha256"]:
        raise ValueError("Frozen labels or prepared inputs changed")
    records = read(work / "prepared.json")["records"]
    held = [r for r in records if r["role"] in ("outer_validation", "additional_test")]
    assert len(held) == 445
    output.mkdir(parents=True, exist_ok=False)
    (output / "models").mkdir()
    (output / "examples").mkdir()
    (output / "training-logs").mkdir()
    selection = read(work / "selections.json")
    summaries, predictions, timings = {}, {}, {}
    cards = {}
    for mode in ("panel", "whole"):
        by_task, gpu_seconds = {}, {}
        for task in ("shape", "size"):
            model = load_model(work, mode, task)
            batches = loader(work, mode, held, task)
            started = time.perf_counter()
            values, _ = predict(model, batches)
            gpu_seconds[task] = time.perf_counter() - started
            by_task[task] = {r["index"]: r for r in values}
            name = f"{mode}-{task}"
            source = work / "training" / name / "final/model.pt"
            shutil.copyfile(source, output / "models" / (name + ".pt"))
            history = read(work / "training" / name / "final/complete.json")
            cards[name] = {"input": mode, "task": task, "classes": SHAPES if task == "shape" else SIZES,
                           "model_sha256": sha(source), "initial_tensor_sha256": history["initial_tensor_sha256"],
                           "training_count": history["fit_count"], "epochs": history["epochs"],
                           "training_seconds": history["seconds"], "gpu_peak_allocated_bytes": history["gpu_peak_allocated_bytes"],
                           "base_sha256": protocol["base_sha256"], "retired_weights_used": False,
                           "architecture": "ResNet18 ImageNet backbone, stride 16, learned spatial-attention pooling, one independent classifier",
                           "size_scope": "17 observed raw labels; not open-vocabulary OCR", "input_side": SIDE,
                           "input_crop": "whole placard located by training-only silhouette templates" if mode == "panel" else "none; entire photograph retained with aspect-preserving resize and letterbox",
                           "probabilities": "uncalibrated softmax scores", "app_integrated": False}
            save(output / "models" / (name + ".json"), cards[name])
            for stage in ("inner", "final"):
                for filename in ("epochs.jsonl", "complete.json"):
                    shutil.copyfile(work / "training" / name / stage / filename,
                                    output / "training-logs" / f"{name}-{stage}-{filename}")
            del model
            torch.cuda.empty_cache()
        rows = []
        for row in held:
            shape = by_task["shape"][row["index"]]
            size = by_task["size"][row["index"]]
            valid = row["location"]["valid"] if mode == "panel" else True
            predicted_shape = SHAPES[shape["predicted"]] if valid else "localization_failed"
            predicted_size = SIZES[size["predicted"]] if valid else "localization_failed"
            rows.append({"code": row["code"], "role": row["role"], "truth_shape": row["shape"], "truth_size": row["size"],
                         "predicted_shape": predicted_shape, "predicted_size": predicted_size,
                         "shape_correct": predicted_shape == row["shape"], "size_correct": predicted_size == row["size"],
                         "shape_scores": dict(zip(SHAPES, shape["probabilities"], strict=True)),
                         "size_scores": dict(zip(SIZES, size["probabilities"], strict=True)),
                         "locator_valid": valid, "locator_score": row["location"]["score"] if mode == "panel" else None})
        predictions[mode] = rows
        summaries[mode] = {"all_445": summarize(rows),
                           "earlier_162": summarize([r for r in rows if r["role"] == "outer_validation"]),
                           "additional_283": summarize([r for r in rows if r["role"] == "additional_test"])}
        timings[mode] = {"cached_batch_inference_seconds": gpu_seconds, "batch_size": 12,
                         "note": "Includes prepared-input loading and GPU inference; excludes original JPEG decoding and localization"}
        save(output / (mode + "-predictions.json"), rows)
        with (output / (mode + "-predictions.csv")).open("x", encoding="utf-8-sig", newline="") as stream:
            flat = [{k: json.dumps(v) if isinstance(v, dict) else v for k, v in r.items()} for r in rows]
            writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
        print(json.dumps({"evaluated": mode, "shape_correct": summaries[mode]["all_445"]["shape"]["correct"],
                          "size_correct": summaries[mode]["all_445"]["size"]["correct"],
                          "both_correct": summaries[mode]["all_445"]["joint_correct"]}), flush=True)
    assert cards["panel-shape"]["initial_tensor_sha256"] == cards["whole-shape"]["initial_tensor_sha256"]
    assert cards["panel-size"]["initial_tensor_sha256"] == cards["whole-size"]["initial_tensor_sha256"]
    assert len({r["model_sha256"] for r in cards.values()}) == 4
    changed = [r["code"] for r in records if sha(r["source"]) != r["sha256"]]
    if changed:
        raise ValueError(f"Original photos changed: {changed}")
    for name, fingerprint in read(work / "manifest.json")["labels_sha256"].items():
        if sha(name) != fingerprint:
            raise ValueError("Original label file changed")
    save(output / "results.json", {"models": cards, "metrics": summaries, "timings": timings,
               "same_initial_weights_within_task": True, "separate_final_weights": 4,
               "source_front_hashes_unchanged": len(records), "training_protocol": protocol,
               "selected_epochs": selection, "evaluation_code_sha256": sha(__file__),
               "no_held_out_fitting_or_post_result_tuning": True})
    shutil.copyfile(work / "manifest.json", output / "dataset-manifest.json")
    shutil.copyfile(work / "train-protocol.json", output / "train-protocol.json")
    shutil.copyfile(work / "references/references.json", output / "locator-references.json")
    shutil.copytree(work / "references", output / "references")
    shutil.copyfile(work / "selections.json", output / "selected-epochs.json")
    shutil.copyfile(work.parent / "ab-assets/TORCHVISION_LICENSE", output / "TORCHVISION_LICENSE")
    shutil.copyfile(work.parent / "ab-assets/sources.json", output / "base-model-sources.json")
    write_gallery(work, output, held, predictions, summaries)


def write_gallery(work, output, held, predictions, summaries):
    by_mode = {mode: {r["code"]: r for r in rows} for mode, rows in predictions.items()}
    errors = {r["code"] for rows in predictions.values() for r in rows if not r["shape_correct"] or not r["size_correct"]}
    display = sorted(held, key=lambda r: (r["code"] not in errors, int(r["code"])))
    cards = []
    names = {"adult_male": "성인 남성", "adult_female": "성인 여성", "child": "아동", "localization_failed": "추출 실패"}
    for row in display:
        code = row["code"]
        panel_path, whole_path = f"examples/{code}-panel.jpg", f"examples/{code}-whole.jpg"
        photo = load_photo(row["source"], limit=540)
        photo.thumbnail((220, 390))
        photo.save(output / whole_path, quality=90)
        with Image.open(work / "panel_photos" / (code + ".png")) as p:
            panel = p.convert("RGB")
            panel.thumbnail((300, 340))
            panel.save(output / panel_path, quality=93)
        text = []
        for mode, title in (("panel", "판넬"), ("whole", "원본")):
            pred = by_mode[mode][code]
            correct = pred["shape_correct"] and pred["size_correct"]
            text.append(f'<p class="{"ok" if correct else "wrong"}">{title}: {names[pred["predicted_shape"]]} / {html.escape(pred["predicted_size"])} · {"정답" if correct else "오류"}</p>')
        cards.append(f'<article class="{"error" if code in errors else ""}"><b>{code} · 정답 {names[row["shape"]]} / {row["size"]}</b><div class="images"><img src="{whole_path}" loading="lazy" title="원본 축소"><img src="{panel_path}" loading="lazy" title="자동 추출 판넬"></div>{"".join(text)}</article>')
    table = '<table><tr><th>입력 방식</th><th>성별·연령</th><th>사이즈</th><th>둘 다 정답</th></tr>'
    for mode, title in (("panel", "판넬 전체"), ("whole", "원본 전체")):
        r = summaries[mode]["all_445"]
        table += f'<tr><td>{title}</td><td>{r["shape"]["correct"]}/445 ({r["shape"]["accuracy"]:.1%})</td><td>{r["size"]["correct"]}/445 ({r["size"]["accuracy"]:.1%})</td><td>{r["joint_correct"]}/445 ({r["joint_correct"]/445:.1%})</td></tr>'
    table += '</table>'
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>새 모델 4개 비교</title><style>body{font:17px system-ui;background:#edf2f7;color:#173047;margin:24px}table{border-collapse:collapse;background:white}td,th{padding:12px;border:1px solid #b5c5d4}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(370px,1fr));gap:18px}article{background:white;border:2px solid #ccd7e0;border-radius:12px;padding:14px}article.error{border-color:#c62828}.images{display:flex;gap:12px;height:270px;margin:10px 0}.images img{width:48%;object-fit:contain}.wrong{color:#b22020}.ok{color:#17683a}p{line-height:1.5}</style><h1>새 모델 4개 비교</h1><p>판넬 전체/원본 전체 × 성별·연령/사이즈 = 별도로 학습한 4개 모델. 학습 645장, 검증 445장. 원본 방식은 자르지 않고 비율을 유지해 축소·여백 처리했습니다.</p>' + table + '<p>사이즈는 기존 17개 표기 중 선택하며, 새로운 표기를 읽는 OCR은 아닙니다. 아동 남녀 구분을 학습하지 않았습니다. 오류가 있는 사진을 먼저 표시합니다. 왼쪽은 원본 축소, 오른쪽은 실제 자동 추출 판넬입니다.</p><div class="grid">' + ''.join(cards) + '</div></html>'
    (output / "비교보기.html").write_text(page, encoding="utf-8")
    # Error contact sheets retain actual auto-cropped inputs for visual audit.
    font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 16)
    error_rows = [r for r in held if r["code"] in errors]
    for page_index, start in enumerate(range(0, len(error_rows), 24)):
        group = error_rows[start:start + 24]
        canvas = Image.new("RGB", (1200, ((len(group) + 5) // 6) * 260), "#e9eef3")
        draw = ImageDraw.Draw(canvas)
        for j, row in enumerate(group):
            x, y = j % 6 * 200, j // 6 * 260
            code = row["code"]
            a, b = by_mode["panel"][code], by_mode["whole"][code]
            draw.text((x + 3, y + 3), f'{code} 정답 {row["size"]}', font=font, fill="black")
            with Image.open(work / "panel_photos" / (code + ".png")) as image:
                image.thumbnail((192, 188))
                canvas.paste(image, (x + (200 - image.width) // 2, y + 27))
            draw.text((x + 3, y + 216), f'A {a["predicted_size"]} / B {b["predicted_size"]}', font=font, fill="black")
            draw.text((x + 3, y + 237), f'A형태 {"O" if a["shape_correct"] else "X"} B형태 {"O" if b["shape_correct"] else "X"}', font=font, fill="black")
        canvas.save(output / f"errors-{page_index:02}.jpg", quality=94)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=Path(".local/ab-work-20261024-r2"))
    parser.add_argument("--output", type=Path, default=Path("model-results/20261024-four-model-comparison"))
    args = parser.parse_args()
    evaluate(args.work, args.output)
