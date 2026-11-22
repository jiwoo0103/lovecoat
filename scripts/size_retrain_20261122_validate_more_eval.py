"""Resumable, evaluation-only shared-core whole-photo comparison."""
from __future__ import annotations

import argparse
from collections import Counter
import os
import shutil
import time

import numpy as np
from PIL import Image
import torch

from coatdesk_ab.data import SHAPES, letterbox, load_photo
from coatdesk_ab.network import PhotoClassifier
from coatdesk_locator.model import build_detector, choose_detection
from scripts.retrain_four_training import change
from scripts.retraining_20261122_gpu import gpu_lease
from scripts.size_retrain_20261122 import BASE, CLASSES, INPUT, KINDS, WORK, configure, read, save, sha, tensor
from scripts.size_retrain_20261122_rare_fit import features, load_model
from scripts.size_retrain_20261122_validate_more import VOUT, VWORK, exposure_groups, status


def freeze_real():
    path = VOUT / 'real-manifest.json'
    if path.exists():
        return read(path)
    inventory = read(INPUT / 'inventory-annotated.json')
    fitted = exposure_groups(inventory)
    selected = {r['code']: r for r in read(WORK / 'frozen.json')}
    rows = [dict(r, cohort='real', fitted_group=r['group'] in fitted,
                 main_split=selected[r['code']]['split'] if r['code'] in selected else 'outside2600',
                 truth=r['raw_size'], truth_business=r['size'], source_kind='real') for r in inventory]
    assert len(rows) == 7329 and len({r['code'] for r in rows}) == 7329
    assert sum(r['truth'] is None for r in rows) == 578
    save(path, rows)
    save(VOUT / 'real-scope.json', {'photos': len(rows), 'raw_known': sum(r['truth'] is not None for r in rows),
         'main_split': dict(Counter(r['main_split'] for r in rows)),
         'gradient_exposure_groups': dict(Counter(r['fitted_group'] for r in rows)),
         'raw_truth_not_inferred': True, 'business_truth': 'unchanged inventory workbook size',
         'inventory_sha256': sha(INPUT / 'inventory-annotated.json')})
    return rows


def freeze_real2600():
    path = VOUT / 'real2600-manifest.json'
    if path.exists():
        return read(path)
    selected = {r['code'] for r in read(WORK / 'frozen.json')}
    rows = [r for r in freeze_real() if r['code'] in selected]
    assert len(rows) == 2600 and all(r['truth'] is not None for r in rows)
    assert Counter(r['main_split'] for r in rows) == {'train': 2000, 'dev': 300, 'test': 300}
    save(path, rows)
    return rows


def shared_core(before, after):
    a, b = before.state_dict(), after.state_dict()
    changed = [k for k in a if not torch.equal(a[k], b[k])]
    assert set(changed) == {'output.1.weight', 'output.1.bias'}, changed
    other = [i for i, c in enumerate(CLASSES) if c not in ('60', '2XS', '170')]
    assert torch.equal(a['output.1.weight'][other], b['output.1.weight'][other])
    assert torch.equal(a['output.1.bias'][other], b['output.1.bias'][other])
    return changed


@torch.inference_mode()
def one_photo(row, detector, shape, before, after_head):
    started = time.perf_counter()
    photo = load_photo(row['source'])
    photos = [change(photo, kind) for kind in KINDS]
    inputs = [torch.from_numpy(np.asarray(p.resize((448, 448), Image.Resampling.LANCZOS)).transpose(2, 0, 1).copy()).float().cuda() / 255 for p in photos]
    detections = detector(inputs)
    locations, active, panels = [], [], []
    for k, (p, d) in enumerate(zip(photos, detections)):
        d['boxes'] = d['boxes'] * d['boxes'].new_tensor([p.width / 448, p.height / 448] * 2)
        loc = choose_detection(d, p.width, p.height)
        locations.append(loc)
        if loc['crop_box']:
            active.append(k)
            panels.append(tensor(letterbox(p.crop(loc['crop_box']))[0]))
    calculated = {}
    if panels:
        images = torch.stack(panels).cuda()
        sp = shape(images).softmax(1).cpu()
        values = features(before, images)
        bp = before.output(values).softmax(1).cpu()
        ap = after_head(values).softmax(1).cpu()
        calculated = {k: (s, b, a) for k, s, b, a in zip(active, sp, bp, ap)}
    result = {'before': [], 'after': []}
    for k, kind in enumerate(KINDS):
        common = {key: row.get(key) for key in ('code', 'group', 'age', 'source_kind', 'cohort', 'main_split', 'fitted_group', 'truth', 'truth_business', 'parent_code')}
        common.update(kind=kind, panel_score=locations[k]['score'], crop_box=locations[k]['crop_box'])
        if k not in calculated:
            for which in result:
                result[which].append(dict(common, predicted='<failure>', score=0., shape='', shape_score=0., consistent=False, failure='no_panel'))
            continue
        s, b, a = calculated[k]
        shape_score, shape_index = s.max(0)
        predicted_shape = SHAPES[int(shape_index)]
        for which, p in [('before', b), ('after', a)]:
            score, index = p.max(0)
            raw = CLASSES[int(index)]
            result[which].append(dict(common, predicted=raw, score=float(score), shape=predicted_shape,
                shape_score=float(shape_score), consistent=raw.isdigit() == (predicted_shape == 'child'), failure=None,
                probabilities={c: float(v) for c, v in zip(CLASSES, p)}))
    result['elapsed_seconds'] = time.perf_counter() - started
    return result


def run(scope):
    configure()
    rows = (freeze_real() if scope == 'real' else freeze_real2600() if scope == 'real2600'
            else read(VOUT / 'synthetic-fullframe-manifest.json'))
    protocol = read(VOUT / 'protocol.json')
    for model in protocol['models'].values():
        assert sha(model['path']) == model['sha256']
    cache = VWORK / f'{scope}-predictions'
    cache.mkdir(parents=True, exist_ok=True)
    manifest_name = 'synthetic-fullframe-manifest.json' if scope == 'synthetic' else scope + '-manifest.json'
    provenance = {'manifest_sha256': sha(VOUT / manifest_name),
                  'models': protocol['models'], 'conditions': list(KINDS), 'pipeline': 'full-frame SSD448 + shape512 + shared exact size core512; FP32'}
    provenance_path = cache / 'provenance.json'
    if provenance_path.exists():
        assert read(provenance_path) == provenance
    else:
        save(provenance_path, provenance)
    if scope == 'real2600':
        old_cache = VWORK / 'real-predictions'
        old_provenance = read(old_cache / 'provenance.json')
        assert old_provenance['models'] == provenance['models'] and old_provenance['conditions'] == list(KINDS)
        reused = []
        for row in rows:
            previous, target = old_cache / (row['code'] + '.json'), cache / (row['code'] + '.json')
            if previous.exists() and not target.exists():
                assert read(previous)['source_sha256'] == row['sha256']
                shutil.copy2(previous, target)
                reused.append(row['code'])
        save(VOUT / 'real2600-cache-reuse.json', {'reused': reused,
             'reason': 'User clarified scope to frozen2600; same source/model/conditions, no outcome-based exclusion.'})
    status('waiting_gpu', scope=scope, evaluation_pid=os.getpid(), total_photos=len(rows), error=None)
    started = time.monotonic()
    with gpu_lease('size'):
        before = load_model(protocol['models']['before']['path'])
        after = load_model(protocol['models']['after']['path'])
        shared_core(before, after)
        after_head = after.output.cuda().eval()
        before = before.cuda().eval()
        detector = build_detector().cuda().eval()
        detector.transform.fixed_size = (448, 448)
        detector.load_state_dict(torch.load(BASE / 'detector.pt', map_location='cpu', weights_only=True))
        shape = PhotoClassifier(3).cuda().eval()
        shape.load_state_dict(torch.load(BASE / 'panel-shape.pt', map_location='cpu', weights_only=True))
        for i, row in enumerate(rows):
            path = cache / f"{row['code']}.json"
            assert sha(row['source']) == row['sha256'], row['code']
            if not path.exists():
                result = one_photo(row, detector, shape, before, after_head)
                save(path, dict(result, source_sha256=row['sha256']))
            if i % 25 == 0 or i + 1 == len(rows):
                status('evaluating', scope=scope, evaluated_photos=i + 1, total_photos=len(rows),
                       elapsed_seconds=time.monotonic() - started, error=None)
    save(VOUT / f'{scope}-evaluation-complete.json', dict(provenance, photos=len(rows), conditions=len(rows) * 9,
        elapsed_seconds=time.monotonic() - started, evaluation_only=True))
    status('evaluation_phase_complete', scope=scope, evaluated_photos=len(rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('scope', choices=['real', 'real2600', 'synthetic'])
    try:
        run(parser.parse_args().scope)
    except Exception as error:
        status('error', error=repr(error))
        raise
