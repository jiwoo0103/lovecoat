"""CPU application validation and auditable report for rare-only followup."""
from __future__ import annotations

import base64
from copy import copy
from datetime import datetime, timezone
import html
import json
import time

import torch
from PIL import Image

from coatdesk_v2.ai_runtime import Models
from scripts.retrain_four_training import change
from scripts.size_retrain_20261122 import BASE, CLASSES, KINDS, OUT, ROOT, WORK, configure, read, save, sha
from scripts.size_retrain_20261122_fit import write_csv
from scripts.size_retrain_20261122_evaluate import automatic
from scripts.size_retrain_20261122_evaluate import safe_metrics
from scripts.size_retrain_20261122_rare import ROUT, RWORK, TARGETS, status
from scripts.size_retrain_20261122_rare_fit import OTHER, RARE, load_model


def legacy_synthetic_evaluate():
    from torch.utils.data import DataLoader
    from coatdesk_ab.network import PhotoClassifier
    from scripts.size_retrain_20261122_fit import Panels
    from scripts.retraining_20261122_gpu import gpu_lease
    configure()
    rows = [r for r in read(ROUT / 'synthetic.json') if r['split'] == 'test']
    classes = read(BASE / 'manifest.json')['size_classes']
    results = []
    with gpu_lease('size'):
        model = PhotoClassifier(len(classes)).cuda().eval()
        model.load_state_dict(torch.load(BASE / 'panel-size.pt', map_location='cpu', weights_only=True))
        with torch.inference_mode():
            for images, targets, indices, kinds in DataLoader(Panels(rows, kinds=KINDS), batch_size=16):
                probabilities = model(images.cuda()).softmax(1).cpu()
                for p, target, i, k in zip(probabilities, targets, indices, kinds):
                    r = rows[int(i)]
                    score, predicted = p.max(0)
                    results.append({'code': r['code'], 'group': r['group'], 'kind': KINDS[int(k)],
                        'truth': CLASSES[int(target)], 'predicted': classes[int(predicted)], 'score': float(score),
                        'source_kind': 'synthetic', 'probabilities': {c: float(v) for c, v in zip(classes, p)}})
        del model
        torch.cuda.empty_cache()
    save(ROUT / 'legacy18-synthetic-test.json', {'predictions': results,
        'original': safe_metrics([r for r in results if r['kind'] == 'original']), 'nine_conditions': safe_metrics(results),
        'classes': classes, 'sha256': sha(BASE / 'panel-size.pt'),
        'warning': 'Deployed18 lacks literal60/adult2XS outputs; synthetic panel-only reference, not real accuracy.'})


def cpu_validate():
    configure()
    if (ROUT / 'cpu-verification.json').exists():
        return
    status('cpu_verification')
    before = Models(BASE)
    before.size_classes = CLASSES
    before.classifiers['size'] = load_model(OUT / 'size20.pt')
    after = copy(before)
    after.classifiers = dict(before.classifiers)
    after.classifiers['size'] = load_model(ROUT / 'rare-size20.pt')
    frozen = {r['code']: r for r in read(WORK / 'frozen.json')}
    real = read(ROUT / 'real-test-predictions.json')
    by_key = {w: {(r['code'], r['kind']): r for r in real[w]} for w in ('before', 'after')}
    chosen = []
    for label in CLASSES:
        candidates = [r for r in real['before'] if r['kind'] == 'original' and r['truth'] == label]
        if candidates:
            chosen.append(candidates[0]['code'])
    chosen += ['5068', '6839']
    chosen += [r['code'] for r in real['after'] if r['predicted'] != by_key['before'][(r['code'], r['kind'])]['predicted']]
    chosen = list(dict.fromkeys(chosen))
    inputs = RWORK / 'real-cpu-inputs'
    inputs.mkdir(exist_ok=True)
    outputs = []
    thresholds = {'panel': .9, 'shape': .9, 'size': .9}
    for i, code in enumerate(chosen):
        for kind in KINDS:
            path = frozen[code]['source'] if kind == 'original' else inputs / f'{code}-{kind}.png'
            if kind != 'original' and not path.exists():
                from coatdesk_ab.data import load_photo
                change(load_photo(frozen[code]['source']), kind).save(path)
            for which, model in [('before', before), ('after', after)]:
                started = time.perf_counter()
                result = model.analyze(path, thresholds)
                expected = by_key[which][(code, kind)]
                outputs.append({'source': 'real', 'code': code, 'kind': kind, 'model': which,
                    'truth': frozen[code]['raw_size'], 'cpu': result.get('raw_size'), 'gpu': expected['predicted'],
                    'raw_equal': result.get('raw_size') == expected['predicted'],
                    'review_equal': bool(result['held']) == (not automatic(expected, .9)),
                    'cpu_business': result['candidates'].get('size'), 'held': result['held'],
                    'seconds': time.perf_counter() - started})
        status('cpu_verification', cpu_real_photos=i + 1, cpu_real_total=len(chosen))
    synthetic = [r for r in read(ROUT / 'synthetic.json') if r['split'] == 'test']
    predictions = read(ROUT / 'synthetic-test-predictions.json')
    expected = {w: {(r['code'], r['kind']): r for r in predictions[w]} for w in ('before', 'after')}
    directory = RWORK / 'synthetic-cpu-inputs'
    directory.mkdir(exist_ok=True)
    for row in synthetic:
        for kind in KINDS:
            path = directory / f"{row['code']}-{kind}.png"
            change(Image.open(row['panel']).convert('RGB'), kind).save(path)
            for which, model in [('before', before), ('after', after)]:
                started = time.perf_counter()
                result = model.analyze(path, thresholds, manual_roi=[0., 0., 1., 1.])
                reference = expected[which][(row['code'], kind)]
                outputs.append({'source': 'synthetic_heldout_manual_roi', 'code': row['code'], 'kind': kind,
                    'model': which, 'truth': row['raw_size'], 'cpu': result['raw_size'], 'gpu': reference['predicted'],
                    'raw_equal': result['raw_size'] == reference['predicted'], 'review_equal': None,
                    'cpu_business': result['candidates']['size'], 'held': result['held'],
                    'seconds': time.perf_counter() - started})
    save(ROUT / 'cpu-verification.json', {'observations': outputs, 'real_photos': len(chosen),
        'real_conditions_each_model': len(chosen) * 9, 'synthetic_heldout_panels': len(synthetic),
        'synthetic_conditions_each_model': len(synthetic) * 9,
        'raw_disagreements': sum(not r['raw_equal'] for r in outputs),
        'real_review_disagreements': sum(r['review_equal'] is False for r in outputs),
        'scope': 'actual CPU Models.analyze;20class classifier injected in isolated instances; synthetic uses manual panel ROI',
        'application_changed': False})
    write_csv(ROUT / 'cpu-verification.csv', outputs)


def audit_weights_and_integrity():
    base = torch.load(OUT / 'size20.pt', map_location='cpu', weights_only=True)
    selected = torch.load(ROUT / 'rare-size20.pt', map_location='cpu', weights_only=True)
    last = torch.load(ROUT / 'last-trained.pt', map_location='cpu', weights_only=True)
    for candidate in (selected, last):
        for key in base:
            if key in ('output.1.weight', 'output.1.bias'):
                assert torch.equal(base[key][OTHER], candidate[key][OTHER]), key
            else:
                assert torch.equal(base[key], candidate[key]), key
    changes = {c: {'selected_head_l2': float((selected['output.1.weight'][i] - base['output.1.weight'][i]).norm()),
                   'last_trained_head_l2': float((last['output.1.weight'][i] - base['output.1.weight'][i]).norm())}
               for c, i in zip(TARGETS, RARE)}
    assert any(r['last_trained_head_l2'] > 0 for r in changes.values())
    assert sha(OUT / 'size20.pt') == read(ROUT / 'protocol.json')['base_sha256']
    protected = {path: sha(path) == expected for path, expected in read(WORK / 'protected.json').items()}
    changed_sources = [r['code'] for r in read(WORK / 'frozen.json') if sha(r['source']) != r['sha256']]
    assert all(protected.values()) and not changed_sources
    save(ROUT / 'integrity.json', {'core_and_other17_rows_bit_exact': True, 'rare_row_changes': changes,
         'base_checkpoint_unchanged': True, 'protected_unchanged': all(protected.values()),
         'original_sources_verified': 2600, 'original_sources_changed': changed_sources})


def gallery():
    rows = read(ROUT / 'synthetic.json')
    before_after = read(ROUT / 'synthetic-test-predictions.json')
    indexed = {which: {r['code']: r for r in data if r['kind'] == 'original'} for which, data in before_after.items()}
    parts = []
    for row in rows:
        encoded = base64.b64encode(__import__('pathlib').Path(row['panel']).read_bytes()).decode('ascii')
        comparison = ''
        if row['split'] == 'test':
            comparison = f"<br>이전 {indexed['before'][row['code']]['predicted']} → 추가학습 {indexed['after'][row['code']]['predicted']}"
        parts.append(f"<article><img src='data:image/png;base64,{encoded}'><p>{html.escape(row['split'])} · 원문목표 {row['raw_size']}<br>부모 {row['parent_code']} ({row['parent_raw_size']}){comparison}</p></article>")
    content = "<!doctype html><meta charset='utf-8'><title>Rare size synthetic review</title><style>body{font:16px sans-serif;background:#f1f4f8;padding:24px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}article{background:white;padding:12px;border-radius:10px}img{width:100%}</style><h1>60·2XS·170 추가 합성60장</h1><p>종류별 학습12 / 개발4 / 최종4. 모든 부모 그룹을 분리했습니다. 합성 최종 성적은 실사 성인2XS·170 정확도를 뜻하지 않습니다.</p><main>" + ''.join(parts) + '</main>'
    (ROUT / 'review.html').write_text(content, encoding='utf-8')


def report():
    comparison = read(ROUT / 'comparison.json')
    selection = read(ROUT / 'selection.json')
    cpu = read(ROUT / 'cpu-verification.json')
    protocol = read(ROUT / 'protocol.json')
    epochs = [json.loads(line) for line in (ROUT / 'epochs.jsonl').read_text(encoding='utf-8').splitlines()]
    checks = read(ROUT / 'verification.json')
    review_before = comparison['real']['before']['nine_conditions']['auto90']['review']
    review_after = comparison['real']['after']['nine_conditions']['auto90']['review']
    if comparison['real']['old_only_correct']:
        verdict = '실사에서 기존 정답이 오답으로 바뀐 조건이 있어 적용 보류를 권한다.'
    elif review_after > review_before:
        verdict = f'이번 비교에서 정확도 이득은 없고 확인필요가 {review_after - review_before}조건 늘었다. 직전20종 후보를 유지하고 추가학습 후보는 별도 보관하는 것을 권한다.'
    else:
        verdict = '이번 고정 자료의 성적과 실사 희소 클래스 부족을 함께 고려해야 한다. 합성 성적으로 실사 성능 향상을 확정하지 않는다.'
    lines = ['# 60·성인2XS·170 추가 합성 및 제한 학습', '',
        f"새 합성60장(각20장)을 제작했다. 종류별 학습12/개발4/최종4이며60개 부모 그룹이 서로 다르고 이전 학습용 합성18장의 부모도 제외했다. 학습{selection['epochs_completed']}회차, 선택{selection['selected_epoch']}회차.", '',
        '|원문|실사학습|기존합성학습|추가합성학습|학습기본본 합계|합성개발/최종|', '|---|---:|---:|---:|---:|---:|',
        '|60|2|6|12|20|4/4|', '|성인2XS|0|6|12|18|4/4|', '|170|1|0|12|13|4/4|', '',
        '학습기본본51개 각각 원본1회+랜덤증강2회, 회차당153입력이다. 70 및 다른 사이즈 자료는 gradient 학습에 넣지 않았다. 특징 추출부·attention·다른17출력행은 비트 단위로 동일함을 확인했다. 갱신 대상은3출력행1539개 값이다.', '',
        '## 같은 자료의 추가학습 전후', '', '|자료·조건|표본/조건|이전20종|추가학습20종|', '|---|---:|---:|---:|']
    for source, name in [('real', '실사'), ('synthetic', '부모분리 합성최종')]:
        for condition, label in [('original', '원본'), ('nine_conditions', '9조건')]:
            a, b = [comparison[source][w][condition] for w in ('before', 'after')]
            lines.append(f"|{name}/{label}|{a['n_photos']}/{a['n_conditions']}|{a['correct']}/{a['n_conditions']} ({a['accuracy']:.2%})|{b['correct']}/{b['n_conditions']} ({b['accuracy']:.2%})|")
    lines += ['', '합성 최종의 독립 부모는12개(종류당4개)이며9조건108개를 독립108벌로 세지 않는다. 실사 최종300벌은 앞 실험에서도 평가한 반복 자료다.', '',
        '## 합성 최종 종류별', '', '|원문|기본본 수|이전 원본/9조건 정답|추가학습 원본/9조건 정답|', '|---|---:|---:|---:|']
    for c in TARGETS:
        values = {w: {k: comparison['synthetic'][w][k]['per_class'][c] for k in ('original', 'nine_conditions')} for w in ('before', 'after')}
        parts = {w: f"{round(values[w]['original']['recall'] * 4)}/4 · {round(values[w]['nine_conditions']['recall'] * 36)}/36" for w in values}
        lines.append(f"|{c}|4|{parts['before']}|{parts['after']}|")
    lines += ['', '**성인2XS 실사는0장,170 실사 개발/최종도0장이다. 합성 성적은 해당 클래스의 실사 정확도를 대신하지 않는다. 실사60 최종은1장뿐이다.**', '',
        '## 기존 사이즈와 자동 처리', '']
    for condition in ('original', 'nine_conditions'):
        a, b = [comparison['real'][w][condition] for w in ('before', 'after')]
        lines.append(f"- {condition}: 비대상17종 {a['common17']['correct']}/{a['common17']['n_conditions']} → {b['common17']['correct']}/{b['common17']['n_conditions']}; 자동90% {a['auto90']['auto']}/{a['auto90']['n']} → {b['auto90']['auto']}/{b['auto90']['n']}; 자동 원문오답 {a['auto90']['auto_raw_errors']} → {b['auto90']['auto_raw_errors']}; 확인필요 {a['auto90']['review']} → {b['auto90']['review']}.")
    lines += [f"- 실사9조건 기존만 정답 {comparison['real']['old_only_correct']}, 추가학습만 정답 {comparison['real']['new_only_correct']}조건.",
        '- 자동 처리율은 판넬·성별연령·사이즈 경로 범위다. 카테고리와 아동 성별 사람 확인은 포함하지 않았다. 앱 기준은 변경하지 않았다.', '',
        '새3종 점수 변화가 softmax 확률 정규화에 반영되면서 나머지 사이즈의 확률과 보류 판정도 달라질 수 있다.', '',
        '## 검증·출처·한계', '',
        f"- CPU 실제 앱 경로 실사{cpu['real_photos']}벌×9조건×2모델, 합성 최종{cpu['synthetic_heldout_panels']}판넬×9조건×2모델. CPU/GPU 원문 차이{cpu['raw_disagreements']}, 실사 보류판정 차이{cpu['real_review_disagreements']}조건.",
        '- 검출기·성별연령·특징 추출부가 같아 실사 비교는 앞 실험의 동일 전체사진9조건/검출박스/성별확률을 고정하고 사이즈 출력만 비교했다. 실제 CPU검증에서는 검출부터 다시 실행했다.',
        '- 새 합성은 내장 ImageGen만 사용했다. 60은80/90의 첫숫자,170은주로160의 가운데숫자,2XS는여성XS/S를 변경했다. 문자를 생성한 뒤 원본 입력의 해당 글자 마스크만 합쳐 옷/배경을 보존했다. 프롬프트·부모·해시·검수는 synthetic.json/generated/에 있다.',
        '- 개발/최종 합성의 부모는 원래 실사 개발/최종 분할에서만 가져왔다. 학습 부모나 이전 합성 부모와 겹치지 않는다. 생성모델·폰트·마스크 처리에 따른 도메인 편향은 남을 수 있다.',
        '- 개발 실사에서 기존정답을 보존하는 후보 중 합성개발 macroF1/정확도/CE로만 회차를 선택했다. 최종 결과로 후보를 다시 선택하거나 정답을 바꾸지 않았다.',
        f"- 실제 epoch 로그 {len(epochs)}개, 모두153입력 처리={all(e['views'] == 153 for e in epochs)}. 새 테스트{checks['pytest_passed']}개·Ruff·Python컴파일 검사 통과. 기존 후보/배포본/원본2600사진 불변을 확인했다.",
        '- 합성 검증은 직접 판넬 분류이며 실제 의복 전체 검출 성능의 확장은 평가하지 않는다. 실제 CPU 합성 경로는 수동ROI로 확인했다. 실제 브라우저 클릭 검증은 하지 않았다.', '',
        '## 파일·재개', '',
        f"- 선택 후보: rare-size20.pt SHA-256 `{selection['sha256']}`. 직전20종 SHA-256 `{protocol['base_sha256']}`.",
        '- 마지막 실제 학습값 last-trained.pt; 재개상태 .local/size-retrain-20261122/rare-followup/resume.pt; epochs.jsonl / protocol.json / selection.json / integrity.json.',
        '- raw/업무사이즈별 precision·recall·F1·macroF1·혼동표: comparison.json. 원본 및9조건 CSV: real-photo-comparison.csv / synthetic-photo-comparison.csv. 사진내장 검토화면 review.html.',
        '- 재개: `.local/ab-venv/Scripts/python.exe -u -m scripts.size_retrain_20261122_rare_fit`; CPU검증·보고서: `.local/ab-venv/Scripts/python.exe -u -m scripts.size_retrain_20261122_rare_report`.',
        '- 앱 모델 교체·코드 통합·설치/빌드·커밋·푸시·배포·사용자DB 쓰기는 하지 않았다.', '']
    if selection['unchanged_base_selected']:
        lines += ['추가 학습은 수행했으나 개발 선택 기준을 넘은 후보가 없어 이전20종을 유지한 결과다. last-trained.pt는 비교·진단용으로 별도 보존했다.', '']
    lines += ['## 판정', '', verdict, '']
    if (ROUT / 'legacy18-synthetic-test.json').exists():
        legacy = read(ROUT / 'legacy18-synthetic-test.json')
        lines += ['## 배포18종 참고 비교', '',
            '배포18종에는 원문60/성인2XS 출력이 없다. 동일 합성 최종12판넬을 실제로 분류한 참고 결과이며, 직전20종 대비 추가학습 효과와 구분한다.', '',
            '|합성 최종 조건|배포18종|직전20종|추가학습20종|', '|---|---:|---:|---:|']
        for condition in ('original', 'nine_conditions'):
            a, b, c = legacy[condition], comparison['synthetic']['before'][condition], comparison['synthetic']['after'][condition]
            lines.append(f"|{condition}|{a['correct']}/{a['n_conditions']}|{b['correct']}/{b['n_conditions']}|{c['correct']}/{c['n_conditions']}|")
        lines += ['']
    (ROUT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    save(ROUT / 'candidate-manifest.json', {'classes': CLASSES, 'sha256': selection['sha256'],
        'architecture': 'PhotoClassifier ResNet18 stride16 attention; only3outputrows finetuned',
        'input': read(OUT / 'candidate-manifest.json')['input'], 'mapping': read(OUT / 'candidate-manifest.json')['mapping'],
        'candidate_only': True, 'base_sha256': protocol['base_sha256']})
    save(ROUT / 'completion.json', {'completed_at_utc': datetime.now(timezone.utc).isoformat(), 'stage': 'complete',
        'selection': selection, 'synthetic_bases': 60, 'training_bases': 51,
        'real_before_original': comparison['real']['before']['original']['correct'],
        'real_after_original': comparison['real']['after']['original']['correct'],
        'real_before_nine': comparison['real']['before']['nine_conditions']['correct'],
        'real_after_nine': comparison['real']['after']['nine_conditions']['correct'],
        'synthetic_before_original': comparison['synthetic']['before']['original']['correct'],
        'synthetic_after_original': comparison['synthetic']['after']['original']['correct'],
        'synthetic_before_nine': comparison['synthetic']['before']['nine_conditions']['correct'],
        'synthetic_after_nine': comparison['synthetic']['after']['nine_conditions']['correct'],
        'cpu_raw_disagreements': cpu['raw_disagreements'], 'cpu_review_disagreements': cpu['real_review_disagreements'],
        'real_review_before_nine': review_before, 'real_review_after_nine': review_after,
        'recommendation': verdict, 'application_changed': False, 'checks': checks})
    path = ROOT / 'docs/SIZE_RETRAIN_20261122.md'
    document = path.read_text(encoding='utf-8').split('\n## 희소3종 추가 합성·학습\n')[0]
    document += '\n## 희소3종 추가 합성·학습\n\n사용자 추가 승인으로 60/성인2XS/170 각각20장(학습12/개발4/최종4), 총60장을 새 합성했다. 학습에는 해당3종51개 기본본만 사용하고 특징 추출부/attention/다른17출력행을 고정했다. 결과·한계·재개는 `model-results/20261122-size-retrain/rare-followup/REPORT.md`에 있다. 기존 첫 실험 자료/보고서/후보와 앱은 보존했다.\n'
    path.write_text(document, encoding='utf-8')
    status('complete', selected_epoch=selection['selected_epoch'], epochs_completed=selection['epochs_completed'], checkpoint_sha256=selection['sha256'])


if __name__ == '__main__':
    try:
        cpu_validate()
        audit_weights_and_integrity()
        if not (ROUT / 'legacy18-synthetic-test.json').exists():
            legacy_synthetic_evaluate()
        gallery()
        report()
    except Exception as error:
        status('error', error=repr(error))
        raise
