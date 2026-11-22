"""Paired development selection, frozen test evaluation and real CPU verification."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
import html
import json
import os
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from scripts.category_retrain_20261122 import (
    BASE, CLASSES, INITIAL, KINDS, OUT, ROOT, WORK, build, changed, configure,
    infer, metrics, prepared_image, read, save, sha, status,
)


def select_threshold(predictions):
    # Include the next float above every bad confidence. No test access here.
    candidates = sorted({0., 1.000001} | {p['score'] for p in predictions}
                        | {float(np.nextafter(p['score'], float('inf'))) for p in predictions})
    for threshold in candidates:
        accepted = [p for p in predictions if p['valid'] and p['score'] >= threshold]
        if not any(p['predicted'] != p['truth'] for p in accepted):
            return threshold
    raise AssertionError('No threshold')


def automatic(predictions, threshold=None):
    accepted = [p for p in predictions if p['valid'] and (not p['baseline_review'] if threshold is None else p['score'] >= threshold)]
    return {'automatic': len(accepted), 'review': len(predictions)-len(accepted),
            'coverage': len(accepted)/max(1, len(predictions)),
            'automatic_errors': sum(p['predicted'] != p['truth'] for p in accepted),
            'threshold': 'deployed sleeve90/length90 with long-sleeveless always held' if threshold is None else threshold}


def summary(predictions, threshold=None):
    def one(rows):
        return {**metrics(rows), 'automation': automatic(rows, threshold)}
    original = [p for p in predictions if p['kind'] == 'original']
    codes = {p['code'] for p in predictions}
    grouped = {c: [p for p in predictions if p['code'] == c] for c in codes}
    return {'original': one(original), 'nine_conditions': one(predictions),
            'by_condition': {k: one([p for p in predictions if p['kind'] == k]) for k in KINDS},
            'age_original': {age: one([p for p in original if p['age'] == age]) for age in ('성인', '유아동')},
            'age_nine_conditions': {age: one([p for p in predictions if p['age'] == age]) for age in ('성인', '유아동')},
            'garments_correct_in_all_nine': sum(all(p['truth'] == p['predicted'] for p in pp) for pp in grouped.values()),
            'unique_garments': len(codes), 'observations_are_correlated_not_independent': True}


def paired(old, new, threshold):
    assert [(p['code'], p['kind']) for p in old] == [(p['code'], p['kind']) for p in new]
    result = []
    for a, b in zip(old, new):
        result.append({'code': a['code'], 'group': a['group'], 'kind': a['kind'], 'age': a['age'], 'truth': a['truth'],
                       'old': a['predicted'], 'new': b['predicted'], 'old_score': a['score'], 'new_score': b['score'],
                       'old_correct': a['truth'] == a['predicted'], 'new_correct': b['truth'] == b['predicted'],
                       'old_review': a['baseline_review'], 'new_review': not b['valid'] or b['score'] < threshold,
                       'old_valid': a['valid'], 'new_valid': b['valid']})
    return result


def pair_summary(rows):
    return {'old_only_correct': sum(p['old_correct'] and not p['new_correct'] for p in rows),
            'new_only_correct': sum(not p['old_correct'] and p['new_correct'] for p in rows),
            'both_correct': sum(p['old_correct'] and p['new_correct'] for p in rows),
            'both_wrong': sum(not p['old_correct'] and not p['new_correct'] for p in rows),
            'predictions_disagree': sum(p['old'] != p['new'] for p in rows),
            'jacket_to_coat_old': sum(p['truth'] == '자켓' and p['old'] == '코트' for p in rows),
            'coat_to_jacket_old': sum(p['truth'] == '코트' and p['old'] == '자켓' for p in rows),
            'jacket_to_coat_new': sum(p['truth'] == '자켓' and p['new'] == '코트' for p in rows),
            'coat_to_jacket_new': sum(p['truth'] == '코트' and p['new'] == '자켓' for p in rows)}


def verify_integrity(data):
    for path, digest in data['protected'].items():
        if sha(path) != digest:
            raise ValueError('Protected asset changed: ' + path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        hashes = list(pool.map(lambda r: sha(r['source']), data['records']))
    mismatch = [r['code'] for r, h in zip(data['records'], hashes) if h != r['sha256']]
    if mismatch:
        raise ValueError('Source changed: ' + str(mismatch))
    save(OUT / 'integrity-after.json', {'original_photos_checked': len(hashes), 'all_unchanged': True,
                                       'protected_assets_checked': len(data['protected']), 'all_protected_unchanged': True,
                                       'workbook_post_check': 'separate specific-file read required; see workbook-hash-after.json',
                                       'best_sha256': sha(WORK / 'best.pt')})


def cpu_verify(data, pairs, threshold):
    import torch
    from coatdesk_ab.data import load_photo
    from coatdesk_v2.category import CategoryModel
    original = [p for p in pairs if p['kind'] == 'original']
    # All actual other, all original disagreement, plus class/age representatives.
    selected = {p['code'] for p in original if p['truth'] == '기타' or p['old'] != p['new']
                or not p['old_correct'] or not p['new_correct']}
    selected.update(p['code'] for p in pairs if not p['old_valid'] or not p['new_valid'])
    for c in CLASSES:
        for age in ('성인', '유아동'):
            selected.update(p['code'] for p in [p for p in original if p['truth'] == c and p['age'] == age][:2])
    rows = {r['code']: r for r in data['records']}
    items = [(c, 'original') for c in sorted(selected)]
    # Include a fixed transformed boundary condition for each selected photo.
    for c in sorted(selected):
        candidates = [p for p in pairs if p['code'] == c and p['kind'] != 'original']
        p = min(candidates, key=lambda p: abs(p['new_score']-threshold))
        items.append((c, p['kind']))
    items = sorted(set(items) | {(p['code'], p['kind']) for p in pairs if not p['old_valid'] or not p['new_valid']})
    baseline = CategoryModel(BASE)
    candidate = build()
    candidate.load_state_dict(torch.load(WORK / 'best.pt', map_location='cpu', weights_only=True)['state_dict'])
    candidate.eval()
    gpu_pairs = {(p['code'], p['kind']): p for p in pairs}
    output = []
    status('cpu_verification', cpu_total=len(items), cpu_completed=0)
    for i, (code, kind) in enumerate(items):
        photo = changed(load_photo(rows[code]['source']), kind)
        begin = time.perf_counter()
        try:
            old = baseline.analyze(photo)
            old_pred, old_review = old['raw_category'], old['needs_review']
        except ValueError:
            old_pred, old_review = 'FAILED_MASK', True
        old_seconds = time.perf_counter()-begin
        begin = time.perf_counter()
        # Actual candidate CPU runtime: fresh full-photo locator and preprocessing.
        location_new = baseline.locator.locate(photo)
        if location_new['valid']:
            arr = prepared_image(photo, location_new)
            x = torch.from_numpy(arr.transpose(2, 0, 1).copy()).float().unsqueeze(0)/255
            x = (x-x.new_tensor([.485, .456, .406])[None, :, None, None])/x.new_tensor([.229, .224, .225])[None, :, None, None]
            with torch.inference_mode():
                probs = candidate(x)[0].softmax(0).tolist()
            new_pred, score = CLASSES[int(np.argmax(probs))], max(probs)
            new_review = score < threshold
        else:
            new_pred, score, new_review = 'FAILED_MASK', 0., True
        new_seconds = time.perf_counter()-begin
        gpu = gpu_pairs[(code, kind)]
        output.append({'code': code, 'kind': kind, 'truth': rows[code]['category'], 'old_cpu': old_pred,
                       'new_cpu': new_pred, 'new_cpu_score': score, 'old_cpu_review': old_review,
                       'new_cpu_review': new_review, 'old_seconds': old_seconds, 'new_seconds': new_seconds,
                       'old_gpu_prediction_match': old_pred == gpu['old'], 'new_gpu_prediction_match': new_pred == gpu['new'],
                       'old_gpu_review_match': old_review == gpu['old_review'], 'new_gpu_review_match': new_review == gpu['new_review']})
        if i % 10 == 0:
            save(OUT / 'cpu-predictions.json', output)
            status('cpu_verification', cpu_completed=i+1, cpu_total=len(items))
    save(OUT / 'cpu-predictions.json', output)
    result = {'unique_photos': len(selected), 'conditions': len(output), 'all_other_originals_included': True,
              'all_original_disagreements_included': True,
              'all_test_mask_failure_conditions_included': True,
              'old_prediction_mismatches': sum(not p['old_gpu_prediction_match'] for p in output),
              'new_prediction_mismatches': sum(not p['new_gpu_prediction_match'] for p in output),
              'old_review_mismatches': sum(not p['old_gpu_review_match'] for p in output),
              'new_review_mismatches': sum(not p['new_gpu_review_match'] for p in output),
              'old_mean_seconds': sum(p['old_seconds'] for p in output)/len(output),
              'new_mean_seconds': sum(p['new_seconds'] for p in output)/len(output),
              'includes': 'full-photo runtime locator, mask, letterbox and FP32 network; image decode excluded'}
    save(OUT / 'cpu-summary.json', result)
    return result


def comparisons(data, pairs):
    from coatdesk_ab.data import load_photo
    rows = {r['code']: r for r in data['records']}
    original = [p for p in pairs if p['kind'] == 'original']
    chosen = [p for p in original if p['old'] != p['new'] or p['truth'] == '기타'
              or not p['old_correct'] or not p['new_correct']]
    thumbs = OUT / 'comparison-photos'
    thumbs.mkdir(exist_ok=True)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/malgun.ttf', 17)
    except OSError:
        font = ImageFont.load_default()
    cards = []
    contact = Image.new('RGB', (1000, max(1, (min(len(chosen), 40)+4)//5)*390), 'white')
    for i, p in enumerate(chosen):
        image = load_photo(rows[p['code']]['source'])
        image.thumbnail((190, 320))
        card = Image.new('RGB', (200, 390), 'white')
        card.paste(image, ((200-image.width)//2, 0))
        text = f"{p['code']} 정답 {p['truth']}\n기존 {p['old']}\n신규 {p['new']}"
        ImageDraw.Draw(card).multiline_text((4, 322), text, font=font, fill='black')
        card.save(thumbs / (p['code']+'.jpg'))
        if i < 40:
            contact.paste(card, ((i % 5)*200, (i//5)*390))
        cards.append(f'<figure><img src="comparison-photos/{p["code"]}.jpg" alt="{html.escape(text)}"><figcaption>{html.escape(text)}</figcaption></figure>')
    contact.save(OUT / 'comparison-contact.jpg')
    (OUT / 'comparison.html').write_text('<!doctype html><meta charset="utf-8"><title>카테고리 동일 사진 비교</title><style>body{font:16px sans-serif;margin:24px;background:#f5f7f9}.grid{display:flex;flex-wrap:wrap}figure{margin:8px;background:white;padding:12px}img{width:200px}figcaption{white-space:pre-line}</style><h1>동결 test500 기존·신규 비교</h1><p>원본 불일치·양쪽 오답·실제 기타 전부. 최신 엑셀 정답을 유지했습니다. 확인 필요는 기타와 별개입니다.</p><div class="grid">'+''.join(cards)+'</div>', encoding='utf-8')


def report(data, results, pair_result, cpu, timing, threshold):
    history = read(OUT / 'training-history.json')
    selected = read(WORK / 'training-complete.json')
    lines = ['# 2026-11-22 카테고리 직접 4종 재학습 비교', '',
             '앱 모델 교체·빌드·설치·커밋·푸시·배포는 수행하지 않았습니다. 아래 결과는 최신 사용자 엑셀과의 엄격 일치이며, 새로운 촬영의 독립 맹검 성능이 아닙니다.', '',
             "- EfficientNet-B0 ImageNet 범용 초기화 → 자켓/코트/조끼/기타 단일 softmax. 입력 320×320; 보조 출력 없음.",
             f"- 학습 3,000장, 개발 500장, 최종 500장. 기타 47/12/11장. 보수적 반복 연결 {data['repeat_audit']['links_verified']:,}개가 그룹·분할 안에 있음을 검증.",
             f"- 실제 {selected['epochs']} epoch. 원본 관측 {sum(h['original_observations'] for h in history):,}회와 증강 관측 {sum(h['augmented_observations'] for h in history):,}회를 구분. 매 epoch 원본당 1회+랜덤 증강2회.",
             '- 전체 종횡비·소매·밑단을 유지하는 letterbox, 현행 안내판 템플릿 위치+8px 흰 가림. 밝기/대비/색온도/좌우그림자/약한흐림. 공간 잘림과 반전 없음.',
             '- 개발 원본 macro-F1 최고 회차를 선택하고 개발 9조건의 관찰 자동 오답0에서 최대 처리율인 전역 보류 기준을 선택. 최종 결과로 튜닝하지 않음.',
             f'- 신규 보류 기준: {threshold:.9f}. 점수는 보정된 정확도가 아니며, 개발 오류0이 미래 오류0을 보장하지 않음.', '',
             '|최종 자료|모델|정답/관측|정확도|macro-F1|자동처리|자동오답|보류|', '|---|---|---:|---:|---:|---:|---:|---:|']
    for condition in ('original', 'nine_conditions'):
        for which in ('baseline', 'candidate'):
            m = results[which][condition]
            a = m['automation']
            lines.append(f"|{condition}|{which}|{m['correct']}/{m['count']}|{m['accuracy']:.4%}|{m['macro_f1']:.6f}|{a['automatic']}|{a['automatic_errors']}|{a['review']}|")
    lines += ['', '9조건 4,500관측은 500벌의 반복 관측이며 독립 사진 4,500장으로 해석하지 않습니다.', '', '|원본 클래스|모델|표본|precision|recall|F1|', '|---|---|---:|---:|---:|---:|']
    for c in CLASSES:
        for which in ('baseline', 'candidate'):
            m = results[which]['original']['per_class'][c]
            lines.append(f"|{c}|{which}|{m['support']}|{m['precision']:.6f}|{m['recall']:.6f}|{m['f1']:.6f}|")
    dev_results = read(OUT / 'dev-metrics.json')
    lines += ['', '|개발 자료|모델|정답/관측|macro-F1|자동처리|자동오답|', '|---|---|---:|---:|---:|---:|']
    for condition in ('original', 'nine_conditions'):
        for which in ('baseline', 'candidate'):
            m = dev_results[which][condition]
            a = m['automation']
            lines.append(f"|{condition}|{which}|{m['correct']}/{m['count']}|{m['macro_f1']:.6f}|{a['automatic']}|{a['automatic_errors']}|")
    lines += ['', '혼동표의 행=정답, 열=예측; 순서 자켓/코트/조끼/기타. 가림 실패는 별도 실패 수이며 정확도 분모에서 유지.', '']
    for which in ('baseline', 'candidate'):
        lines += [f"- {which}: `{results[which]['original']['confusion_rows_truth_columns_prediction']}`",
                  f"  기타 누락 {results[which]['original']['other_missed']}, 정상외투→기타 {results[which]['original']['outerwear_to_other']}, 입력실패 {results[which]['original']['failures']}."]
    lines += ['', '|원본 연령|모델|정답/표본|macro-F1|', '|---|---|---:|---:|']
    for age in ('성인', '유아동'):
        for which in ('baseline', 'candidate'):
            m = results[which]['age_original'][age]
            lines.append(f"|{age}|{which}|{m['correct']}/{m['count']}|{m['macro_f1']:.6f}|")
    lines += ['', '원본 양방향 오류: `'+json.dumps(pair_result['original'], ensure_ascii=False)+'`.',
              '9조건 양방향 오류: `'+json.dumps(pair_result['nine_conditions'], ensure_ascii=False)+'`.', '',
              'CPU 실제 경로: `'+json.dumps(cpu, ensure_ascii=False)+'`.', '',
              'GPU 속도(동일 원본·9조건, 캐시320 I/O+전처리+네트워크; 위치탐지 제외): `'+json.dumps(timing, ensure_ascii=False)+'`.', '',
              '노출 이력: `'+json.dumps(data['exposure_counts'], ensure_ascii=False)+'`.',
              '과거 모델·라벨 검토·개발/최종 평가에 노출된 사진을 포함합니다. 알려진 과거 gradient-fit 그룹은 새 학습에만 유지했고 현행 배포 학습 집합과의 누수를 검사했습니다. 미기록 과거 노출까지 부재를 보장하지 않습니다.', '',
              '원본 사진 4,000장과 현행 배포 모델·전처리·참조 자산, ImageNet 초기화의 SHA-256 불변을 재검증했습니다. 엑셀 최종 원본 해시 확인은 workbook-hash-after.json에 별도 기록합니다. 어린이 원문 없는 행은 카테고리 라벨만 사용하고 사이즈 정답을 추정하지 않았습니다.', '',
              '산출물: best.pt/last.pt는 .local/category-retrain-20261122에 있으며 model-contract.json에 해시·출력순서·재현명령을 기록했습니다. 사진별 predictions CSV/JSON, CPU 대조, 조건/연령별 전체 지표는 같은 결과 폴더입니다. comparison.html에 원본 불일치와 기타 사진을 모두 표시합니다.', '',
              '기타가 11벌뿐이므로 세부 옷종류 일반화 판단에는 한계가 있습니다. 최종 오답을 제외하거나 정답을 변경하지 않았습니다. 채택 판단과 앱 통합은 별도입니다.']
    text = '\n'.join(lines)+'\n'
    (OUT / 'REPORT.md').write_text(text, encoding='utf-8')
    (ROOT / 'docs/CATEGORY_RETRAIN_20261122.md').write_text(text, encoding='utf-8')


def evaluate_all(data):
    import torch
    from scripts.retraining_20261122_gpu import gpu_lease
    configure()
    dev = [r for r in data['records'] if r['split'] == 'dev']
    test = [r for r in data['records'] if r['split'] == 'test']
    all_predictions, timing = {}, {}
    status('waiting_gpu_evaluation')
    with gpu_lease('category'):
        candidate = build()
        candidate.load_state_dict(torch.load(WORK / 'best.pt', map_location='cpu', weights_only=True)['state_dict'])
        candidate.cuda().eval()
        dev_path = OUT / 'dev-candidate.json'
        if dev_path.exists():
            dev_predictions = read(dev_path)
        else:
            status('evaluating_dev_policy')
            dev_predictions, _ = infer(candidate, dev, KINDS)
            save(dev_path, dev_predictions)
        threshold = select_threshold(dev_predictions)
        save(OUT / 'selected-policy.json', {'threshold': threshold, 'selected_using': 'dev500 fixed9 only',
                                          'dev': summary(dev_predictions, threshold), 'test_used': False})
        for which in ('candidate', 'baseline'):
            path = OUT / ('test-'+which+'.json')
            model = candidate if which == 'candidate' else build(baseline=True).cuda().eval()
            if which == 'baseline':
                dev_old_path = OUT / 'dev-baseline.json'
                if dev_old_path.exists():
                    dev_old = read(dev_old_path)
                else:
                    status('evaluating_dev_baseline')
                    dev_old, dev_old_timing = infer(model, dev, KINDS, baseline=True)
                    save(dev_old_path, dev_old)
                    save(OUT / 'timing-dev-baseline.json', dev_old_timing)
                save(OUT / 'dev-metrics.json', {'baseline': summary(dev_old), 'candidate': summary(dev_predictions, threshold)})
            if path.exists():
                predictions = read(path)
                t = read(OUT / ('timing-'+which+'.json'))
            else:
                status('evaluating_test', evaluating_model=which)
                predictions, t = infer(model, test, KINDS, baseline=which == 'baseline')
                save(path, predictions)
                save(OUT / ('timing-'+which+'.json'), t)
            all_predictions[which], timing[which] = predictions, t
            if which == 'baseline':
                model.cpu()
        candidate.cpu()
        torch.cuda.empty_cache()
    pairs = paired(all_predictions['baseline'], all_predictions['candidate'], threshold)
    save(OUT / 'photo-comparison.json', pairs)
    with (OUT / 'photo-comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    results = {w: summary(p, threshold if w == 'candidate' else None) for w, p in all_predictions.items()}
    pair_result = {'original': pair_summary([p for p in pairs if p['kind'] == 'original']), 'nine_conditions': pair_summary(pairs)}
    save(OUT / 'metrics.json', results)
    save(OUT / 'paired-errors.json', pair_result)
    cpu = cpu_verify(data, pairs, threshold)
    verify_integrity(data)
    comparisons(data, pairs)
    checkpoint = torch.load(WORK / 'best.pt', map_location='cpu', weights_only=True)
    contract = {'checkpoint': str(WORK / 'best.pt'), 'checkpoint_sha256': sha(WORK / 'best.pt'),
                'resume_checkpoint': str(WORK / 'last.pt'), 'resume_sha256': sha(WORK / 'last.pt'),
                'initialization': str(INITIAL), 'initialization_sha256': sha(INITIAL),
                'baseline': str(BASE / 'model.pt'), 'baseline_sha256': sha(BASE / 'model.pt'),
                'baseline_architecture': 'efficientnet_b0_sleeve_length_2x2',
                'baseline_output_order': ['sleeves_present', 'sleeves_absent', 'length_normal', 'length_long'],
                'baseline_activation': 'two separate pair softmax; not a direct four-class softmax',
                'architecture': checkpoint['architecture'], 'classes': CLASSES, 'input_side': 320,
                'best_epoch': checkpoint['epoch'], 'policy': {'threshold': threshold, 'other_is_real_class': True, 'review_is_not_other': True},
                'preprocessing': data['protocol'], 'frozen_data_sha256': sha(WORK / 'frozen.json'),
                'implementation_hashes': {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'scripts/category_retrain_20261122.py', Path(__file__))},
                'executed_training_source_snapshot': str(WORK / 'training-source-frozen.py'),
                'executed_training_source_sha256': sha(WORK / 'training-source-frozen.py'),
                'source_note': 'Final trainer snapshot retained after pre-training checkpoint-copy fix. Initial preparation snapshot and handoff audit retained separately.',
                'commands': {'resume_training_and_evaluate': '.local/ab-venv/Scripts/python.exe -u -m scripts.category_retrain_20261122 all',
                             'evaluate_frozen': '.local/ab-venv/Scripts/python.exe -u -m scripts.category_retrain_20261122 evaluate'},
                'monitoring': {'status': str(OUT / 'status.json'), 'event_log': str(OUT / 'events.jsonl'),
                               'epoch_history': str(OUT / 'training-history.json'), 'pid_field': 'training_pid in status.json'},
                'environment': {'torch': torch.__version__, 'numpy': np.__version__, 'pid': os.getpid(), 'device': 'NVIDIA RTX3060; GPU serialized with category gpu_lease'},
                'external_state_changed': False, 'production_changed': False}
    save(OUT / 'model-contract.json', contract)
    report(data, results, pair_result, cpu, timing, threshold)
    complete = {'completed_at_utc': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                'checkpoint_sha256': contract['checkpoint_sha256'], 'best_epoch': checkpoint['epoch'],
                'metrics': {w: results[w]['original'] for w in results}, 'paired_original': pair_result['original'],
                'cpu': cpu, 'report': str(OUT / 'REPORT.md'), 'production_changed': False,
                'workbook_postcheck': 'specific-file verification pending until workbook-hash-after.json is written'}
    save(OUT / 'completion.json', complete)
    status('complete', best_epoch=checkpoint['epoch'], error=None)
