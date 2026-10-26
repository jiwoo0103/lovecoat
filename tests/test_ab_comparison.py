import json

import pytest
from PIL import Image

from coatdesk_ab.data import SHAPES, SIZES, letterbox
from coatdesk_ab.locator import reference_assets


def test_whole_photo_letterbox_keeps_all_four_corners():
    source = Image.new("RGB", (8, 16), "gray")
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    points = [(0, 0), (7, 0), (0, 15), (7, 15)]
    for point, color in zip(points, colors, strict=True):
        source.putpixel(point, color)
    actual, transform = letterbox(source, 16)
    assert transform["offset"] == (4, 0)
    assert transform["no_crop"]
    for point, color in zip(points, colors, strict=True):
        assert actual.getpixel((point[0] + 4, point[1])) == color
    assert actual.getpixel((0, 8)) == (255, 255, 255)


def test_letterbox_does_not_mutate_source():
    source = Image.new("RGB", (30, 10), "red")
    before = source.tobytes()
    result, metadata = letterbox(source, 60)
    assert source.size == (30, 10) and source.tobytes() == before
    assert result.size == (60, 60) and metadata["resized_size"] == (60, 20)


def test_detector_references_must_not_use_validation(tmp_path):
    with pytest.raises(ValueError, match="inner fitting"):
        reference_assets(tmp_path, [{"code": "0039", "role": "outer_validation"}])


def test_size_and_shape_labels_remain_distinct():
    assert len(SIZES) == 17 and len(set(SIZES)) == 17
    assert SHAPES == ["adult_male", "adult_female", "child"]
    assert "female_child" not in SHAPES


def test_separate_classifier_instances_do_not_share_parameters():
    torch = pytest.importorskip("torch")
    from coatdesk_ab.network import PhotoClassifier

    torch.set_num_threads(2)
    shape = PhotoClassifier(3)
    size = PhotoClassifier(17)
    other_shape = PhotoClassifier(3)
    assert next(shape.parameters()).data_ptr() != next(other_shape.parameters()).data_ptr()
    image = torch.randn(2, 3, 96, 96)
    assert shape(image).shape == (2, 3)
    assert size(image).shape == (2, 17)
    assert shape.output[-1].weight.data_ptr() != size.output[-1].weight.data_ptr()
    assert json.dumps(SHAPES)
