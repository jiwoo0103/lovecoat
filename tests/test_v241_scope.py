import copy

import pytest
from PySide6.QtCore import QObject, QMetaObject, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from coatdesk_v2.backend import Backend
from coatdesk_v2.category import decode
from coatdesk_v2.domain import Garment, Workspace
from coatdesk_v2.scanner import scan
from coatdesk_v2.storage import Repository
from test_v2_ui_flow import ui as shared_ui, capture, click, select_option, visual
from test_v2_workspace import application, photo, wait_for

ui = shared_ui


def test_photo_empty_folders_are_distinct_from_absent_numbers_and_partial_photos(tmp_path):
    photo(tmp_path, "0001", 0)
    (tmp_path / "0_0003").mkdir()
    workspace = Workspace("test", "test", "", str(tmp_path), 1, 4, [3], scan(tmp_path, 1, 4))
    assert workspace.empty_photo_codes == ["0003"]
    assert workspace.counts["missing"] == 2  # Internal absent-number count is not a UI photo warning.
    assert workspace.counts["excluded"] == 1
    assert workspace.active[0].code == "0001"


def test_scope_expand_shrink_restore_keeps_values_review_models_and_other_workspaces(tmp_path):
    app = application()
    for i in range(1, 5):
        photo(tmp_path / "photos", f"{i:04}")
    repo = Repository(tmp_path / "data")
    key = repo.create("one", "", tmp_path / "photos", 1, 2, "", scan(tmp_path / "photos", 1, 2))
    other = repo.create("other", "", tmp_path / "photos", 1, 2, "", scan(tmp_path / "photos", 1, 2))
    def input_value(g):
        g.fields = {"category": "코트", "gender": "여성", "age": "성인", "size": "M"}
        g.field_sources = {k: "manual" for k in g.fields}
        g.suggestion = {"values": {"size": "M"}, "scores": {"size": .99}}
        g.analysis_state, g.analysis_key = "done", "old-cache"
        g.confirm_review()
    repo.edit(key, ["0001", "0002"], input_value)
    original = copy.deepcopy(repo.load(key).garments[1].to_dict())
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    backend.previewExcel(str(tmp_path / "export.xlsx"), "date")
    wait_for(lambda: not backend.jobs)
    assert backend.plan
    backend.updateWorkspaceScope("2", "4", "3")
    wait_for(lambda: not backend.jobs)
    w = repo.load(key)
    assert (w.start, w.end, w.errors) == (2, 4, [3])
    assert [g.code for g in w.active] == ["0002", "0004"]
    assert next(g for g in w.garments if g.code == "0002").to_dict() == original
    assert next(g for g in w.garments if g.code == "0004").analysis_state == "pending"
    assert backend.plan is None
    assert repo.load(other).end == 2 and repo.load(other).errors == []
    backend.updateWorkspaceScope("1", "2", "")
    wait_for(lambda: not backend.jobs)
    assert len(repo.load(key).garments) == 4
    assert [g.code for g in repo.load(key).active] == ["0001", "0002"]
    assert next(r for r in repo.recent() if r["id"] == key)["count"] == 2
    backend.updateWorkspaceScope("1", "4", "")
    wait_for(lambda: not backend.jobs)
    assert next(g for g in repo.load(key).garments if g.code == "0002").to_dict() == original
    assert backend.state["emptyPhotoCodes"] == []
    backend.shutdown()
    assert app


@pytest.mark.parametrize("start,end,errors", [("3", "1", ""), ("0", "100001", ""), ("1", "2", "3"), ("x", "2", ""), ("8", "9", "")])
def test_failed_scope_changes_leave_workspace_and_input_untouched(tmp_path, start, end, errors):
    app = application()
    photo(tmp_path / "photos", "0001")
    repo = Repository(tmp_path / "data")
    key = repo.create("test", "", tmp_path / "photos", 1, 2, "", scan(tmp_path / "photos", 1, 2))
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    before = repo.load(key).export_fingerprint()
    backend.updateWorkspaceScope(start, end, errors)
    wait_for(lambda: not backend.jobs)
    assert backend.error and repo.load(key).export_fingerprint() == before
    backend.shutdown()
    assert app


def test_missing_existing_folder_is_retained_but_not_active_and_can_return(tmp_path):
    repo = Repository(tmp_path / "data")
    saved = Garment("0002", {}, fields={"size": "XL"})
    key = repo.create("test", "", tmp_path, 1, 2, "", [Garment("0001", {}), saved])
    repo.change_scope(key, 1, 2, "", [Garment("0001", {})])
    w = repo.load(key)
    assert [g.code for g in w.active] == ["0001"]
    assert w.empty_photo_codes == ["0001"]
    assert w.garments[1].fields["size"] == "XL"
    repo.change_scope(key, 1, 2, "", [Garment("0001", {}), Garment("0002", {})])
    assert [g.code for g in repo.load(key).active] == ["0001", "0002"]
    assert repo.load(key).garments[1].fields["size"] == "XL"


def test_scope_merge_rolls_back_if_code_spelling_conflicts(tmp_path):
    repo = Repository(tmp_path)
    key = repo.create("test", "", tmp_path, 1, 2, "", [Garment("0001", {}), Garment("0002", {})])
    before = repo.load(key).export_fingerprint()
    with pytest.raises(ValueError, match="외투코드"):
        repo.change_scope(key, 2, 3, "", [Garment("2", {}), Garment("0003", {})])
    assert repo.load(key).export_fingerprint() == before
    assert len(repo.load(key).garments) == 2


def test_scope_dialog_save_and_always_visible_photo_count(ui):
    backend, window, keys, tmp_path = ui
    photo(tmp_path / "photos", "0005")
    (tmp_path / "photos/0_0006").mkdir()
    backend.openWorkspace(keys[0])
    dialog = window.findChild(QObject, "sourceDialog")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(40)
    window.findChild(QObject, "editRangeStart").setProperty("text", "2")
    window.findChild(QObject, "editRangeEnd").setProperty("text", "6")
    window.findChild(QObject, "editErrorNumbers").setProperty("text", "3")
    capture(window, "scope-editor.png")
    click(window, window.findChild(QObject, "saveWorkspaceScope"))
    wait_for(lambda: not backend.jobs)
    QTest.qWait(30)
    assert not dialog.property("opened"), backend.error
    assert [g.code for g in backend.workspace.active] == ["0002", "0004", "0005", "0006"]
    QMetaObject.invokeMethod(window.findChild(QObject, "transferDialog"), "open")
    QTest.qWait(30)
    text = window.findChild(QObject, "emptyPhotosSummary").property("text")
    assert "1개" in text and "0006" in text
    assert "누락" not in window.findChild(QObject, "exportCounts").property("text")
    capture(window, "export-photo-empty.png")
    QMetaObject.invokeMethod(window.findChild(QObject, "transferDialog"), "close")
    backend.updateWorkspaceScope("2", "5", "3")
    wait_for(lambda: not backend.jobs)
    QMetaObject.invokeMethod(window.findChild(QObject, "transferDialog"), "open")
    QTest.qWait(30)
    summary = window.findChild(QObject, "emptyPhotosSummary")
    assert summary.property("visible") and summary.property("text") == "사진 없는 폴더 0개"


def test_dropdown_selected_blue_readable_and_tracks_reused_card(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    backend.setField("0001", "category", "자켓")
    QTest.qWait(40)
    combo = visual(window.contentItem(), "category_0001_combo")
    click(window, combo)
    context = QQmlEngine.contextForObject(combo)
    def evaluate(expression):
        value, _ = QQmlExpression(context, combo, expression).evaluate()
        return value
    assert evaluate('popup.contentItem.itemAtIndex(1).background.color.toString()') == "#2563eb"
    assert evaluate('popup.contentItem.itemAtIndex(1).contentItem.color.toString()') == "#ffffff"
    assert evaluate('popup.contentItem.itemAtIndex(2).contentItem.color.toString()') == "#172b42"
    capture(window, "dropdown-selected-blue.png")
    QTest.keyClick(window, Qt.Key_Escape)
    select_option(window, combo, 2)
    backend.setField("0001", "category", "조끼")
    QTest.qWait(30)
    assert combo.property("displayText") == "조끼"
    click(window, combo)
    assert evaluate('popup.contentItem.itemAtIndex(3).background.color.toString()') == "#2563eb"
    QTest.keyClick(window, Qt.Key_Escape)
    assert backend.repository.load(keys[0]).garments[0].fields["category"] == "조끼"


def test_sleeve_threshold_default_change_restart_and_manual_confirmation(tmp_path):
    app = application()
    repo = Repository(tmp_path / "data")
    garment = Garment("0001", {}, fields={"category": "기타", "gender": "남성", "age": "성인", "size": "M"},
                      field_sources={"category": "ai"},
                      suggestion={"category": decode([.92, .08, .99, .01], .95, .9)})
    key = repo.create("threshold", "", tmp_path, 1, 1, "", [garment])
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    assert backend.state["categoryThresholds"]["sleeve"] == .9
    assert backend.workspace.active[0].fields["category"] == "자켓"
    assert backend.workspace.active[0].export_ready
    backend.setAiThresholds(90, 90, 90, 95)
    assert backend.workspace.active[0].fields["category"] == "기타"
    assert backend.workspace.active[0].pending_review == ["category"]
    backend.shutdown()
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    assert backend.state["categoryThresholds"]["sleeve"] == .95
    assert backend.workspace.active[0].pending_review == ["category"]
    backend.setField("0001", "category", "코트")
    backend.confirmGarment("0001")
    backend.setAiThresholds(90, 90, 90, 90)
    assert backend.workspace.active[0].fields["category"] == "코트"
    assert backend.workspace.active[0].export_ready
    assert repo.load(key).garments[0].fields["category"] == "코트"
    backend.shutdown()
    assert app


def test_invalid_sleeve_setting_does_not_partly_update_other_thresholds(tmp_path):
    app = application()
    backend = Backend(tmp_path)
    backend.setAiThresholds(91, 92, 93, 0)
    assert backend.error
    assert backend.thresholds == {"panel": .9, "shape": .9, "size": .9}
    assert backend.category_thresholds["sleeve"] == .9
    backend.shutdown()
    assert app


def test_ai_dialog_edits_sleeve_threshold(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    dialog = window.findChild(QObject, "aiDialog")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(40)
    spin = window.findChild(QObject, "sleeveThreshold")
    assert spin.property("value") == 90
    capture(window, "ai-sleeve-settings.png")
    spin.setProperty("value", 94)
    click(window, window.findChild(QObject, "saveAiThresholds"))
    assert backend.category_thresholds == {"sleeve": .94, "length": .9}
    assert backend.repository.setting("category_thresholds")["sleeve"] == .94
