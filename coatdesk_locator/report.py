from __future__ import annotations

import argparse
from pathlib import Path

from coatdesk_ab.data import read
from .evaluate import OUTPUT


def fraction(n, total):
    return f"{n}/{total} ({n / total:.2%})"


def report(output):
    result = read(output / "results.json")
    benchmark = read(output / "cpu-benchmark.json")
    predictions = read(output / "predictions.json")
    review = read(output / "crop-visual-review.json")
    labels = read(output / "box-labels.json")
    m = result["metrics"]["all_445"]
    text = ["# 판넬 위치 AI 학습 결과", "", "2026-10-25. 원본 사진 → 위치 AI → 판넬 전체 자르기 → 기존 성별·사이즈 모델로 평가했습니다.", "",
            "**성별·사이즈 모델은 재학습하지 않았으며 두 모델 모두 AI가 자른 판넬 전체만 입력받습니다.** 앱과 설치파일에는 아직 연결하지 않았습니다.", "",
            "## 검증 결과", "", "| 자료 | 방식 | 성별·연령 | 사이즈 | 둘 다 정답 |", "|---|---|---|---|---|"]
    for cohort, name in (("all_445", "전체 445장"), ("earlier_162", "기존 검증 162장"), ("additional_283", "추가 성인 283장")):
        a = result["metrics"][cohort]
        old = result["previous_template_classifier_metrics"][cohort]
        n = a["count"]
        text += [f'| {name} | 기존 코드 위치 찾기 | {fraction(old["shape"]["correct"], n)} | {fraction(old["size"]["correct"], n)} | {fraction(old["joint_correct"], n)} |',
                 f'| {name} | 학습한 위치 AI | {fraction(a["shape_correct"], n)} | {fraction(a["size_correct"], n)} | {fraction(a["both_correct"], n)} |']
    text += ["", "## 판넬 위치와 실제 자르기", "",
             f'- 점수 0.5 이상 위치 검출: {fraction(m["located"], 445)}.',
             f'- 검수한 정답 박스와 IoU 0.75 이상: {fraction(m["iou_75"], 445)}. 평균 IoU {m["mean_iou"]:.4f}.',
             f'- 여백 8%를 더한 추출 영역이 정답 박스의 99% 이상 포함: {fraction(m["reference_box_coverage_99"], 445)}.',
             f'- 같은 정답 박스 기준 기존 코드의 99% 이상 포함은 {result["template_box_reference_coverage_99"]}/445장입니다.',
             '- 박스 포함률은 검수한 사각형과의 기하학적 비교입니다. 머리 픽셀의 정밀한 분할 정답이나 새로운 촬영일의 성공률은 아닙니다.',
             f'- 평가 후 실제 추출 영역 전체를 Codex가 육안 검토했습니다: {len(review["codes"])}장. 독립 담당자 검수는 아닙니다.',
             f'- 육안 검토 메모: {review["notes"]}', "",
             "## 실제 추출 예시", "", "![학습된 위치 AI가 실제로 자른 판넬](판넬_AI_예시.jpg)", "",
             "위 예시는 최종 검증 사진에서 성인 남성·성인 여성·아동 각 4장으로, 평가 전에 코드 목록을 정했습니다. 수동으로 잘라 교체한 이미지가 아닙니다. 모든 사진은 [검수보기.html](검수보기.html)에서 확인할 수 있습니다.", "",
             "## 오류와 판단 보류", "", "| 코드 | 정답 | 예측 | 위치 점수 | 기준 박스 포함률 |", "|---|---|---|---:|---:|"]
    errors = [r for r in predictions if not (r["shape_correct"] and r["size_correct"])]
    if not errors:
        text.append("| — | — | 이번 445장에서는 성별·사이즈 오답 없음 | — | — |")
    for row in errors:
        text.append(f'| {row["code"]} | {row["truth_shape"]}/{row["truth_size"]} | {row["predicted_shape"]}/{row["predicted_size"]} | {row["location"]["score"]:.3f} | {row["coverage"]:.1%} |')
    r = next(r for r in predictions if r["code"] == "1345")
    text += ["", f'이전 위치 오류가 있던 1345번: 현재 위치 점수 {r["location"]["score"]:.3f}, 정답 박스 포함 {r["coverage"]:.1%}, 성별·연령 {r["predicted_shape"]}, 사이즈 {r["predicted_size"]}. 이 사진은 최종 검증용이며 위치 모델 학습에 넣지 않았습니다.', "",
             "## GPU 없는 CPU 실행 시간", "",
             f'{benchmark["cpu"]}, CPU {benchmark["torch_threads"]}스레드. GPU 비활성화를 확인하고 같은 32장에 기존 방식과 새 방식을 번갈아 실행했습니다. 모델·런타임의 첫 로딩 시간은 제외하고 JPEG 읽기부터 실제 위치 찾기·자르기·두 분류기 추론까지 포함합니다.', "",
             "| 방식 | 위치 찾기 평균 초/벌 | 전체 평균 초/벌 | 전체 95백분위 | GPU 평가와 달라진 코드 |", "|---|---:|---:|---:|---|"]
    for mode, name in (("template", "기존 코드"), ("ai", "학습한 위치 AI")):
        b = benchmark["summary"][mode]
        t = b["timings"]
        comparison = (", ".join(b["gpu_prediction_differences"]) or "없음") if mode == "ai" else "이번 GPU 비교 대상 아님"
        text.append(f'| {name} | {t["locate_seconds"]["mean_seconds"]:.3f} | {t["total_seconds"]["mean_seconds"]:.3f} | {t["total_seconds"]["p95_seconds"]:.3f} | {comparison} |')
    if (output / "cpu-full-validation.json").exists():
        cpu = read(output / "cpu-full-validation.json")
        text += ["", f'추가로 CPU만 사용해 최종 검증 {cpu["count"]}장 전체를 원본부터 재추론했습니다. 성별·연령 {cpu["shape_correct"]}/{cpu["count"]}, 사이즈 {cpu["size_correct"]}/{cpu["count"]}, 둘 다 {cpu["both_correct"]}/{cpu["count"]}. GPU와 달라진 코드는 {", ".join(cpu["gpu_prediction_differences"]) or "없음"}입니다. 개별 기록은 `cpu-full-validation.json`에 있습니다.']
    text += ["", "앞서 0.35초/벌로 계산했던 값은 일반 COCO 모델의 속도로 추정한 값입니다. 위 표는 이번에 학습한 판넬 위치 모델과 실제 추출 사진으로 다시 측정한 결과입니다. 다른 PC의 처리시간 보장은 아닙니다.", "",
             "## 학습 자료와 방법", "",
             '- 기존 라벨 정면 1,090장과 원래 그룹 분할을 유지했습니다. 위치용 박스만 별도 파일로 추가했습니다.',
             f'- 기존 코드 박스를 초안으로 사용하고, 얼굴 안쪽 흰 종이·검은 윤곽으로 보정한 뒤 31개 페이지 전체를 Codex가 검수했습니다. 잔여 오류/과도한 박스 4건은 수동 수정했습니다: {", ".join(labels["corrections"])}. 기존 사진·성별·사이즈 라벨은 수정하지 않았습니다.',
             '- 박스에는 머리카락·귀·턱 전체와 경계 여유 2%를 포함합니다. 정답 박스는 픽셀 단위 마스크가 아닙니다.',
             '- 내부 학습 516장·조건 선택 129장, 최대 50회. 내부 점검에서 정답 박스 포함 수·IoU로 횟수를 정한 뒤, 일반 COCO 모델에서 새로 시작해 학습용 645장 전체로 최종 학습했습니다.',
             f'- 최종 학습 {result["training"]["epochs"]}회, 기록된 최종 학습 시간 {result["training"]["seconds"]:.1f}초. 실행 중단 시 마지막 완료 회차의 모델·최적화 상태·난수 상태부터 재개할 수 있도록 원자적 체크포인트를 저장했습니다.',
             '- SSDLite320 MobileNetV3-Large를 판넬 1종(+배경)으로 추가 학습했습니다. 일반 COCO 초기 모델의 호환 가중치를 사용하고 출력 크기가 다른 분류 출력은 초기화했습니다.',
             '- 작은 판넬에 맞춰 앵커 크기 범위를 0.10~0.70으로 미리 지정했습니다. 회전·확대축소·이동·뒤집기·밝기·대비를 보강하되 정답 머리가 화면 밖으로 잘리는 변환은 사용하지 않았습니다.',
             '- 최종 검증 445장은 학습·체크포인트 선택에 사용하지 않았으며, 결과를 본 뒤 라벨·추출 정책·가중치를 조정하지 않았습니다.', "",
             "## 파일과 한계", "",
             '- `detector.pt`, `model-card.json`: 학습한 위치 모델과 사용 설정·해시. 실행 코드는 `coatdesk_locator/pipeline.py`입니다.',
             '- `box-labels.json`, `review-events.json`: 별도 위치 정답과 검수 기록. 원본 좌표계와 분할·사진 해시를 포함합니다.',
             '- `predictions.json/csv`, `crops/`, `review-pages/`: 445장 개별 예측·점수·실제 추출 사진·검토 자료.',
             '- `training-logs/`, `train-protocol.json`: 고정 설정·코드 해시와 실제 학습 기록. 기존 분류 모델과 이전 코드의 해시는 불변을 확인했습니다.',
             '- 같은 촬영 자료에서 이미 검토했던 사진입니다. 독립 담당자의 박스 정답 검수와 새 촬영일의 검증은 수행하지 않았습니다.',
             '- 성별·연령은 판넬의 성인 남성/성인 여성/아동이며, 아동 남녀는 학습하지 않았습니다. 사이즈는 기존 17개 표기 중 고르는 분류입니다.',
             '- 사진에 판넬이 없거나 여러 개인 조건의 일반 성능은 검증하지 않았습니다. 위치 점수는 보정된 확률이 아닙니다.',
             '- 앱·설치파일 연결, 외부 사진 업로드, 전역 설치, Google 쓰기, 커밋·푸시·배포는 하지 않았습니다.', ""]
    with (output / "RESULTS.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(text))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    report(parser.parse_args().output)
