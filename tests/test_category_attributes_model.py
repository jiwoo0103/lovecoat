import pytest
from PIL import Image

from coatdesk_ab.data import letterbox
from scripts.category_attributes_model import attribute_targets, check_partition, decide


def test_length_output_controls_jacket_and_coat():
    assert decide([.99, .01], [.98, .02])["display_category"] == "자켓"
    assert decide([.99, .01], [.02, .98])["display_category"] == "코트"
    assert decide([.01, .99], [.98, .02])["display_category"] == "조끼"


def test_long_sleeveless_and_low_either_attribute_are_held():
    long_vest = decide([.01, .99], [.01, .99])
    assert long_vest["candidate"] == "조끼"
    assert long_vest["display_category"] == "기타" and long_vest["long_sleeveless_exception"]
    assert "긴 민소매 예외" in long_vest["review_reasons"]
    assert decide([.2, .8], [.99, .01])["needs_review"]
    assert decide([.99, .01], [.6, .4])["needs_review"]


@pytest.mark.parametrize("values", [[float("nan"), 0], [.8, .8], [-.1, 1.1]])
def test_invalid_probabilities_are_not_published(values):
    with pytest.raises(ValueError):
        decide(values, [.99, .01])


def test_only_human_confirmed_classes_supply_proxy_targets():
    assert attribute_targets("코트", True) == (0, 1)
    assert attribute_targets("조끼", True) == (1, 0)
    for label, confirmed in (("조끼", False), ("기타", True), (None, False)):
        with pytest.raises(ValueError):
            attribute_targets(label, confirmed)


@pytest.mark.parametrize("key", ["code", "source_group", "source_sha256"])
def test_validation_overlap_is_rejected(key):
    fit = {"code": "a", "source_group": "ga", "source_sha256": "ha"}
    held = {"code": "b", "source_group": "gb", "source_sha256": "hb", key: fit[key]}
    with pytest.raises(ValueError, match="overlap"):
        check_partition([fit], [held])


def test_full_photo_corners_and_aspect_survive_for_adult_and_child_inputs():
    for size in ((80, 160), (40, 80)):
        photo = Image.new("RGB", size, "black")
        for point in ((0, 0), (size[0] - 1, size[1] - 1)):
            photo.putpixel(point, (255, 0, 0))
        image, meta = letterbox(photo, 320)
        assert meta["no_crop"] and meta["resized_size"] == (160, 320)
        assert image.getpixel((80, 0))[0] > 200
        assert image.getpixel((239, 319))[0] > 200
