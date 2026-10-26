import json

import cv2
import numpy as np
import pytest
import torch

from coatdesk_locator.dataset import box_affine
from coatdesk_locator.model import build_detector
from coatdesk_locator_v2.experiment import SIDE, Fitting, build_v2, change, native_affine
from coatdesk_locator_v2 import evaluate
from coatdesk_ab.data import sha


def test_portrait_rotation_is_converted_without_changing_physical_geometry():
    native=(1080,1920)
    frame=(448,448)
    box=np.array([420,450,650,700],dtype=np.float32)
    direct=cv2.getRotationMatrix2D((540,960),8,1.1)
    direct[:,2]+=[.03*1080,-.04*1920]
    scale=np.array([448/1080,448/1920]*2)
    expected=box_affine(box,direct)*scale
    actual=box_affine(box*scale,native_affine(frame,native,8,1.1,.03,-.04))
    assert actual==pytest.approx(expected,abs=.001)


def test_stress_translation_moves_both_photo_and_reference():
    pixels=np.full((100,100,3),255,dtype=np.uint8)
    pixels[30:60,30:60]=0
    moved,box=change(pixels,[30,30,60,60],"x_right")
    assert box==pytest.approx([31,30,61,60])
    assert np.array_equal(moved[30:60,31:61],pixels[30:60,30:60])
    with pytest.raises(ValueError,match="clip"):
        change(pixels,[90,30,100,60],"x_right")


def test_regression_photographs_cannot_be_admitted_to_final_fitting(tmp_path):
    np.save(tmp_path/"photos.npy",np.zeros((1,3,SIDE,SIDE),dtype=np.uint8))
    (tmp_path/"training-labels.json").write_text(json.dumps([{"role":"fresh_holdout","cache_index":0}]))
    with pytest.raises(ValueError,match="forbidden"):
        Fitting(tmp_path,final=True)
    (tmp_path/"training-labels.json").write_text(json.dumps([{"role":"inner_dev","cache_index":0}]))
    assert len(Fitting(tmp_path))==0 and len(Fitting(tmp_path,final=True))==1


def test_new_resolution_keeps_the_original_factory_and_supports_inference():
    original=build_detector()
    candidate=build_v2().eval()
    assert original.transform.fixed_size==(320,320)
    assert candidate.transform.fixed_size==(448,448)
    with torch.inference_mode():
        result=candidate([torch.ones(3,SIDE,SIDE)])[0]
    assert result["boxes"].shape[1]==4 and result["scores"].ndim==1


def test_partial_evaluation_refuses_a_different_exported_model(tmp_path,monkeypatch):
    work=tmp_path/"work"
    output=tmp_path/"output"
    work.mkdir()
    output.mkdir()
    labels=work/"regression-labels.json"
    labels.write_text("[]")
    weights=output/"detector.pt"
    weights.write_bytes(b"original frozen model")
    protocol={"weights_sha256":sha(weights),"code_sha256":sha(evaluate.__file__),
              "regression_labels_sha256":sha(labels),"baseline_prediction_sha256":{},"classifiers_sha256":{}}
    (output/"evaluation-protocol.json").write_text(json.dumps(protocol))
    monkeypatch.setattr(evaluate,"check",lambda _:None)
    assert evaluate.freeze_evaluation(work,output)==protocol
    weights.write_bytes(b"different model")
    with pytest.raises(ValueError,match="weights changed"):
        evaluate.freeze_evaluation(work,output)


def test_coverage_and_downstream_classification_are_measured_separately():
    good={"code":"ok","location":{"valid":True},"coverage":1.,"iou":.8,
          "shape_correct":True,"size_correct":True,"timing":{"decode":.01,"locate":.06,"classify":.2}}
    incomplete={"code":"edge","location":{"valid":True},"coverage":.9,"iou":.8,
                "shape_correct":True,"size_correct":False}
    pending={"code":"pending","location":{"valid":False},"coverage":1.,"iou":.85,
             "shape_correct":False,"size_correct":False}
    metrics=evaluate.summarize([good,incomplete,pending])
    assert metrics["located"]==2 and metrics["reference_coverage_99"]==1
    assert metrics["both_correct"]==1 and metrics["count"]==3
    assert metrics["pending_or_wrong"]==["edge","pending"]
