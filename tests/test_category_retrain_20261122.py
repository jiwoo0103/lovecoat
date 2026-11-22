"""Leakage, full frame, metrics and observation invariants for the isolated run."""
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from scripts.category_retrain_20261122 import CLASSES, Photos, check_split, metrics, prepared_image
from scripts.category_retrain_20261122_evaluation import automatic, select_threshold


class CategoryRetrainTests(unittest.TestCase):
    def test_group_and_photo_leakage_rejected(self):
        for key in ('group', 'sha256'):
            rows = [dict(code='1', group='a', sha256='x', split='train'),
                    dict(code='2', group='b', sha256='y', split='test')]
            rows[1][key] = rows[0][key]
            with self.assertRaises(ValueError):
                check_split(rows)

    def test_whole_frame_and_panel_mask(self):
        photo = Image.new('RGB', (100, 200), 'red')
        photo.paste('blue', (0, 190, 100, 200))
        image = prepared_image(photo, {'valid': True, 'box': [40, 20, 60, 40]})
        self.assertEqual(image.shape, (320, 320, 3))
        self.assertTrue(np.all(image[315, 100] == [0, 0, 255]))
        self.assertTrue(np.all(image[45, 160] == 255))
        self.assertTrue(np.all(image[100, 0] == 255))

    def test_failure_counts_as_miss_and_other_is_class(self):
        rows = [{'truth': c, 'predicted': c} for c in CLASSES]
        rows.append({'truth': '기타', 'predicted': 'FAILED_MASK'})
        m = metrics(rows)
        self.assertEqual(m['count'], 5)
        self.assertEqual(m['correct'], 4)
        self.assertEqual(m['failures'], 1)
        self.assertEqual(m['other_missed'], 1)
        self.assertAlmostEqual(m['per_class']['기타']['recall'], .5)

    def test_exactly_original_plus_two_augments(self):
        row = {'code': '0012', 'category': '기타'}
        data = Photos([row], epoch=1, training=True)
        self.assertEqual(len(data), 3)
        with patch('scripts.category_retrain_20261122.np.load', return_value=np.zeros((1, 320, 320, 3), dtype=np.uint8)), patch('scripts.category_retrain_20261122.changed', side_effect=lambda image, *args: image) as change:
            for i in range(3):
                data[i]
            self.assertEqual(change.call_count, 2)

    def test_development_threshold_rejects_high_confidence_error(self):
        rows = [{'truth': '자켓', 'predicted': '자켓', 'valid': True, 'score': .99},
                {'truth': '자켓', 'predicted': '코트', 'valid': True, 'score': .97},
                {'truth': '기타', 'predicted': '기타', 'valid': True, 'score': .95}]
        threshold = select_threshold(rows)
        self.assertGreater(threshold, .97)
        self.assertLessEqual(threshold, .99)
        self.assertEqual(automatic(rows, threshold)['automatic_errors'], 0)
        self.assertEqual(automatic(rows, threshold)['automatic'], 1)

    def test_all_correct_other_can_be_automatic(self):
        rows = [{'truth': '기타', 'predicted': '기타', 'valid': True, 'score': .91}]
        self.assertEqual(automatic(rows, select_threshold(rows))['automatic'], 1)


if __name__ == '__main__':
    unittest.main()
