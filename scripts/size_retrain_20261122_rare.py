"""Additional 60/2XS/170-only fit with parent-disjoint synthetic validation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scripts.size_retrain_20261122 import CLASSES, OUT, SEED, WORK, read, save, sha

RWORK = WORK / 'rare-followup'
ROUT = OUT / 'rare-followup'
TARGETS = ['60', '2XS', '170']


def status(stage, **values):
    path = ROUT / 'status.json'
    record = read(path) if path.exists() else {}
    record.update(stage=stage, pid=os.getpid(), updated_at_utc=datetime.now(timezone.utc).isoformat(), **values)
    save(path, record)
    print(record, flush=True)


def distributed(rows, count):
    unique = {}
    for r in sorted(rows, key=lambda r: (r['capture_bin'], int(r['code']))):
        unique.setdefault(r['group'], r)
    rows = list(unique.values())
    assert len(rows) >= count
    if count == 1:
        return [rows[len(rows) // 2]]
    return [rows[round(i * (len(rows) - 1) / (count - 1))] for i in range(count)]


def prepare():
    assert not (ROUT / 'parents.json').exists(), 'Already frozen; retain existing run.'
    status('preparing', epoch=0, error=None)
    RWORK.mkdir(parents=True, exist_ok=True)
    (ROUT / 'inputs').mkdir(parents=True, exist_ok=True)
    (ROUT / 'generated').mkdir(exist_ok=True)
    rows = read(WORK / 'frozen.json')
    previous = read(OUT / 'synthetic/manifest.json')['records']
    previous_groups = {r['parent_group'] for r in previous}
    parents = []
    for target in TARGETS:
        for split, count in [('train', 12), ('dev', 4), ('test', 4)]:
            pool = [r for r in rows if r['split'] == split and r['group'] not in previous_groups and
                (r['raw_size'] in ('80', '90') if target == '60' else
                 r['raw_size'] in ('150', '160') if target == '170' else
                 r['raw_size'] in ('XS', 'S') and r['gender'] == '여성')]
            if target == '170':
                closest = [r for r in pool if r['raw_size'] == '160']
                selected = distributed(closest, count) if len(closest) >= count else closest + distributed([r for r in pool if r['raw_size'] == '150'], count - len(closest))
            else:
                selected = distributed(pool, count)
            for parent in selected:
                assert sha(parent['source']) == parent['sha256']
                assert sha(parent['panel']) == parent['panel_sha256']
                identifier = f"rare-{target}-{split}-{parent['code']}"
                path = ROUT / 'inputs' / f'{identifier}.png'
                Image.open(parent['panel']).save(path)
                parents.append(dict(parent, target=target, synthetic_split=split, synthetic_id=identifier,
                                    input=str(path), input_sha256=sha(path)))
    assert len(parents) == 60 and len({r['group'] for r in parents}) == 60
    save(ROUT / 'parents.json', parents)
    save(ROUT / 'protocol.json', {'seed': SEED, 'classes': CLASSES, 'targets': TARGETS,
         'base_checkpoint': str(OUT / 'size20.pt'), 'base_sha256': sha(OUT / 'size20.pt'),
         'additional_synthetic_each': {'train': 12, 'dev': 4, 'test': 4},
         'all_60_parent_groups_unique': True, 'previous_synthetic_parent_groups_excluded': True,
         'original_split_preserved': True, 'parent_source_kinds': 'real frozen original photographs',
         'train_scope': 'only real and accepted synthetic 60/2XS/170; freeze features, attention and other17 output rows',
         'validation': 'real dev300 and synthetic dev12 only for selection; real test300 repeated audit plus synthetic test12 heldout parents',
         'synthetic_limits': 'Generated panels are not real adult2XS/170 accuracy. Old real test already inspected.'})
    for target in TARGETS:
        group = [r for r in parents if r['target'] == target]
        contact = Image.new('RGB', (5 * 240, 4 * 268), 'white')
        draw = ImageDraw.Draw(contact)
        for j, r in enumerate(group):
            x, y = j % 5 * 240, j // 5 * 268
            contact.paste(Image.open(r['input']).resize((240, 240)), (x, y))
            draw.text((x + 2, y + 242), f"{r['synthetic_split']} {r['code']} {r['raw_size']}->{target}", fill='black')
        contact.save(ROUT / f'parents-{target}.jpg')
    status('generating', frozen_parents=60, counts={t: dict(Counter(r['synthetic_split'] for r in parents if r['target'] == t)) for t in TARGETS})


def contained_ink(image, bounds):
    """Keep complete glyphs; discard hair/border/adjacent digit fragments at edges."""
    import cv2
    x0, y0, x1, y1 = bounds
    binary = (image[y0:y1, x0:x1].mean(2) < 150).astype('uint8')
    count, labels = cv2.connectedComponents(binary, connectivity=8)
    boundary = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
    selected = [i for i in range(1, count) if i not in boundary and np.sum(labels == i) >= 5]
    result = np.zeros(image.shape[:2], dtype='uint8')
    result[y0:y1, x0:x1] = np.isin(labels, selected).astype('uint8') * 255
    return result


def composite():
    rows = read(ROUT / 'parents.json')
    (ROUT / 'panels').mkdir(exist_ok=True)
    records = []
    for r in rows:
        path = ROUT / 'generated' / f"{r['synthetic_id']}.png"
        if not path.exists() or not path.with_suffix('.json').exists():
            continue
        original = Image.open(r['input']).convert('RGB')
        native = Image.open(path).convert('RGB')
        generated = native.resize((512, 512), Image.Resampling.LANCZOS)
        a, b = np.asarray(original), np.asarray(generated)
        bounds = {'60': (120, 210, 280, 395), '170': (195, 210, 320, 395), '2XS': (125, 230, 395, 410)}[r['target']]
        roi = np.zeros((512, 512), bool)
        x0, y0, x1, y1 = bounds
        roi[y0:y1, x0:x1] = True
        paper = roi & (a.min(2) > 160) & (b.min(2) > 160)
        assert paper.sum() > 100
        ratio = np.median(a[paper].astype(float) / np.maximum(1, b[paper]), axis=0)
        corrected = Image.fromarray(np.clip(b.astype(float) * ratio[None, None, :], 0, 255).astype('uint8'))
        ink = np.maximum(contained_ink(a, bounds), contained_ink(b, bounds))
        assert np.sum(ink > 0) > 100
        mask = np.asarray(Image.fromarray(ink).filter(ImageFilter.MaxFilter(11))).copy()
        mask[~roi] = 0
        result = Image.composite(corrected, original, Image.fromarray(mask))
        c = np.asarray(result)
        assert np.array_equal(a[mask == 0], c[mask == 0])
        output = ROUT / 'panels' / f"{r['synthetic_id']}.png"
        result.save(output)
        metadata = read(path.with_suffix('.json'))
        records.append({'code': r['synthetic_id'], 'synthetic_id': r['synthetic_id'], 'group': r['group'],
            'parent_code': r['code'], 'parent_group': r['group'], 'parent_split': r['split'],
            'parent_source': r['source'], 'parent_sha256': r['sha256'], 'parent_raw_size': r['raw_size'],
            'split': r['synthetic_split'], 'raw_size': r['target'], 'age': r['age'], 'gender': r['gender'],
            'source_kind': 'synthetic', 'panel': str(output), 'panel_sha256': sha(output),
            'generated': str(path), 'generated_sha256': sha(path), 'generation_dimensions': list(native.size),
            'generator': 'built_in_image_gen', 'prompt': metadata['prompt'], 'bounds': list(bounds),
            'paper_tone_ratio': ratio.tolist(), 'outside_text_mask_pixel_exact': True,
            'changed_fraction': float(np.any(a != c, 2).mean()), 'review': {'accepted': False}})
    save(ROUT / 'synthetic.json', records)
    for target in TARGETS:
        selected = [r for r in records if r['raw_size'] == target]
        contact = Image.new('RGB', (5 * 256, 4 * 284), 'white')
        draw = ImageDraw.Draw(contact)
        for j, r in enumerate(selected):
            x, y = j % 5 * 256, j // 5 * 284
            contact.paste(Image.open(r['panel']).resize((256, 256)), (x, y))
            draw.text((x + 2, y + 258), f"{r['split']} {r['parent_code']} -> {target}", fill='black')
        contact.save(ROUT / f'review-{target}.jpg')
    status('synthetic_review', generated=len(records), planned=60, error=None)


def accept():
    records = read(ROUT / 'synthetic.json')
    assert len(records) == 60
    assert all(Counter(r['split'] for r in records if r['raw_size'] == t) == {'train': 12, 'dev': 4, 'test': 4} for t in TARGETS)
    for r in records:
        r['review'] = {'accepted': True, 'text': 'exact target visually checked',
             'font': 'matches original heavy sans-serif approximately; 2XS spacing fits original sign',
             'context': 'original pixels retained outside generated target glyph mask',
             'reviewer': 'Codex visual QA, not independent human labeling'}
    save(ROUT / 'synthetic.json', records)
    status('ready_to_train', accepted=60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'composite', 'accept'])
    args = parser.parse_args()
    try:
        {'prepare': prepare, 'composite': composite, 'accept': accept}[args.command]()
    except Exception as error:
        status('error', error=repr(error))
        raise
