from __future__ import annotations

import argparse
import csv
import html
import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coatdesk_ab.data import load_photo, read, save, sha
from coatdesk_ab.locator import WholePanelLocator
from .labels import PREVIOUS, WORK
from .model import overlap
from .pipeline import FrozenPanelClassifiers, PlacardLocator
from .train import configure, freeze_protocol

OUTPUT = Path("model-results/20261025-panel-detector")
CLASSIFIERS = Path("model-results/20261024-four-model-comparison/models")
EXAMPLES = ["1345", "3037", "1281", "1912", "0009", "0012", "1248", "1432", "1188", "1527", "1531", "1719"]


def verify_sources(work, check_photos=True):
    locked = read(work / "source-lock.json")
    for group in ("previous_code", "models", "labels"):
        if any(sha(path) != fingerprint for path, fingerprint in locked[group].items()):
            raise ValueError(f"Pre-existing {group} changed")
    rows = read(work / "box-labels.json")["records"]
    if check_photos and any(sha(r["source"]) != r["sha256"] for r in rows):
        raise ValueError("Original photograph changed")
    return locked


def summarize(rows):
    return {"count": len(rows), "located": sum(r["location"]["valid"] for r in rows),
            "iou_50": sum(r["location"]["valid"] and r["iou"] >= .50 for r in rows),
            "iou_75": sum(r["location"]["valid"] and r["iou"] >= .75 for r in rows),
            "mean_iou": float(np.mean([r["iou"] for r in rows])),
            "reference_box_coverage_99": sum(r["location"]["valid"] and r["coverage"] >= .99 for r in rows),
            "shape_correct": sum(r["shape_correct"] for r in rows), "size_correct": sum(r["size_correct"] for r in rows),
            "both_correct": sum(r["shape_correct"] and r["size_correct"] for r in rows),
            "errors": [r["code"] for r in rows if not (r["shape_correct"] and r["size_correct"])],
            "crop_review_candidates": [r["code"] for r in rows if not r["location"]["valid"] or r["coverage"] < .99]}


def evaluate(work, output):
    configure()
    protocol = freeze_protocol(work)
    complete = read(work / "training/final/complete.json")
    verify_sources(work)
    output.mkdir(parents=True)
    (output / "crops").mkdir()
    (output / "review-pages").mkdir()
    rows = [r for r in read(work / "box-labels.json")["records"] if r["role"] in ("outer_validation", "additional_test")]
    assert len(rows) == 445
    detector = PlacardLocator(work / "training/final/model.pt", "cuda")
    classifiers = FrozenPanelClassifiers(CLASSIFIERS, "cuda")
    template = WholePanelLocator(PREVIOUS)
    predictions = []
    for index, row in enumerate(rows, 1):
        photo = load_photo(row["source"])
        location = detector.locate(photo)
        iou, _ = overlap(location["box"], row["box"])
        _, coverage = overlap(location["crop_box"], row["box"])
        prediction = {"shape": "unresolved", "size": "unresolved", "shape_scores": {}, "size_scores": {}}
        if location["valid"]:
            crop = photo.crop(location["crop_box"])
            crop.save(output / "crops" / (row["code"] + ".png"))
            prediction = classifiers.predict(crop)
        old_location = row["location"]
        _, template_coverage = overlap(old_location["box"], row["box"])
        old_iou, _ = overlap(old_location["box"], row["box"])
        predictions.append({"code": row["code"], "role": row["role"], "source": row["source"],
                            "truth_shape": row["shape"], "truth_size": row["size"], "reference_box": row["box"],
                            "location": location, "iou": iou, "coverage": coverage,
                            "template_iou": old_iou, "template_coverage": template_coverage,
                            "predicted_shape": prediction["shape"], "predicted_size": prediction["size"],
                            "shape_correct": prediction["shape"] == row["shape"], "size_correct": prediction["size"] == row["size"],
                            "shape_scores": prediction["shape_scores"], "size_scores": prediction["size_scores"]})
        if index % 100 == 0:
            print(json.dumps({"evaluated_held_out": index, "total": len(rows)}), flush=True)
    metrics = {"all_445": summarize(predictions),
               "earlier_162": summarize([r for r in predictions if r["role"] == "outer_validation"]),
               "additional_283": summarize([r for r in predictions if r["role"] == "additional_test"])}
    save(output / "predictions.json", predictions)
    with (output / "predictions.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        flat = [{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()} for r in predictions]
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    old_metrics = read(Path("model-results/20261024-four-model-comparison/results.json"))["metrics"]["panel"]
    card = {"architecture": protocol["architecture"], "classes": ["background", "whole_placard"],
            "weights_sha256": sha(work / "training/final/model.pt"), "fit_count": complete["fit_count"],
            "epochs": complete["epochs"], "input": protocol["detector_input"], "score_threshold": protocol["score_threshold"],
            "crop_margin": protocol["crop_margin"], "anchor_scale_min": .10, "anchor_scale_max": .70,
            "classifier_weights_sha256": {str(p): sha(p) for p in sorted(CLASSIFIERS.glob("panel-*.pt"))},
            "frozen_classifiers": True, "app_integrated": False, "torch": protocol["torch"]}
    save(output / "model-card.json", card)
    save(output / "results.json", {"metrics": metrics, "previous_template_classifier_metrics": old_metrics,
                                   "model": card, "training": complete, "training_protocol": protocol,
                                   "template_box_reference_coverage_99": sum(r["template_coverage"] >= .99 for r in predictions),
                                   "previous_model_and_label_integrity": verify_sources(work),
                                   "source_front_hashes_unchanged": 1090, "post_held_out_model_tuning": False,
                                   "evaluation_code_sha256": sha(__file__)})
    shutil.copyfile(work / "training/final/model.pt", output / "detector.pt")
    for filename in ("box-labels.json", "review-events.json", "train-protocol.json", "source-lock.json"):
        shutil.copyfile(work / filename, output / filename)
    shutil.copytree(work / "label-review", output / "label-review")
    shutil.copytree(work / "training", output / "training-logs", ignore=shutil.ignore_patterns("*.pt", "*.tmp"))
    shutil.copyfile(Path(".local/ab-assets/TORCHVISION_LICENSE"), output / "TORCHVISION_LICENSE")
    shutil.copyfile(Path(".local/ab-assets/ssdlite320-source.json"), output / "initialization-source.json")
    write_visuals(output, predictions, metrics)
    # Retain a direct source-photo inference path for the CPU benchmark.
    del detector, classifiers, template
    print(json.dumps({"final_metrics": metrics}), flush=True)


def write_visuals(output, predictions, metrics):
    font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 16)
    for page in range(math.ceil(len(predictions) / 36)):
        group = predictions[page * 36:(page + 1) * 36]
        canvas = Image.new("RGB", (1440, math.ceil(len(group) / 6) * 272), "#edf2f6")
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(group):
            photo = load_photo(row["source"])
            box = row["location"]["crop_box"] or row["reference_box"]
            gt = row["reference_box"]
            x0, y0, x1, y1 = min(box[0], gt[0]), min(box[1], gt[1]), max(box[2], gt[2]), max(box[3], gt[3])
            w, h = x1 - x0, y1 - y0
            view = [max(0, round(x0 - .25 * w)), max(0, round(y0 - .25 * h)),
                    min(photo.width, round(x1 + .25 * w)), min(photo.height, round(y1 + .25 * h))]
            image = photo.crop(view)
            if row["location"]["crop_box"]:
                ImageDraw.Draw(image).rectangle([box[0] - view[0], box[1] - view[1], box[2] - view[0] - 1, box[3] - view[1] - 1], outline="#006aff", width=3)
            image.thumbnail((234, 235))
            x, y = i % 6 * 240, i // 6 * 272
            draw.text((x + 3, y + 2), f'{row["code"]} {row["predicted_size"]} C{row["coverage"]:.2f} {row["location"]["score"]:.2f}', font=font, fill="#172433")
            canvas.paste(image, (x + (240 - image.width) // 2, y + 28))
        canvas.save(output / "review-pages" / f"page-{page:02}.jpg", quality=95)
    by_code = {r["code"]: r for r in predictions}
    canvas = Image.new("RGB", (1200, 960), "#edf2f6")
    draw = ImageDraw.Draw(canvas)
    names = {"adult_male": "성인 남성", "adult_female": "성인 여성", "child": "아동", "unresolved": "확인 필요"}
    for i, code in enumerate(EXAMPLES):
        row = by_code[code]
        x, y = i % 4 * 300, i // 4 * 320
        draw.text((x + 8, y + 5), f'{code} · {names[row["predicted_shape"]]} · {row["predicted_size"]}', font=font, fill="#172433")
        path = output / "crops" / (code + ".png")
        if path.exists():
            with Image.open(path) as image:
                image.thumbnail((280, 278))
                canvas.paste(image, (x + (300 - image.width) // 2, y + 34))
    canvas.save(output / "판넬_AI_예시.jpg", quality=96)
    cards = []
    ordered = sorted(predictions, key=lambda r: (r["shape_correct"] and r["size_correct"], r["coverage"] >= .99, int(r["code"])))
    for row in ordered:
        photo = load_photo(row["source"], limit=540)
        photo.thumbnail((200, 356))
        (output / "original-thumbnails").mkdir(exist_ok=True)
        photo.save(output / "original-thumbnails" / (row["code"] + ".jpg"), quality=90)
        good = row["shape_correct"] and row["size_correct"]
        crop = f'<img src="crops/{row["code"]}.png" loading="lazy">' if row["location"]["valid"] else "위치 검출 보류"
        cards.append(f'<article class="{"ok" if good else "error"}"><b>{row["code"]} · 정답 {names[row["truth_shape"]]}/{row["truth_size"]}</b><div class="photos"><img src="original-thumbnails/{row["code"]}.jpg" loading="lazy">{crop}</div><p>예측 {names[row["predicted_shape"]]}/{html.escape(row["predicted_size"])} · {"정답" if good else "오류"}</p><small>위치 점수 {row["location"]["score"]:.3f} · 기준 박스 포함 {row["coverage"]:.1%}</small></article>')
    m = metrics["all_445"]
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>판넬 위치 AI 결과</title><style>body{font:17px system-ui;background:#edf2f6;color:#173047;margin:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px}article{border:2px solid #b4c7d7;border-radius:12px;background:white;padding:14px}.error{border-color:#c42c32}.photos{display:flex;height:280px;gap:12px;margin-top:12px}.photos img{width:48%;object-fit:contain}p{line-height:1.5}</style><h1>판넬 위치 AI 학습 결과</h1>'
    page += f'<p>학습 645장 · 평가 445장. 성별·사이즈 모델은 변경하지 않고 AI가 자른 판넬 전체만 넣었습니다. 성별·연령 {m["shape_correct"]}/445, 사이즈 {m["size_correct"]}/445, 둘 다 {m["both_correct"]}/445.</p><p>왼쪽은 원본 축소, 오른쪽은 실제 AI 추출 결과입니다. 오류와 기준 박스 포함률이 낮은 사진을 먼저 표시합니다. 위치 점수는 보정된 확률이 아닙니다.</p><div class="grid">' + ''.join(cards) + '</div></html>'
    (output / "검수보기.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    evaluate(args.work, args.output)
