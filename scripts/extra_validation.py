"""New, duplicate-screened test photographs for the frozen first models."""
from __future__ import annotations

import argparse
import csv
import html
import json
import random
import shutil
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from scripts.prepare_size_dataset import digest, preview, write_csv, write_json

FIRST_FRESH_CODE = 1109
BODY_DISTANCE = 6
SEED = 20261025


def original_body(path):
    with Image.open(path) as photo:
        photo.draft("RGB", (540, 960))
        photo = ImageOps.exif_transpose(photo).convert("RGB")
        w, h = photo.size
        return photo.crop((round(w * .12), round(h * .40), round(w * .88), round(h * .87)))


def minimum_hash_distance(code, other_codes, inventory):
    value = int(inventory[code]["body_dhash"], 16)
    return min(((value ^ int(inventory[c]["body_dhash"], 16)).bit_count(), c) for c in other_codes)


def freeze(models):
    card = json.loads((models / "model-card.json").read_text(encoding="utf-8"))
    for name, expected in card["files_sha256"].items():
        if digest(models / name) != expected:
            raise ValueError(f"Published model artifact changed: {name}")
    for name, expected in card["code_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"Published inference/training code changed: {name}")
    return {"models": str(models.resolve()), "model_version": card["model_version"],
            "files": {name: digest(models / name) for name in ("coat_size.traineddata", "head_shape.npz")},
            "code": card["code_sha256"], "new_training_performed": False}


def select(work: Path, models: Path, inventory_path: Path, reviewed_path: Path):
    work.mkdir(parents=True, exist_ok=False)
    frozen = freeze(models)
    inventory = {r["code"]: r for r in json.loads(inventory_path.read_text(encoding="utf-8"))}
    labels = json.loads(reviewed_path.read_text(encoding="utf-8"))
    old = json.loads((models / "dataset-split.json").read_text(encoding="utf-8"))
    used = {r["code"] for r in old}
    old_set = set(used)
    candidates = {c: v for c, v in labels.items() if c not in used and int(c) >= FIRST_FRESH_CODE}
    nearest = {c: minimum_hash_distance(c, used, inventory) for c in candidates}
    eligible = {c: v for c, v in candidates.items() if nearest[c][0] > BODY_DISTANCE}
    pools = defaultdict(list)
    for code, (_, raw) in eligible.items():
        pools[raw].append(code)
    rng = random.Random(SEED)
    for pool in pools.values():
        rng.shuffle(pool)
    chosen = []
    for raw in ("XS", "2XL", "XL", "L", "M", "S"):
        target = min(74, len(pools[raw]))
        count = 0
        for code in pools[raw]:
            if chosen and minimum_hash_distance(code, chosen, inventory)[0] <= BODY_DISTANCE:
                continue
            chosen.append(code)
            count += 1
            if count >= target:
                break
    codes = sorted(chosen, key=int)
    all_codes = sorted(used | set(codes), key=int)
    (work / "body").mkdir()

    def body(code):
        image = original_body(Path(inventory[code]["source"]))
        feature = np.asarray(image.resize((32, 40)), dtype=np.float32).ravel() / 255
        image.thumbnail((140, 200))
        image.save(work / "body" / (code + ".jpg"), quality=93)
        return feature

    with ThreadPoolExecutor(max_workers=5) as pool:
        features = np.array(list(pool.map(body, all_codes)))
    index = {code: i for i, code in enumerate(all_codes)}
    old_features = features[[index[c] for c in sorted(used)]]
    new_features = features[[index[c] for c in codes]]
    old_codes = sorted(used)

    def distances(a, b):
        return np.maximum(0, np.sum(a * a, axis=1)[:, None] + np.sum(b * b, axis=1)[None, :] - 2 * a @ b.T) / a.shape[1]

    old_mse = distances(new_features, old_features)
    new_mse = distances(new_features, new_features)
    np.fill_diagonal(new_mse, np.inf)
    audit = []
    for i, code in enumerate(codes):
        oi, ni = int(old_mse[i].argmin()), int(new_mse[i].argmin())
        audit.append({"code": code, "nearest_old_body_code": old_codes[oi],
                      "nearest_old_body_rmse": float(np.sqrt(old_mse[i, oi])),
                      "nearest_new_body_code": codes[ni], "nearest_new_body_rmse": float(np.sqrt(new_mse[i, ni])),
                      "nearest_old_dhash_code": nearest[code][1], "min_old_dhash_distance": nearest[code][0],
                      "stratum_from_previous_visual_inventory": labels[code]})
    write_json(work / "selection.json", {"source": str(Path(next(iter(inventory.values()))["source"]).parents[1]),
               "excluded_code_range": "0001-1108", "first_fresh_code": FIRST_FRESH_CODE,
               "used_old_codes": sorted(used), "available_unseen_by_age_size": dict(Counter("/".join(v) for v in candidates.values())),
               "candidate_count": len(candidates), "body_distance_threshold": BODY_DISTANCE,
               "old_body_candidates_excluded": len(candidates) - len(eligible), "selected_codes": codes,
               "selected_counts": dict(Counter(labels[c][1] for c in codes)), "seed": SEED,
               "excluded_unseen_children": 0,
               "reason_no_child_or_3XL": "Every child and every 3XL photograph is already in the original 807-garment dataset"})
    write_json(work / "frozen_models.json", frozen)
    write_json(work / "body_audit.json", audit)
    write_json(work / "inventory_selected.json", {c: inventory[c] for c in codes})
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 18)
    # Each candidate is shown beside the nearest old and nearest new clothing
    # body. No model prediction, expected size, or gender is displayed.
    (work / "audit_contacts").mkdir()
    for page, start in enumerate(range(0, len(audit), 15)):
        group = audit[start:start + 15]
        canvas = Image.new("RGB", (1260, ((len(group) + 2) // 3) * 235), "#e4e7eb")
        draw = ImageDraw.Draw(canvas)
        for j, item in enumerate(group):
            base_x, base_y = j % 3 * 420, j // 3 * 235
            examples = [(item["code"], "NEW"), (item["nearest_old_body_code"], "OLD"),
                        (item["nearest_new_body_code"], "PEER")]
            for k, (code, role) in enumerate(examples):
                x = base_x + k * 140
                draw.text((x + 3, base_y + 3), f"{role} {code}", font=font, fill="black")
                with Image.open(work / "body" / (code + ".jpg")) as image:
                    canvas.paste(image, (x + (140 - image.width) // 2, base_y + 29))
        canvas.save(work / "audit_contacts" / f"body-{page:02}.jpg", quality=94)
    if used != old_set:
        raise ValueError("Old code set changed")
    print(json.dumps({"selected": len(codes), "size_counts": dict(Counter(labels[c][1] for c in codes)),
                      "body_audit_pages": (len(codes) + 14) // 15, "no_predictions_generated": True}), flush=True)


def prepare(work, models):
    frozen = freeze(models)
    if frozen != json.loads((work / "frozen_models.json").read_text(encoding="utf-8")):
        raise ValueError("Frozen model changed")
    selection = json.loads((work / "selection.json").read_text(encoding="utf-8"))
    exclusions = json.loads((work / "visual_exclusions.json").read_text(encoding="utf-8"))
    inventory = json.loads((work / "inventory_selected.json").read_text(encoding="utf-8"))
    old_root = Path(json.loads(Path(".local/gender-label-work/update/manifest.json").read_text(encoding="utf-8"))["root"])
    with (old_root / "files_sha256.csv").open(encoding="utf-8-sig", newline="") as stream:
        old_hashes = {r["sha256"] for r in csv.DictReader(stream)}
    excluded = set(exclusions["excluded_codes"])
    kept, photos, exact_excluded, seen = [], [], [], set()
    for code in selection["selected_codes"]:
        if code in excluded:
            continue
        front = Path(inventory[code]["source"])
        hashes = [{"code": code, "source": str(p), "sha256": digest(p)}
                  for p in sorted(front.parent.glob("img*.jpg"))]
        current = {r["sha256"] for r in hashes}
        if current & (old_hashes | seen):
            exact_excluded.append(code)
            continue
        if digest(front) != inventory[code]["sha256"]:
            raise ValueError(f"Source changed: {code}")
        seen.update(current)
        kept.append(code)
        photos.extend(hashes)
    (work / "blind_previews").mkdir()
    (work / "blind_contacts").mkdir()
    for code in kept:
        preview(Path(inventory[code]["source"]), work / "blind_previews" / (code + ".jpg"))
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 18)
    for page, start in enumerate(range(0, len(kept), 48)):
        group = kept[start:start + 48]
        canvas = Image.new("RGB", (1440, ((len(group) + 7) // 8) * 200), "#e4e7eb")
        draw = ImageDraw.Draw(canvas)
        for j, code in enumerate(group):
            x, y = j % 8 * 180, j // 8 * 200
            draw.text((x + 5, y + 3), code, font=font, fill="black")
            with Image.open(work / "blind_previews" / (code + ".jpg")) as photo:
                photo.thumbnail((178, 172))
                canvas.paste(photo, (x + (180 - photo.width) // 2, y + 27))
        canvas.save(work / "blind_contacts" / f"labels-{page:02}.jpg", quality=96)
    write_json(work / "prepared.json", {"codes": kept, "visual_excluded": sorted(excluded),
               "exact_hash_excluded": exact_excluded, "source_photos_checked": photos,
               "old_photo_hash_count": len(old_hashes), "predictions_generated": False})
    print(json.dumps({"kept": len(kept), "visual_excluded": len(excluded), "exact_excluded": exact_excluded,
                      "all_angles_hashed": len(photos), "blind_pages": (len(kept) + 47) // 48}), flush=True)


def read_blind_labels(codes, annotation):
    if annotation["predictions_seen"]:
        raise ValueError("Ground truth must precede predictions")
    tokens = " ".join(annotation["pages"]).split()
    if len(tokens) != len(codes) or len(set(codes)) != len(codes):
        raise ValueError("Annotation/code count mismatch")
    records = []
    for code, token in zip(codes, tokens, strict=True):
        if int(code) < FIRST_FRESH_CODE or token[:1] not in ("m", "f") or token[1:] not in {"XS", "S", "M", "L", "XL", "2XL", "3XL"}:
            raise ValueError(f"Unsupported or incomplete ground truth: {code}/{token}")
        records.append({"code": code, "age": "adult", "age_ko": "성인", "raw_text": token[1:],
                        "size_label": token[1:], "gender": "male" if token[0] == "m" else "female",
                        "gender_ko": "남성" if token[0] == "m" else "여성",
                        "shape_class": "adult_male" if token[0] == "m" else "adult_female",
                        "gender_provisional": False, "human_verified": False,
                        "annotation_method": "codex_blind_visual_read_user_confirmed_head_references",
                        "split": "additional_test", "use_for_training": False})
    return records


def seal(work, models, output):
    prepared = json.loads((work / "prepared.json").read_text(encoding="utf-8"))
    selection = json.loads((work / "selection.json").read_text(encoding="utf-8"))
    if set(prepared["codes"]) & set(selection["used_old_codes"]):
        raise ValueError("Old training/validation item included")
    annotation = json.loads((work / "blind_manual_pages.json").read_text(encoding="utf-8"))
    records = read_blind_labels(prepared["codes"], annotation)
    original_labels = json.loads(Path(".local/size-dataset-work/reviewed_labels.json").read_text(encoding="utf-8"))
    mismatches = [r["code"] for r in records if original_labels[r["code"]] != [r["age"], r["raw_text"]]]
    if mismatches:
        raise ValueError(f"Fresh readings disagree with previous visual inventory; adjudicate originals: {mismatches}")
    frozen = freeze(models)
    output.mkdir(parents=True, exist_ok=False)
    inventory = json.loads((work / "inventory_selected.json").read_text(encoding="utf-8"))
    for row in records:
        source = Path(inventory[row["code"]]["source"])
        relative = Path("추가검증용")
        relative = relative / "성인" / row["size_label"] / ("0_" + row["code"])
        folder = output / relative
        folder.mkdir(parents=True)
        shutil.copyfile(source, folder / source.name)
        expected = inventory[row["code"]]["sha256"]
        if digest(folder / source.name) != expected or digest(source) != expected:
            raise ValueError("Source/copy mismatch")
        row.update(front_path=(relative / source.name).as_posix(), front_sha256=expected, source_front=str(source),
                   preview_path=(relative / "preview.jpg").as_posix())
        shutil.copyfile(work / "blind_previews" / (row["code"] + ".jpg"), folder / "preview.jpg")
        write_json(folder / "label.json", row)
    with (output / "labels.jsonl").open("x", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_csv(output / "labels.csv", records)
    for name in ("selection.json", "visual_exclusions.json", "body_audit.json", "prepared.json", "blind_manual_pages.json"):
        shutil.copyfile(work / name, output / name)
    write_json(output / "protocol.json", {"models": frozen, "ground_truth_sha256": digest(output / "labels.jsonl"),
               "ground_truth_sealed_before_predictions": True, "count": len(records), "predictions_seen": False,
               "size_counts": dict(Counter(r["raw_text"] for r in records)),
               "shape_counts": dict(Counter(r["shape_class"] for r in records)), "new_training": False,
               "source_photos_hashed_all_angles": len(prepared["source_photos_checked"]),
               "scope": "Same original capture collection, new adult garments only; no unseen child or 3XL remaining",
               "duplicate_screening": "0001-1108 excluded, old 807 codes and exact all-angle hashes excluded, body dHash >6, all 300 nearest-body comparisons visually reviewed; 17 ambiguous/repeated clothing candidates conservatively excluded. Physical garment identity cannot be guaranteed from these photographs alone.",
               "labels": annotation["method"]})
    print(json.dumps({"sealed": len(records), "sizes": dict(Counter(r["raw_text"] for r in records)),
                      "shapes": dict(Counter(r["shape_class"] for r in records)), "fresh_label_mismatches": mismatches}), flush=True)


def evaluate(work, models, output):
    from coatdesk_ml.crops import detect, load_front
    from coatdesk_ml.inference import TrainedRecognizer
    from coatdesk_ml.training import TESS, measure, ocr

    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    if digest(output / "labels.jsonl") != protocol["ground_truth_sha256"] or freeze(models) != protocol["models"]:
        raise ValueError("Ground truth or model changed")
    if (output / "results.json").exists():
        raise FileExistsError("Evaluation already exists; never overwrite held-out results")
    records = [json.loads(line) for line in (output / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    scratch = work / "evaluation_scratch"
    scratch.mkdir()
    recognizer = TrainedRecognizer(models)

    def predict(row):
        front = output / row["front_path"]
        if digest(front) != row["front_sha256"]:
            raise ValueError("Copied photograph changed")
        try:
            result = recognizer.predict(front, scratch)
            placard = detect(load_front(front))
            old_raw = ""
            if placard is not None:
                with tempfile.NamedTemporaryFile(dir=scratch, suffix=".png", delete=False) as stream:
                    crop = Path(stream.name)
                try:
                    placard.text.save(crop)
                    old_raw = ocr(crop, TESS / "tessdata", "eng")
                finally:
                    crop.unlink(missing_ok=True)
        except Exception as exc:
            result = {"detected": False, "reason": type(exc).__name__ + ": " + str(exc)}
            old_raw = ""
        predicted_raw, predicted_shape = result.get("raw_text", ""), result.get("shape_class", "")
        return {"code": row["code"], "expected_raw": row["raw_text"], "predicted_raw": predicted_raw,
                "expected_shape": row["shape_class"], "predicted_shape": predicted_shape,
                "size_correct": predicted_raw == row["raw_text"], "shape_correct": predicted_shape == row["shape_class"],
                "both_correct": predicted_raw == row["raw_text"] and predicted_shape == row["shape_class"],
                "baseline_raw": old_raw, "detected": result.get("detected", False), "inference": result}

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        predictions = list(pool.map(predict, records))
    seconds = time.perf_counter() - started
    metrics = {"count": len(records),
               "size": measure([r["expected_raw"] for r in predictions], [r["predicted_raw"] for r in predictions]),
               "shape": measure([r["expected_shape"] for r in predictions], [r["predicted_shape"] for r in predictions]),
               "baseline_size": measure([r["expected_raw"] for r in predictions], [r["baseline_raw"] for r in predictions]),
               "both_correct": sum(r["both_correct"] for r in predictions),
               "detected": sum(r["detected"] for r in predictions), "seconds_including_baseline_ocr": seconds,
               "workers": 3, "errors": [r for r in predictions if not r["both_correct"]]}
    prepared = json.loads((work / "prepared.json").read_text(encoding="utf-8"))
    changed = [r["source"] for r in prepared["source_photos_checked"] if digest(Path(r["source"])) != r["sha256"]]
    if changed or freeze(models) != protocol["models"] or digest(output / "labels.jsonl") != protocol["ground_truth_sha256"]:
        raise ValueError("Source/model/label integrity check failed")
    metrics["integrity"] = {"source_photos_unchanged": len(prepared["source_photos_checked"]),
                            "models_unchanged": True, "ground_truth_unchanged": True, "retraining_performed": False}
    write_json(output / "predictions.json", predictions)
    write_csv(output / "predictions.csv", [{k: v for k, v in r.items() if k != "inference"} for r in predictions])
    write_json(output / "results.json", metrics)
    write_report(output, records, predictions, metrics, protocol)
    print(json.dumps({k: metrics[k] for k in ("count", "both_correct", "detected", "seconds_including_baseline_ocr", "errors")}), flush=True)


def write_report(output, records, predictions, metrics, protocol):
    cards = []
    for row, pred in sorted(zip(records, predictions, strict=True), key=lambda pair: pair[1]["both_correct"]):
        status = "정답" if pred["both_correct"] else "오류"
        cards.append(f'<article class="{"ok" if pred["both_correct"] else "error"}"><a href="{html.escape(row["front_path"])}"><img loading="lazy" src="{html.escape(row["preview_path"])}"></a><b>{row["code"]} · {status}</b><p>정답: {row["raw_text"]} / {row["gender_ko"]}</p><p>예측: {html.escape(pred["predicted_raw"])} / {html.escape(pred["predicted_shape"])}</p></article>')
    correction_note = (
        "최초 판독에서는 1407번 성인 남성을 여성으로 잘못 기록했습니다. 예측 후 원본 재검수에서 이를 발견했고, "
        "283벌 전체를 다시 육안 대조하여 이 1건만 정정했습니다. 초기 라벨·예측·성적은 _initial-evaluation에 보존했습니다. "
        "초기 성적은 글자 275/283, 판넬 281/283, 동시 274/283이며 아래는 라벨 정정 후 성적입니다. "
        "예측 출력·모델·추출 규칙은 정정 전후 동일합니다. 따라서 최종 라벨 전체가 예측 이전에 확정됐다는 주장은 하지 않습니다.\n"
        if protocol.get("adjudications") else ""
    )
    text = f"""# 기존 모델 추가 검증 (2026-10-24)

새 검증 {len(records)}벌. 초반 0001~1108번 전체와 기존 807벌을 제외하고,
몸통 이미지 해시 및 최근접 사진 육안 비교 후 중복 의심 17벌을 추가 제외했습니다.
정답을 모델 예측 없이 판독·고정한 뒤 기존 모델을 그대로 실행했습니다. 새 학습이나 규칙 조정은 하지 않았습니다.
{correction_note}

- 사이즈 글자: {metrics['size']['correct']}/{len(records)} ({metrics['size']['accuracy']:.1%})
- 남녀 판넬: {metrics['shape']['correct']}/{len(records)} ({metrics['shape']['accuracy']:.1%})
- 두 항목 동시 정답: {metrics['both_correct']}/{len(records)}
- 기존 OCR 비교: {metrics['baseline_size']['correct']}/{len(records)} ({metrics['baseline_size']['accuracy']:.1%}); 같은 동결된 글자 추출 영역과 실행 설정 사용
- 판넬 검출 플래그: {metrics['detected']}/{len(records)}; 올바른 영역 추출을 보장하는 수치가 아니며 실패·미지원 결과도 오답으로 포함
- 원본 정면·옆·뒤 849장 SHA-256, 복사 정면 {len(records)}장, 모델 해시 불변 확인; 정답은 위 정정 내역과 별도 SHA-256 기록

사이즈 분포: {json.dumps(protocol['size_counts'], ensure_ascii=False)}
판넬 분포: {json.dumps(protocol['shape_counts'], ensure_ascii=False)}

이번 추가 검증은 **성인만** 포함합니다. 원본의 아동 139벌과 3XL 68벌은 기존 807벌에 모두 포함되어 새 표본이 없습니다.
XS·2XL도 각 2벌뿐이므로 해당 사이즈의 정확도를 일반화할 수 없습니다.
같은 촬영 자료에서 고른 표본이며, 새 촬영일·배경·카메라 성능 보장이 아닙니다.
라벨은 Codex 판독이며 담당자의 독립적인 개별 검수는 수행하지 않았습니다.
사진만으로 실제 옷의 완전한 중복 제거를 보장할 수 없습니다. 아동 남아 임시 규칙은 이번에 검증하지 않았습니다.
현재 앱·설치파일에는 새 모델을 연결하지 않았습니다. 원본·기존 학습 자료는 변경하지 않았습니다.

개별 정답과 예측은 predictions.csv, 오류 근거는 results.json, 모델/정답 고정 기록은 protocol.json을 확인하세요.
검수보기.html에서 정면 사진을 클릭하면 복사한 원본을 볼 수 있습니다.
"""
    if protocol.get("adjudications"):
        text += "\n오류 8벌: 1635·2348·2409·2587번은 글자 대신 얼굴 윤곽 일부를 추출했고, 2509·2896·2900번은 귀·윤곽/옷이 글자 영역에 섞였으며, 2617번은 글자 상단이 잘렸습니다. 2617번은 머리 영역도 잘못 추출해 여성→남성으로 틀렸습니다. 원본 및 실제 입력 영역을 error_evidence/corrected-errors-contact.jpg에 함께 보존했습니다. 다음 개선은 영역 추출과 잘못된 영역의 판단 보류 처리가 우선입니다. 이번 검증 후에는 수정하지 않았습니다.\n"
    (output / "RESULTS.md").write_text(text, encoding="utf-8")
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>외투 추가 검증 283벌</title><style>body{font:16px system-ui;margin:24px;background:#f3f5f8;color:#17212b}pre{white-space:pre-wrap;line-height:1.7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}article{background:white;border:2px solid #c9d6cc;padding:12px;border-radius:10px}article.error{border:4px solid #c52929}img{width:100%;height:220px;object-fit:contain}p{margin:6px 0}</style><h1>외투 추가 검증 결과</h1><pre>' + html.escape(text) + '</pre><div class="grid">' + "".join(cards) + '</div></html>'
    (output / "검수보기.html").write_text(page, encoding="utf-8")


def rescore(records, original_predictions):
    from coatdesk_ml.training import measure

    predictions = json.loads(json.dumps(original_predictions))
    for row, prediction in zip(records, predictions, strict=True):
        if row["code"] != prediction["code"]:
            raise ValueError("Ground truth/prediction code mismatch")
        prediction["expected_raw"] = row["raw_text"]
        prediction["expected_shape"] = row["shape_class"]
        prediction["size_correct"] = row["raw_text"] == prediction["predicted_raw"]
        prediction["shape_correct"] = row["shape_class"] == prediction["predicted_shape"]
        prediction["both_correct"] = prediction["size_correct"] and prediction["shape_correct"]
    metrics = {"size": measure([r["expected_raw"] for r in predictions], [r["predicted_raw"] for r in predictions]),
               "shape": measure([r["expected_shape"] for r in predictions], [r["predicted_shape"] for r in predictions]),
               "baseline_size": measure([r["expected_raw"] for r in predictions], [r["baseline_raw"] for r in predictions]),
               "both_correct": sum(r["both_correct"] for r in predictions),
               "errors": [r for r in predictions if not r["both_correct"]]}
    return predictions, metrics


def adjudicate(work, models, output):
    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    if freeze(models) != protocol["models"] or digest(output / "labels.jsonl") != protocol["ground_truth_sha256"]:
        raise ValueError("Original frozen protocol changed")
    corrections = json.loads((work / "adjudications.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (output / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    before = json.loads((output / "predictions.json").read_text(encoding="utf-8"))
    metrics = json.loads((output / "results.json").read_text(encoding="utf-8"))
    archive = output / "_initial-evaluation"
    archive.mkdir()
    files = ("protocol.json", "labels.jsonl", "labels.csv", "predictions.json", "predictions.csv", "results.json", "RESULTS.md", "검수보기.html")
    for name in files:
        shutil.copyfile(output / name, archive / name)
        if digest(output / name) != digest(archive / name):
            raise ValueError("Initial result preservation failed")
    for correction in corrections["changes"]:
        row = next(r for r in records if r["code"] == correction["code"])
        if row["shape_class"] != correction["before_shape"]:
            raise ValueError("Unexpected label revision")
        row.update(shape_class=correction["after_shape"], gender=correction["after_gender"],
                   gender_ko=correction["after_gender_ko"], label_revision="v2_adjudicated",
                   annotation_method="codex_visual_adjudication_after_predictions", predictions_seen_during_adjudication=True)
        label_path = output / Path(row["front_path"]).parent / "label.json"
        shutil.copyfile(label_path, archive / (row["code"] + "-label.json"))
        label_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "labels.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output / "labels.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted({k for r in records for k in r}))
        writer.writeheader()
        writer.writerows(records)
    predictions, recomputed = rescore(records, before)
    metrics.update(recomputed)
    metrics["integrity"].update(ground_truth_unchanged=False, initial_ground_truth_preserved=True, model_outputs_unchanged=True)
    metrics["label_adjudication"] = corrections
    protocol.update(initial_ground_truth_sha256=protocol["ground_truth_sha256"],
                    ground_truth_sha256=digest(output / "labels.jsonl"), ground_truth_sealed_before_predictions=False,
                    initial_ground_truth_sealed_before_predictions=True, adjudications=corrections,
                    shape_counts=dict(Counter(r["shape_class"] for r in records)))
    for name, value in (("protocol.json", protocol), ("predictions.json", predictions), ("results.json", metrics)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "predictions.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        flat = [{k: v for k, v in r.items() if k != "inference"} for r in predictions]
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    write_report(output, records, predictions, metrics, protocol)
    print(json.dumps({"size_correct": metrics["size"]["correct"], "shape_correct": metrics["shape"]["correct"],
                      "both_correct": metrics["both_correct"], "label_corrections": len(corrections["changes"])}))


def verify_bundle(models, output):
    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    if freeze(models) != protocol["models"] or digest(output / "labels.jsonl") != protocol["ground_truth_sha256"]:
        raise ValueError("Published protocol mismatch")
    records = [json.loads(line) for line in (output / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    predictions = json.loads((output / "predictions.json").read_text(encoding="utf-8"))
    metrics = json.loads((output / "results.json").read_text(encoding="utf-8"))
    _, recomputed = rescore(records, predictions)
    for key, value in recomputed.items():
        if metrics[key] != value:
            raise ValueError(f"Incorrect reported metric: {key}")
    used = {r["code"] for r in json.loads((models / "dataset-split.json").read_text(encoding="utf-8"))}
    codes = {r["code"] for r in records}
    if len(records) != protocol["count"] or len(codes) != len(records) or used & codes:
        raise ValueError("Old or repeated code in new test")
    if min(map(int, codes)) < FIRST_FRESH_CODE:
        raise ValueError("Early repeated photograph range included")
    with (output / "labels.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    if len(csv_rows) != len(records):
        raise ValueError("Label CSV count differs")
    gallery = (output / "검수보기.html").read_text(encoding="utf-8")
    for row, csv_row in zip(records, csv_rows, strict=True):
        for key, value in row.items():
            if csv_row[key] != str(value):
                raise ValueError(f"CSV label mismatch {row['code']}/{key}")
        front = output / row["front_path"]
        if digest(front) != row["front_sha256"] or digest(Path(row["source_front"])) != row["front_sha256"]:
            raise ValueError("Original/copied front hash mismatch")
        if json.loads((front.parent / "label.json").read_text(encoding="utf-8")) != row:
            raise ValueError("Per-garment label differs")
        if not (output / row["preview_path"]).is_file() or html.escape(row["preview_path"]) not in gallery or html.escape(row["front_path"]) not in gallery:
            raise ValueError("Missing gallery image/link")
        if row["use_for_training"] or row["human_verified"]:
            raise ValueError("Incorrect annotation/training provenance")
    if gallery.count('<article class="error">') != len(metrics["errors"]):
        raise ValueError("Gallery error count differs")
    prepared = json.loads((output / "prepared.json").read_text(encoding="utf-8"))
    if set(prepared["codes"]) != codes or codes & set(prepared["visual_excluded"]):
        raise ValueError("Excluded photograph was retained")
    seen = set()
    for photo in prepared["source_photos_checked"]:
        if digest(Path(photo["source"])) != photo["sha256"] or photo["sha256"] in seen:
            raise ValueError("Source photo changed or repeated")
        seen.add(photo["sha256"])
    initial = output / "_initial-evaluation"
    initial_protocol = json.loads((initial / "protocol.json").read_text(encoding="utf-8"))
    if digest(initial / "labels.jsonl") != protocol["initial_ground_truth_sha256"] or initial_protocol["ground_truth_sha256"] != protocol["initial_ground_truth_sha256"]:
        raise ValueError("Initial blind ground truth was not preserved")
    initial_predictions = json.loads((initial / "predictions.json").read_text(encoding="utf-8"))
    for before, after in zip(initial_predictions, predictions, strict=True):
        for key in ("code", "predicted_raw", "predicted_shape", "baseline_raw", "detected", "inference"):
            if before[key] != after[key]:
                raise ValueError("Model output changed after adjudication")
    return {"passed": True, "new_garments": len(records), "source_photos_unchanged": len(seen),
            "copied_fronts_verified": len(records), "old_code_overlap": 0, "early_range_included": 0,
            "label_json_csv_per_folder_consistent": True, "gallery_links_checked": len(records) * 2,
            "model_outputs_and_weights_unchanged": True, "initial_labels_preserved": True,
            "label_corrections_after_predictions": len(protocol["adjudications"]["changes"]),
            "browser_rendering_verified": False, "external_uploads": False}


def publish(models, output, destination):
    verify_bundle(models, output)
    files = sorted(p for p in output.rglob("*") if p.is_file())
    manifest = [{"path": p.relative_to(output).as_posix(), "sha256": digest(p), "bytes": p.stat().st_size} for p in files]
    destination.mkdir(parents=False, exist_ok=False)
    for entry in manifest:
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output / entry["path"], target)
        if digest(target) != entry["sha256"]:
            raise ValueError(f"Desktop copy mismatch: {entry['path']}")
    write_json(destination / "desktop-copy-verification.json", {"passed": True, "files": len(files),
               "bytes": sum(r["bytes"] for r in manifest), "manifest": manifest})
    verified = verify_bundle(models, destination)
    print(json.dumps({"destination": str(destination), "copied_files": len(files), "verification": verified}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["select", "prepare", "seal", "evaluate", "adjudicate", "verify", "publish"])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=Path("model-results/20261024-first"))
    parser.add_argument("--inventory", type=Path, default=Path(".local/size-dataset-work/inventory.json"))
    parser.add_argument("--reviewed", type=Path, default=Path(".local/size-dataset-work/reviewed_labels.json"))
    parser.add_argument("--output", type=Path, default=Path("model-results/20261024-extra-validation"))
    parser.add_argument("--destination", type=Path, default=Path.home() / "Desktop" / "외투_추가검증데이터_20261024")
    args = parser.parse_args()
    if args.phase == "select":
        select(args.work, args.models, args.inventory, args.reviewed)
    elif args.phase == "prepare":
        prepare(args.work, args.models)
    elif args.phase == "seal":
        seal(args.work, args.models, args.output)
    elif args.phase == "evaluate":
        evaluate(args.work, args.models, args.output)
    elif args.phase == "adjudicate":
        adjudicate(args.work, args.models, args.output)
    elif args.phase == "verify":
        print(json.dumps(verify_bundle(args.models, args.output)))
    else:
        publish(args.models, args.output, args.destination)


if __name__ == "__main__":
    main()
