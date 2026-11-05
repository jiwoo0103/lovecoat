"""Workspace lifecycle, export isolation and 800-garment integrity checks."""
import hashlib
import json
import threading
import time

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QEventLoop, QMetaObject, QObject, QPointF, QTimer, Qt
from PySide6.QtTest import QTest

from coatdesk_v2 import excel
from coatdesk_v2.domain import Garment
from coatdesk_v2.storage import Repository, encode
from test_v2_ui_flow import capture, click, type_text, visual
from test_v2_ui_flow import ui as ui
from test_v2_workspace import application, wait_for


def records(count):
    sizes = ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL"]
    categories = ["자켓", "코트", "조끼", "기타"]
    return [Garment(f"{i:04}", {}, fields={"category": categories[i % 4],
            "gender": "남성" if i % 2 else "여성", "age": "성인", "size": sizes[i % 8]},
            confirmed=True) for i in range(1, count+1)]


def wait_jobs(backend):
    if not backend.jobs:
        return
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    def changed():
        if not backend.jobs:
            loop.quit()
    backend.changed.connect(changed)
    timer.start(15000)
    loop.exec()
    timer.stop()
    backend.changed.disconnect(changed)
    assert not backend.jobs, backend.error


def assert_sheet(path, title, expected):
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        actual = list(book[title].iter_rows(min_row=3, max_row=len(expected)+2,
                                          min_col=2, max_col=14, values_only=True))
        assert [[v or "" for v in row] for row in actual] == expected
        assert book[title].max_row >= len(expected)+2
    finally:
        book.close()


def test_800_export_backup_restore_delete_and_existing_sheet_preservation(tmp_path):
    repo = Repository(tmp_path / "data")
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original photo sentinel")
    before_photo = hashlib.sha256(photo.read_bytes()).hexdigest()
    garments = records(800)
    garments[0].photos = {"0": photo.name}
    key = repo.create("800벌", "2026-10-29", tmp_path, 1, 800, "", garments)
    target = tmp_path / "800.xlsx"
    durations = []
    for index in range(2):
        plan = excel.build_plan(repo.load(key), target, "20261029")
        if index:
            before = target.read_bytes()
            with excel.ZipFile(target) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
        started = time.perf_counter()
        messages = []
        excel.save(repo, plan, messages.append)
        durations.append(time.perf_counter()-started)
        assert len(messages) == 4
        assert_sheet(target, plan.title, plan.rows)
        if index:
            assert target.read_bytes() != before
            with excel.ZipFile(target) as archive:
                assert all(archive.read(name) == value for name, value in parts.items()
                           if name not in excel.MODIFIED_PARTS)
    backup = tmp_path / "800-backup.json"
    repo.backup_workspace(key, backup)
    with pytest.raises(ValueError, match="같은 이름"):
        repo.backup_workspace(key, backup)
    restored = repo.restore_workspace(backup)
    assert restored != key
    assert [g.to_dict() for g in repo.load(restored).garments] == [g.to_dict() for g in repo.load(key).garments]
    repo.rename(restored, "복원한 작업")
    assert repo.load(restored).name == "복원한 작업"
    repo.set_settings({"open_workspaces": [key, restored], "last_workspace": key})
    repo.record_export("export-history", key, "done", {})
    final_excel = target.read_bytes()
    repo.delete(key)
    assert repo.setting("open_workspaces") == [restored] and repo.setting("last_workspace") == ""
    assert len(repo.load(restored).garments) == 800
    assert target.read_bytes() == final_excel
    assert hashlib.sha256(photo.read_bytes()).hexdigest() == before_photo
    assert not list(tmp_path.glob(".coatdesk-backup-*"))
    with pytest.raises(ValueError):
        repo.load(key)
    with repo.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT COUNT(*) FROM exports WHERE workspace_id=?", (key,)).fetchone()[0] == 0
    print(f"800 garments save seconds: new={durations[0]:.3f}, append={durations[1]:.3f}")


def test_restore_rejects_unsafe_photo_paths_without_changing_existing_workspace(tmp_path):
    repo = Repository(tmp_path / "data")
    key = repo.create("original", "", tmp_path, 1, 1, "", records(1))
    target = tmp_path / "backup.json"
    repo.backup_workspace(key, target)
    data = json.loads(target.read_text(encoding="utf-8"))
    data["garments"][0]["photos"] = {"0": "../outside.jpg"}
    target.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="정상 작업 백업"):
        repo.restore_workspace(target)
    assert len(repo.recent()) == 1 and repo.load(key).name == "original"


def expand_ui(backend, key, count=800):
    photos = backend.repository.load(key).garments[0].photos
    garments = records(count)
    for garment in garments:
        garment.photos = dict(photos)
    with backend.repository.connection() as db:
        db.execute("DELETE FROM garments WHERE workspace_id=?", (key,))
        db.executemany("INSERT INTO garments VALUES(?,?,?,?)",
                       [(key, g.code, int(g.code), encode(g.to_dict())) for g in garments])
        db.execute("UPDATE workspaces SET end=? WHERE id=?", (count, key))
    backend.openWorkspace(key)


def test_home_management_tabs_selection_and_dialog_design(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    backend.goHome()
    window.resize(1000, 720)
    QTest.qWait(80)
    click(window, visual(window.contentItem(), "workspaceManage_" + keys[0]))
    click(window, visual(window.contentItem(), "renameWorkspace_" + keys[0]))
    window.findChild(QObject, "renameWorkspaceField").setProperty("text", "변경한 작업")
    click(window, window.findChild(QObject, "renameDialogAccept"))
    assert backend.repository.load(keys[0]).name == "변경한 작업"
    click(window, visual(window.contentItem(), "homeWorkspace_" + keys[0]))
    wait_for(lambda: not backend.jobs)
    assert window.findChild(QObject, "clearSelectionButton") is None
    assert "selectedCount" not in backend.state
    click(window, visual(window.contentItem(), "closeWorkspace_" + keys[0]))
    assert backend.show_home and keys[0] not in backend.opened
    assert backend.repository.load(keys[0]).name == "변경한 작업"
    capture(window, "home-management-1000.png")
    click(window, visual(window.contentItem(), "workspaceManage_" + keys[0]))
    click(window, visual(window.contentItem(), "deleteWorkspace_" + keys[0]))
    capture(window, "delete-dialog-1000.png")
    click(window, window.findChild(QObject, "deleteDialogCancel"))
    assert len(backend.repository.recent()) == 2
    click(window, visual(window.contentItem(), "workspaceManage_" + keys[0]))
    click(window, visual(window.contentItem(), "deleteWorkspace_" + keys[0]))
    click(window, window.findChild(QObject, "deleteDialogAccept"))
    assert len(backend.repository.recent()) == 1
    for width, height in ((1000, 720), (1380, 920)):
        window.resize(width, height)
        dialog = window.findChild(QObject, "createDialog")
        QMetaObject.invokeMethod(dialog, "open")
        QTest.qWait(100)
        assert dialog.property("height") < height-48
        capture(window, f"create-unified-{width}.png")
        QMetaObject.invokeMethod(dialog, "close")


def test_800_export_allows_other_workspace_edits_and_protects_source(ui, monkeypatch):
    backend, window, keys, tmp_path = ui
    expand_ui(backend, keys[0])
    backend.openWorkspace(keys[1])
    backend.openWorkspace(keys[0])
    target = tmp_path / "ui-800.xlsx"
    backend.previewExcel(str(target), "800벌")
    wait_for(lambda: not backend.jobs)
    expected = backend.plan.rows
    started, release = threading.Event(), threading.Event()
    real_save = excel.save

    def delayed_save(repository, plan, progress=None):
        started.set()
        assert release.wait(15)
        return real_save(repository, plan, progress)

    monkeypatch.setattr(excel, "save", delayed_save)
    # Open the actual dialog first, then prepare again (opening invalidates a plan).
    QMetaObject.invokeMethod(window.findChild(QObject, "transferDialog"), "open")
    window.findChild(QObject, "excelPathField").setProperty("text", str(target))
    window.findChild(QObject, "newExcelSheetName").setProperty("text", "800벌")
    click(window, window.findChild(QObject, "excelPreviewButton"))
    wait_for(lambda: not backend.jobs)
    for width, height in ((1380, 920), (1000, 720)):
        window.resize(width, height)
        QTest.qWait(80)
        save_button = window.findChild(QObject, "excelSaveButton")
        corner = save_button.mapToScene(QPointF(save_button.width(), save_button.height()))
        assert corner.x() <= width-16 and corner.y() <= height-48
        capture(window, f"export-unified-800-{width}.png")
    preview = window.findChild(QObject, "exportPreviewList")
    QMetaObject.invokeMethod(preview, "positionViewAtEnd")
    QTest.qWait(80)
    assert visual(window.contentItem(), "exportRow_0800") is not None
    click(window, window.findChild(QObject, "excelSaveButton"))
    try:
        wait_for(started.is_set)
        assert not window.findChild(QObject, "transferDialog").property("opened")
        assert backend.state["workspaceLocked"] and not backend.state["navigationBlocked"]
        assert not visual(window.contentItem(), "category_0001_combo").property("enabled")
        backend.automaticPanel()
        assert backend.repository.load(keys[0]).garments[0].confirmed
        search = window.findChild(QObject, "garmentSearch")
        click(window, search)
        type_text(window, "0800")
        assert [g.code for g in backend.filtered] == ["0800"]
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(window, Qt.Key_Backspace)
        assert len(backend.filtered) == 800
        backend.setField("0001", "size", "3XL")
        assert backend.repository.load(keys[0]).garments[0].fields["size"] == "XS"
        backend.dismissError()
        click(window, visual(window.contentItem(), "workspaceTab_" + keys[1]))
        wait_for(lambda: backend.workspace.id == keys[1] and not backend.state["loadingWorkspace"])
        assert not backend.state["workspaceLocked"]
        backend.setField("0001", "size", "L")
        assert backend.repository.load(keys[1]).garments[0].fields["size"] == "L"
        click(window, window.findChild(QObject, "workspaceMenuButton"))
        click(window, window.findChild(QObject, "viewSettingsAction"))
        QMetaObject.invokeMethod(window.findChild(QObject, "viewDialog"), "close")
        QTest.qWait(60)
        click(window, window.findChild(QObject, "homeButton"))
        assert backend.show_home and backend.exporting
        release.set()
        wait_jobs(backend)
        assert not backend.exporting and not backend.export_workspace_id
        assert_sheet(target, "800벌", expected)
        assert backend.repository.load(keys[1]).garments[0].fields["size"] == "L"
        assert backend.workspace.id == keys[1] and backend.show_home
    finally:
        release.set()
        wait_jobs(backend)


def test_single_garment_reanalysis_keeps_other_799_results(ui):
    backend, window, keys, _ = ui
    expand_ui(backend, keys[0])
    calls = []
    class Engine:
        def key(self, workspace, garment):
            return garment.code
        def analyze(self, workspace, garment):
            calls.append(garment.code)
            return {"values": {"size": "M"}}
    backend.engine_factory = Engine
    backend.focusGarment("0003")
    backend.analyzeCurrent()
    wait_for(lambda: not backend.jobs)
    garments = backend.repository.load(keys[0]).garments
    assert calls == ["0003"]
    assert sum(g.analysis_state == "done" for g in garments) == 1
    assert garments[0].fields["size"] == "XS"
    assert application() and window
