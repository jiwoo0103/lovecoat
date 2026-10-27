from coatdesk_v2.backend import Backend
from coatdesk_v2.domain import Garment
from coatdesk_v2.storage import Repository
from test_v2_workspace import application, wait_for


def suggestion(**changes):
    return {"values": {"gender": "여성", "age": "성인", "size": "M"}, **changes}


def test_default_jacket_removes_only_missing_category_status():
    garment = Garment("0001", {}, suggestion=suggestion())
    garment.apply_analysis()
    assert garment.fields["category"] == "자켓"
    assert garment.field_sources["category"] == "default"
    assert garment.status == "확인 완료"
    assert garment.export_ready and not garment.confirmed


def test_jacket_default_keeps_uncertain_and_missing_values_pending():
    uncertain = Garment("0001", {}, suggestion=suggestion(held=["사이즈"]))
    uncertain.apply_analysis()
    assert uncertain.fields["category"] == "자켓"
    assert uncertain.pending_review == ["size"]
    assert uncertain.status == "확인 필요"
    missing = Garment("0002", {}, suggestion={"values": {}})
    missing.apply_analysis()
    assert missing.fields == {"category": "자켓"}
    assert missing.status == "확인 필요" and not missing.export_ready


def test_analysis_preserves_manually_entered_category():
    garment = Garment("0001", {}, fields={"category": "조끼"}, suggestion=suggestion())
    garment.apply_analysis()
    assert garment.fields["category"] == "조끼"
    assert garment.export_ready


def test_cached_analysis_backfills_default_without_rerunning_model(tmp_path):
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
