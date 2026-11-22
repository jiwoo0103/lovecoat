"""Freeze built-in ImageGen edits as text-only derived training panels.

ImageGen supplies all new glyphs. This preprocessing copies only reviewed text pixels
and their antialiasing margin into the frozen model input, retaining original context.
No fonts, programmatically drawn glyphs, or generated garments are used.
"""
from __future__ import annotations

import argparse
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scripts.size_retrain_20261122 import OUT, WORK, read, save, sha, status


def prepare():
    parents = read(OUT / 'synthetic/parents.json')
    frozen = {r['code']: r for r in read(WORK / 'frozen.json')}
    output = OUT / 'synthetic/panels'
    output.mkdir(exist_ok=True)
    records = []
    contact = Image.new('RGB', (6 * 256, 3 * 290), 'white')
    draw = ImageDraw.Draw(contact)
    for j, parent in enumerate(parents):
        identifier = parent['synthetic_id']
        original = Image.open(frozen[parent['code']]['panel']).convert('RGB')
        path = OUT / 'synthetic/generated' / f'{identifier}.png'
        generated_native = Image.open(path).convert('RGB')
        generated = generated_native.resize(original.size, Image.Resampling.LANCZOS)
        a, b = np.asarray(original), np.asarray(generated)
        # Reviewed central text bounds, excluding hair, ears, face border and collar.
        if parent['target'] in ('60', '70'):
            bounds = (145, 226, 365, 376)
        else:
            bounds = (132, 248, 385, 401)
        roi = np.zeros((512, 512), dtype=bool)
        x0, y0, x1, y1 = bounds
        roi[y0:y1, x0:x1] = True
        # Match the parent's local paper tone so generation brightness is not a
        # class cue. ImageGen's glyph geometry and antialiasing remain unchanged.
        paper = roi & (a.min(2) > 160) & (b.min(2) > 160)
        ratio = np.median(a[paper].astype(float) / np.maximum(1, b[paper]), axis=0)
        corrected = np.clip(b.astype(float) * ratio[None, None, :], 0, 255).astype('uint8')
        generated = Image.fromarray(corrected)
        ink = ((a.mean(2) < 150) | (b.mean(2) < 150)) & roi
        mask = Image.fromarray((ink * 255).astype('uint8')).filter(ImageFilter.MaxFilter(11))
        # Crop mask dilation to reviewed bounds; never modify nearby face contours.
        mask_array = np.asarray(mask).copy()
        mask_array[~roi] = 0
        mask = Image.fromarray(mask_array)
        result = Image.composite(generated, original, mask)
        final = output / f'{identifier}.png'
        result.save(final)
        c = np.asarray(result)
        outside = mask_array == 0
        assert np.array_equal(a[outside], c[outside])
        metadata = read(path.with_suffix('.json'))
        records.append({'synthetic_id': identifier, 'parent_code': parent['code'],
            'parent_group': parent['group'], 'parent_split': parent['split'],
            'parent_source': parent['source'], 'parent_sha256': parent['sha256'],
            'parent_raw_size': parent['raw_size'], 'target': parent['target'],
            'source_kind': 'synthetic', 'panel': str(final), 'sha256': sha(final),
            'generation_file': str(path), 'generation_sha256': sha(path),
            'generation_dimensions': list(generated_native.size), 'input_dimensions': [512, 512],
            'prompt': metadata['prompt'], 'generator': 'built_in_image_gen',
            'processing': 'LANCZOS to512; copy ImageGen glyph union+5px margin only, retain original non-text context',
            'paper_tone_ratio': ratio.tolist(),
            'bounds': list(bounds), 'changed_fraction': float(np.any(a != c, 2).mean()),
            'outside_text_mask_exact': True, 'outside_text_mae': float(np.abs(a[outside].astype(float) - c[outside]).mean()),
            'full_generated_outside_roi_mae': float(np.abs(a[~roi].astype(float) - b[~roi]).mean()),
            'review': {'accepted': False, 'text': 'pending', 'font': 'pending', 'structure': 'pending'},
            'evaluation_eligible': False})
        x, y = j % 6 * 256, j // 6 * 290
        contact.paste(result.resize((256, 256)), (x, y))
        draw.text((x + 3, y + 258), f"{identifier} from {parent['raw_size']}", fill='black')
    contact.save(OUT / 'synthetic/review-contact.jpg')
    save(OUT / 'synthetic/manifest.json', {'records': records, 'independent_parent_groups_per_target': 6,
         'accepted': False, 'evaluation_warning': 'All synthetic records inherit TRAIN parents; no real adult2XS evaluation exists.'})
    status('synthetic_review', synthetic_bases=18)


def accept():
    manifest = read(OUT / 'synthetic/manifest.json')
    for r in manifest['records']:
        r['review'] = {'accepted': True, 'text': 'exact target visually read',
                       'font': 'heavy sans-serif approximate original; 2XS spacing adjusted to fit',
                       'structure': 'head silhouette/garment/background retained; outside text mask pixel-exact',
                       'reviewer': 'Codex visual review of final contact and full panel crops; not independent human review'}
    manifest['accepted'] = True
    save(OUT / 'synthetic/manifest.json', manifest)
    status('ready_to_train', synthetic_bases=18, synthetic_per_target=6)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'accept'])
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else accept()
