"""Paired whole-photo nine-condition evaluation and actual CPU application checks."""
from __future__ import annotations

import base64
from copy import copy
import html
from io import BytesIO
import time

import numpy as np
from PIL import Image
import torch

from coatdesk_ab.data import SHAPES, letterbox, load_photo
from coatdesk_ab.network import PhotoClassifier
from coatdesk_locator.model import build_detector, choose_detection
from coatdesk_v2.ai_runtime import Models
from scripts.retrain_four_training import change
from scripts.retraining_20261122_gpu import gpu_lease
from scripts.size_retrain_20261122 import BASE, CLASSES, KINDS, OUT, WORK, configure, read, save, sha, status, tensor
from scripts.size_retrain_20261122_fit import build, business, evaluate, metrics, write_csv


def cuda_time():
    torch.cuda.synchronize()
    return time.perf_counter()


@torch.inference_mode()
def pipeline(rows, split):
    device = 'cuda'
    detector = build_detector().to(device).eval()
    detector.transform.fixed_size = (448, 448)
    detector.load_state_dict(torch.load(BASE / 'detector.pt', map_location='cpu', weights_only=True))
    shape = PhotoClassifier(3).to(device).eval()
    shape.load_state_dict(torch.load(BASE / 'panel-shape.pt', map_location='cpu', weights_only=True))
    old = build(True).to(device).eval()
    new = PhotoClassifier(20).to(device).eval()
    new.load_state_dict(torch.load(OUT / 'size20.pt', map_location='cpu', weights_only=True))
    old_classes = read(BASE / 'manifest.json')['size_classes']
    results = {'old': [], 'new': []}
    timing = {'shared_detector_seconds': 0., 'shared_shape_seconds': 0., 'old_size_seconds': 0., 'new_size_seconds': 0.}
    started = time.monotonic()
    for i, row in enumerate(rows):
        photo = load_photo(row['source'])
        changed = [change(photo, kind) for kind in KINDS]
        detector_inputs = [torch.from_numpy(np.asarray(p.resize((448, 448), Image.Resampling.LANCZOS)).transpose(2, 0, 1).copy()).float().to(device) / 255 for p in changed]
        t0 = cuda_time()
        predictions = detector(detector_inputs)
        timing['shared_detector_seconds'] += cuda_time() - t0
        locations, panels, active = [], [], []
        for k, (p, detection) in enumerate(zip(changed, predictions)):
            detection['boxes'] = detection['boxes'] * detection['boxes'].new_tensor([p.width / 448, p.height / 448] * 2)
            location = choose_detection(detection, p.width, p.height)
            locations.append(location)
            if location['crop_box']:
                active.append(k)
                panels.append(tensor(letterbox(p.crop(location['crop_box']))[0]))
        calculated = {}
        if panels:
            batch = torch.stack(panels).to(device)
            t0 = cuda_time()
            shape_p = shape(batch).softmax(1).cpu()
            timing['shared_shape_seconds'] += cuda_time() - t0
            t0 = cuda_time()
            old_p = old(batch).softmax(1).cpu()
            timing['old_size_seconds'] += cuda_time() - t0
            t0 = cuda_time()
            new_p = new(batch).softmax(1).cpu()
            timing['new_size_seconds'] += cuda_time() - t0
            calculated = {k: (a, b, c) for k, a, b, c in zip(active, shape_p, old_p, new_p)}
        for k, kind in enumerate(KINDS):
            common = {'code': row['code'], 'group': row['group'], 'kind': kind, 'truth': row['raw_size'],
                      'age': row['age'], 'source_kind': 'real', 'panel_score': locations[k]['score'],
                      'crop_box': locations[k]['crop_box'], 'split': split}
            if k not in calculated:
                for which in results:
                    results[which].append(dict(common, predicted='<failure>', score=0., shape='', shape_score=0., consistent=False, failure='no_panel'))
                continue
            sp, op, np_ = calculated[k]
            sscore, shape_index = sp.max(0)
            predicted_shape = SHAPES[int(shape_index)]
            for which, p, classes in [('old', op, old_classes), ('new', np_, CLASSES)]:
                score, index = p.max(0)
                raw = classes[int(index)]
                consistent = raw.isdigit() == (predicted_shape == 'child')
                results[which].append(dict(common, predicted=raw, score=float(score), shape=predicted_shape,
                         shape_score=float(sscore), consistent=consistent, failure=None,
                         probabilities={c: float(x) for c, x in zip(classes, p)}))
        if i % 25 == 0 or i + 1 == len(rows):
            status('evaluating', evaluation_split=split, evaluated_photos=i + 1, total_photos=len(rows),
                   evaluation_elapsed_seconds=time.monotonic() - started)
    save(OUT / f'{split}-pipeline-predictions.json', results)
    save(OUT / f'{split}-timing.json', dict(timing, total_seconds=time.monotonic() - started,
          n_photos=len(rows), n_conditions=len(rows) * 9, precision='FP32', batch='nine conditions of one photo',
          shared_detector_counted_once=True))
    return results


def automatic(row, threshold):
    return not row['failure'] and row['panel_score'] >= .9 and row['shape_score'] >= .9 and row['score'] >= threshold and row['consistent']


def auto_summary(rows, threshold):
    accepted = [r for r in rows if automatic(r, threshold)]
    return {'threshold_size': threshold, 'threshold_panel': .9, 'threshold_shape': .9,
            'n': len(rows), 'auto': len(accepted), 'auto_rate': len(accepted) / max(1, len(rows)),
            'auto_raw_errors': sum(r['truth'] != r['predicted'] for r in accepted),
            'auto_business_errors': sum(business(r['truth']) != business(r['predicted']) for r in accepted),
            'review': len(rows) - len(accepted), 'failures': sum(bool(r['failure']) for r in rows),
            'scope': 'panel+shape+size only; category and child gender/manual review excluded'}


def choose_threshold(rows):
    choices = [0., .5, .7, .8, .9, .95, .98, .99, .995, .999, 1.000001]
    choices += [min(1.000001, r['score'] + 1e-7) for r in rows if r['truth'] != r['predicted']]
    candidates = [auto_summary(rows, c) for c in sorted(set(choices))]
    clean = [r for r in candidates if r['auto_raw_errors'] == 0]
    choice = max(clean, key=lambda r: (r['auto'], -r['threshold_size']))
    return dict(choice, selection_split='dev_only', warning='Zero observed dev errors is not a guarantee on future photographs.')


def safe_metrics(rows):
    classes = CLASSES + (['<failure>'] if any(r['predicted'] == '<failure>' for r in rows) else [])
    result = metrics(rows, classes)
    business_classes = ['2XS', 'XS', 'S', 'M', 'L', 'XL', '2XL', '3XL']
    if '<failure>' in classes:
        business_classes.append('<failure>')
    converted = [dict(r, truth=business(r['truth']), predicted=business(r['predicted'])) for r in rows]
    converted_metrics = metrics(converted, business_classes)
    converted_metrics['macro_f1_all_business_classes_zero_for_absent'] = converted_metrics.pop('macro_f1_all20_zero_for_absent')
    result['business_metrics'] = converted_metrics
    return result


def summarize(results, thresholds):
    old_classes = read(BASE / 'manifest.json')['size_classes']
    summary = {}
    for which, predictions in results.items():
        summary[which] = {}
        for condition, selected in [('original', [r for r in predictions if r['kind'] == 'original']), ('nine_conditions', predictions)]:
            summary[which][condition] = {}
            groups = {'all': selected, 'common18': [r for r in selected if r['truth'] in old_classes],
                      'real60': [r for r in selected if r['truth'] == '60'],
                      'real70': [r for r in selected if r['truth'] == '70'],
                      'adult': [r for r in selected if r['age'] == '성인'],
                      'child': [r for r in selected if r['age'] != '성인']}
            for name, subset in groups.items():
                summary[which][condition][name] = dict(safe_metrics(subset),
                    auto_at_current90=auto_summary(subset, .9),
                    auto_at_dev_selected=auto_summary(subset, thresholds[which]['threshold_size']))
    paired = []
    indexed = {(r['code'], r['kind']): r for r in results['new']}
    for old in results['old']:
        new = indexed[(old['code'], old['kind'])]
        paired.append({'code': old['code'], 'group': old['group'], 'kind': old['kind'], 'truth': old['truth'],
            'truth_business': business(old['truth']), 'old': old['predicted'], 'new': new['predicted'],
            'old_business': business(old['predicted']), 'new_business': business(new['predicted']),
            'old_score': old['score'], 'new_score': new['score'],
            'old_correct': old['predicted'] == old['truth'], 'new_correct': new['predicted'] == new['truth'],
            'old_auto90': automatic(old, .9), 'new_auto90': automatic(new, .9),
            'old_auto_dev': automatic(old, thresholds['old']['threshold_size']),
            'new_auto_dev': automatic(new, thresholds['new']['threshold_size']),
            'old_failure': old['failure'], 'new_failure': new['failure']})
    comparison = {}
    for condition, group in [('original', [r for r in paired if r['kind'] == 'original']), ('nine_conditions', paired)]:
        comparison[condition] = {'old_only_correct': sum(r['old_correct'] and not r['new_correct'] for r in group),
            'new_only_correct': sum(r['new_correct'] and not r['old_correct'] for r in group),
            'both_correct': sum(r['old_correct'] and r['new_correct'] for r in group),
            'both_wrong': sum(not r['old_correct'] and not r['new_correct'] for r in group),
            'disagreements': sum(r['old'] != r['new'] for r in group)}
    summary['paired'] = comparison
    summary['robust_garments'] = {}
    for which, predictions in results.items():
        codes = sorted({r['code'] for r in predictions})
        by_code = {c: [r for r in predictions if r['code'] == c] for c in codes}
        summary['robust_garments'][which] = {
            'n_photos': len(codes),
            'all9_raw_correct': sum(all(r['truth'] == r['predicted'] for r in group) for group in by_code.values()),
            'all9_business_correct': sum(all(business(r['truth']) == business(r['predicted']) for r in group) for group in by_code.values())}
    summary['limitations'] = {'adult_2XS_real_photos': 0, '170_dev_test_photos': 0,
        'conditions_are_correlated': True, 'final_test_photos': 300,
        'prior_test_photos_reused': 142, 'new_to_prior_snapshot_photos': 158,
        'post_evaluation_label_changes': 0, 'post_evaluation_exclusions': 0}
    return summary, paired


def resampling_diagnostic(model):
    """Paired original/reencoded real controls; keep claims about synth modest."""
    rows = read(WORK / 'frozen.json')
    manifest = read(OUT / 'synthetic/manifest.json')['records']
    records = []
    by_code = {r['code']: r for r in rows}
    model.cpu().eval()
    for r in manifest:
        parent = by_code[r['parent_code']]
        panels = []
        for name, path in [('real', parent['panel']), ('synthetic', r['panel'])]:
            image = Image.open(path).convert('RGB')
            for resampled in (False, True):
                p = image.resize(tuple(r['generation_dimensions']), Image.Resampling.LANCZOS).resize((512, 512), Image.Resampling.LANCZOS) if resampled else image
                panels.append((name, resampled, tensor(p)))
        with torch.inference_mode():
            probabilities = model(torch.stack([x[2] for x in panels])).softmax(1)
        for (name, resampled, _), p in zip(panels, probabilities):
            score, index = p.max(0)
            records.append({'synthetic_id': r['synthetic_id'], 'parent': r['parent_code'], 'input': name,
                            'resampled': resampled, 'truth': parent['raw_size'] if name == 'real' else r['target'],
                            'predicted': CLASSES[int(index)], 'score': float(score)})
    switches = 0
    for i in range(0, len(records), 2):
        switches += records[i]['predicted'] != records[i + 1]['predicted']
    save(OUT / 'resampling-diagnostic.json', {'records': records, 'paired_prediction_switches': switches,
         'pairs': len(records) // 2, 'synthetic_training_parent_pairs_not_independent_test': True,
         'unique_real_parent_groups': len({r['parent_group'] for r in manifest}),
         'unique_synthetic_bases': len(manifest),
         'outside_text_exact_for_all': all(r['outside_text_mask_exact'] for r in manifest),
         'interpretation': 'Checks resampling sensitivity; does not establish unseen real adult2XS accuracy or rule out every generative font cue.'})


def cpu_checks(paired):
    configure()
    rows = read(WORK / 'frozen.json')
    test = [r for r in rows if r['split'] == 'test']
    chosen = []
    for c in CLASSES:
        group = [r for r in test if r['raw_size'] == c]
        if group:
            chosen.append(group[0]['code'])
    chosen += [r['code'] for r in paired if r['old'] != r['new']]
    chosen = list(dict.fromkeys(chosen))
    by_code = {r['code']: r for r in rows}
    old = Models(BASE)
    new = copy(old)
    new.classifiers = dict(old.classifiers)
    new.classifiers['size'] = PhotoClassifier(20).cpu().eval()
    new.classifiers['size'].load_state_dict(torch.load(OUT / 'size20.pt', map_location='cpu', weights_only=True))
    new.size_classes = CLASSES
    thresholds = {'panel': .9, 'shape': .9, 'size': .9}
    gpu = read(OUT / 'test-pipeline-predictions.json')
    expected = {which: {(r['code'], r['kind']): r for r in gpu[which]} for which in ('old', 'new')}
    observations = []
    directory = WORK / 'cpu-photometric-inputs'
    directory.mkdir(exist_ok=True)
    for i, code in enumerate(chosen):
        for kind in KINDS:
            path = by_code[code]['source']
            if kind != 'original':
                path = directory / f'{code}-{kind}.png'
                change(load_photo(by_code[code]['source']), kind).save(path)
            for which, model in [('old', old), ('new', new)]:
                t0 = time.perf_counter()
                result = model.analyze(path, thresholds)
                elapsed = time.perf_counter() - t0
                ref = expected[which][(code, kind)]
                observations.append({'code': code, 'kind': kind, 'which': which, 'truth': by_code[code]['raw_size'],
                    'cpu_raw': result.get('raw_size'), 'gpu_raw': ref['predicted'],
                    'raw_equal': result.get('raw_size') == ref['predicted'],
                    'cpu_review': result['review'] if 'raw_size' not in result else bool(result['held']),
                    'gpu_review': not automatic(ref, .9), 'cpu_score': result.get('scores', {}).get('size'),
                    'gpu_score': ref['score'], 'held': result['held'], 'seconds': elapsed})
        if i % 5 == 0:
            status('cpu_verification', cpu_photos=i + 1, cpu_total_photos=len(chosen))
    save(OUT / 'cpu-app-verification.json', {'n_photos': len(chosen), 'n_conditions_each_model': len(chosen) * 9,
         'observations': observations, 'raw_disagreements': sum(not r['raw_equal'] for r in observations),
         'review_disagreements': sum(r['cpu_review'] != r['gpu_review'] for r in observations),
         'runtime': 'actual Models.analyze CPU detector/crop/normalization/shape/size/child conversion; new classifier injected in isolated instance',
         'loader_limitation': 'Shipping Models manifest validation accepts17/18 classes only. 20-class support requires separately authorized integration; application unchanged.'})
    write_csv(OUT / 'cpu-app-verification.csv', observations)
    resampling_diagnostic(new.classifiers['size'])


def review_html(paired):
    rows = {r['code']: r for r in read(WORK / 'frozen.json')}
    predictions = {(r['code'], r['kind']): r for r in read(OUT / 'test-pipeline-predictions.json')['new']}
    chosen = [r for r in paired if r['kind'] == 'original' or r['old'] != r['new']]
    sections = []
    for r in chosen:
        data = PathLike(rows[r['code']]['panel']).read_bytes()
        if r['kind'] != 'original':
            photo = change(load_photo(rows[r['code']]['source']), r['kind'])
            box = predictions[(r['code'], r['kind'])]['crop_box']
            if box:
                buffer = BytesIO()
                letterbox(photo.crop(box))[0].save(buffer, format='PNG')
                data = buffer.getvalue()
        encoded = base64.b64encode(data).decode('ascii')
        sections.append(f"<article data-disagree='{int(r['old'] != r['new'])}'><img src='data:image/png;base64,{encoded}'><p>{html.escape(r['code'])} · {html.escape(r['kind'])}<br>정답 {html.escape(r['truth'])}<br>기존 {html.escape(r['old'])} ({r['old_score']:.1%})<br>신규 {html.escape(r['new'])} ({r['new_score']:.1%})</p></article>")
    content = "<!doctype html><meta charset='utf-8'><title>Size paired comparison</title><style>body{font:16px sans-serif;background:#f4f6fa;padding:24px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}article{background:white;padding:12px;border-radius:12px}img{width:100%}button{padding:10px;margin:10px}</style><h1>사이즈 동일 사진 비교</h1><p>원본300 + 변형 불일치. 해당 조건에서 추출한 판넬을 표시합니다. 정답은 동결 후 수정하지 않았습니다.</p><button onclick=\"document.querySelectorAll('article').forEach(x=>x.hidden=x.dataset.disagree==='0')\">불일치만</button><button onclick=\"document.querySelectorAll('article').forEach(x=>x.hidden=false)\">전체</button><main>" + ''.join(sections) + '</main>'
    (OUT / 'comparison.html').write_text(content, encoding='utf-8')


def PathLike(path):
    from pathlib import Path
    return Path(path)


def synthetic_cpu_runtime_checks():
    """Exercise new literals/conversion on synthetic panels through manual ROI."""
    configure()
    model = Models(BASE)
    model.classifiers['size'] = PhotoClassifier(20).cpu().eval()
    model.classifiers['size'].load_state_dict(torch.load(OUT / 'size20.pt', map_location='cpu', weights_only=True))
    model.size_classes = CLASSES
    observations = []
    for r in read(OUT / 'synthetic/manifest.json')['records']:
        result = model.analyze(r['panel'], {'panel': .9, 'shape': .9, 'size': .9}, manual_roi=[0., 0., 1., 1.])
        observations.append({'id': r['synthetic_id'], 'truth': r['target'], 'raw': result['raw_size'],
             'expected_business': business(r['target']), 'business': result['candidates']['size'],
             'raw_correct': result['raw_size'] == r['target'], 'held': result['held'],
             'score': result['scores']['size'], 'age': result['candidates']['age']})
    save(OUT / 'synthetic-cpu-app-diagnostic.json', {'observations': observations,
         'n_training_panels': len(observations), 'raw_correct': sum(r['raw_correct'] for r in observations),
         'manual_roi': True, 'independent_real_evaluation': False,
         'warning': 'Synthetic TRAIN panels through actual CPU manual-panel app path; not real adult2XS or independent accuracy.'})


def run():
    configure()
    status('waiting_gpu_evaluation')
    rows = read(WORK / 'frozen.json')
    with gpu_lease('size'):
        dev = pipeline([r for r in rows if r['split'] == 'dev'], 'dev')
        thresholds = {which: choose_threshold(predictions) for which, predictions in dev.items()}
        save(OUT / 'dev-selected-thresholds.json', thresholds)
        # Thresholds are persisted before opening final test inference.
        results = pipeline([r for r in rows if r['split'] == 'test'], 'test')
        summary, paired = summarize(results, thresholds)
        save(OUT / 'comparison.json', summary)
        write_csv(OUT / 'photo-comparison.csv', paired)
        write_csv(OUT / 'photo-comparison-original.csv', [r for r in paired if r['kind'] == 'original'])
        model = PhotoClassifier(20).cuda().eval()
        model.load_state_dict(torch.load(OUT / 'size20.pt', map_location='cpu', weights_only=True))
        synthetic = read(OUT / 'synthetic/manifest.json')['records']
        diagnostic_rows = [dict(code=r['synthetic_id'], group=r['parent_group'], raw_size=r['target'], panel=r['panel'],
                                age='성인' if r['target'] == '2XS' else '유아동', source_kind='synthetic') for r in synthetic]
        predictions, training_diagnostic = evaluate(model, diagnostic_rows, kinds=KINDS)
        save(OUT / 'synthetic-training-fit-diagnostic.json', {'summary': training_diagnostic, 'predictions': predictions,
             'warning': 'Training parents only. This is a fit diagnostic, not independent evaluation and not real adult2XS accuracy.'})
        del model
        torch.cuda.empty_cache()
    cpu_checks(paired)
    review_html(paired)
    integrity = {}
    for path, expected in read(WORK / 'protected.json').items():
        integrity[path] = sha(path) == expected
    original_changed = [r['code'] for r in rows if sha(r['source']) != r['sha256']]
    save(OUT / 'post-run-integrity.json', {'protected_unchanged': all(integrity.values()), 'protected': integrity,
         'original_sources_checked': len(rows), 'original_sources_changed': original_changed})
    assert all(integrity.values()) and not original_changed
    save(OUT / 'candidate-manifest.json', {'classes': CLASSES, 'sha256': sha(OUT / 'size20.pt'), 'file': 'size20.pt',
         'architecture': 'PhotoClassifier ResNet18 stride16 learned spatial attention pooling',
         'input': {'side': 512, 'photo_preview_limit': [1080, 1920], 'panel': 'shipped448detector; choose_detection8%margin; RGB white letterbox',
                   'normalization_mean': [.485, .456, .406], 'normalization_std': [.229, .224, .225]},
         'mapping': read(BASE / 'size_mapping.json'), 'baseline_directory': str(BASE),
         'dev_selected_epoch': read(OUT / 'best-dev.json')['epoch'], 'candidate_only_not_adopted': True})
    status('evaluated', checkpoint_sha256=sha(OUT / 'size20.pt'))


if __name__ == '__main__':
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--wait-trained', action='store_true')
        args = parser.parse_args()
        if args.wait_trained:
            deadline = time.monotonic() + 14400
            while read(OUT / 'status.json')['stage'] != 'trained':
                state = read(OUT / 'status.json')
                if state['stage'] == 'error' or time.monotonic() > deadline:
                    raise RuntimeError(f"Training not completed: {state}")
                time.sleep(5)
        run()
    except Exception as error:
        status('error', error=repr(error))
        raise
