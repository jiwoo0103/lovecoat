"""Matched real-parent/synthetic panel resampling sensitivity; CPU only."""
from PIL import Image
import torch

from scripts.size_retrain_20261122 import CLASSES, configure, read, save, sha, tensor
from scripts.size_retrain_20261122_rare_fit import load_model
from scripts.size_retrain_20261122_validate_more import VOUT


@torch.inference_mode()
def run():
    configure()
    models = read(VOUT / 'protocol.json')['models']
    parents = {r['code']: r for r in read(VOUT / 'parents.json')}
    records = []
    for which, checkpoint in models.items():
        assert sha(checkpoint['path']) == checkpoint['sha256']
        model = load_model(checkpoint['path'])
        for row in read(VOUT / 'new-synthetic.json'):
            batch, metadata = [], []
            parent = parents[row['parent_code']]
            for kind, path in [('real_parent_control', parent['input']), ('new_synthetic', row['panel'])]:
                image = Image.open(path).convert('RGB')
                for roundtrip in (False, True):
                    value = image.resize(tuple(row['generation_dimensions']), Image.Resampling.LANCZOS).resize((512, 512), Image.Resampling.LANCZOS) if roundtrip else image
                    batch.append(tensor(value))
                    metadata.append(dict(model=which, code=row['code'], parent_code=row['parent_code'],
                        kind=kind, roundtrip=roundtrip,
                        truth=row['truth'] if kind == 'new_synthetic' else parent['source_text'],
                        parent_truth_is_visual_cue_only=True))
            probabilities = model(torch.stack(batch)).softmax(1)
            for item, p in zip(metadata, probabilities):
                score, index = p.max(0)
                records.append(dict(item, predicted=CLASSES[int(index)], score=float(score)))
    switches = sum(records[i]['predicted'] != records[i + 1]['predicted'] for i in range(0, len(records), 2))
    save(VOUT / 'resampling-diagnostic.json', {'records': records, 'pairs': len(records) // 2,
        'prediction_switches': switches, 'models': models,
        'scope': '20 matched real parents and20 new synthetic panels; two20class models; original vs generated-resolution roundtrip',
        'limitation': 'Tests resampling sensitivity only; does not rule out all synthetic font cues or establish real adult2XS accuracy.'})
    print({'pairs': len(records) // 2, 'prediction_switches': switches}, flush=True)


if __name__ == '__main__':
    run()
