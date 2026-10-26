"""Write a comparison report from completed, unchanged evaluation artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path

from .data import SHAPES, SIZES, read, sha

NAMES = {"panel": "판넬 전체", "whole": "원본 사진 전체", "adult_male": "성인 남성",
         "adult_female": "성인 여성", "child": "아동"}


def fraction(correct, count):
    return f"{correct}/{count} ({correct / count:.2%})"


def write_report(output):
    result = read(output / "results.json")
    benchmark = read(output / "benchmark.json")
    diagnostics = read(output / "diagnostics.json")
    manifest = read(output / "dataset-manifest.json")
    predictions = {mode: read(output / (mode + "-predictions.json")) for mode in ("panel", "whole")}
    text = ["# 새 모델 4개 학습·비교 결과", "", "2026-10-24. 학습 645벌, 평가 445벌. 모두 정면 사진입니다.", "",
            "판넬 전체와 원본 전체에 각각 성인 남성/여성/아동 모델, 사이즈 모델을 별도로 학습했습니다. 기존 외투 모델의 가중치와 예측은 사용하지 않았습니다.", "",
            "| 평가 자료 | 입력 | 성별·연령 정답 | 사이즈 정답 | 둘 다 정답 |", "|---|---|---|---|---|"]
    for cohort, label in (("all_445", "전체 445벌"), ("earlier_162", "이전 검증 162벌"), ("additional_283", "추가 성인 283벌")):
        for mode in ("panel", "whole"):
            score = result["metrics"][mode][cohort]
            n = score["count"]
            text.append(f'| {label} | {NAMES[mode]} | {fraction(score["shape"]["correct"], n)} | {fraction(score["size"]["correct"], n)} | {fraction(score["joint_correct"], n)} |')
    a, b = (result["metrics"][mode]["all_445"] for mode in ("panel", "whole"))
    text += ["", f'이번 동일 조건에서는 판넬 방식이 사이즈 {a["size"]["correct"] - b["size"]["correct"]}벌, 동시 정답 {a["joint_correct"] - b["joint_correct"]}벌 더 맞혔습니다. 한 촬영 자료·한 시드의 비교이며 다른 촬영일의 성능을 보장하지 않습니다.', "",
             "## 종류별 결과", "", "| 작업 | 정답 종류 | 평가 수 | 판넬 정답 | 원본 정답 |", "|---|---|---:|---:|---:|"]
    for task, labels in (("shape", SHAPES), ("size", SIZES)):
        for label in labels:
            left = a[task]["per_class"].get(label, {"count": 0, "correct": 0})
            right = b[task]["per_class"].get(label, {"count": 0, "correct": 0})
            text.append(f'| {"성별·연령" if task == "shape" else "사이즈"} | {NAMES.get(label, label)} | {left["count"]} | {left["correct"]} | {right["correct"]} |')
    text += ["", "## 틀린 사진", "", "사진별 실제 원본과 자동 추출 영역은 [비교보기.html](비교보기.html)에 오류부터 표시합니다. 원시 점수는 보정된 신뢰 확률이 아닙니다.", "",
             "| 입력 | 코드 | 정답 성별·연령/사이즈 | 예측 성별·연령/사이즈 | 예측 클래스 점수(형태/사이즈) |", "|---|---|---|---|---|"]
    for mode, rows in predictions.items():
        wrong = [r for r in rows if not (r["shape_correct"] and r["size_correct"])]
        if not wrong:
            text.append(f"| {NAMES[mode]} | — | — | 오답 없음 | — |")
        for row in wrong:
            shape_score = row["shape_scores"].get(row["predicted_shape"], 0)
            size_score = row["size_scores"].get(row["predicted_size"], 0)
            text.append(f'| {NAMES[mode]} | {row["code"]} | {NAMES[row["truth_shape"]]}/{row["truth_size"]} | {NAMES.get(row["predicted_shape"], row["predicted_shape"])}/{row["predicted_size"]} | {shape_score:.2%}/{size_score:.2%} |')
    text += ["", "## 원본부터 처리한 속도", "", "개발 PC RTX 3060, 32장, 한 벌당 두 모델 실행. 모델/런타임 시작 시간은 제외하고 JPEG 읽기·축소·추론과 판넬 방식의 위치 찾기를 포함합니다. GPU는 FP16, CPU는 FP32·4스레드입니다.", "",
             "| 실행 | 입력 | 평균 초/벌 | 중앙값 | 95백분위 | 일괄 평가와 결과가 달라진 코드 |", "|---|---|---:|---:|---:|---|"]
    for device in ("cuda", "cpu"):
        for mode in ("panel", "whole"):
            item = benchmark[device][mode]
            mismatches = ", ".join(item["cached_argmax_differences"]) or "없음"
            text.append(f'| {device} | {NAMES[mode]} | {item["mean_seconds"]:.3f} | {item["median_seconds"]:.3f} | {item["p95_seconds"]:.3f} | {mismatches} |')
    text += ["", "## 가림 진단", "", "학습 후 같은 32장에만 적용한 보조 실험입니다. 원본 방식의 화면 전체와 판넬 위치는 유지하고 지정한 부분을 흰색으로 칠했습니다. 중앙 얼굴 마스크는 글자만 정확히 지운 것이 아니며 일부 머리 윤곽도 가릴 수 있습니다. 결과를 보고 모델을 다시 조정하지 않았습니다.", "",
             "| 입력 | 가린 부분 | 성별·연령 정답/32 | 사이즈 정답/32 |", "|---|---|---:|---:|"]
    for mode in ("panel", "whole"):
        for variant, label in (("original", "없음"), ("central_face_masked", "얼굴 중앙"), ("background_masked", "판넬 밖 전체"), ("plate_masked", "판넬 전체")):
            item = diagnostics["modes"][mode]["summary"][variant]
            text.append(f'| {NAMES[mode]} | {label} | {item["shape_correct"]} | {item["size_correct"]} |')
    text += ["", "## 학습 기록과 보존 확인", "", "| 모델 | 학습 수 | 선택 학습 횟수 | 최종 학습 초 | 모델 파일 |", "|---|---:|---:|---:|---|"]
    for name, card in result["models"].items():
        assert sha(output / "models" / (name + ".pt")) == card["model_sha256"]
        text.append(f'| {name} | {card["training_count"]} | {card["epochs"]} | {card["training_seconds"]:.1f} | [가중치](models/{name}.pt) / [설정·해시](models/{name}.json) |')
    text += ["", f'- 원본 정면 {result["source_front_hashes_unchanged"]}장 및 기존 라벨 SHA-256 불변을 확인했습니다.',
             f'- 자료 분할: {manifest["roles"]}. 같은 중복 후보 그룹·동일 파일 해시가 분할을 넘지 않는지 확인했습니다.',
             '- 네 최종 가중치 SHA-256이 모두 다르고, 같은 작업의 두 입력 방식은 초기 가중치 SHA-256이 같습니다.',
             '- 학습 기록은 `training-logs/`, 고정한 사전 설정은 `train-protocol.json`, 세 기준 판넬과 마스크는 `references/`에 보존했습니다.',
             '- 컴퓨터 종료로 중단된 원본 사이즈 내부 학습은 부분 기록을 보존한 뒤 같은 초기 조건으로 다시 실행했습니다. 완료된 세 내부 학습은 유지했습니다.', "",
             "## 적용 범위와 한계", "",
             '- 사이즈는 기존 17개 표기 중 선택하는 분류입니다. 처음 보는 사이즈 글자를 자유롭게 읽는 OCR이 아닙니다.',
             '- 성별·연령은 판넬 기준 성인 남성/성인 여성/아동 3종입니다. 아동 남아는 사용자 임시 규칙이며 남녀를 학습한 결과가 아닙니다.',
             '- 기존 라벨을 재사용했습니다. 독립 담당자 전수 검수 자료나 새로운 촬영일의 독립 시험이 아닙니다. 추가 283벌도 이전에 검토한 자료입니다.',
             '- 아동 170 전체 2벌, 평가 1벌 등 적은 표본의 정답률을 일반화하지 마세요. 성인 2XS·아동 60/70은 학습·검증하지 못했습니다.',
             '- 원본은 사진 전체를 512×512 안에 축소·여백 처리합니다. 판넬 방식보다 글자가 작아지는 조건도 비교에 포함됩니다.',
             '- 필수 보류 기준이나 신뢰 점수 보정은 아직 정하지 않았습니다. 이 결과만으로 자동 확정을 적용하지 않습니다.',
             '- 앱·설치파일에 새 모델을 연결하지 않았습니다. 기존 모델 파일은 보존하되 이번 실험에서 사용하지 않았습니다.',
             '- 원본 사진·기존 라벨 변경, 전역 패키지 설치, 사진 업로드, Google 쓰기, 커밋·푸시·배포는 하지 않았습니다.', "",
             "방법과 재실행 안내: [FOUR_MODEL_COMPARISON.md](../../docs/FOUR_MODEL_COMPARISON.md).", ""]
    with (output / "RESULTS.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(text))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("model-results/20261024-four-model-comparison"))
    write_report(parser.parse_args().output)
