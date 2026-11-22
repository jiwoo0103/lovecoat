"""Training/evaluation for the isolated, frozen 2026-11-22 size experiment."""
from __future__ import annotations

from collections import Counter
import csv
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import time

from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from coatdesk_ab.network import PhotoClassifier, freeze_batch_norm
from scripts.retrain_four_training import change
from scripts.retraining_20261122_gpu import gpu_lease
from scripts.size_retrain_20261122 import (BASE, CLASSES, KINDS, OUT, SEED, WORK,
                                         configure, read, save, sha, status, tensor)


def mapped_initialization(state, old_classes):
    model = PhotoClassifier(len(CLASSES))
    target = model.state_dict()
    for key, value in state.items():
        if key not in ('output.1.weight', 'output.1.bias'):
            target[key] = value.clone()
    for name in old_classes:
        new, old = CLASSES.index(name), old_classes.index(name)
        target['output.1.weight'][new].copy_(state['output.1.weight'][old])
        target['output.1.bias'][new].copy_(state['output.1.bias'][old])
    model.load_state_dict(target, strict=True)
    return model


def build(baseline=False):
    old_classes = read(BASE / 'manifest.json')['size_classes']
    state = torch.load(BASE / 'panel-size.pt', map_location='cpu', weights_only=True)
    if baseline:
        model = PhotoClassifier(len(old_classes))
        model.load_state_dict(state, strict=True)
    else:
        model = mapped_initialization(state, old_classes)
    return model


@lru_cache(maxsize=20)
def business(raw):
    mapping = {n: r['size_label'] for r in read(BASE / 'size_mapping.json')['child_correspondence'] for n in r['visible_numbers']}
    return mapping.get(raw, raw)


def metrics(rows, classes=CLASSES):
    table = [[0] * len(classes) for _ in classes]
    index = {c: i for i, c in enumerate(classes)}
    for r in rows:
        table[index[r['truth']]][index[r['predicted']]] += 1
    by_class = {}
    for i, c in enumerate(classes):
        tp, support, predicted = table[i][i], sum(table[i]), sum(row[i] for row in table)
        precision, recall = tp / max(1, predicted), tp / max(1, support)
        by_class[c] = {'support': support, 'precision': precision, 'recall': recall,
                       'f1': 2 * precision * recall / max(1e-12, precision + recall)}
    present = [m['f1'] for m in by_class.values() if m['support']]
    n = len(rows)
    return {'n_conditions': n, 'n_photos': len({r['code'] for r in rows}),
            'correct': sum(r['truth'] == r['predicted'] for r in rows),
            'accuracy': sum(r['truth'] == r['predicted'] for r in rows) / max(1, n),
            'business_accuracy': sum(business(r['truth']) == business(r['predicted']) for r in rows) / max(1, n),
            'macro_f1_present': sum(present) / max(1, len(present)),
            'macro_f1_all20_zero_for_absent': sum(m['f1'] for m in by_class.values()) / len(classes),
            'per_class': by_class, 'classes': classes, 'confusion': table}


class Panels(Dataset):
    def __init__(self, rows, epoch=0, train=False, kinds=('original',)):
        self.rows, self.epoch, self.train, self.kinds = rows, epoch, train, kinds

    def __len__(self):
        return len(self.rows) * (3 if self.train else len(self.kinds))

    def __getitem__(self, index):
        i, k = divmod(index, 3 if self.train else len(self.kinds))
        row = self.rows[i]
        image = Image.open(row['panel']).convert('RGB')
        kind, strength = ('original', 1.) if self.train else (self.kinds[k], 1.)
        if self.train and k:
            rng = random.Random(SEED + self.epoch * 1000003 + i * 31 + k)
            kind, strength = rng.choice(KINDS[1:]), rng.uniform(.25, 1.)
        # Full-panel photometric transforms only, never mirrored/cropped letters.
        image = change(image, kind, strength)
        return tensor(image), CLASSES.index(row['raw_size']), i, k


def loader(rows, epoch=0, train=False, kinds=('original',)):
    generator = torch.Generator().manual_seed(SEED + epoch)
    return DataLoader(Panels(rows, epoch, train, kinds), batch_size=16, shuffle=train,
                      num_workers=0, pin_memory=True, generator=generator)


@torch.inference_mode()
def evaluate(model, rows, kinds=('original',), baseline=False, device='cuda'):
    model.to(device).eval()
    classes = read(BASE / 'manifest.json')['size_classes'] if baseline else CLASSES
    results = []
    started = time.perf_counter()
    for images, targets, indices, variants in loader(rows, kinds=kinds):
        probabilities = model(images.to(device)).softmax(1).cpu()
        for p, target, index, variant in zip(probabilities, targets, indices, variants):
            r = rows[int(index)]
            score, predicted = p.max(0)
            results.append({'code': r['code'], 'group': r['group'], 'kind': kinds[int(variant)],
                            'truth': CLASSES[int(target)], 'predicted': classes[int(predicted)],
                            'score': float(score), 'age': r['age'], 'source_kind': r['source_kind'],
                            'panel_score': r.get('location', {}).get('score'),
                            'probabilities': {c: float(x) for c, x in zip(classes, p)}})
    elapsed = time.perf_counter() - started
    return results, dict(metrics(results), elapsed_seconds=elapsed, seconds_per_condition=elapsed / max(1, len(results)))


def checked_data():
    rows = read(WORK / 'frozen.json')
    for r in rows:
        assert sha(r['panel']) == r['panel_sha256'], r['code']
    synthetic = read(OUT / 'synthetic/manifest.json')['records']
    selected = []
    by_code = {r['code']: r for r in rows}
    for r in synthetic:
        assert r['review']['accepted'] and r['parent_split'] == 'train'
        parent = by_code[r['parent_code']]
        assert parent['split'] == 'train' and parent['group'] == r['parent_group']
        assert sha(r['panel']) == r['sha256']
        selected.append(dict(parent, code=r['synthetic_id'], source_kind='synthetic',
                             raw_size=r['target'], panel=r['panel'], panel_sha256=r['sha256']))
    assert Counter(r['raw_size'] for r in selected) == {'60': 6, '70': 6, '2XS': 6}
    return rows, selected


def fit(epochs=16):
    configure()
    rows, synthetic = checked_data()
    training = [r for r in rows if r['split'] == 'train'] + synthetic
    dev = [r for r in rows if r['split'] == 'dev']
    counts = Counter(r['raw_size'] for r in training)
    weights = torch.tensor([min(8., (len(training) / (20 * max(1, counts[c]))) ** .5) for c in CLASSES])
    save(OUT / 'training-protocol.json', {'seed': SEED, 'max_epochs': epochs, 'early_stopping_patience': 4,
         'minimum_epochs': 6, 'input_side': 512, 'classes': CLASSES, 'old_classes': read(BASE / 'manifest.json')['size_classes'],
         'initialization': '2.6.3 18-class weights mapped by class name; only 60 and 2XS initialized',
         'model': 'PhotoClassifier ResNet18 stride16 learned attention',
         'optimizer': 'AdamW lr=0.00005 weight_decay=0.0001; cosine decay to 0.1',
         'loss': 'sqrt-inverse-frequency capped8 weighted cross entropy + 0.2*T2 KL old18 teacher on real common classes',
         'every_epoch': 'each real original exactly1 + two independent seeded random photometric views; same for each synthetic base',
         'training_counts': dict(counts), 'views_per_epoch': len(training) * 3, 'no_geometric_augmentation': True,
         'transforms': 'existing change(): brightness +-20%, contrast -15%, warm/cool, left/right shadow <=25%, mild blur; strength .25..1',
         'dev_conditions': ['original', 'dark', 'shadow_left'],
         'selection': 'highest mean(accuracy, macroF1 supported dev classes); ties business accuracy; final test unused',
         'threshold_selection': 'highest dev coverage with zero observed errors, size-only including age consistency and panel>=0.9; not a population guarantee',
         'synthetic_manifest_sha256': sha(OUT / 'synthetic/manifest.json'),
         'frozen_sha256': sha(WORK / 'frozen.json'), 'source_sha256': sha(__file__)})
    status('waiting_gpu', training_pid=os.getpid(), error=None)
    with gpu_lease('size'):
        model, teacher = build().cuda(), build(True).cuda().eval()
        optimizer = torch.optim.AdamW(model.parameters(), lr=5e-5, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, epochs, eta_min=5e-6)
        scaler = torch.amp.GradScaler('cuda')
        best, start, stale = -1., 0, 0
        checkpoint = WORK / 'resume.pt'
        if checkpoint.exists():
            ckpt = torch.load(checkpoint, map_location='cpu', weights_only=False)
            assert ckpt['frozen_sha256'] == sha(WORK / 'frozen.json')
            assert ckpt['synthetic_sha256'] == sha(OUT / 'synthetic/manifest.json')
            model.load_state_dict(ckpt['model'])
            optimizer.load_state_dict(ckpt['optimizer'])
            scheduler.load_state_dict(ckpt['scheduler'])
            scaler.load_state_dict(ckpt['scaler'])
            best, start, stale = ckpt['best'], ckpt['epoch'], ckpt['stale']
            torch.set_rng_state(ckpt['torch_rng'])
            torch.cuda.set_rng_state_all(ckpt['cuda_rng'])
        old_classes = read(BASE / 'manifest.json')['size_classes']
        common = [CLASSES.index(c) for c in old_classes]
        criterion = nn.CrossEntropyLoss(weight=weights.cuda())
        for epoch in range(start, epochs):
            status('training', epoch=epoch + 1, epochs=epochs, best_dev=best)
            started = time.monotonic()
            model.train()
            freeze_batch_norm(model)
            total, seen = 0., 0
            for batch, (images, targets, indices, variants) in enumerate(loader(training, epoch, True)):
                images, targets = images.cuda(non_blocking=True), targets.cuda(non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast('cuda', dtype=torch.float16):
                    logits = model(images)
                    loss = criterion(logits, targets)
                    mask = torch.tensor([training[int(i)]['source_kind'] == 'real' and training[int(i)]['raw_size'] in old_classes for i in indices], device='cuda')
                    if mask.any():
                        with torch.no_grad():
                            old = teacher(images[mask])
                        loss += .2 * 4 * nn.functional.kl_div(nn.functional.log_softmax(logits[mask][:, common].float() / 2, 1),
                                      nn.functional.softmax(old.float() / 2, 1), reduction='batchmean')
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), 5.)
                scaler.step(optimizer)
                scaler.update()
                total += float(loss.detach()) * len(images)
                seen += len(images)
                if batch % 50 == 0:
                    status('training', epoch=epoch + 1, batch=batch, seen=seen, views_total=len(training) * 3,
                           best_dev=best, training_loss=total / seen)
            predictions, summary = evaluate(model, dev, kinds=('original', 'dark', 'shadow_left'))
            score = (summary['accuracy'] + summary['macro_f1_present']) / 2
            improved = score > best + 1e-9
            if improved:
                best, stale = score, 0
                torch.save({k: v.cpu() for k, v in model.state_dict().items()}, OUT / 'size20.pt')
                save(OUT / 'best-dev.json', {'epoch': epoch + 1, 'score': score, 'summary': summary, 'predictions': predictions})
            else:
                stale += 1
            scheduler.step()
            event = {'epoch': epoch + 1, 'loss': total / seen, 'dev': summary, 'selection_score': score,
                     'best': best, 'improved': improved, 'seconds': time.monotonic() - started, 'views': seen}
            with (OUT / 'epochs.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event) + '\n')
            temporary = WORK / 'resume.tmp.pt'
            torch.save({'model': model.state_dict(), 'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
                        'scaler': scaler.state_dict(), 'epoch': epoch + 1, 'best': best, 'stale': stale,
                        'frozen_sha256': sha(WORK / 'frozen.json'), 'synthetic_sha256': sha(OUT / 'synthetic/manifest.json'),
                        'torch_rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all()}, temporary)
            os.replace(temporary, checkpoint)
            status('training', epoch=epoch + 1, epoch_complete=True, best_dev=best, dev_accuracy=summary['accuracy'],
                   dev_macro_f1=summary['macro_f1_present'], epoch_seconds=event['seconds'])
            if epoch + 1 >= 6 and stale >= 4:
                break
        status('trained', epoch=epoch + 1, best_dev=best, checkpoint=str(OUT / 'size20.pt'), checkpoint_sha256=sha(OUT / 'size20.pt'))


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['train'])
    parser.add_argument('--epochs', type=int, default=16)
    args = parser.parse_args()
    try:
        fit(args.epochs)
    except Exception as error:
        status('error', error=repr(error))
        raise


if __name__ == '__main__':
    main()
