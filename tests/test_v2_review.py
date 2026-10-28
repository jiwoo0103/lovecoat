from coatdesk_v2.domain import Garment
from coatdesk_v2.storage import Repository


def complete(**changes):
    return {"category": "코트", "gender": "여성", "age": "성인", "size": "M", **changes}


def test_normal_complete_values_need_no_whole_card_confirmation():
    garment = Garment("0001", {}, fields=complete())
    assert garment.export_ready
    assert not garment.pending_review
    assert not garment.confirmed


def test_low_score_candidates_fill_blanks_and_require_explicit_review():
    garment = Garment("0001", {}, fields={"category": "코트", "size": "L"})
    garment.suggestion = {"values": {}, "candidates": {"gender": "남성", "age": "성인", "size": "M"},
                          "held": ["성별/성인·아동", "사이즈"]}
    garment.apply_analysis()
    assert garment.fields == complete(gender="남성", size="L")
    assert set(garment.pending_review) == {"gender", "age", "size"}
    garment.set_field("gender", "여성")
    assert not garment.export_ready
    garment.confirm_review()
    assert garment.export_ready
    garment.set_field("size", "S")
    assert garment.pending_review == ["size"]


def test_child_default_and_review_survive_repository_restart_and_undo(tmp_path):
    repository = Repository(tmp_path)
    garment = Garment("0001", {}, fields={"category": "조끼", "size": "S"})
    garment.set_field("age", "유아동")
    assert garment.fields["gender"] == "남성"
    assert garment.pending_review == ["gender"]
    key = repository.create("child", "", tmp_path, 1, 1, "", [garment])
    before = repository.edit(key, ["0001"], lambda item: item.confirm_review())
    assert Repository(tmp_path).load(key).garments[0].export_ready
    repository.undo(key, before)
    assert repository.load(key).garments[0].pending_review == ["gender"]
    repository.edit(key, ["0001"], lambda item: item.set_field("gender", "여성"))
    assert repository.load(key).garments[0].pending_review == ["gender"]


def test_review_changes_invalidate_preview_fingerprint(tmp_path):
    repository = Repository(tmp_path)
    garment = Garment("0001", {}, fields=complete(), review_required=["size"])
    key = repository.create("review", "", tmp_path, 1, 1, "", [garment])
    before = repository.load(key).export_fingerprint()
    repository.edit(key, ["0001"], lambda item: item.confirm_review())
    assert before != repository.load(key).export_fingerprint()


def test_old_confirmed_payload_preserves_human_review():
    garment = Garment("0001", {}, fields=complete(age="유아동"), confirmed=True,
                      suggestion={"held": ["사이즈"]})
    assert garment.export_ready
    garment.set_field("size", "L")
    assert garment.pending_review == ["size"]


def test_becoming_child_invalidates_previous_adult_gender_review():
    garment = Garment("0001", {}, fields=complete(), confirmed=True)
    garment.set_field("age", "유아동")
    assert garment.pending_review == ["gender"]
