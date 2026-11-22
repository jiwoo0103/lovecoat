"""Evaluation-only expansion: fresh 60/2XS panels and whole-photo paired audit."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import argparse

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scripts.size_retrain_20261122 import BASE, INPUT, OUT, WORK, configure, read, save, sha
from scripts.size_retrain_20261122_rare import contained_ink, distributed

VOUT = OUT / 'validation-more'
VWORK = WORK / 'validation-more'
TARGETS = ['60', '2XS']


def status(stage, **values):
    path = VOUT / 'status.json'
    record = read(path) if path.exists() else {}
    record.update(stage=stage, pid=os.getpid(), updated_at_utc=datetime.now(timezone.utc).isoformat(), **values)
    save(path, record)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def exposure_groups(inventory):
    by_code = {r['code']: r for r in inventory}
    fitted = {r['group'] for r in read(WORK / 'frozen.json') if r['split'] == 'train'}
    history = WORK.parent / 'robust-four-models-20261102/frozen-dataset.json'
    if history.exists():
        for row in read(history)['records']:
            code = str(row['code']).zfill(4)
            if row['split'] == 'train' and row.get('panel_eligible', True) and code in by_code:
                fitted.add(by_code[code]['group'])
    return fitted


def prepare():
    import torch
    from coatdesk_ab.data import letterbox, load_photo
    from coatdesk_locator.model import build_detector, choose_detection
    assert not (VOUT / 'parents.json').exists(), 'Reuse frozen parents; do not resample after evaluation.'
    configure()
    status('preparing', error=None)
    VWORK.mkdir(parents=True, exist_ok=True)
    for name in ('inputs', 'generated', 'panels', 'fullframes'):
        (VOUT / name).mkdir(exist_ok=True)
    inventory = read(INPUT / 'inventory-annotated.json')
    prior = read(OUT / 'synthetic/manifest.json')['records']
    follow = read(OUT / 'rare-followup/parents.json')
    used = {r['parent_group'] for r in prior} | {r['group'] for r in follow}
    fitted = exposure_groups(inventory)
    frozen = {r['code']: r for r in read(WORK / 'frozen.json')}
    eligible = [r for r in inventory if r['split'] in ('dev', 'test') and r['group'] not in used | fitted]
    children = [r for r in eligible if r['age'] == '유아동' and r['size'] == 'XS' and r['raw_size'] is None]
    assert len(children) == 9
    known = next(r for r in eligible if r['code'] == '3696' and r['raw_size'] == '90')
    children = [known, *children]
    adults = distributed([r for r in eligible if r['age'] == '성인' and r['gender'] == '여성'
                          and r['raw_size'] in ('XS', 'S') and r['code'] not in frozen], 10)
    parents = []
    detector = build_detector().cpu().eval()
    detector.transform.fixed_size = (448, 448)
    detector.load_state_dict(torch.load(BASE / 'detector.pt', map_location='cpu', weights_only=True))
    for target, selected in [('60', children), ('2XS', adults)]:
        for row in selected:
            assert sha(row['source']) == row['sha256']
            photo = load_photo(row['source'])
            if row['code'] in frozen:
                location = frozen[row['code']]['location']
            else:
                image = torch.from_numpy(np.asarray(photo.resize((448, 448), Image.Resampling.LANCZOS)).transpose(2, 0, 1).copy()).float() / 255
                with torch.inference_mode():
                    prediction = detector([image])[0]
                    prediction['boxes'] = prediction['boxes'] * prediction['boxes'].new_tensor([photo.width / 448, photo.height / 448] * 2)
                    location = choose_detection(prediction, photo.width, photo.height)
            assert location['crop_box'], row['code']
            panel, letterbox_info = letterbox(photo.crop(location['crop_box']))
            identifier = f"new-validation-{target}-{row['code']}"
            path = VOUT / 'inputs' / f'{identifier}.png'
            panel.save(path)
            parents.append(dict(row, synthetic_id=identifier, target=target, input=str(path),
                source_text=row['raw_size'], source_text_reviewed=False, input_sha256=sha(path),
                location=location, coordinate_size=list(photo.size), letterbox=letterbox_info,
                previously_in_main_frozen_dataset=row['code'] in frozen, parent_fitted=False))
    assert len(parents) == 20 and len({r['group'] for r in parents}) == 20
    save(VOUT / 'parents.json', parents)
    save(VOUT / 'protocol.json', {'models': {
        'before': {'path': str(OUT / 'size20.pt'), 'sha256': sha(OUT / 'size20.pt')},
        'after': {'path': str(OUT / 'rare-followup/rare-size20.pt'), 'sha256': sha(OUT / 'rare-followup/rare-size20.pt')}},
        'training_permitted': False, 'new_synthetic_each': 10, 'new_synthetic_role': 'evaluation_only',
        'original_inventory_sha256': sha(INPUT / 'inventory-annotated.json'),
        'new_parent_gradient_group_overlap': 0, 'prior_training_synthetic_parent_overlap': 0,
        'untranscribed_parent_raw_labels_not_promoted_to_original_real_ground_truth': True,
        'evaluation_conditions': ['original', 'dark', 'bright', 'warm', 'cool', 'contrast', 'shadow_left', 'shadow_right', 'blur']})
    for target in TARGETS:
        selected = [r for r in parents if r['target'] == target]
        contact = Image.new('RGB', (5 * 256, 2 * 284), 'white')
        draw = ImageDraw.Draw(contact)
        for i, row in enumerate(selected):
            x, y = i % 5 * 256, i // 5 * 284
            contact.paste(Image.open(row['input']).resize((256, 256)), (x, y))
            draw.text((x + 2, y + 259), f"{row['code']} source={row['source_text']} -> {target}", fill='black')
        contact.save(VOUT / f'parent-review-{target}.jpg')
    status('parent_text_review', parents=20, error=None)


def restore_fullframe(photo, panel, mask, location):
    """Inverse letterbox, transferring generated glyph pixels only to a new PNG."""
    from coatdesk_ab.data import letterbox
    box = location['crop_box']
    crop = photo.crop(box)
    _, info = letterbox(crop)
    ox, oy = info['offset']
    width, height = info['resized_size']
    region = (ox, oy, ox + width, oy + height)
    resized = panel.crop(region).resize(crop.size, Image.Resampling.LANCZOS)
    resized_mask = mask.crop(region).resize(crop.size, Image.Resampling.NEAREST)
    output = photo.copy()
    output.paste(Image.composite(resized, crop, resized_mask), (box[0], box[1]))
    full_mask = Image.new('L', photo.size)
    full_mask.paste(resized_mask, (box[0], box[1]))
    a, b, m = np.asarray(photo), np.asarray(output), np.asarray(full_mask)
    assert np.array_equal(a[m == 0], b[m == 0])
    return output, full_mask


def contact(records, target, name):
    selected = [r for r in records if r['raw_size'] == target]
    sheet = Image.new('RGB', (5 * 256, ((len(selected) + 4) // 5) * 284), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, r in enumerate(selected):
        x, y = i % 5 * 256, i // 5 * 284
        sheet.paste(Image.open(r['panel']).resize((256, 256)), (x, y))
        draw.text((x + 2, y + 260), r['code'], fill='black')
    sheet.save(VOUT / f'{name}-{target}.jpg')


def composite():
    from coatdesk_ab.data import load_photo
    records = []
    for r in read(VOUT / 'parents.json'):
        path = VOUT / 'generated' / f"{r['synthetic_id']}.png"
        if not path.exists() or not path.with_suffix('.json').exists():
            continue
        assert r['source_text_reviewed'] and sha(r['source']) == r['sha256']
        a = np.asarray(Image.open(r['input']).convert('RGB'))
        native = Image.open(path).convert('RGB')
        b = np.asarray(native.resize((512, 512), Image.Resampling.LANCZOS))
        bounds = (115, 205, 270, 400) if r['target'] == '60' else (120, 225, 400, 415)
        roi = np.zeros((512, 512), dtype=bool)
        x0, y0, x1, y1 = bounds
        roi[y0:y1, x0:x1] = True
        paper = roi & (a.min(2) > 160) & (b.min(2) > 160)
        assert paper.sum() > 100
        ratio = np.median(a[paper].astype(float) / np.maximum(1, b[paper]), axis=0)
        corrected = Image.fromarray(np.clip(b.astype(float) * ratio[None, None, :], 0, 255).astype('uint8'))
        ink = np.maximum(contained_ink(a, bounds), contained_ink(b, bounds))
        assert (ink > 0).sum() > 100
        mask = np.asarray(Image.fromarray(ink).filter(ImageFilter.MaxFilter(11))).copy()
        mask[~roi] = 0
        panel = Image.composite(corrected, Image.fromarray(a), Image.fromarray(mask))
        assert np.array_equal(a[mask == 0], np.asarray(panel)[mask == 0])
        panel_path = VOUT / 'panels' / f"{r['synthetic_id']}.png"
        panel.save(panel_path)
        full, full_mask = restore_fullframe(load_photo(r['source']), panel, Image.fromarray(mask), r['location'])
        full_path = VOUT / 'fullframes' / f"{r['synthetic_id']}.png"
        full.save(full_path)
        full_mask.save(full_path.with_suffix('.mask.png'))
        metadata = read(path.with_suffix('.json'))
        records.append(dict(code=r['synthetic_id'], group=r['group'], parent_code=r['code'],
            parent_group=r['group'], parent_source=r['source'], parent_sha256=r['sha256'],
            parent_raw_size=r['raw_size'], parent_visual_text=r['source_text'],
            age=r['age'], gender=r['gender'], raw_size=r['target'], source_kind='synthetic',
            cohort='new_validation', truth=r['target'], source=str(full_path), sha256=sha(full_path),
            panel=str(panel_path), panel_sha256=sha(panel_path), generated=str(path), generated_sha256=sha(path),
            generation_dimensions=list(native.size), prompt=metadata['prompt'], generator='built_in_image_gen',
            bounds=list(bounds), paper_tone_ratio=ratio.tolist(), outside_text_mask_pixel_exact=True,
            fullframe_outside_glyph_mask_pixel_exact=True, parent_gradient_exposed=False,
            review={'accepted': False}, evaluation_only=True))
    save(VOUT / 'new-synthetic.json', records)
    for target in TARGETS:
        if any(r['raw_size'] == target for r in records):
            contact(records, target, 'new-review')
    print({'new_composited': len(records)}, flush=True)


def accept_and_freeze():
    from coatdesk_ab.data import letterbox, load_photo
    from scripts.size_retrain_20261122_fit import business
    records = read(VOUT / 'new-synthetic.json')
    assert len(records) == 20
    for r in records:
        r['review'] = {'accepted': True, 'text': 'exact target visually checked',
            'font': 'approximate original heavy sans-serif; synthetic font domain remains a limitation',
            'context': 'original pixels outside glyph mask retained in panel and fullframe', 'reviewer': 'Codex visual QA'}
    save(VOUT / 'new-synthetic.json', records)
    frozen = {r['code']: r for r in read(WORK / 'frozen.json')}
    old = read(OUT / 'synthetic/manifest.json')['records']
    follow = read(OUT / 'rare-followup/synthetic.json')
    for r in old + follow:
        parent = frozen[r['parent_code']]
        target = r.get('target', r.get('raw_size'))
        assert r['review']['accepted'] and sha(r['parent_source']) == r['parent_sha256']
        assert sha(r['panel']) == r.get('panel_sha256', r.get('sha256'))
        original, _ = letterbox(load_photo(parent['source']).crop(parent['location']['crop_box']))
        panel = Image.open(r['panel']).convert('RGB')
        mask = Image.fromarray((np.any(np.asarray(original) != np.asarray(panel), 2) * 255).astype('uint8'))
        full, full_mask = restore_fullframe(load_photo(parent['source']), panel, mask, parent['location'])
        identifier = r['synthetic_id']
        path = VOUT / 'fullframes' / f'{identifier}.png'
        full.save(path)
        full_mask.save(path.with_suffix('.mask.png'))
        records.append(dict(code=identifier, group=parent['group'], parent_code=parent['code'],
            parent_source=parent['source'], parent_sha256=parent['sha256'],
            age=parent['age'], gender=parent['gender'], raw_size=target, truth=target,
            source_kind='synthetic', cohort='initial_train' if r in old else 'followup_' + r['split'],
            source=str(path), sha256=sha(path), panel=r['panel'], panel_sha256=sha(r['panel']),
            evaluation_only=True, original_record_manifest=str(OUT / ('synthetic/manifest.json' if r in old else 'rare-followup/synthetic.json')),
            fullframe_outside_glyph_mask_pixel_exact=True))
    for r in records:
        r['truth_business'] = business(r['truth'])
        r['main_split'] = r['cohort']
    assert len(records) == 98 and len({r['code'] for r in records}) == 98
    # Keep legacy previews explicitly outside accepted validation accuracy.
    inventory_by_code = {r['code']: r for r in read(INPUT / 'inventory-annotated.json')}
    for r in read(INPUT / 'synthetic-previews/manifest.json')['records']:
        parent = inventory_by_code[r['parent_code']]
        records.append(dict(code='preview-' + r['raw_size'] + '-' + r['parent_code'],
            group=parent['group'], parent_code=parent['code'], age=parent['age'], gender=parent['gender'],
            raw_size=r['raw_size'], truth=r['raw_size'], truth_business=business(r['raw_size']),
            source=r['output'], sha256=r['sha256'], source_kind='synthetic_preview',
            cohort='preview_reference_only', main_split='preview_reference_only',
            evaluation_only=True, accepted_validation=False,
            limitation='Historical whole-frame altered preview; separate diagnostic, not validation or training promotion.'))
    save(VOUT / 'synthetic-fullframe-manifest.json', records)
    print({'synthetic_fullframes': len(records)}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'composite', 'accept'])
    try:
        {'prepare': prepare, 'composite': composite, 'accept': accept_and_freeze}[parser.parse_args().command]()
    except Exception as error:
        status('error', error=repr(error))
        raise
