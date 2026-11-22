"""Authorized isolated category fit. Original photographs and deployed assets are read-only."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import time
import traceback

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.local/category-retrain-20261122'
OUT = ROOT / 'model-results/20261122-category-retrain'
SOURCE = ROOT / 'model-results/20261122-training-candidates'
BASE = ROOT / 'dist-releases/v2-50c3b52756d1/CoatDesk2/_internal/resources/ai/category'
INITIAL = ROOT / '.local/category-first-20261027-01/assets/efficientnet_b0_rwightman-7f5810bc.pth'
CLASSES = ['자켓', '코트', '조끼', '기타']
KINDS = ('original', 'dark', 'bright', 'warm', 'cool', 'contrast', 'shadow_left', 'shadow_right', 'blur')
SEED = 20261122


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def status(stage, **kwargs):
    path = OUT / 'status.json'
    item = read(path) if path.exists() else {}
    item.update(stage=stage, pid=os.getpid(), updated_at_utc=datetime.now(timezone.utc).isoformat(), **kwargs)
    save(path, item)
    with (OUT / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(item, ensure_ascii=False) + '\n')
    print(json.dumps(item, ensure_ascii=True), flush=True)


def check_split(rows):
    for key in ('code', 'group', 'sha256'):
        seen = {}
        for r in rows:
            if r[key] in seen and seen[r[key]] != r['split']:
                raise ValueError(f'Cross-partition {key}: {r[key]}')
            seen[r[key]] = r['split']
    if len({r['code'] for r in rows}) != len(rows):
        raise ValueError('Duplicate code')


def freeze():
    if (WORK / 'frozen.json').exists():
        return read(WORK / 'frozen.json')
    status('freezing', epoch=0, best_dev_macro_f1=None, error=None)
    rows = read(SOURCE / 'category-candidates.json')
    inv = read(SOURCE / 'inventory-annotated.json')
    bycode = {r['code']: r for r in inv}
    check_split(inv)
    check_split(rows)
    if Counter(r['split'] for r in rows) != {'train': 3000, 'dev': 500, 'test': 500}:
        raise ValueError('Unexpected candidate partition')
    links = read(SOURCE / 'possible-repeat-links.json')
    for link in links:
        a, b = bycode[link['a']], bycode[link['b']]
        if a['group'] != b['group'] or a['split'] != b['split']:
            raise ValueError('Conservative repeat link crosses group/split')
    old = read(ROOT / '.local/robust-four-models-20261102/frozen-dataset.json')['records']
    deployed = read(ROOT / 'model-results/20261026-category-label-review/approved-dataset-20261027-01/dataset.json')['records']
    histories = {'previous_20261102': {r['code']: r['split'] for r in old},
                 'deployed_category_dataset': {r['code']: r['dataset_split'] for r in deployed}}
    # Validate known fit groups against new holdouts, including unselected inventory members.
    for name, history in histories.items():
        fitted = {bycode[c]['group'] for c, s in history.items() if c in bycode and s == 'train'}
        leak = [r['code'] for r in rows if r['split'] != 'train' and r['group'] in fitted]
        if leak:
            raise ValueError(f'Previous fit exposure in holdout ({name}): {leak}')
    for r in rows:
        r['historical_exposure'] = {name: hist.get(r['code'], 'not_in_manifest') for name, hist in histories.items()}
        r['selection_status'] = 'frozen_before_training'
    protected_paths = [p for p in BASE.rglob('*') if p.is_file()] + [INITIAL, ROOT / 'coatdesk_v2/category.py', ROOT / 'coatdesk_ab/locator.py', ROOT / 'coatdesk_ab/data.py']
    snapshot = read(SOURCE / 'input-snapshot.json')
    protected = {str(p): sha(p) for p in protected_paths}
    receipt = read(WORK / 'workbook-hash-before.json')
    if receipt['sha256'] != snapshot['book_sha256']:
        raise ValueError('Workbook changed')
    with ThreadPoolExecutor(max_workers=6) as pool:
        hashes = list(pool.map(lambda r: sha(r['source']), rows))
    if any(h != r['sha256'] for h, r in zip(hashes, rows)):
        raise ValueError('Source photo hash mismatch')
    data = {'seed': SEED, 'classes': CLASSES, 'records': rows, 'protected': protected, 'workbook_hash_before': receipt,
            'inputs': {name: sha(SOURCE / name) for name in ('category-candidates.json', 'inventory-annotated.json', 'possible-repeat-links.json', 'final-verification.json')},
            'repeat_audit': {'inventory_rows': len(inv), 'links_verified': len(links), 'cross_split_links': 0,
                             'interpretation': 'conservative split linkage; not verified identical garments'},
            'protocol': {'architecture': 'efficientnet_b0_direct4_softmax', 'input_side': 320,
                         'epochs_max': 16, 'early_stop_patience': 5, 'epochs_min': 8,
                         'selection': 'maximum dev original macro_f1; tie accuracy; test never used',
                         'original_per_epoch': 1, 'random_augmented_per_original_per_epoch': 2,
                         'optimizer': 'AdamW', 'backbone_lr': 0.00015, 'head_lr': 0.001,
                         'weight_decay': 0.0001, 'label_smoothing': 0.02,
                         'class_weights': 'sqrt inverse training frequency normalized mean 1',
                         'threshold_selection': 'dev nine conditions: maximum coverage with zero observed automatic errors, global threshold',
                         'normalization_mean': [.485, .456, .406], 'normalization_std': [.229, .224, .225],
                         'mask': 'deployed WholePanelLocator; full panel plus 8px white at <=1080 width',
                         'mask_failure': 'retained as failure in evaluation; conservative top panel region mask for training only',
                         'geometry': 'whole photo aspect ratio preserved letterbox320; no crops/flips',
                         'test_conditions': list(KINDS), 'retrospective_not_blind': True},
            'exposure_counts': {name: {s: dict(Counter(r['historical_exposure'][name] for r in rows if r['split'] == s)) for s in ('train', 'dev', 'test')} for name in histories}}
    save(WORK / 'frozen.json', data)
    save(OUT / 'frozen-data.json', data)
    shutil.copy2(__file__, WORK / 'training-source-frozen.py')
    status('frozen', frozen_sha256=sha(WORK / 'frozen.json'), original_count=len(rows))
    return data


def changed(photo, kind, strength=1.):
    from scripts.retrain_four_training import change
    return change(photo, kind, strength)


def prepared_image(photo, location, training=False):
    from coatdesk_ab.data import letterbox
    image = photo.copy()
    if location['valid']:
        x1, y1, x2, y2 = map(int, location['box'])
        box = (max(0, x1-8), max(0, y1-8), min(image.width-1, x2+8), min(image.height-1, y2+8))
    elif training:
        box = (int(image.width*.25), 0, int(image.width*.75), int(image.height*.27))
    else:
        box = None
    if box:
        ImageDraw.Draw(image).rectangle(box, fill='white')
    return np.asarray(letterbox(image, 320)[0])


def prepare(data):
    from coatdesk_ab.data import load_photo
    from coatdesk_ab.locator import WholePanelLocator
    (WORK / 'cache').mkdir(parents=True, exist_ok=True)
    locator = WholePanelLocator(BASE)
    rows = data['records']

    def one(r):
        path = WORK / 'cache' / (r['code'] + '.json')
        if path.exists():
            return read(path)
        if sha(r['source']) != r['sha256']:
            raise ValueError('Photo changed: ' + r['code'])
        photo = load_photo(r['source'])
        kinds = KINDS if r['split'] != 'train' else ('original',)
        locations, arrays = {}, []
        for k in kinds:
            p = changed(photo, k)
            loc = locator.locate(p)
            locations[k] = loc
            arrays.append(prepared_image(p, loc, training=r['split'] == 'train'))
        np.save(WORK / 'cache' / (r['code'] + '.npy'), np.stack(arrays), allow_pickle=False)
        item = {'code': r['code'], 'sha256': r['sha256'], 'locations': locations,
                'array_sha256': sha(WORK / 'cache' / (r['code'] + '.npy'))}
        save(path, item)
        return item
    locations = {}
    status('preparing_masks', completed=0, total=len(rows))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, item in enumerate(pool.map(one, rows)):
            locations[item['code']] = item
            if i % 50 == 0:
                status('preparing_masks', completed=i+1, total=len(rows))
    save(WORK / 'mask-locations.json', locations)
    save(OUT / 'preprocessing-audit.json', {'records': len(locations), 'conditions': sum(len(x['locations']) for x in locations.values()),
                                          'invalid_locations': {s: sum(not v['valid'] for r in rows if r['split'] == s for v in locations[r['code']]['locations'].values()) for s in ('train', 'dev', 'test')},
                                          'failures': [{'code': c, 'kind': k} for c, x in locations.items() for k, v in x['locations'].items() if not v['valid']]})
    status('prepared', completed=len(rows))


def configure():
    import torch
    torch.set_num_threads(4)
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def build(initial=False, baseline=False):
    import torch
    from torchvision.models import efficientnet_b0
    model = efficientnet_b0(weights=None)
    if initial:
        model.load_state_dict(torch.load(INITIAL, map_location='cpu', weights_only=True))
    model.classifier[1] = torch.nn.Linear(model.classifier[1].in_features, 4)
    if baseline:
        model.load_state_dict(torch.load(BASE / 'model.pt', map_location='cpu', weights_only=True)['state_dict'])
    return model


class Photos:
    def __init__(self, rows, epoch=0, training=False, kinds=('original',)):
        self.rows, self.epoch, self.training, self.kinds = rows, epoch, training, kinds
        self.count = 3 if training else len(kinds)

    def __len__(self):
        return len(self.rows) * self.count

    def __getitem__(self, index):
        import torch
        i, k = divmod(index, self.count)
        r = self.rows[i]
        arr = np.load(WORK / 'cache' / (r['code'] + '.npy'), mmap_mode='r')
        if self.training:
            image = Image.fromarray(arr[0])
            if k:
                rng = random.Random(SEED + self.epoch*1000003 + int(r['code'])*13+k)
                image = changed(image, rng.choice(KINDS[1:]), rng.uniform(.5, 1.5))
            value = np.asarray(image).copy()
        else:
            value = np.array(arr[KINDS.index(self.kinds[k])], copy=True)
        x = torch.from_numpy(value.transpose(2, 0, 1)).float()/255
        x = (x-torch.tensor([.485, .456, .406])[:, None, None])/torch.tensor([.229, .224, .225])[:, None, None]
        return x, CLASSES.index(r['category']), i, k


def loader(rows, epoch=0, training=False, kinds=('original',)):
    import torch
    return torch.utils.data.DataLoader(Photos(rows, epoch, training, kinds), batch_size=32, shuffle=training,
                                      generator=torch.Generator().manual_seed(SEED+epoch), num_workers=4,
                                      pin_memory=True, persistent_workers=False)


def metrics(predictions):
    matrix = [[0]*4 for _ in CLASSES]
    failures = 0
    for p in predictions:
        if p['predicted'] in CLASSES:
            matrix[CLASSES.index(p['truth'])][CLASSES.index(p['predicted'])] += 1
        else:
            failures += 1
    per = {}
    for i, c in enumerate(CLASSES):
        support = sum(p['truth'] == c for p in predictions)
        n_pred = sum(row[i] for row in matrix)
        tp = matrix[i][i]
        precision, recall = tp/max(n_pred, 1), tp/max(support, 1)
        per[c] = {'support': support, 'precision': precision, 'recall': recall,
                  'f1': 2*precision*recall/max(precision+recall, 1e-12)}
    return {'count': len(predictions), 'correct': sum(matrix[i][i] for i in range(4)),
            'accuracy': sum(matrix[i][i] for i in range(4))/max(len(predictions), 1),
            'macro_f1': sum(p['f1'] for p in per.values())/4, 'per_class': per,
            'confusion_rows_truth_columns_prediction': matrix, 'failures': failures,
            'other_missed': sum(p['truth'] == '기타' and p['predicted'] != '기타' for p in predictions),
            'outerwear_to_other': sum(p['truth'] != '기타' and p['predicted'] == '기타' for p in predictions)}


def infer(model, rows, kinds=('original',), baseline=False, device='cuda'):
    import torch
    from coatdesk_v2.category import decode
    locations = read(WORK / 'mask-locations.json')
    model.eval()
    result = []
    start = time.perf_counter()
    with torch.inference_mode():
        for x, y, ids, ks in loader(rows, kinds=kinds):
            logits = model(x.to(device)).float()
            for n, (i, k) in enumerate(zip(ids.tolist(), ks.tolist())):
                r, kind = rows[i], kinds[k]
                valid = locations[r['code']]['locations'][kind]['valid']
                if baseline:
                    probs = torch.cat((logits[n, :2].softmax(0), logits[n, 2:].softmax(0))).tolist()
                    d = decode(probs)
                    predicted, score, review = d['raw_category'], d['score'], d['needs_review']
                else:
                    probs = logits[n].softmax(0).tolist()
                    predicted, score, review = CLASSES[int(np.argmax(probs))], max(probs), False
                result.append({'code': r['code'], 'group': r['group'], 'split': r['split'], 'kind': kind,
                               'age': r['age'], 'truth': r['category'], 'predicted': predicted if valid else 'FAILED_MASK',
                               'score': score if valid else 0., 'probabilities': probs, 'valid': valid,
                               'baseline_review': review or not valid})
    if device == 'cuda':
        torch.cuda.synchronize()
    return result, {'seconds': time.perf_counter()-start, 'conditions': len(result), 'includes': 'cached320 I/O, normalization and network; excludes full-photo locator'}


def checkpoint_save(path, payload):
    import torch
    tmp = path.with_suffix('.tmp')
    torch.save(payload, tmp)
    os.replace(tmp, path)


def train(data):
    import torch
    from scripts.retraining_20261122_gpu import gpu_lease
    if (WORK / 'training-complete.json').exists():
        return
    configure()
    train_rows = [r for r in data['records'] if r['split'] == 'train']
    dev = [r for r in data['records'] if r['split'] == 'dev']
    status('waiting_gpu_training', training_pid=os.getpid())
    with gpu_lease('category'):
        model = build(initial=True).cuda()
        backbone = [p for n, p in model.named_parameters() if not n.startswith('classifier.')]
        optimizer = torch.optim.AdamW([{'params': backbone, 'lr': .00015}, {'params': model.classifier.parameters(), 'lr': .001}], weight_decay=.0001)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=16, eta_min=1e-6)
        scaler = torch.amp.GradScaler('cuda')
        count = Counter(r['category'] for r in train_rows)
        weights = torch.tensor([np.sqrt(len(train_rows)/count[c]) for c in CLASSES], device='cuda', dtype=torch.float32)
        weights /= weights.mean()
        criterion = torch.nn.CrossEntropyLoss(weight=weights, label_smoothing=.02)
        history, start_epoch, best, stale = [], 0, (-1., -1.), 0
        last = WORK / 'last.pt'
        if last.exists():
            state = torch.load(last, map_location='cpu', weights_only=False)
            if state['frozen_sha256'] != sha(WORK / 'frozen.json'):
                raise ValueError('Resume dataset mismatch')
            model.load_state_dict(state['state_dict'])
            optimizer.load_state_dict(state['optimizer'])
            scheduler.load_state_dict(state['scheduler'])
            scaler.load_state_dict(state['scaler'])
            history, start_epoch, best, stale = state['history'], state['epoch'], tuple(state['best']), state['stale']
            torch.set_rng_state(state['rng_cpu'])
            torch.cuda.set_rng_state_all(state['rng_cuda'])
        status('training', training_pid=os.getpid(), epoch=start_epoch, best_dev_macro_f1=best[0])
        for epoch in range(start_epoch, 16):
            if epoch >= 8 and stale >= 5:
                break
            model.train()
            total_loss, n = 0., 0
            begin = time.perf_counter()
            for step, (x, y, _, _) in enumerate(loader(train_rows, epoch, True)):
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast('cuda', dtype=torch.float16):
                    loss = criterion(model(x.cuda(non_blocking=True)), y.cuda(non_blocking=True))
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                total_loss += loss.item()*len(y)
                n += len(y)
                if step % 75 == 0:
                    status('training', epoch=epoch+1, step=step, epoch_observations=n, best_dev_macro_f1=best[0])
            scheduler.step()
            predictions, timing = infer(model, dev)
            m = metrics(predictions)
            score = (m['macro_f1'], m['accuracy'])
            entry = {'epoch': epoch+1, 'loss': total_loss/n, 'original_observations': len(train_rows),
                     'augmented_observations': n-len(train_rows), 'dev': m, 'seconds': time.perf_counter()-begin}
            history.append(entry)
            improved = score > best
            best, stale = (score, 0) if improved else (best, stale+1)
            payload = {'state_dict': {k: v.detach().cpu() for k, v in model.state_dict().items()}, 'epoch': epoch+1, 'best': best, 'stale': stale,
                       'history': history, 'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
                       'scaler': scaler.state_dict(), 'rng_cpu': torch.get_rng_state(), 'rng_cuda': torch.cuda.get_rng_state_all(),
                       'architecture': 'efficientnet_b0_direct4_softmax', 'classes': CLASSES, 'input_side': 320,
                       'frozen_sha256': sha(WORK / 'frozen.json')}
            checkpoint_save(last, payload)
            if improved:
                checkpoint_save(WORK / 'best.pt', {k: payload[k] for k in ('state_dict', 'epoch', 'architecture', 'classes', 'input_side', 'frozen_sha256')})
                save(WORK / 'best-dev-original.json', predictions)
            save(OUT / 'training-history.json', history)
            status('training', epoch=epoch+1, dev_macro_f1=m['macro_f1'], best_dev_macro_f1=best[0], stale_epochs=stale,
                   originals_per_epoch=3000, augmentations_per_epoch=6000)
        save(WORK / 'training-complete.json', {'epochs': len(history), 'best': best, 'best_sha256': sha(WORK / 'best.pt')})
    status('trained', epoch=len(history), best_dev_macro_f1=best[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'train', 'all', 'evaluate'])
    args = parser.parse_args()
    try:
        data = freeze()
        if args.action in ('prepare', 'all'):
            prepare(data)
        if args.action in ('train', 'all'):
            train(data)
        if args.action in ('evaluate', 'all'):
            from scripts.category_retrain_20261122_evaluation import evaluate_all
            evaluate_all(data)
    except BaseException:
        status('error', error=traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
