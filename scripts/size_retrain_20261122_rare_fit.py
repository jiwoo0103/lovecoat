"""Fit only three output rows; all other weights stay bit-identical to size20."""
from __future__ import annotations

from collections import Counter
import json
import os
import time

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from coatdesk_ab.data import letterbox, load_photo
from coatdesk_ab.network import PhotoClassifier
from scripts.retrain_four_training import change
from scripts.retraining_20261122_gpu import gpu_lease
from scripts.size_retrain_20261122 import CLASSES, KINDS, OUT, SEED, WORK, configure, read, save, sha, tensor
from scripts.size_retrain_20261122_fit import Panels, write_csv
from scripts.size_retrain_20261122_evaluate import auto_summary, safe_metrics
from scripts.size_retrain_20261122_rare import ROUT, RWORK, TARGETS, status

RARE = [CLASSES.index(c) for c in TARGETS]
OTHER = [i for i in range(20) if i not in RARE]


def load_model(path):
    model = PhotoClassifier(20).eval()
    model.load_state_dict(torch.load(path, map_location='cpu', weights_only=True), strict=True)
    return model


@torch.inference_mode()
def features(model, images):
    value = model.features(images)
    attention = model.attention(value).flatten(2).softmax(-1)
    return (value.flatten(2) * attention).sum(-1)


def training_rows():
    frozen = read(WORK / 'frozen.json')
    real = [r for r in frozen if r['split'] == 'train' and r['raw_size'] in TARGETS]
    previous = read(OUT / 'synthetic/manifest.json')['records']
    by_code = {r['code']: r for r in frozen}
    old = [dict(by_code[r['parent_code']], code=r['synthetic_id'], raw_size=r['target'],
                panel=r['panel'], panel_sha256=r['sha256'], source_kind='synthetic')
           for r in previous if r['target'] in TARGETS]
    synthetic = read(ROUT / 'synthetic.json')
    assert len(synthetic) == 60 and all(r['review']['accepted'] for r in synthetic)
    new = [r for r in synthetic if r['split'] == 'train']
    result = real + old + new
    assert len(result) == 51 and set(r['raw_size'] for r in result) == set(TARGETS)
    groups = {r['group'] for r in result}
    for r in synthetic:
        if r['split'] != 'train':
            assert r['group'] not in groups
    assert not ({r['group'] for r in synthetic if r['split'] == 'dev'} & {r['group'] for r in synthetic if r['split'] == 'test'})
    for r in result:
        assert sha(r['panel']) == r['panel_sha256']
    return result


class RealPanels(Dataset):
    """Reuse unchanged detector/shape results and whole-photo transforms exactly."""
    def __init__(self, split):
        self.metadata = read(OUT / f'{split}-pipeline-predictions.json')['new']
        self.sources = {r['code']: r['source'] for r in read(WORK / 'frozen.json')}
        self.last_code = None
        self.last_photo = None

    def __len__(self):
        return len(self.metadata)

    def __getitem__(self, index):
        row = self.metadata[index]
        assert row['crop_box'] and not row['failure'], 'No silently excluded evaluation rows.'
        if self.last_code != row['code']:
            self.last_photo = load_photo(self.sources[row['code']])
            self.last_code = row['code']
        photo = change(self.last_photo, row['kind'])
        return tensor(letterbox(photo.crop(row['crop_box']))[0])


@torch.inference_mode()
def feature_cache(model, split, synthetic=False):
    path = RWORK / f"{'synthetic' if synthetic else 'real'}-{split}-features.pt"
    metadata_path = path.with_suffix('.json')
    if path.exists():
        provenance = read(path.with_suffix('.provenance.json'))
        assert provenance['base_sha256'] == sha(OUT / 'size20.pt')
        assert provenance['synthetic_sha256'] == sha(ROUT / 'synthetic.json')
        return torch.load(path, map_location='cpu', weights_only=True).cuda(), read(metadata_path)
    if synthetic:
        rows = [r for r in read(ROUT / 'synthetic.json') if r['split'] == split]
        dataset = Panels(rows, kinds=KINDS)
        metadata = [dict(code=r['code'], group=r['group'], kind=kind, truth=r['raw_size'], age=r['age'],
                         source_kind='synthetic', failure=None) for r in rows for kind in KINDS]
    else:
        dataset = RealPanels(split)
        metadata = dataset.metadata
    values = []
    started = time.monotonic()
    for batch, data in enumerate(DataLoader(dataset, batch_size=16, num_workers=0)):
        images = data[0] if synthetic else data
        values.append(features(model, images.cuda()).cpu())
        if batch % 40 == 0:
            status('preparing_features', feature_split=split, feature_source='synthetic' if synthetic else 'real',
                   conditions=min((batch + 1) * 16, len(dataset)), total_conditions=len(dataset))
    value = torch.cat(values)
    torch.save(value, path)
    save(metadata_path, metadata)
    save(path.with_suffix('.provenance.json'), {'base_sha256': sha(OUT / 'size20.pt'),
        'synthetic_sha256': sha(ROUT / 'synthetic.json'), 'n': len(metadata),
        'elapsed_seconds': time.monotonic() - started,
        'pipeline': 'original whole-photo fixed9 transforms; same frozen detector boxes/shape scores as baseline20 evaluation' if not synthetic else 'heldout synthetic panel fixed9 transforms'})
    return value.cuda(), metadata


@torch.inference_mode()
def predictions(head, values, metadata):
    probabilities = head(values).softmax(1).cpu()
    result = []
    for r, p in zip(metadata, probabilities):
        score, index = p.max(0)
        raw = CLASSES[int(index)]
        row = dict(r, predicted=raw, score=float(score), probabilities={c: float(x) for c, x in zip(CLASSES, p)})
        if r['source_kind'] == 'real':
            row['consistent'] = raw.isdigit() == (r['shape'] == 'child')
        result.append(row)
    return result


def select_key(real, synthetic, baseline_real):
    preserve = all(r['truth'] == r['predicted'] or b['truth'] != b['predicted'] for r, b in zip(real, baseline_real))
    summary = safe_metrics(synthetic)
    loss = -sum(__import__('math').log(max(1e-12, r['probabilities'][r['truth']])) for r in synthetic) / len(synthetic)
    return (preserve, summary['macro_f1_present'], summary['accuracy'], -loss), summary


def lock_other_rows(head):
    mask = head.weight.new_zeros(20)
    mask[RARE] = 1
    head.weight.register_hook(lambda grad: grad * mask[:, None])
    head.bias.register_hook(lambda grad: grad * mask)


def fit_and_evaluate(epochs=24):
    configure()
    rows = training_rows()
    protocol = read(ROUT / 'protocol.json')
    assert sha(OUT / 'size20.pt') == protocol['base_sha256']
    protocol.update(training_base_count=len(rows), training_counts=dict(Counter(r['raw_size'] for r in rows)),
        every_epoch='each rare real/synthetic base once unmodified + two seeded random photometric views',
        views_per_epoch=len(rows) * 3, epochs_max=epochs, minimum_epochs=8, patience=8,
        optimizer='Adam lr0.0003 weight_decay0; FP32; gradient mask3rows; .1 sum-squared anchor to baseline rare rows',
        selection='preserve every baseline-correct real dev condition, then synthetic dev macroF1, accuracy, CE; test never selects',
        trainable_effective_parameters=3 * 513, source_sha256=sha(__file__), synthetic_sha256=sha(ROUT / 'synthetic.json'))
    save(ROUT / 'protocol.json', protocol)
    status('waiting_gpu', training_pid=os.getpid(), error=None)
    with gpu_lease('size'):
        model = load_model(OUT / 'size20.pt').cuda().eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        head = model.output[1]
        head.weight.requires_grad_(True)
        head.bias.requires_grad_(True)
        lock_other_rows(head)
        initial_weight, initial_bias = head.weight.detach().clone(), head.bias.detach().clone()
        real_values, real_meta = feature_cache(model, 'dev')
        synthetic_values, synthetic_meta = feature_cache(model, 'dev', True)
        baseline_real = predictions(head, real_values, real_meta)
        baseline_synthetic = predictions(head, synthetic_values, synthetic_meta)
        cache_audit = {'raw_disagreements': sum(a['predicted'] != b['predicted'] for a, b in zip(baseline_real, real_meta)),
            'max_score_delta': max(abs(a['score'] - b['score']) for a, b in zip(baseline_real, real_meta)),
            'n_conditions': len(real_meta)}
        save(ROUT / 'dev-feature-cache-verification.json', cache_audit)
        assert cache_audit['raw_disagreements'] == 0
        best, baseline_summary = select_key(baseline_real, baseline_synthetic, baseline_real)
        assert best[0]
        best_epoch, start, stale = 0, 0, 0
        if not (ROUT / 'rare-size20.pt').exists():
            torch.save({k: v.cpu() for k, v in model.state_dict().items()}, ROUT / 'rare-size20.pt')
        save(ROUT / 'dev-baseline.json', {'real': safe_metrics(baseline_real), 'synthetic': baseline_summary, 'selection_key': best})
        optimizer = torch.optim.Adam([head.weight, head.bias], lr=3e-4, weight_decay=0.)
        counts = Counter(r['raw_size'] for r in rows)
        weights = head.weight.new_ones(20)
        for c in TARGETS:
            weights[CLASSES.index(c)] = (len(rows) / (3 * counts[c])) ** .5
        criterion = nn.CrossEntropyLoss(weight=weights)
        resume_path = RWORK / 'resume.pt'
        if resume_path.exists():
            checkpoint = torch.load(resume_path, map_location='cpu', weights_only=False)
            assert checkpoint['synthetic_sha256'] == protocol['synthetic_sha256']
            model.load_state_dict(checkpoint['model'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            start, best_epoch, stale, best = checkpoint['epoch'], checkpoint['best_epoch'], checkpoint['stale'], tuple(checkpoint['best'])
        epoch = start - 1
        for epoch in range(start, epochs):
            started = time.monotonic()
            status('training', epoch=epoch + 1, epochs=epochs, selected_epoch=best_epoch)
            total, seen = 0., 0
            generator = torch.Generator().manual_seed(SEED + epoch)
            data = DataLoader(Panels(rows, epoch, True), batch_size=16, shuffle=True, generator=generator, num_workers=0)
            for images, targets, _, _ in data:
                optimizer.zero_grad(set_to_none=True)
                with torch.no_grad():
                    value = features(model, images.cuda()).clone()
                logits = head(value)
                loss = criterion(logits, targets.cuda())
                loss += .1 * ((head.weight[RARE] - initial_weight[RARE]).square().sum() + (head.bias[RARE] - initial_bias[RARE]).square().sum())
                loss.backward()
                nn.utils.clip_grad_norm_([head.weight, head.bias], 1.)
                optimizer.step()
                assert torch.equal(head.weight[OTHER], initial_weight[OTHER]) and torch.equal(head.bias[OTHER], initial_bias[OTHER])
                total += float(loss.detach()) * len(images)
                seen += len(images)
            real = predictions(head, real_values, real_meta)
            synthetic = predictions(head, synthetic_values, synthetic_meta)
            key, synthetic_summary = select_key(real, synthetic, baseline_real)
            improved = key[0] and key > best
            if improved:
                best, best_epoch, stale = key, epoch + 1, 0
                torch.save({k: v.cpu() for k, v in model.state_dict().items()}, ROUT / 'rare-size20.pt')
                save(ROUT / 'best-dev.json', {'epoch': best_epoch, 'key': best, 'real': safe_metrics(real), 'synthetic': synthetic_summary})
            else:
                stale += 1
            event = {'epoch': epoch + 1, 'views': seen, 'loss': total / seen, 'key': key,
                     'synthetic_dev': synthetic_summary, 'real_dev': safe_metrics(real),
                     'selected_epoch': best_epoch, 'seconds': time.monotonic() - started}
            with (ROUT / 'epochs.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event) + '\n')
            checkpoint = {'model': model.state_dict(), 'optimizer': optimizer.state_dict(), 'epoch': epoch + 1,
                'best': best, 'best_epoch': best_epoch, 'stale': stale, 'synthetic_sha256': protocol['synthetic_sha256']}
            torch.save(checkpoint, RWORK / 'resume.tmp.pt')
            os.replace(RWORK / 'resume.tmp.pt', resume_path)
            status('training', epoch=epoch + 1, seen=seen, selected_epoch=best_epoch,
                   best_dev=best, real_dev_correct=event['real_dev']['correct'], synthetic_dev_correct=synthetic_summary['correct'])
            if epoch + 1 >= 8 and stale >= 8:
                break
        torch.save({k: v.cpu() for k, v in model.state_dict().items()}, ROUT / 'last-trained.pt')
        model.load_state_dict(torch.load(ROUT / 'rare-size20.pt', map_location='cpu', weights_only=True))
        save(ROUT / 'selection.json', {'selected_epoch': best_epoch, 'epochs_completed': epoch + 1,
             'key': best, 'sha256': sha(ROUT / 'rare-size20.pt'), 'base_sha256': protocol['base_sha256'],
             'unchanged_base_selected': best_epoch == 0})
        evaluate_final(model)
        torch.cuda.empty_cache()
    status('gpu_evaluated', selected_epoch=best_epoch)


def evaluate_final(model):
    result = {}
    base = load_model(OUT / 'size20.pt').cuda().eval()
    for source, synthetic in [('real', False), ('synthetic', True)]:
        values, metadata = feature_cache(model, 'test', synthetic)
        before = predictions(base.output[1], values, metadata)
        after = predictions(model.output[1], values, metadata)
        save(ROUT / f'{source}-test-predictions.json', {'before': before, 'after': after})
        result[source] = {}
        for name, rows in [('before', before), ('after', after)]:
            result[source][name] = {}
            for condition, subset in [('original', [r for r in rows if r['kind'] == 'original']), ('nine_conditions', rows)]:
                result[source][name][condition] = safe_metrics(subset)
                if not synthetic:
                    result[source][name][condition]['auto90'] = auto_summary(subset, .9)
                    result[source][name][condition]['common17'] = safe_metrics([r for r in subset if r['truth'] not in TARGETS])
        paired = [dict(code=a['code'], group=a['group'], kind=a['kind'], truth=a['truth'],
             before=a['predicted'], after=b['predicted'], before_score=a['score'], after_score=b['score'],
             before_correct=a['truth'] == a['predicted'], after_correct=b['truth'] == b['predicted']) for a, b in zip(before, after)]
        write_csv(ROUT / f'{source}-photo-comparison.csv', paired)
        result[source]['old_only_correct'] = sum(r['before_correct'] and not r['after_correct'] for r in paired)
        result[source]['new_only_correct'] = sum(r['after_correct'] and not r['before_correct'] for r in paired)
    save(ROUT / 'comparison.json', result)


if __name__ == '__main__':
    try:
        fit_and_evaluate()
    except Exception as error:
        status('error', error=repr(error))
        raise
