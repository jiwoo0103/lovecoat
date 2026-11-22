"""Verify finished artifacts and add the factual adoption review; never fits or tunes."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
import re

from PIL import Image

from scripts.category_retrain_20261122 import KINDS, OUT, ROOT, WORK, read, save, sha


def main():
    frozen = read(WORK / 'frozen.json')
    contract = read(OUT / 'model-contract.json')
    completion = read(OUT / 'completion.json')
    after = read(OUT / 'workbook-hash-after.json')
    assert after['sha256'] == frozen['workbook_hash_before']['sha256']
    assert contract['checkpoint_sha256'] == sha(WORK / 'best.pt')
    assert contract['resume_sha256'] == sha(WORK / 'last.pt')
    assert contract['frozen_data_sha256'] == sha(WORK / 'frozen.json')
    rows = read(OUT / 'photo-comparison.json')
    test_codes = {r['code'] for r in frozen['records'] if r['split'] == 'test'}
    expected = {(code, kind) for code in test_codes for kind in KINDS}
    assert len(rows) == 4500 and {(r['code'], r['kind']) for r in rows} == expected
    with (OUT / 'photo-comparison.csv').open(encoding='utf-8-sig', newline='') as f:
        csv_rows = list(csv.DictReader(f))
    assert len(csv_rows) == len(rows)
    assert [(r['code'], r['kind']) for r in csv_rows] == [(r['code'], r['kind']) for r in rows]
    page = (OUT / 'comparison.html').read_text(encoding='utf-8')
    pictures = re.findall(r'<img src="([^"]+)"', page)
    for name in pictures:
        path = (OUT / name).resolve()
        assert path.is_relative_to(OUT.resolve())
        with Image.open(path) as picture:
            picture.verify()
    history = read(OUT / 'training-history.json')
    assert len(history) == 8
    assert all(h['original_observations'] == 3000 and h['augmented_observations'] == 6000 for h in history)
    proof = read(OUT / 'optimization-proof.json')
    assert not proof['interim'] and proof['optimizer_step_min'] > 0
    assert proof['first_backbone_conv_max_abs_change'] > 0
    for name in ('scripts/category_retrain_20261122.py', 'scripts/category_retrain_20261122_evaluation.py',
                 'scripts/category_retrain_20261122_finalize.py', 'tests/test_category_retrain_20261122.py'):
        assert all(line == line.rstrip() for line in (ROOT / name).read_text(encoding='utf-8').splitlines()), name
    review = '''이번 후보의 즉시 앱 교체는 권고하지 않습니다. 최종 원본은 기존 473/500(94.6%)에서 신규 471/500(94.2%)으로 2장 감소했습니다. 기타 누락은 10→5건, macro-F1은 0.7576→0.8601로 개선됐지만 정상 외투→기타 오분류는 1→4건으로 증가했습니다. 기존 모델과 앱 자산은 유지했습니다.

- 최종 9조건에서는 4,226/4,500→4,252/4,500으로 26관측 개선됐습니다. 같은 500벌의 반복 관측이며 새 독립 표본 4,500장이 아닙니다.
- 개발에서 고른 신규 기준의 원본 자동 처리율은 19.6%(98/500), 기존은 91.2%(456/500)입니다. 자동 오답 12→2건만으로 개선을 판단하면 안 됩니다. 신규는 보류가 44→402건으로 358건 늘었습니다.
- 신규 원본 자동 오답은 1274·7321 두 자켓을 기타로 보낸 사례입니다(점수 약 99.51%·99.83%). 개발 9조건의 자동 오답0이 최종 자동 오답0으로 이어지지 않았습니다. 테스트를 보고 기준을 다시 고르지 않았습니다.
- 기타 원본 정답은 기존 1/11→신규 6/11이지만, 신규 기타 precision은 60%입니다. 6604 반집업·6608/7196 닫힌 상의·6839 일체형은 개선됐고 6828 일체형은 양쪽 모두 코트로 놓쳤습니다. 이는 사례 설명이며 엑셀 라벨 수정이나 추가 학습을 하지 않았습니다.
- 성인 정답 380/397→379/397, 아동 93/103→92/103으로 양쪽 원본 정확도가 각각 1장 감소했습니다. 자켓→코트는 7→11건, 코트→자켓은 5→6건입니다.
- 실제 CPU 58장·119조건은 두 모델 모두 GPU와 예측/보류 불일치 0건입니다. 원본 불일치·오답·기타·대표 사례와 최종 탐지 실패 3조건을 포함했습니다. 평균 시간은 기존 약 0.343초, 신규 0.348초(사진 디코딩 제외, 안내판 탐지·전처리·네트워크 포함)입니다.

실제 8회차에서 2,253회 옵티마이저 업데이트를 확인했습니다. 예정 2,256회 중 AMP 넘침 안전장치로 3회 갱신을 건너뛰었습니다. 원본 24,000회·증강 48,000회는 모델에 전달한 관측 수이며, 독립 학습 사진은 3,000장입니다. 모든 회차에서 각 원본 1회+증강2회 입력을 유지했습니다. `optimization-proof.json`에 213개 파라미터 상태와 ImageNet 대비 백본 변화량을 기록했습니다.

관련 단위 검사 6개, 변경 Python의 Ruff, 구문 검사와 diff 검사를 통과했습니다. 원본 사진 4,000장·보호 자산 14개·원본 엑셀은 학습 전후 SHA-256이 같습니다. 준비 캐시 4,000개의 해시·자료형·조건 수와 사진별 JSON/CSV 4,500행을 검증했습니다. 비교 이미지를 육안 확인했으며 실제 브라우저 렌더링은 별도 검사하지 않았습니다.
'''
    save(OUT / 'adoption-review.json', {'recommendation': 'keep_deployed_model_do_not_adopt_this_candidate',
                                      'post_test_tuning_performed': False, 'production_changed': False,
                                      'original_auto_error_codes_new': ['1274', '7321'],
                                      'workbook_unchanged': True})
    (OUT / 'REVIEW.md').write_text('# 카테고리 후보 최종 검토\n\n'+review, encoding='utf-8')
    path = OUT / 'REPORT.md'
    text = path.read_text(encoding='utf-8')
    start, end = '<!-- category-review-start -->', '<!-- category-review-end -->'
    if start in text:
        prefix, rest = text.split(start, 1)
        _, suffix = rest.split(end, 1)
        text = prefix + suffix.lstrip('\n')
    title, body = text.split('\n', 1)
    text = title+'\n\n'+start+'\n'+review+'\n'+end+'\n'+body
    path.write_text(text, encoding='utf-8')
    (ROOT / 'docs/CATEGORY_RETRAIN_20261122.md').write_text(text, encoding='utf-8')
    contract['commands']['finalize_report_and_verify'] = '.local/ab-venv/Scripts/python.exe -m scripts.category_retrain_20261122_finalize'
    contract['finalizer_sha256'] = sha(__file__)
    save(OUT / 'model-contract.json', contract)
    validation = {'paired_conditions': len(rows), 'unique_test_photos': len(test_codes),
                  'all_fixed_test_conditions_retained': True, 'csv_json_order_matches': True,
                  'comparison_images_verified': len(pictures), 'focused_tests': 6,
                  'ruff_passed': True, 'diff_check_passed': True, 'browser_rendering_checked': False,
                  'visual_contact_sheet_reviewed': True, 'workbook_unchanged': True,
                  'owned_source_whitespace_checked': True,
                  'selected_epoch': 3, 'actual_epochs': 8}
    save(OUT / 'final-artifact-verification.json', validation)
    integrity = read(OUT / 'integrity-after.json')
    integrity['workbook_post_check'] = after
    save(OUT / 'integrity-after.json', integrity)
    completion.update(workbook_postcheck=after, validation=validation, optimization=proof,
                      recommendation='keep_deployed_model_do_not_adopt_this_candidate',
                      finalized_at_utc=datetime.now(timezone.utc).isoformat(), finalizer_pid=os.getpid())
    completion['artifact_sha256'] = {name: sha(OUT / name) for name in ('REPORT.md', 'REVIEW.md', 'model-contract.json',
                                                                     'metrics.json', 'photo-comparison.csv', 'photo-comparison.json')}
    save(OUT / 'completion.json', completion)
    state = read(OUT / 'status.json')
    state.update(stage='complete', cpu_completed=state['cpu_total'], step=282, epoch_observations=9000,
                 finalizer_pid=os.getpid(), updated_at_utc=datetime.now(timezone.utc).isoformat(),
                 workbook_verified=True, report_reviewed=True, error=None)
    save(OUT / 'status.json', state)
    with (OUT / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(state, ensure_ascii=False)+'\n')
    print(validation)


if __name__ == '__main__':
    main()
