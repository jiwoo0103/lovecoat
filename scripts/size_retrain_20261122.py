"""Isolated size retraining; never writes application assets or original photographs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import time

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.local/size-retrain-20261122'
OUT = ROOT / 'model-results/20261122-size-retrain'
INPUT = ROOT / 'model-results/20261122-training-candidates'
BASE = ROOT / 'dist-releases/v2-50c3b52756d1/CoatDesk2/_internal/resources/ai'
CLASSES = [str(n) for n in range(60, 171, 10)] + ['2XS', 'XS', 'S', 'M', 'L', 'XL', '2XL', '3XL']
SEED = 20261122
KINDS = ('original', 'dark', 'bright', 'warm', 'cool', 'contrast', 'shadow_left', 'shadow_right', 'blur')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def status(stage, **values):
    previous = read(OUT / 'status.json') if (OUT / 'status.json').exists() else {}
    previous.update(stage=stage, pid=os.getpid(), updated_at_utc=datetime.now(timezone.utc).isoformat(), **values)
    save(OUT / 'status.json', previous)
    print(json.dumps(previous, ensure_ascii=False), flush=True)


def configure():
    import numpy as np
    import torch
    torch.set_num_threads(4)
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)


def tensor(image):
    import numpy as np
    import torch
    value = torch.from_numpy(np.asarray(image).transpose(2, 0, 1).copy()).float() / 255
    return (value - value.new_tensor([.485, .456, .406])[:, None, None]) / value.new_tensor([.229, .224, .225])[:, None, None]


def validate_split(rows):
    groups, hashes = {}, {}
    for row in rows:
        assert row['raw_size'] in CLASSES and row['source_kind'] == 'real'
        for key, value, table in [('group', row['group'], groups), ('sha256', row['sha256'], hashes)]:
            if table.setdefault(value, row['split']) != row['split']:
                raise ValueError(f'{key} crosses splits: {value}')
    assert Counter(r['split'] for r in rows) == {'train': 2000, 'dev': 300, 'test': 300}


def select_parents(rows):
    selected = []
    for target in ('60', '70', '2XS'):
        eligible = [r for r in rows if r['split'] == 'train' and
                    (r['raw_size'] in ('80', '90') if target != '2XS' else
                     r['raw_size'] in ('XS', 'S') and r['gender'] == '여성')]
        # Different garments/capture bins; no dev/test parent and no brightness clones.
        eligible.sort(key=lambda r: (r['capture_bin'], int(r['code'])))
        chosen = []
        for r in eligible:
            if r['group'] not in {x['group'] for x in chosen}:
                chosen.append(r)
        # Distributed through available photos, rather than the same capture sequence.
        chosen = [chosen[round(i * (len(chosen) - 1) / 5)] for i in range(6)]
        selected.extend(dict(r, target=target, synthetic_id=f"syn-{target}-{r['code']}") for r in chosen)
    return selected


def prepare():
    import numpy as np
    import torch
    from PIL import Image, ImageDraw
    from coatdesk_ab.data import letterbox, load_photo
    from coatdesk_locator.model import build_detector, choose_detection
    configure()
    status('preparing', epoch=0, best_dev=None, error=None)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / 'panels').mkdir(exist_ok=True)
    (OUT / 'synthetic/inputs').mkdir(parents=True, exist_ok=True)
    rows = read(INPUT / 'size-candidates.json')
    validate_split(rows)
    protected = {str(p): sha(p) for p in BASE.rglob('*') if p.is_file()}
    protected.update({str(INPUT / name): sha(INPUT / name) for name in
                      ('size-candidates.json', 'inventory-annotated.json', 'final-verification.json')})
    save(WORK / 'protected.json', protected)
    save(WORK / 'frozen-candidates.json', rows)
    manifest = read(BASE / 'manifest.json')
    assert len(manifest['size_classes']) == 18
    for name, digest in manifest['files'].items():
        assert sha(BASE / name) == digest, name
    parents = select_parents(rows)
    save(OUT / 'synthetic/parents.json', parents)
    detector = build_detector().eval()
    detector.transform.fixed_size = (448, 448)
    detector.load_state_dict(torch.load(BASE / 'detector.pt', map_location='cpu', weights_only=True))
    by_code = {r['code']: r for r in rows}
    order = list(dict.fromkeys([p['code'] for p in parents] + [r['code'] for r in rows]))
    prepared = read(WORK / 'prepared-progress.json') if (WORK / 'prepared-progress.json').exists() else {}
    started = time.monotonic()
    for index, code in enumerate(order):
        r = by_code[code]
        if sha(r['source']) != r['sha256']:
            raise ValueError(f'Source changed: {code}')
        if code not in prepared:
            photo = load_photo(r['source'])
            image = torch.from_numpy(np.asarray(photo.resize((448, 448), Image.Resampling.LANCZOS)).transpose(2, 0, 1).copy()).float() / 255
            with torch.inference_mode():
                prediction = detector([image])[0]
            prediction['boxes'] = prediction['boxes'] * prediction['boxes'].new_tensor([photo.width / 448, photo.height / 448] * 2)
            location = choose_detection(prediction, photo.width, photo.height)
            if not location['crop_box']:
                raise ValueError(f'No panel candidate; no silent exclusion: {code}')
            panel = letterbox(photo.crop(location['crop_box']))[0]
            path = WORK / 'panels' / f'{code}.png'
            panel.save(path)
            prepared[code] = dict(r, panel=str(path), panel_sha256=sha(path), location=location,
                                  coordinate_size=list(photo.size))
            save(WORK / 'prepared-progress.json', prepared)
        if index % 25 == 0 or index + 1 == len(order):
            status('preparing', prepared=index + 1, total=len(order), elapsed_seconds=time.monotonic() - started)
        if index == len(set(p['code'] for p in parents)) - 1:
            for p in parents:
                Image.open(prepared[p['code']]['panel']).save(OUT / 'synthetic/inputs' / f"{p['synthetic_id']}.png")
            contact = Image.new('RGB', (6 * 256, 3 * 290), 'white')
            draw = ImageDraw.Draw(contact)
            for j, p in enumerate(parents):
                thumb = Image.open(prepared[p['code']]['panel']).resize((256, 256))
                x, y = j % 6 * 256, j // 6 * 290
                contact.paste(thumb, (x, y))
                draw.text((x + 4, y + 256), f"{p['code']} {p['raw_size']} -> {p['target']}", fill='black')
            contact.save(OUT / 'synthetic/parent-contact.jpg')
    frozen = [prepared[r['code']] for r in rows]
    save(WORK / 'frozen.json', frozen)
    exposure = {}
    history_path = ROOT / '.local/robust-four-models-20261102/frozen-dataset.json'
    if history_path.exists():
        history = {r['code']: r['split'] for r in read(history_path)['records']}
        exposure = {s: dict(Counter(history.get(r['code'], 'not_in_prior_snapshot') for r in rows if r['split'] == s)) for s in ('train', 'dev', 'test')}
    save(OUT / 'data-integrity.json', {'counts': dict(Counter(r['split'] for r in rows)),
         'group_cross_split_overlap': 0, 'hash_cross_split_overlap': 0, 'all_source_hashes_verified': True,
         'frozen_sha256': sha(WORK / 'frozen.json'), 'input_sha256': sha(INPUT / 'size-candidates.json'),
         'protected': protected, 'prior_20261102_exposure': exposure, 'classes': CLASSES,
         'limitations': ['Retrospective photographs; not an independently collected blind test.',
                        'Adult 2XS has no real examples; 170 has no independent dev/test.']})
    status('synthetic_preparation', prepared=len(rows), total=len(rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare'])
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare()
    except Exception as error:
        status('error', error=repr(error))
        raise


if __name__ == '__main__':
    main()
