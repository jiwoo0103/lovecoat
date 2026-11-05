import pytest

from coatdesk_v2.backend import Backend
from coatdesk_v2.category import decode
from coatdesk_v2.domain import Garment
from coatdesk_v2.storage import Repository
from test_v2_workspace import application, wait_for


def suggestion(**changes):
    return {"values": {"gender": "여성", "age": "성인", "size": "M"}, "category": {"display_category": "자켓", "candidate": "자켓", "needs_review": False}, **changes}


def test_category_ai_completes_missing_category():
    garment = Garment("0001", {}, suggestion=suggestion())
    garment.apply_analysis()
    assert garment.fields["category"] == "자켓"
    assert garment.field_sources["category"] == "ai"
    assert garment.status == "확인 완료"
    assert garment.export_ready and not garment.confirmed


@pytest.mark.parametrize("probabilities,category", [
    ([.99, .01, .7, .3], "자켓"),
    ([.99, .01, .3, .7], "코트"),
    ([.3, .7, .99, .01], "조끼"),
])
def test_low_score_category_is_prefilled_and_one_confirmation_is_enough(tmp_path, probabilities, category):
    garment = Garment("0001", {}, suggestion=suggestion(category=decode(probabilities)))
    garment.apply_analysis()
    assert garment.fields["category"] == category
    assert garment.pending_review == ["category"]
    assert not garment.export_ready
    repo = Repository(tmp_path)
    key = repo.create("prefill", "", tmp_path, 1, 1, "", [garment])
    assert repo.load(key).garments[0].fields["category"] == category
    repo.edit(key, ["0001"], lambda g: g.confirm_review())
    restored = Repository(tmp_path).load(key).garments[0]
    assert restored.fields["category"] == category
    assert restored.export_ready


def test_saved_low_score_other_is_replaced_by_candidate_without_overwriting_human_choice(tmp_path):
    category = decode([.99, .01, .7, .3])
    category["display_category"] = "기타"  # Saved by the previous UI policy.
    garment = Garment("0001", {}, fields={"category": "기타", "gender": "남성", "age": "성인", "size": "M"},
                      field_sources={"category": "ai"}, suggestion=suggestion(category=category))
    repo = Repository(tmp_path)
    key = repo.create("old", "", tmp_path, 1, 1, "", [garment])
    assert repo.load(key).garments[0].fields["category"] == "자켓"
    assert repo.load(key).garments[0].pending_review == ["category"]
    repo.edit(key, ["0001"], lambda g: g.set_field("category", "코트"))
    assert Repository(tmp_path).load(key).garments[0].fields["category"] == "코트"


def test_category_ai_keeps_uncertain_and_missing_values_pending():
    uncertain = Garment("0001", {}, suggestion=suggestion(held=["사이즈"]))
    uncertain.apply_analysis()
    assert uncertain.fields["category"] == "자켓"
    assert uncertain.pending_review == ["size"]
    assert uncertain.status == "확인 필요"
    missing = Garment("0002", {}, suggestion={"values": {}})
    missing.apply_analysis()
    assert missing.fields == {}
    assert missing.status == "확인 필요" and not missing.export_ready


def test_analysis_preserves_manually_entered_category():
    garment = Garment("0001", {}, fields={"category": "조끼"}, suggestion=suggestion())
    garment.apply_analysis()
    assert garment.fields["category"] == "조끼"
    assert garment.export_ready


def test_cached_analysis_applies_category_result_without_rerunning_model(tmp_path):
    app = application()
    repo = Repository(tmp_path / "data")
    garments = [Garment("0001", {}, suggestion=suggestion(), analysis_key="cached", analysis_state="done"),
                Garment("0002", {}, suggestion=suggestion(held=["사이즈"]),
                        analysis_key="cached", analysis_state="done")]
    key = repo.create("20261002", "", tmp_path, 1, 2, "", garments)

    class Engine:
        def key(self, workspace, garment):
            return "cached"

        def analyze(self, workspace, garment):
            raise AssertionError("cached result must not rerun inference")

    backend = Backend(repo.directory, engine_factory=Engine)
    backend.openWorkspace(key)
    backend.startAnalysis(False)
    wait_for(lambda: not backend.jobs)
    assert not backend.error
    assert [g.fields["category"] for g in repo.load(key).active] == ["자켓", "자켓"]
    assert backend.state["reviewCount"] == 1
    assert backend.state["readyCount"] == 1
    backend.shutdown()
    assert app
