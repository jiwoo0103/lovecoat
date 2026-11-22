"""Checks that evaluation preprocessing preserves pixels and missing truth."""
import numpy as np
from PIL import Image

from coatdesk_ab.data import letterbox
from scripts.size_retrain_20261122_validate_more import restore_fullframe
from scripts.size_retrain_20261122_validate_more_report import cohort_metrics, paired_rows


def test_inverse_letterbox_transfers_only_selected_pixels():
    rng = np.random.default_rng(4)
    photo = Image.fromarray(rng.integers(0, 256, (300, 200, 3), dtype='uint8'))
    location = {'crop_box': [21, 31, 162, 143]}
    panel, _ = letterbox(photo.crop(location['crop_box']))
    changed = np.asarray(panel).copy()
    changed[220:260, 210:280] = [12, 30, 91]
    mask = np.zeros((512, 512), dtype='uint8')
    mask[220:260, 210:280] = 255
    restored, full_mask = restore_fullframe(photo, Image.fromarray(changed), Image.fromarray(mask), location)
    a, b, m = np.asarray(photo), np.asarray(restored), np.asarray(full_mask)
    assert np.array_equal(a[m == 0], b[m == 0])
    assert np.any(a[m != 0] != b[m != 0])
    assert restored.size == photo.size
    assert np.count_nonzero(m[:31]) == 0


def test_zero_mask_roundtrip_is_exact_even_for_non_square_crop():
    photo = Image.new('RGB', (211, 400), (84, 137, 201))
    panel, _ = letterbox(photo.crop((23, 38, 198, 135)))
    restored, mask = restore_fullframe(photo, panel, Image.new('L', (512, 512)), {'crop_box': [23, 38, 198, 135]})
    assert restored.tobytes() == photo.tobytes()
    assert not np.asarray(mask).any()


def sample(**values):
    row = dict(code='missing', group='g1', kind='original', age='유아동', source_kind='real',
        cohort='real', main_split='outside2600', fitted_group=False, truth=None, truth_business='XS',
        predicted='90', score=.99, panel_score=.99, shape_score=.99, shape='child', consistent=True, failure=None)
    row.update(values)
    return row


def test_missing_raw_truth_is_not_counted_as_an_error_or_imputed():
    rows = [sample(), sample(code='known', truth='60', truth_business='2XS', predicted='60')]
    result = cohort_metrics(rows)
    assert result['conditions'] == 2
    assert result['raw_unknown_conditions'] == 1
    assert result['raw']['n_conditions'] == 1
    assert result['raw']['correct'] == 1
    assert result['workbook_business']['correct'] == 2
    assert result['automatic']['0.85']['auto_raw_known'] == 1
    assert rows[0]['truth'] is None


def test_unknown_raw_still_counts_business_error_and_failed_detection():
    result = cohort_metrics([sample(predicted='100'), sample(code='failed', predicted='<failure>',
        failure='no_panel', panel_score=0., shape_score=0., score=0., consistent=False)])
    assert result['raw']['n_conditions'] == 0
    assert result['workbook_business']['n_conditions'] == 2
    assert result['workbook_business']['correct'] == 0
    assert result['automatic']['0.85']['auto_business_errors'] == 1
    assert result['automatic']['0.85']['failures'] == 1
    assert result['automatic']['0.85']['review'] == 1


def test_paired_unknown_raw_truth_stays_unknown():
    pair = paired_rows([sample()], [sample(predicted='80')])[0]
    assert pair['before_raw_correct'] is None
    assert pair['after_raw_correct'] is None
    assert pair['before_business_correct'] and pair['after_business_correct']
