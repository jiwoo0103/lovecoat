import time
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlEngine, QQmlExpression

from coatdesk_v2 import excel
from coatdesk_v2.backend import Backend
from coatdesk_v2.category import decode
from coatdesk_v2.domain import Garment, OPTIONS, Workspace, validate_fields
from coatdesk_v2.recognition import RecognitionEngine
from coatdesk_v2.storage import Repository, encode
from test_v2_ui_flow import ui as shared_ui, click, visual, select_option

from test_v2_workspace import application, photo, wait_for

ui = shared_ui


def fields(**changes):
    return {"category": "자켓", "gender": "남성", "age": "성인", "size": "M", **changes}


@pytest.mark.parametrize("sleeve,length,held", [(.899, .99, True), (.90, .90, False), (.99, .899, True), (.99, .901, False)])
def test_policy_boundaries(sleeve, length, held):
    result = decode([sleeve, 1-sleeve, length, 1-length])
    assert result["needs_review"] == held
    assert result["display_category"] == ("기타" if held else "자켓")
    assert result["candidate"] == "자켓"


def test_long_sleeveless_stays_other_and_requires_review():
    result = decode([.001, .999, .001, .999])
    assert result["candidate"] == "조끼"
    assert result["raw_category"] == result["display_category"] == "기타"
    assert result["needs_review"]


def test_category_failure_preserves_panel_results_and_cache_identity(tmp_path):
    path = photo(tmp_path, "0001")
    workspace = Workspace("test", "test", "2026-10-28", str(tmp_path), 1, 1, [], [])
    garment = Garment("0001", {"0": str(path.relative_to(tmp_path))})
    def failed_category(image):
        raise ValueError("no placard")
    engine = RecognitionEngine.__new__(RecognitionEngine)
    engine.models = SimpleNamespace(analyze=lambda *args: {"values": fields(), "scores": {}, "held": []})
    engine.category_model = SimpleNamespace(analyze=failed_category)
    engine.thresholds = {"panel": .9, "shape": .9, "size": .9}
    engine.category_thresholds = {"sleeve": .9, "length": .9}
    engine.model_key = "old"
    result = engine.analyze(workspace, garment)
    assert result["values"]["size"] == "M" and result["category"]["needs_review"]
    garment.suggestion = result
    garment.apply_analysis()
    assert garment.fields["size"] == "M" and "category" not in garment.fields
    old = engine.key(workspace, garment)
    engine.model_key = "new-category-assets"
    assert engine.key(workspace, garment) != old
    old = engine.key(workspace, garment)
    engine.thresholds["size"] = .85
    assert engine.key(workspace, garment) != old
    old = engine.key(workspace, garment)
    engine.category_thresholds["sleeve"] = .95
    assert engine.key(workspace, garment) != old


def test_default_replacement_same_value_human_choice_and_failure():
    garment = Garment("0001", {}, fields=fields(), field_sources={"category": "default"})
    assert garment.pending_review == ["category"]
    garment.set_field("category", "자켓")
    garment.suggestion = {"category": decode([.99, .01, .01, .99])}
    garment.apply_analysis()
    assert garment.fields["category"] == "자켓" and garment.field_sources["category"] == "manual"
    garment.field_sources["category"] = "default"
    garment.apply_analysis()
    assert garment.fields["category"] == "코트" and garment.field_sources["category"] == "ai"
    garment.suggestion = {"category": {"error": "ValueError", "needs_review": True}}
    garment.apply_analysis()
    assert garment.fields["category"] == "코트" and garment.pending_review == ["category"]
    garment.confirm_review()
    assert garment.export_ready


def test_rethreshold_retires_only_old_ai_reasons_and_preserves_child_review():
    garment = Garment("0001", {}, fields=fields(age="유아동"), review_required=["size", "category"],
                      suggestion={"scores": {"size": .938}, "thresholds": {"size": .95}, "held": ["사이즈"]})
    garment.rethreshold({"panel": .9, "shape": .9, "size": .9})
    assert garment.pending_review == ["category", "gender"]
    assert "size" not in garment.review_required
    assert "category" in garment.review_required


def test_threshold_migration_runs_once_and_backs_up_existing_database(tmp_path):
    repo = Repository(tmp_path)
    repo.create("old", "", tmp_path, 1, 1, "", [Garment("0001", {})])
    with repo.connection() as db:
        db.execute("DELETE FROM settings WHERE key='v24_thresholds_migrated'")
        db.execute("UPDATE settings SET value=? WHERE key='ai_thresholds'", (encode({"panel": .5, "shape": .95, "size": .95}),))
    repo = Repository(tmp_path)
    assert repo.setting("ai_thresholds") == {"panel": .9, "shape": .9, "size": .9}
    assert len(list(tmp_path.glob("before-v24-*.sqlite3"))) == 1
    repo.set_setting("ai_thresholds", {"panel": .8, "shape": .85, "size": .95})
    assert Repository(tmp_path).setting("ai_thresholds")["panel"] == .8


def test_legacy_invalid_size_is_retained_and_blocks_confirmation_and_excel(tmp_path):
    repo = Repository(tmp_path)
    key = repo.create("old", "", tmp_path, 1, 1, "", [Garment("0001", {}, fields=fields(size="100/110"), confirmed=True)])
    garment = repo.load(key).garments[0]
    assert garment.fields["size"] == "100/110" and not garment.export_ready
    with pytest.raises(ValueError):
        garment.confirm_review()
    with pytest.raises(ValueError):
        excel.build_plan(repo.load(key), tmp_path / "out.xlsx", "date")
    for size in OPTIONS["size"]:
        validate_fields({"size": size})
    with pytest.raises(ValueError):
        validate_fields({"size": "100/110"})


def test_category_hold_restart_undo_and_excel_gate(tmp_path):
    repo = Repository(tmp_path)
    garment = Garment("0001", {}, fields=fields(), field_sources={"category": "default"}, suggestion={"category": decode([.99, .01, .7, .3])})
    garment.apply_analysis()
    key = repo.create("test", "", tmp_path, 1, 1, "", [garment])
    assert Repository(tmp_path).load(key).garments[0].pending_review == ["category"]
    previous = repo.edit(key, ["0001"], lambda g: (g.set_field("category", "코트"), g.confirm_review()))
    assert excel.build_plan(repo.load(key), tmp_path / "out.xlsx", "date").rows[0][3] == "코트"
    repo.undo(key, previous)
    assert repo.load(key).garments[0].pending_review == ["category"]
    with pytest.raises(ValueError):
        excel.build_plan(repo.load(key), tmp_path / "out.xlsx", "date")


def test_latest_async_workspace_request_wins(tmp_path):
    app = application()
    backend = Backend(tmp_path)
    keys = [backend.repository.create(str(i), "", tmp_path, 1, 1, "", [Garment("0001", {})]) for i in range(2)]
    original = backend.repository.load
    def load(key):
        if key == keys[0]:
            time.sleep(.08)
        return original(key)
    backend.repository.load = load
    backend.requestWorkspace(keys[0])
    backend.requestWorkspace(keys[1])
    wait_for(lambda: not backend.jobs)
    assert backend.workspace.id == keys[1]
    backend.shutdown()
    assert app


def test_repeated_analysis_reuses_one_engine_and_one_python_worker(tmp_path):
    app = application()
    created, threads = [], []
    class Engine:
        def __init__(self):
            created.append(self)
        def key(self, workspace, garment):
            return "same-model"
        def analyze(self, workspace, garment):
            threads.append(threading.get_ident())
            return {"values": fields(), "category": decode([.99, .01, .99, .01])}
    backend = Backend(tmp_path, engine_factory=Engine)
    key = backend.repository.create("reuse", "", tmp_path, 1, 1, "", [Garment("0001", {})])
    backend.openWorkspace(key)
    for force in (False, False, True):
        backend.startAnalysis(force)
        wait_for(lambda: not backend.jobs)
    assert len(created) == 1 and len(threads) == 2 and len(set(threads)) == 1
    assert threads[0] != threading.get_ident()
    backend.shutdown()
    assert app


def test_app_theme_stays_light_when_app_color_scheme_changes():
    from coatdesk_v2.__main__ import force_light_theme
    # Offscreen Qt reports Unknown for every requested color scheme. Exercise
    # the same native signal contract without changing the Windows theme.
    class Hints(QObject):
        colorSchemeChanged = Signal(object)
        scheme = Qt.ColorScheme.Unknown
        def setColorScheme(self, scheme):
            if self.scheme != scheme:
                self.scheme = scheme
                self.colorSchemeChanged.emit(scheme)
    hints = Hints()
    force_light_theme(SimpleNamespace(styleHints=lambda: hints))
    assert hints.scheme == Qt.ColorScheme.Light
    hints.setColorScheme(Qt.ColorScheme.Dark)
    assert hints.scheme == Qt.ColorScheme.Light


def test_all_grids_fast_mode_restore_and_selection_free_navigation(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    # Expand the fixture to four full pages without touching photo originals.
    with backend.repository.connection() as db:
        for i in range(5, 65):
            garment = Garment(f"{i:04}", {})
            db.execute("INSERT INTO garments VALUES(?,?,?,?)", (keys[0], garment.code, i, encode(garment.to_dict())))
        db.execute("UPDATE workspaces SET end=64 WHERE id=?", (keys[0],))
    backend.openWorkspace(keys[0])
    window.resize(1000, 720)
    backend.setLayout(2, 2)
    for _ in range(3):
        QTest.keyClick(window, Qt.Key_Right)
    assert backend.page == 3 and not backend.selected
    QTest.keyClick(window, Qt.Key_Left)
    assert backend.page == 2
    backend.pageBy(-2)
    QTest.qWait(60)
    select_option(window, visual(window.contentItem(), "category_0001_combo"), 2)
    QTest.keyClick(window, Qt.Key_Right)
    assert backend.page == 1
    assert backend.repository.load(keys[0]).garments[0].fields["category"] == "코트"
    for mode in ("review", "photos"):
        backend.setMode(mode)
        for fast in (False, True):
            backend.setFastInput(fast)
            for rows in range(1, 5):
                for columns in range(1, 5):
                    backend.setLayout(rows, columns)
                    QTest.qWait(10)
                    assert len(backend.model.rows) == min(rows*columns, len(backend.filtered)-backend.page*rows*columns)
                    assert not window.grabWindow().isNull()
                    for row in backend.model.rows:
                        card = visual(window.contentItem(), "card_"+row["code"])
                        corner = card.mapToScene(QPointF(card.width(), card.height()))
                        assert corner.x() <= window.width()+1
                        assert corner.y() <= window.height()-48+1
    backend.setMode("review")
    backend.setLayout(2, 2)
    backend.pageBy(-100)
    backend.setFastInput(True)
    QTest.qWait(30)
    click(window, visual(window.contentItem(), "fast_category_0001_2"))
    QTest.keyClick(window, Qt.Key_Right)
    assert backend.page == 1
    backend.openWorkspace(keys[1])
    backend.openWorkspace(keys[0])
    assert backend.fast_input and backend.layouts["review"] == [2, 2]


def test_scaled_card_dropdown_stays_normal_size_and_commits_once(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    window.resize(1000, 720)
    backend.setLayout(4, 4)
    QTest.qWait(50)
    combo = visual(window.contentItem(), "category_0001_combo")
    click(window, combo)
    context = QQmlEngine.contextForObject(combo)
    opened, _ = QQmlExpression(context, combo, "popup.opened").evaluate()
    width, _ = QQmlExpression(context, combo, "popup.width").evaluate()
    assert opened and width >= 190
    undo_count = len(backend.undo_stack)
    QTest.keyClick(window, Qt.Key_Home)
    QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    assert len(backend.undo_stack) == undo_count+1
    assert backend.repository.load(keys[0]).garments[0].fields["category"] == "자켓"


def test_popup_and_text_edit_do_not_move_background_page(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    for name in ("createDialog", "viewDialog", "headDialog", "zoomDialog", "transferDialog"):
        dialog = window.findChild(QObject, name)
        QMetaObject.invokeMethod(dialog, "open")
        before = backend.page
        QTest.keyClick(window, Qt.Key_Right)
        assert backend.page == before
        QMetaObject.invokeMethod(dialog, "close")
    click(window, window.findChild(QObject, "garmentSearch"))
    QTest.keyClick(window, Qt.Key_Right)
    assert backend.page == 0
    click(window, window.findChild(QObject, "layoutButton"))
    QTest.keyClick(window, Qt.Key_Right)
    assert backend.page == 0
    QTest.keyClick(window, Qt.Key_Escape)
