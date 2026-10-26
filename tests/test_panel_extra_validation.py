import copy

import pytest

from scripts.validate_panel_extra import metrics_for, reviewed_records
from scripts.review_panel_extra import rescore


def sample():
    rows = [{"code":"1800","box":[20,30,60,80],"coordinate_size":[100,150],
             "prior_visual_inventory":["adult","S"],"role":"fresh_holdout"}]
    review = {"predictions_seen":False,"pages":{"0":"fS"},"boxes":{}}
    return rows,review


def test_new_holdout_rejects_training_or_earlier_evaluation_codes():
    rows, review = sample()
    with pytest.raises(ValueError,match="previously used"):
        reviewed_records(rows,review,["1800"])
    rows[0]["code"]="0100"
    with pytest.raises(ValueError,match="Early"):
        reviewed_records(rows,review,[])


@pytest.mark.parametrize("change",["predictions_seen","missing_page","short_labels","unsupported","wrong_size","invalid_box"])
def test_unreviewed_or_inconsistent_ground_truth_is_blocked(change):
    rows, review = sample()
    if change=="predictions_seen":
        review["predictions_seen"]=True
    elif change=="missing_page":
        review["pages"]={}
    elif change=="short_labels":
        review["pages"]["0"]=""
    elif change=="unsupported":
        review["pages"]["0"]="fXXL"
    elif change=="wrong_size":
        review["pages"]["0"]="fM"
    else:
        review["boxes"]={"1800":[20,30,10,80]}
    with pytest.raises(ValueError):
        reviewed_records(rows,review,[])


def test_visual_labels_and_manual_boxes_are_separate_from_predictions():
    rows, review = sample()
    original = copy.deepcopy(rows)
    review["boxes"]={"1800":[25,35,55,75]}
    result = reviewed_records(rows,review,[])[0]
    assert result["shape"]=="adult_female" and result["size"]=="S"
    assert not result["use_for_training"] and not result["independent_human_review"]
    assert result["reviewed_box_before_margin"]==[25,35,55,75]
    assert result["box"]==[24,34,56,76]
    assert rows==original


def test_missing_detection_is_counted_as_wrong_in_total_denominator():
    common = {"location":{"valid":True},"iou":.8,"coverage":1.,
              "shape_correct":True,"size_correct":True,"timing":{"decode":.01,"locate":.05,"classify":.3}}
    failed = {**common,"location":{"valid":False},"iou":0.,"coverage":0.,
              "shape_correct":False,"size_correct":False}
    metrics = metrics_for([common,failed])
    assert metrics["count"]==2 and metrics["both_correct"]==1 and metrics["located"]==1
    assert metrics["crop_covers_reference_99"]==1


def test_label_adjudication_preserves_raw_scores_and_initial_records():
    rows=[{"code":"2762","shape":"adult_female","gender":"female","size":"XL"}]
    predictions=[{"code":"2762","shape":"adult_male","size":"XL","true_shape":"adult_female",
                  "true_size":"XL","shape_correct":False,"size_correct":True,
                  "shape_scores":{"adult_male":.97,"adult_female":.015,"child":.015},
                  "location":{"score":.987,"crop_box":[375,409,652,651]}}]
    corrections={"2762":{"before":"adult_female","after":"adult_male","reason":"Visual transcription error"}}
    fixed,scored=rescore(rows,predictions,corrections)
    assert fixed[0]["gender"]=="male" and scored[0]["shape_correct"]
    assert rows[0]["shape"]=="adult_female" and not predictions[0]["shape_correct"]
    for key in ("shape","size","shape_scores","location","true_size","size_correct"):
        assert scored[0][key]==predictions[0][key]
    with pytest.raises(ValueError,match="Unexpected label"):
        rescore(fixed,predictions,corrections)
