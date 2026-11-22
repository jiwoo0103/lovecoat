"""Contract tests for class expansion, frozen splits and original exposure."""
from copy import deepcopy
import json

import pytest
from PIL import Image

torch = pytest.importorskip('torch')

from coatdesk_ab.network import PhotoClassifier  # noqa: E402 - skip optional torch before loading training modules
from scripts.size_retrain_20261122 import CLASSES, validate_split  # noqa: E402
from scripts.size_retrain_20261122_fit import Panels, mapped_initialization, metrics  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_mapping(tmp_path, monkeypatch):
    from scripts import size_retrain_20261122_fit as fit
    (tmp_path / 'size_mapping.json').write_text(json.dumps({'child_correspondence': [
        {'size_label': '2XS', 'visible_numbers': ['60', '70']},
        {'size_label': 'XS', 'visible_numbers': ['80', '90']}]}), encoding='utf-8')
    monkeypatch.setattr(fit, 'BASE', tmp_path)
    fit.business.cache_clear()
    yield
    fit.business.cache_clear()


def test_old18_rows_preserved_by_name_not_position():
    old = [str(n) for n in range(70, 171, 10)] + ['XS', 'S', 'M', 'L', 'XL', '2XL', '3XL']
    model = PhotoClassifier(18)
    state = model.state_dict()
    for i in range(18):
        state['output.1.weight'][i].fill_(i + 1)
        state['output.1.bias'][i] = i + 101
    expanded = mapped_initialization(state, old).state_dict()
    for i, name in enumerate(old):
        assert torch.equal(expanded['output.1.weight'][CLASSES.index(name)], state['output.1.weight'][i])
        assert expanded['output.1.bias'][CLASSES.index(name)] == i + 101
    assert torch.equal(expanded['features.0.weight'], state['features.0.weight'])
    assert not torch.equal(expanded['output.1.weight'][0], expanded['output.1.weight'][1])


def test_each_real_original_and_two_distinct_views(tmp_path):
    image = Image.new('RGB', (512, 512), (110, 140, 180))
    path = tmp_path / 'panel.png'
    image.save(path)
    rows = [{'panel': str(path), 'raw_size': '60'}, {'panel': str(path), 'raw_size': '2XS'}]
    dataset = Panels(rows, epoch=3, train=True)
    assert len(dataset) == 6
    assert [(int(dataset[i][2]), int(dataset[i][3])) for i in range(6)] == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
    from scripts.size_retrain_20261122 import tensor
    assert torch.equal(dataset[0][0], tensor(image))
    assert not torch.equal(dataset[1][0], dataset[0][0])
    assert torch.equal(dataset[1][0], dataset[1][0])


def test_no_cross_split_parent_or_hash():
    rows = [{'raw_size': 'S', 'source_kind': 'real', 'split': 'train' if i < 2000 else 'dev' if i < 2300 else 'test',
             'group': str(i), 'sha256': str(i)} for i in range(2600)]
    validate_split(rows)
    bad = deepcopy(rows)
    bad[-1]['group'] = bad[0]['group']
    with pytest.raises(ValueError, match='crosses splits'):
        validate_split(bad)
    bad = deepcopy(rows)
    bad[-1]['sha256'] = bad[0]['sha256']
    with pytest.raises(ValueError, match='crosses splits'):
        validate_split(bad)


def test_raw_and_business_metrics_are_separate():
    result = metrics([{'code': 'a', 'truth': '60', 'predicted': '70'},
                      {'code': 'b', 'truth': '2XS', 'predicted': '2XS'}])
    assert result['accuracy'] == .5
    assert result['business_accuracy'] == 1.
    assert result['per_class']['60']['recall'] == 0.


def test_dev_threshold_holds_high_confidence_error():
    from scripts.size_retrain_20261122_evaluate import auto_summary, choose_threshold
    base = {'failure': None, 'panel_score': .99, 'shape_score': .99, 'consistent': True}
    rows = [dict(base, truth='XS', predicted='XS', score=.999),
            dict(base, truth='60', predicted='70', score=.96)]
    threshold = choose_threshold(rows)['threshold_size']
    summary = auto_summary(rows, threshold)
    assert threshold > .96
    assert summary['auto'] == 1
    assert summary['auto_raw_errors'] == 0
    assert auto_summary(rows, .9)['auto_business_errors'] == 0
    assert auto_summary(rows, .9)['auto_raw_errors'] == 1


def test_age_inconsistent_and_failed_are_reviewed():
    from scripts.size_retrain_20261122_evaluate import automatic
    good = {'failure': None, 'panel_score': .99, 'shape_score': .99, 'consistent': True, 'score': .999}
    assert automatic(good, .9)
    assert not automatic(dict(good, consistent=False), .9)
    assert not automatic(dict(good, failure='no_panel'), .9)
    assert not automatic(dict(good, panel_score=.85), .9)


def test_business_confusion_and_failure_are_not_dropped():
    from scripts.size_retrain_20261122_evaluate import safe_metrics
    rows = [{'code': 'a', 'truth': '60', 'predicted': '70'},
            {'code': 'b', 'truth': '2XS', 'predicted': '<failure>'}]
    result = safe_metrics(rows)
    assert result['n_conditions'] == 2
    assert result['accuracy'] == 0.
    assert result['business_metrics']['accuracy'] == .5
    assert result['business_metrics']['per_class']['2XS']['support'] == 2
