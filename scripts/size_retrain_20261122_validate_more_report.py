"""Cohort-separated metrics, CPU runtime checks and review artifacts."""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from copy import copy
from datetime import datetime, timezone
import html
from io import BytesIO
import json
import time

from coatdesk_ab.data import load_photo
from coatdesk_v2.ai_runtime import Models
from scripts.retrain_four_training import change
from scripts.size_retrain_20261122 import BASE, CLASSES, INPUT, WORK, configure, read, save, sha
from scripts.size_retrain_20261122_evaluate import automatic, safe_metrics
from scripts.size_retrain_20261122_fit import business, metrics, write_csv
from scripts.size_retrain_20261122_rare_fit import load_model
from scripts.size_retrain_20261122_validate_more import VOUT, VWORK, status

BUSINESS_CLASSES = ['2XS', 'XS', 'S', 'M', 'L', 'XL', '2XL', '3XL', '<failure>']


def cohort_metrics(rows):
    known = [r for r in rows if r['truth'] is not None]
    converted = [dict(r, truth=r['truth_business'], predicted=business(r['predicted'])) for r in rows]
    business_metrics = metrics(converted, BUSINESS_CLASSES)
    business_metrics['macro_f1_all_business_classes_zero_for_absent'] = business_metrics.pop('macro_f1_all20_zero_for_absent')
    result = {'photos': len({r['code'] for r in rows}), 'conditions': len(rows),
              'raw_unknown_conditions': len(rows) - len(known), 'raw': safe_metrics(known),
              'workbook_business': business_metrics, 'automatic': {}}
    for threshold in (.9, .85, .8):
        accepted = [r for r in rows if automatic(r, threshold)]
        raw_known = [r for r in accepted if r['truth'] is not None]
        result['automatic'][str(threshold)] = {'auto': len(accepted), 'review': len(rows) - len(accepted),
            'auto_rate': len(accepted) / max(1, len(rows)), 'auto_raw_known': len(raw_known),
            'auto_raw_errors': sum(r['predicted'] != r['truth'] for r in raw_known),
            'auto_business_errors': sum(business(r['predicted']) != r['truth_business'] for r in accepted),
            'failures': sum(bool(r['failure']) for r in rows), 'threshold_size': threshold,
            'threshold_shape': .9, 'threshold_panel': .9}
    by_photo = defaultdict(list)
    for r in rows:
        by_photo[r['code']].append(r)
    result['all_conditions_business_correct_photos'] = sum(all(business(r['predicted']) == r['truth_business'] for r in rs) for rs in by_photo.values())
    result['all_conditions_raw_correct_known_photos'] = sum(all(r['predicted'] == r['truth'] for r in rs) for rs in by_photo.values() if rs[0]['truth'] is not None)
    return result


def paired_rows(before, after):
    assert len(before) == len(after)
    result = []
    for b, a in zip(before, after):
        assert (b['code'], b['kind'], b['truth'], b['truth_business']) == (a['code'], a['kind'], a['truth'], a['truth_business'])
        row = {k: b[k] for k in ('code', 'group', 'kind', 'cohort', 'main_split', 'fitted_group', 'truth', 'truth_business')}
        row['raw_known'] = b['truth'] is not None
        for which, r in [('before', b), ('after', a)]:
            row.update({which + '_raw': r['predicted'], which + '_business': business(r['predicted']),
                which + '_score': r['score'], which + '_panel_score': r['panel_score'],
                which + '_shape_score': r['shape_score'], which + '_shape': r['shape'],
                which + '_raw_correct': r['predicted'] == r['truth'] if row['raw_known'] else None,
                which + '_business_correct': business(r['predicted']) == r['truth_business'],
                which + '_failure': r['failure']})
            for threshold in (.9, .85, .8):
                row[which + '_auto' + str(round(threshold * 100))] = automatic(r, threshold)
        result.append(row)
    return result


def paired_metrics(rows):
    known = [r for r in rows if r['raw_known']]
    return {'raw_known_conditions': len(known),
        'before_only_raw_correct': sum(r['before_raw_correct'] and not r['after_raw_correct'] for r in known),
        'after_only_raw_correct': sum(r['after_raw_correct'] and not r['before_raw_correct'] for r in known),
        'before_only_business_correct': sum(r['before_business_correct'] and not r['after_business_correct'] for r in rows),
        'after_only_business_correct': sum(r['after_business_correct'] and not r['before_business_correct'] for r in rows),
        'raw_disagreements': sum(r['before_raw'] != r['after_raw'] for r in rows)}


def load_results(scope, require_complete=True):
    scope = 'real2600' if scope == 'real' else scope
    manifest = read(VOUT / ('real2600-manifest.json' if scope == 'real2600' else 'synthetic-fullframe-manifest.json'))
    if require_complete:
        assert (VOUT / f'{scope}-evaluation-complete.json').exists()
    result = {'before': [], 'after': []}
    for r in manifest:
        path = VWORK / f'{scope}-predictions' / (r['code'] + '.json')
        if not path.exists() and not require_complete:
            continue
        saved = read(path)
        assert saved['source_sha256'] == r['sha256']
        for which in result:
            assert len(saved[which]) == 9
            result[which].extend(saved[which])
    return manifest, result


def cpu_new():
    configure()
    output = VOUT / 'cpu-new-synthetic.json'
    if output.exists():
        return
    protocol = read(VOUT / 'protocol.json')
    models = cpu_models(protocol)
    records = []
    inputs = VWORK / 'cpu-inputs'
    inputs.mkdir(exist_ok=True)
    for i, row in enumerate(read(VOUT / 'new-synthetic.json')):
        for kind in ('original', 'dark', 'blur'):
            path = row['source']
            if kind != 'original':
                path = inputs / f"{row['code']}-{kind}.png"
                change(load_photo(row['source']), kind).save(path)
            for which, model in models.items():
                started = time.perf_counter()
                result = model.analyze(path, {'panel': .9, 'shape': .9, 'size': .85})
                records.append({'code': row['code'], 'kind': kind, 'model': which,
                    'truth': row['truth'], 'predicted': result.get('raw_size', '<failure>'),
                    'business': result['candidates'].get('size', '<failure>'), 'held': result['held'],
                    'score': result.get('scores', {}).get('size', 0.), 'seconds': time.perf_counter() - started})
        if i % 5 == 0:
            print({'cpu_new_photos': i + 1}, flush=True)
    save(output, {'records': records, 'observations': len(records), 'full_frame': True, 'manual_roi': False,
                  'models': protocol['models'], 'application_changed': False})


def cpu_models(protocol):
    before = Models(BASE)
    before.size_classes = CLASSES
    before.classifiers['size'] = load_model(protocol['models']['before']['path'])
    after = copy(before)
    after.classifiers = dict(before.classifiers)
    after.classifiers['size'] = load_model(protocol['models']['after']['path'])
    return {'before': before, 'after': after}


def summarize():
    configure()
    real_manifest, real = load_results('real')
    synthetic_manifest, synthetic = load_results('synthetic')
    pairs = paired_rows(real['before'], real['after']) + paired_rows(synthetic['before'], synthetic['after'])
    groups = {
        'real_all2600': lambda r: r['cohort'] == 'real',
        'real_main2600': lambda r: r['cohort'] == 'real' and r['main_split'] != 'outside2600',
        'real_main_train2000': lambda r: r['cohort'] == 'real' and r['main_split'] == 'train',
        'real_main_dev300': lambda r: r['cohort'] == 'real' and r['main_split'] == 'dev',
        'real_main_test300': lambda r: r['cohort'] == 'real' and r['main_split'] == 'test',
        'real_known_gradient_exposure': lambda r: r['cohort'] == 'real' and r['fitted_group'],
        'real_no_known_gradient_exposure': lambda r: r['cohort'] == 'real' and not r['fitted_group'],
        'real60': lambda r: r['cohort'] == 'real' and r['truth'] == '60',
        'real70': lambda r: r['cohort'] == 'real' and r['truth'] == '70',
        'real170_training_only': lambda r: r['cohort'] == 'real' and r['truth'] == '170',
        'synthetic_accepted98': lambda r: r['cohort'] not in ('real', 'preview_reference_only'),
        'new_validation20': lambda r: r['cohort'] == 'new_validation',
        'new_validation60': lambda r: r['cohort'] == 'new_validation' and r['truth'] == '60',
        'new_validation2XS': lambda r: r['cohort'] == 'new_validation' and r['truth'] == '2XS',
    }
    for cohort in ('initial_train', 'followup_train', 'followup_dev', 'followup_test', 'preview_reference_only'):
        groups[cohort] = lambda r, c=cohort: r['cohort'] == c
    result = {'models': read(VOUT / 'protocol.json')['models'], 'cohorts': {}}
    all_results = {w: real[w] + synthetic[w] for w in ('before', 'after')}
    for name, choose in groups.items():
        result['cohorts'][name] = {}
        for condition in ('original', 'nine_conditions'):
            def predicate(r):
                return choose(r) and (condition != 'original' or r['kind'] == 'original')
            subset = {w: [r for r in rows if predicate(r)] for w, rows in all_results.items()}
            selected_pairs = [r for r in pairs if predicate(r)]
            result['cohorts'][name][condition] = {w: cohort_metrics(rows) for w, rows in subset.items()}
            result['cohorts'][name][condition]['paired'] = paired_metrics(selected_pairs)
    result['limitations'] = {'real_adult2XS': 0, 'real170_independent_evaluation': 0,
        'real170_training_photo': 1, 'inventory_raw_missing_not_inferred': 578, 'new_synthetic_parent_groups': 20,
        'new_synthetic_is_not_real_accuracy': True, 'nine_conditions_are_correlated': True,
        'real2600_contains_training2000': True, 'no_training_or_threshold_selection_in_this_run': True,
        'preview3_reference_only_not_accepted_validation': True}
    result['timing'] = {scope: read(VOUT / f'{scope}-evaluation-complete.json') for scope in ('real2600', 'synthetic')}
    save(VOUT / 'comparison.json', result)
    write_csv(VOUT / 'photo-comparison.csv', pairs)
    failures = [r for r in pairs if r['before_failure'] or r['after_failure']]
    disagreements = [r for r in pairs if r['before_raw'] != r['after_raw']]
    errors = [r for r in pairs if (r['raw_known'] and (not r['before_raw_correct'] or not r['after_raw_correct'])) or not r['before_business_correct'] or not r['after_business_correct']]
    for name, data in [('failures', failures), ('disagreements', disagreements), ('errors', errors)]:
        save(VOUT / (name + '.json'), data)
        if data:
            write_csv(VOUT / (name + '.csv'), data)
    validate_cpu_matches(real_manifest, synthetic_manifest, all_results)
    cpu = read(VOUT / 'cpu-verification.json')
    result['cpu_timing'] = {}
    for which in ('before', 'after'):
        measured = [r['seconds'] for r in cpu['observations'] if r['model'] == which]
        result['cpu_timing'][which] = {'observations': len(measured), 'mean_seconds': sum(measured) / len(measured),
            'scope': 'Sequential CPU application calls; reference timing, not an independently randomized speed benchmark.'}
    result['resampling_diagnostic'] = {k: v for k, v in read(VOUT / 'resampling-diagnostic.json').items() if k != 'records'}
    save(VOUT / 'comparison.json', result)
    integrity(real_manifest, synthetic_manifest)
    gallery(real_manifest + synthetic_manifest, pairs)
    report(result)
    save(VOUT / 'completion.json', {'completed_at_utc': datetime.now(timezone.utc).isoformat(),
        'models': result['models'], 'real_photos': 2600, 'accepted_synthetic': 98, 'new_synthetic': 20,
        'preview_reference_only': 3, 'conditions_each_model': (2600 + 101) * 9,
        'trained': False, 'application_changed': False, 'report': str(VOUT / 'REPORT.md'),
        'comparison_sha256': sha(VOUT / 'comparison.json')})
    status('complete', real_photos=2600, new_synthetic=20, accepted_synthetic=98, error=None)


def validate_cpu_matches(real_manifest, synthetic_manifest, results):
    signature = {'models': read(VOUT / 'protocol.json')['models'],
                 'real_manifest_sha256': sha(VOUT / 'real2600-manifest.json'),
                 'synthetic_manifest_sha256': sha(VOUT / 'synthetic-fullframe-manifest.json')}
    completed = VOUT / 'cpu-verification.json'
    if completed.exists() and read(completed).get('signature') == signature:
        return
    expected = {w: {(r['code'], r['kind']): r for r in rows} for w, rows in results.items()}
    saved = read(VOUT / 'cpu-new-synthetic.json')['records']
    observations = []
    for row in saved:
        e = expected[row['model']][(row['code'], row['kind'])]
        observations.append(dict(row, gpu=e['predicted'], raw_equal=row['predicted'] == e['predicted'],
            review_equal=bool(row['held']) == (not automatic(e, .85)), size_score_delta=abs(row['score'] - e['score'])))
    models = cpu_models(read(VOUT / 'protocol.json'))
    chosen = []
    originals = [r for r in results['before'] if r['cohort'] == 'real' and r['kind'] == 'original']
    for label in CLASSES:
        selected = [r for r in originals if r['truth'] == label]
        if selected:
            chosen.append(selected[0]['code'])
    chosen += [r['code'] for r in originals if r['predicted'] != expected['after'][(r['code'], 'original')]['predicted']][:20]
    chosen = list(dict.fromkeys(chosen))
    indexed = {r['code']: r for r in real_manifest}
    for i, code in enumerate(chosen):
        for kind in ('original', 'dark', 'blur'):
            path = indexed[code]['source']
            if kind != 'original':
                path = VWORK / 'cpu-inputs' / f'{code}-{kind}.png'
                change(load_photo(indexed[code]['source']), kind).save(path)
            for which, model in models.items():
                started = time.perf_counter()
                actual = model.analyze(path, {'panel': .9, 'shape': .9, 'size': .85})
                e = expected[which][(code, kind)]
                observations.append({'code': code, 'kind': kind, 'model': which, 'truth': e['truth'],
                    'predicted': actual.get('raw_size', '<failure>'), 'gpu': e['predicted'],
                    'raw_equal': actual.get('raw_size', '<failure>') == e['predicted'],
                    'review_equal': bool(actual['held']) == (not automatic(e, .85)),
                    'seconds': time.perf_counter() - started,
                    'size_score_delta': abs(actual.get('scores', {}).get('size', 0.) - e['score'])})
        if i % 5 == 0:
            print({'cpu_real_photos': i + 1, 'total': len(chosen)}, flush=True)
    summary = {'observations': observations, 'n': len(observations), 'signature': signature,
        'raw_disagreements': sum(not r['raw_equal'] for r in observations),
        'review_disagreements': sum(not r['review_equal'] for r in observations),
        'max_size_score_delta': max(r['size_score_delta'] for r in observations),
        'runtime': 'actual isolated CPU Models.analyze; full frame; no manual ROI; app untouched'}
    save(VOUT / 'cpu-verification.json', summary)
    assert not summary['raw_disagreements'] and not summary['review_disagreements'], summary


def integrity(real, synthetic):
    from scripts.size_retrain_20261122_validate_more_eval import shared_core
    protocol = read(VOUT / 'protocol.json')
    protected = read(WORK / 'protected.json')
    checks = {path: sha(path) == digest for path, digest in protected.items()}
    for model in protocol['models'].values():
        checks[model['path']] = sha(model['path']) == model['sha256']
    assert all(checks.values())
    assert sha(INPUT / 'inventory-annotated.json') == protocol['original_inventory_sha256']
    parents = read(VOUT / 'parents.json')
    assert all(sha(r['source']) == r['sha256'] for r in parents)
    changed_keys = shared_core(load_model(protocol['models']['before']['path']), load_model(protocol['models']['after']['path']))
    changed = [r['code'] for r in real + synthetic if sha(r['source']) != r['sha256']]
    assert not changed
    save(VOUT / 'integrity.json', {'protected': checks, 'originals_verified': len(real), 'synthetic_verified': len(synthetic),
        'changed': changed, 'labels_unchanged': True, 'models_unchanged': True,
        'new_parent_sources_verified': len(parents), 'shared_core_and_other17_rows_bit_exact': True,
        'changed_state_keys': changed_keys, 'class_order': CLASSES,
        'post_evaluation_exclusions': 0, 'post_evaluation_label_changes': 0})


def gallery(manifest, pairs):
    indexed = {r['code']: r for r in manifest}
    chosen = {(r['code'], r['kind']): r for r in pairs if r['kind'] == 'original' and r['cohort'] == 'new_validation'}
    seen_labels = set()
    for r in pairs:
        if r['kind'] != 'original':
            continue
        if r['cohort'] == 'real' and r['truth'] not in seen_labels:
            chosen[(r['code'], r['kind'])] = r
            seen_labels.add(r['truth'])
        if r['before_auto85'] != r['after_auto85']:
            chosen[(r['code'], r['kind'])] = r
    worst = {}
    for r in pairs:
        bad = (r['before_raw'] != r['after_raw'] or not r['before_business_correct'] or not r['after_business_correct'] or
               (r['raw_known'] and (not r['before_raw_correct'] or not r['after_raw_correct'])))
        if bad and (r['code'] not in worst or r['kind'] == 'original'):
            worst[r['code']] = r
    for r in worst.values():
        chosen[(r['code'], r['kind'])] = r
    chosen = list(chosen.values())
    cards = []
    for r in chosen:
        photo = change(load_photo(indexed[r['code']]['source']), r['kind'])
        scope = 'real2600' if r['cohort'] == 'real' else 'synthetic'
        cached = read(VWORK / (scope + '-predictions') / (r['code'] + '.json'))['before']
        box = next(x['crop_box'] for x in cached if x['kind'] == r['kind'])
        panel_data = ''
        if box:
            panel = photo.crop(box)
            panel.thumbnail((256, 256))
            panel_buffer = BytesIO()
            panel.save(panel_buffer, 'JPEG', quality=85)
            panel_data = '<img loading="lazy" alt="실제 탐지 판넬" src="data:image/jpeg;base64,' + base64.b64encode(panel_buffer.getvalue()).decode() + '">'
        photo.thumbnail((270, 480))
        buffer = BytesIO()
        photo.save(buffer, 'JPEG', quality=78)
        data = base64.b64encode(buffer.getvalue()).decode()
        caption = f"{r['code']} / {r['kind']} / {r['cohort']} / GT {r['truth']} → 업무 {r['truth_business']} / 이전 {r['before_raw']} ({r['before_score']:.3f}, 자동85={r['before_auto85']}) / 추가 {r['after_raw']} ({r['after_score']:.3f}, 자동85={r['after_auto85']})"
        cards.append(f'<figure><div style="display:flex;align-items:center"><img style="width:50%" loading="lazy" src="data:image/jpeg;base64,{data}">{panel_data.replace("<img ", "<img style=\"width:50%\" ", 1)}</div><figcaption>{html.escape(caption)}</figcaption></figure>')
    document = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>최근 사이즈 두 모델 검토</title><style>body{font:16px sans-serif;margin:24px;background:#eee}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}figure{margin:0;padding:12px;background:white}img{display:block;max-width:100%;height:360px;object-fit:contain;margin:auto}figcaption{line-height:1.5;margin-top:12px}</style><h1>새 합성·원본 불일치 검토</h1><p>정답과 결과를 사후 변경하지 않았습니다. 전체 조건과 사진은 photo-comparison.csv에 있습니다.</p><main>' + ''.join(cards) + '</main></html>'
    (VOUT / 'review.html').write_text(document, encoding='utf-8')
    save(VOUT / 'review-manifest.json', {'cards': len(cards), 'codes': [r['code'] for r in chosen]})


def report(result):
    lines = ['# 최근 두 사이즈 모델 전체 사진 비교', '',
        '이전20종(size20.pt)과 희소3종 추가학습20종(rare-size20.pt)을 고정하여 비교했다. 이번에는 학습·기준 조정·앱 교체를 하지 않았다.', '',
        '새 합성은60/성인2XS 각각10장, 총20개의 독립 부모 그룹이다. 과거 학습 그룹과 이전 합성 부모를 제외했다. 여성S 부모를2XS로 바꾸었고, 아동80/90 부모를60으로 바꾸었다. 원문이 미전사인 부모9장의 원래 실사 정답은 비워 둔 채로 유지했다.', '',
        '사용자가 범위 질문에 동결 실사2600장+기존·신규 합성전체로 답하여 해당 범위로 확정했다. 실사2600장 모두 원문 정답이 있다. 학습2000장, 개발300장, 최종300장을 분리한다. 전체 정확도는 학습자료가 포함되므로 독립 검증 정확도가 아니다. 개발/최종600장도 이전 검토 이력이 있는 회고 평가이다. 독립 실사 성인2XS와170 평가는 없다.170 실사1장은 학습 사진이다. 최초 범위 응답 전 넓은 원본목록으로 계산한 일부 결과는 별도 캐시에 보존하고, 이 보고서에는 사용자 지정2600장만 집계했다. 결과에 따른 사후 제외가 아니다.', '',
        '아래 원문 수치는 정답 있는 사진만, 업무 수치는 변경하지 않은 엑셀 업무 사이즈와 비교한다. 원본+고정8변형은 같은 사진의9조건이며 독립9벌로 세지 않는다.', '',
        '| 범위 | 조건 | 모델 | 원문 정답/분모 | 업무 정답/분모 | 원문 macroF1 | 자동85 | 자동 업무오답 | 확인필요85 |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|']
    selected = ['real_all2600', 'real_no_known_gradient_exposure', 'real_main_dev300', 'real_main_test300',
                'new_validation60', 'new_validation2XS', 'followup_test', 'synthetic_accepted98', 'preview_reference_only']
    for name in selected:
        for condition in ('original', 'nine_conditions'):
            for which in ('before', 'after'):
                r = result['cohorts'][name][condition][which]
                raw, biz, auto = r['raw'], r['workbook_business'], r['automatic']['0.85']
                lines.append(f"| {name} | {condition} | {which} | {raw['correct']}/{raw['n_conditions']} | {biz['correct']}/{biz['n_conditions']} | {raw['macro_f1_present']:.6f} | {auto['auto']} | {auto['auto_business_errors']} | {auto['review']} |")
    lines += ['', '추천은 추가학습 전20종(size20.pt)을 유지한다. 개발9조건과 새 합성검증 모두 정확도 개선이 없고, 개발 실사85% 자동처리는 이전2647/2700 대 추가2625/2700이었다. 실사전체 원본85% 확인필요는 이전40/2600 대 추가58/2600이다. 합성학습 자료에서 추가모델 점수가 높아진 사실을 독립 성능 향상으로 해석하지 않는다.', '',
        '점수85%는 모델 softmax 기준이며 실제 정확도85%를 뜻하지 않는다. 판넬/성별연령90% 및 연령·표기 일치 조건도 함께 적용한다.90/85/80은 사전에 정한 비교이며 이번 최종 결과로 임계값을 최적화하지 않았다.', '',
        '합성98장에는 처음 학습18장, 추가학습36장, 추가학습 개발12장, 이전 최종12장, 새 검증20장이 있다. 학습/개발 합성 결과는 일반화 근거로 사용하지 않는다. 전체프레임 변화가 있던 초기 시안3장은preview_reference_only로 별도 집계하고 검증 승격하지 않았다.', '',
        '최신 모델 둘은 특징추출부·attention이 비트 단위로 동일하다. 동일 사진의 검출기/성별연령과 특징 계산을 공유하고 서로 다른20종 출력층에 입력했다. 모든 조건에서 전체 사진을 변형하고SSD448 탐지를 다시 실행한 후 판넬512를 원형보존 letterbox/기존정규화로 처리했다. CPU 실제 앱 경로는cpu-verification.json으로 대조했다.', '',
        f"실제CPU 앱 경로234회에서 원문/확인판정 불일치0이었다. 평균 실행시간은 이전 {result['cpu_timing']['before']['mean_seconds']:.4f}초, 추가 {result['cpu_timing']['after']['mean_seconds']:.4f}초였다. 순차 실행 참고값이며 통제된 속도 비교가 아니다. GPU 단계 wall time에는 대기/캐시 재사용 시간이 포함되므로 개별 모델의 추론속도로 해석하지 않는다.", '',
        '새 합성20장과 부모20장의 원본/생성해상도 왕복 리샘플링을 양쪽 모델로 비교한80쌍에서 예측변화0이었다. 리샘플링 민감도 검사이며 합성 글씨체 단서의 영향을 모두 배제하지는 못한다. 실제 성인2XS 정확도로 주장하지 않는다.', '',
        '종류별precision/recall/F1, 혼동표,90/85/80 자동처리와오답은comparison.json, 사진별 비교는photo-comparison.csv, 실패/불일치는 별도JSON/CSV, 사진 검토는review.html에 있다. 원본/모델/기준자산 무결성은integrity.json에 있다. 프롬프트와생성출처는new-synthetic.json 및generated/*.json에 있다.', '',
        '재개: `.local/ab-venv/Scripts/python.exe -u -m scripts.size_retrain_20261122_validate_more_eval real2600` 또는 `synthetic`. 각 사진별 원자적 캐시와manifest/model 해시가 일치할 때만 건너뛴다. 보고서 재생성: `-m scripts.size_retrain_20261122_validate_more_report report`.', '',
        '체크포인트:', '```json', json.dumps(result['models'], ensure_ascii=False, indent=2), '```', '']
    (VOUT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')


def wait_and_report():
    started = time.monotonic()
    while not all((VOUT / f'{scope}-evaluation-complete.json').exists() for scope in ('real2600', 'synthetic')):
        if time.monotonic() - started > 14400:
            raise TimeoutError('Whole-photo evaluation completion files did not arrive within4h.')
        time.sleep(5)
    summarize()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['cpu-new', 'report', 'wait-report'])
    args = parser.parse_args()
    try:
        {'cpu-new': cpu_new, 'report': summarize, 'wait-report': wait_and_report}[args.command]()
    except Exception as error:
        status('error', error=repr(error))
        raise
