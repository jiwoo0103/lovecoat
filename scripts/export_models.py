"""Export trained models and an auditable, local evaluation report."""
from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter
from pathlib import Path

from coatdesk_ml.crops import VERSION
from coatdesk_ml.training import CLASSES, PROJECT, prepared_rows, save_json, sha


def export(work: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    results = json.loads((work / "outer-evaluation/report.json").read_text(encoding="utf-8"))
    if results["role"] != "outer_validation" or results["count"] != 162:
        raise ValueError("Expected the untouched outer 162-garment evaluation")
    records = prepared_rows(work)
    provenance = json.loads((work / "prepared-final/provenance.json").read_text(encoding="utf-8"))
    dataset = Path(provenance["dataset"])
    if sha(dataset / "labels.jsonl") != provenance["labels_sha256"]:
        raise ValueError("Dataset labels changed during training")
    output.mkdir(parents=True)
    copies = {"coat_size.traineddata": work / "ocr-final/coat_size.traineddata",
              "head_shape.npz": work / "shape/head_shape.npz",
              "size-mapping.json": dataset / "사이즈기준.json",
              "validation-results.json": work / "outer-evaluation/report.json",
              "validation-predictions.csv": work / "outer-evaluation/predictions.csv",
              "head-training.json": work / "shape/training.json",
              "ocr-training-command.json": work / "ocr-final/command.json",
              "ocr-training.log": work / "ocr-final/training.log",
              "base-model-source.json": work / "base/source.json",
              "LICENSE-tessdata_best.txt": work / "base/LICENSE"}
    for name, source in copies.items():
        shutil.copy2(source, output / name)
        if sha(source) != sha(output / name):
            raise ValueError("Export copy changed")
    save_json(output / "dataset-split.json", [{key: r[key] for key in (
        "code", "split", "split_group", "role", "raw_text", "shape_class", "front_path", "front_sha256")} for r in records])
    training = [r for r in records if r["split"] == "train"]
    card = {"model_version": "2026-10-24.first-v1", "status": "experimental_not_integrated_into_app",
            "detector_version": VERSION, "classes": CLASSES, "trained_on_front_photographs_only": True,
            "dataset": str(dataset), "dataset_labels_sha256": provenance["labels_sha256"],
            "fit_garments_final": len(training), "outer_validation_garments": results["count"],
            "internal_fit_dev": provenance["roles"], "outer_validation_used_for_tuning": False,
            "validated_raw_labels": sorted({r["raw_text"] for r in training}),
            "child_gender_rule": {"gender": "male", "provisional": True, "learned_as_gender_target": False},
            "ocr": {"algorithm": "Tesseract LSTM fine tuning of official float eng model; integer runtime export",
                    "source": "https://github.com/tesseract-ocr/tessdata_best", "tesseract_version": "5.5.3.20260724",
                    "augmented_line_images": len(training) * 3, "maximum_sample_iterations": 4000,
                    "rotations_degrees": [-5, 0, 5], "psm": 13},
            "head": {"algorithm": "HOG + balanced softmax regression; NumPy Adam optimization",
                     "augmented_images": len(training) * 5, "classes": CLASSES,
                     "size_text_and_clothing_excluded": True},
            "validation": {"text_correct": results["trained"]["correct"], "head_correct": results["shape"]["correct"],
                           "joint_correct": results["joint_correct"], "count": results["count"],
                           "existing_ocr_correct_on_identical_new_crops": results["existing"]["correct"],
                           "pretrained_float_ocr_correct_on_identical_new_crops": results["base_float"]["correct"]},
            "limitations": ["Same source capture collection, not a new capture-day independent test",
                            "Size/individual gender labels were visually transcribed by AI; user confirmed reference mapping",
                            "Child male is a provisional application rule, not a learned child gender result",
                            "Adult 2XS and child 60/70 absent; raw child 170 has only one outer validation example",
                            "Probabilities are not calibrated; suggestions still need review",
                            "No UI/package integration or clean-PC deployment validation was performed"],
            "files_sha256": {name: sha(output / name) for name in copies},
            "code_sha256": {name: sha(PROJECT / name) for name in (
                "coatdesk_ml/crops.py", "coatdesk_ml/training.py", "coatdesk_ml/inference.py", "scripts/train_models.py")}}
    save_json(output / "model-card.json", card)
    save_json(output / "training-provenance.json", provenance)
    predictions = results["predictions"]
    errors = [r for r in predictions if r["trained"] != r["expected_raw"] or r["predicted_shape"] != r["expected_shape"]]
    labels = {"adult_male": "성인 남성", "adult_female": "성인 여성", "child": "아동"}
    count = results["count"]
    metrics = [("기존 OCR · 동일한 새 글자 영역", results["existing"]["correct"]),
               ("추가 학습한 글자 모델", results["trained"]["correct"]),
               ("학습한 판넬 모델", results["shape"]["correct"]), ("글자·판넬 모두 정답", results["joint_correct"])]
    table = "\n".join(f"| {name} | {correct}/{count} | {correct / count:.1%} |" for name, correct in metrics)
    guide = f"""# 첫 모델 학습 결과 · 2026-10-24

정면 사진 645벌로 두 모델을 실제 학습했습니다. 학습용 안의 516/129벌로 내부 점검을 한 뒤 최종 모델은 645벌 전체에 맞췄습니다.
최종 검증 162벌은 가중치 학습·모델 설정 선택에 사용하지 않았습니다. 증강은 각 원본의 기존 분할 안에서만 만들었습니다.

| 구분 | 정답/검증 | 비율 |
|---|---:|---:|
{table}

판넬 검증 구성: {', '.join(f'{labels[k]} {v}벌' for k, v in Counter(r['expected_shape'] for r in predictions).items())}.
글자 오류율(CER): {results['trained']['character_error_rate']:.2%}. 불일치 {len(errors)}벌의 상세는 `validation-predictions.csv`에 있습니다.

## 해석과 현재 적용 상태

- 위 비율은 이번 162벌에서 측정한 결과이며, 다른 촬영일·새 판넬·배경에서 같은 성능을 보장하지 않습니다.
- 기존 OCR 비교는 같은 새 글자 추출 영역·동일한 실행 설정의 비교입니다. 기존 앱 전체 흐름의 성능과 구분해야 합니다.
- 판넬은 성인 남성/성인 여성/아동 세 종류를 학습했습니다. 아동 남아 표시는 사용자의 임시 규칙이며 성별 학습 정답이 아닙니다.
- 성인 2XS·아동 60/70 자료는 없고, 아동 170의 최종 검증은 1벌뿐입니다.
- 모델은 별도 파일로 저장했습니다. 현재 앱과 설치파일에는 아직 연결하지 않았으며 기존 OCR 파일도 교체하지 않았습니다.
- 원본 사진·기존 라벨·학습/검증 분할은 유지했습니다. 사진은 외부로 전송하지 않았습니다.

## 파일

- `coat_size.traineddata`: 실제 추가 학습한 Tesseract 글자 모델.
- `head_shape.npz`: 실제 학습한 머리 판넬 분류 가중치.
- `model-card.json`: 모델·전처리·데이터 해시·평가·제약.
- `dataset-split.json`, `validation-results.json`, `validation-predictions.csv`: 학습/평가 근거.
- `ocr-training.log`, `ocr-training-command.json`, `head-training.json`: 재현 가능한 학습 기록.

[Tesseract 학습 문서](https://tesseract-ocr.github.io/tessdoc/tess5/TrainingTesseract-5.html),
[학습 가능한 공식 모델](https://tesseract-ocr.github.io/tessdoc/Data-Files).
"""
    (output / "RESULTS.md").write_text(guide, encoding="utf-8")
    rows_html = "".join(f"<tr><td>{html.escape(name)}</td><td>{correct}/{count}</td><td>{correct / count:.1%}</td></tr>"
                        for name, correct in metrics)
    error_html = ""
    by_code = {r["code"]: r for r in records}
    if errors:
        error_html = "<h2>틀린 항목</h2><table><tr><th>번호</th><th>글자 정답</th><th>인식 결과</th><th>판넬 정답</th><th>판넬 결과</th></tr>"
        for error in errors:
            url = (dataset / by_code[error["code"]]["front_path"]).as_uri()
            values = [f'<a href="{html.escape(url)}">{error["code"]}</a>', html.escape(error["expected_raw"]),
                      html.escape(error["trained"] or "미인식"), labels[error["expected_shape"]],
                      labels.get(error["predicted_shape"], "미인식")]
            error_html += "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
        error_html += "</table>"
    page = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>외투 모델 학습 결과</title><style>body{{font:17px/1.65 system-ui,'Malgun Gothic',sans-serif;max-width:940px;margin:40px auto;padding:0 24px;color:#162231;background:#f5f7fa}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{border:1px solid #c9d1db;padding:10px 14px;text-align:left}}h1{{font-size:28px}}.note{{background:#fff1cf;padding:14px;border-radius:6px}}a{{color:#1552a0}}</style>
<h1>외투 모델 학습 결과</h1><p>2026-10-24 · 학습 645벌 · 최종 검증 162벌 · 정면 사진만 사용</p>
<p class="note">두 모델 학습과 검증을 마쳤습니다. 현재 앱·설치파일에는 아직 연결하지 않았습니다.</p>
<table><tr><th>측정 항목</th><th>정답/검증</th><th>비율</th></tr>{rows_html}</table>
<p>기존 OCR과 학습 모델은 같은 새 글자 영역으로 비교했습니다. 다른 촬영일의 성능은 별도 확인이 필요합니다.</p>
<p>아동 남아는 사용자 임시 규칙입니다. 모델은 아동 판넬까지 구분하며 아동 남녀를 학습하지 않았습니다.</p>
{error_html}<p><a href="RESULTS.md">상세 설명</a> · <a href="validation-predictions.csv">검증 162벌 결과</a> · <a href="model-card.json">모델 정보</a></p></html>"""
    (output / "results.html").write_text(page, encoding="utf-8")
    print(json.dumps({"exported": str(output.resolve()), "validation": card["validation"]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.work.resolve(strict=True), args.output.resolve())
