"""Safety contracts for rare-only additional learning."""
import json

import numpy as np
import pytest

torch = pytest.importorskip('torch')

from scripts.size_retrain_20261122_rare import contained_ink, distributed  # noqa: E402
from scripts.size_retrain_20261122_rare_fit import OTHER, RARE, lock_other_rows, select_key  # noqa: E402


def test_only_three_rows_can_change_with_adam():
    head = torch.nn.Linear(512, 20)
    original_weight, original_bias = head.weight.detach().clone(), head.bias.detach().clone()
    lock_other_rows(head)
    optimizer = torch.optim.Adam(head.parameters(), lr=.01, weight_decay=0.)
    for _ in range(3):
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(head(torch.randn(6, 512)), torch.tensor([0, 12, 11, 0, 12, 11]))
        loss.backward()
        optimizer.step()
    assert torch.equal(head.weight[OTHER], original_weight[OTHER])
    assert torch.equal(head.bias[OTHER], original_bias[OTHER])
    assert not torch.equal(head.weight[RARE], original_weight[RARE])


def test_frozen_features_can_feed_trainable_head_without_inference_tensor_error():
    from coatdesk_ab.network import PhotoClassifier
    from scripts.size_retrain_20261122_rare_fit import features
    torch.set_num_threads(4)
    model = PhotoClassifier(20).eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    head = model.output[1]
    head.weight.requires_grad_(True)
    head.bias.requires_grad_(True)
    lock_other_rows(head)
    with torch.no_grad():
        value = features(model, torch.randn(2, 3, 64, 64)).clone()
    torch.nn.functional.cross_entropy(head(value), torch.tensor([0, 12])).backward()
    assert head.weight.grad[RARE].abs().sum() > 0
    assert head.weight.grad[OTHER].abs().sum() == 0
    assert all(p.grad is None for p in model.features.parameters())


def test_contours_touching_roi_boundary_are_not_glyphs():
    pytest.importorskip('cv2')
    image = np.full((30, 30, 3), 255, dtype='uint8')
    image[5:25, 5:7] = 0
    image[12:18, 13:17] = 0
    mask = contained_ink(image, (5, 5, 25, 25))
    assert not mask[:, 5:7].any()
    assert mask[13:17, 14:16].all()


def test_distributed_singleton_and_unique_group_selection():
    rows = [dict(code=str(i), group=str(i // 2), capture_bin=i // 3) for i in range(12)]
    assert len(distributed(rows, 1)) == 1
    assert len({r['group'] for r in distributed(rows, 4)}) == 4


def test_real_regression_disqualifies_even_better_synthetic(tmp_path, monkeypatch):
    from scripts import size_retrain_20261122_fit as fit
    (tmp_path / 'size_mapping.json').write_text(json.dumps({'child_correspondence': []}), encoding='utf-8')
    monkeypatch.setattr(fit, 'BASE', tmp_path)
    fit.business.cache_clear()
    old = [{'truth': 'M', 'predicted': 'M'}]
    regressed = [{'truth': 'M', 'predicted': '2XS'}]
    synthetic = [{'code': 'a', 'truth': '2XS', 'predicted': '2XS', 'probabilities': {'2XS': .99}}]
    key, _ = select_key(regressed, synthetic, old)
    assert not key[0]
    key, _ = select_key(old, synthetic, old)
    assert key[0]
    fit.business.cache_clear()
