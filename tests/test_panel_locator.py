import json

import numpy as np
import pytest
import torch

from coatdesk_locator.dataset import FRAME, Placards, box_affine
from coatdesk_locator.model import choose_detection, overlap


def test_affine_box_encloses_all_transformed_corners():
    matrix = np.array([[0, -1, 100], [1, 0, 5]], dtype=np.float32)
    assert box_affine([10, 20, 30, 40], matrix).tolist() == [60, 15, 80, 35]


def test_crop_has_margin_and_stays_in_photo():
    prediction = {"scores": torch.tensor([.9]), "boxes": torch.tensor([[0., 4., 95., 99.]])}
    found = choose_detection(prediction, 100, 100)
    assert found["valid"] and found["crop_box"] == [0, 0, 100, 100]


def test_uncertain_and_missing_detection_are_not_success():
    uncertain = {"scores": torch.tensor([.49]), "boxes": torch.tensor([[10., 20., 50., 60.]])}
    assert not choose_detection(uncertain, 100, 100)["valid"]
    empty = {"scores": torch.empty(0), "boxes": torch.empty((0, 4))}
    assert choose_detection(empty, 100, 100)["crop_box"] is None


def test_iou_and_coverage_are_different_measures():
    iou, coverage = overlap([0, 0, 20, 20], [5, 5, 15, 15])
    assert iou == pytest.approx(.25) and coverage == 1
    assert overlap(None, [5, 5, 15, 15]) == (0, 0)


def test_only_requested_roles_and_single_placard_class_enter_training(tmp_path):
    array = np.lib.format.open_memmap(tmp_path / "photos.npy", mode="w+", dtype=np.uint8,
                                      shape=(2, 3, FRAME[1], FRAME[0]))
    array[:] = 255
    array.flush()
    records = [{"index": i, "box": [100, 200, 300, 400], "coordinate_size": [1080, 1920],
                "role": role, "shape_index": 9, "size_index": 99} for i, role in enumerate(["inner_fit", "outer_validation"])]
    (tmp_path / "box-labels.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    dataset = Placards(tmp_path, ["inner_fit"])
    assert len(dataset) == 1
    image, target = dataset[0]
    assert image.shape == (3, 640, 360)
    assert target["labels"].tolist() == [1]
    assert target["boxes"][0].tolist() == pytest.approx([100 / 3, 200 / 3, 100, 400 / 3])
    assert set(target) == {"boxes", "labels", "image_id"}
