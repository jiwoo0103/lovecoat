import itertools
import unittest

from scripts.retrain_four_evaluation import automatic, review_summary, sweep


def observation(code, scores, correct=True, **changes):
    row = {
        "code": code,
        "group": code,
        "kind": "original",
        "panel_score": scores[0],
        "shape_score": scores[1],
        "size_score": scores[2],
        "sleeve_score": 0.95,
        "length_score": 0.95,
        "mandatory_review": False,
        "workflow_correct": correct,
    }
    return {**row, **changes}


class ThresholdTests(unittest.TestCase):
    def test_histogram_optimum_matches_direct_search(self):
        rows = [
            observation("a", (0.95, 0.95, 0.95)),
            observation("b", (0.72, 0.98, 0.98)),
            observation("c", (0.86, 0.73, 0.95), False),
            observation("d", (0.99, 0.99, 0.99), mandatory_review=True),
        ]
        found = sweep(rows)
        direct = []
        for values in itertools.product([0.5, 0.72, 0.73, 0.74, 0.86, 0.87, 0.95, 0.96, 0.98, 0.99], repeat=3):
            thresholds = dict(zip(("panel", "shape", "size"), values))
            result = review_summary(rows, thresholds)
            if result["automatic_errors"] == 0:
                direct.append(result["automatic"])
        self.assertTrue(found["safe_triplet"])
        self.assertEqual(found["automatic"], max(direct))
        self.assertEqual(found["automatic_errors"], 0)
        lower = found["joint_lower_same_dev_cohort"]
        self.assertEqual(lower["automatic"], found["automatic"])
        self.assertEqual(lower["automatic_errors"], 0)
        self.assertEqual(lower["thresholds"], dict(panel=0.5, shape=0.74, size=0.5))

    def test_mandatory_review_cannot_be_removed_by_lower_scores(self):
        p = observation("child", (1, 1, 1), mandatory_review=True)
        self.assertFalse(automatic(p, dict(panel=0.5, shape=0.5, size=0.5)))

    def test_category_threshold_stays_ninety(self):
        p = observation("jacket", (1, 1, 1), length_score=0.899)
        self.assertFalse(automatic(p, dict(panel=0.5, shape=0.5, size=0.5)))

    def test_reports_no_safe_grid_when_confident_error_always_passes(self):
        self.assertFalse(sweep([observation("wrong", (1, 1, 1), False)])["safe_triplet"])

    def test_excel_match_does_not_hide_a_missed_long_vest_review(self):
        p = observation("long-vest", (1, 1, 1), correct=True, long_sleeveless_truth=True)
        self.assertTrue(automatic(p, dict(panel=0.9, shape=0.9, size=0.9)))
        summary = review_summary([p], dict(panel=0.9, shape=0.9, size=0.9))
        self.assertEqual(summary["automatic_errors"], 1)
        self.assertEqual(summary["mandatory_long_sleeveless_misses"], 1)
        self.assertFalse(sweep([p])["safe_triplet"])

    def test_nine_observations_do_not_become_nine_garments(self):
        rows = [observation("same", (1, 1, 1), kind=str(i)) for i in range(9)]
        summary = review_summary(rows, dict(panel=0.9, shape=0.9, size=0.9))
        self.assertEqual(summary["observations"], 9)
        self.assertEqual(summary["independent_garment_groups"], 1)


if __name__ == "__main__":
    unittest.main()
