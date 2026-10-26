"""Exercise the approved workspace UI with synthetic photos and no online calls."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from coatdesk_v2.backend import Backend
from coatdesk_v2.images import Photos
from coatdesk_v2.scanner import scan
from test_v2_workspace import application, photo, wait_for


def visual(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        result = visual(child, name)
        if result is not None:
            return result
    return None


def click(window, item):
    assert item is not None
    assert item.property("visible") and item.property("enabled"), item.objectName()
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
    QTest.qWait(70)


def select_option(window, item, index):
    click(window, item)
    QTest.keyClick(window, Qt.Key_Home)
    for _ in range(index):
        QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    QTest.qWait(60)


def type_text(window, text):
    for character in text:
        QTest.keyClick(window, ord(character.upper()), Qt.ShiftModifier if character.isupper() else Qt.NoModifier)


def capture(window, filename):
    folder = os.environ.get("COATDESK_UI_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        window.grabWindow().save(str(Path(folder) / filename))


@pytest.fixture
def ui(tmp_path):
    app = application()
    QQuickStyle.setStyle("Basic")
    font = Path("C:/Windows/Fonts/malgun.ttf")
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    backend = Backend(tmp_path / "data")
    for code in ("0001", "0002", "0003", "0004"):
        for angle in range(3):
            photo(tmp_path / "photos", code, angle)
    keys = [backend.repository.create(name, "", tmp_path / "photos", 1, 4, "", scan(tmp_path / "photos", 1, 4))
            for name in ("첫 번째 작업", "두 번째 작업")]
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.addImageProvider("photos", Photos())
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "coatdesk_v2/qml/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    QTest.qWait(120)
    yield backend, window, keys, tmp_path
    window.close()
    engine.deleteLater()
    QTest.qWait(40)
    backend.shutdown()
    assert not warnings, warnings
    assert app


def test_home_tiles_create_modal_and_workspace_tabs_restore_view(ui):
    backend, window, keys, tmp_path = ui
    assert backend.state["home"]
    assert not window.findChild(QObject, "createDialog").property("opened")
    capture(window, "home-1380.png")
    click(window, visual(window.contentItem(), "newWorkspaceTile"))
    assert window.findChild(QObject, "createDialog").property("opened")
    capture(window, "create-1380.png")
    window.findChild(QObject, "workspaceName").setProperty("text", "새 작업")
    window.findChild(QObject, "workspaceSource").setProperty("text", str(tmp_path / "photos"))
    window.findChild(QObject, "workspaceEnd").setProperty("text", "4")
    click(window, window.findChild(QObject, "createWorkspaceButton"))
    wait_for(lambda: not backend.jobs)
    assert not backend.state["home"] and backend.workspace.name == "새 작업"
    created = backend.workspace.id
    click(window, window.findChild(QObject, "homeButton"))
    click(window, visual(window.contentItem(), "homeWorkspace_" + keys[0]))
    backend.setSearch("0002")
    backend.setMode("photos")
    click(window, window.findChild(QObject, "homeButton"))
    click(window, visual(window.contentItem(), "homeWorkspace_" + keys[1]))
    assert backend.search == "" and backend.mode == "review"
    click(window, visual(window.contentItem(), "workspaceTab_" + keys[0]))
    assert backend.search == "0002" and backend.mode == "photos"
    assert {r["id"] for r in backend.state["openWorkspaces"]} == {created, *keys}
    click(window, window.findChild(QObject, "homeButton"))
    click(window, visual(window.contentItem(), "workspaceTab_" + keys[0]))
    assert len(backend.state["openWorkspaces"]) == 3
    click(window, window.findChild(QObject, "addWorkspaceTab"))
    click(window, window.findChild(QObject, "newWorkspaceTabAction"))
    assert window.findChild(QObject, "createDialog").property("opened")


def seed_review(backend, key):
    def change(g):
        g.fields = {"category": "코트", "size": "M", "gender": "남성", "age": "성인"}
        if g.code == "0001":
            g.fields["age"] = "유아동"
        elif g.code == "0002":
            g.fields.update(size="L", gender="여성")
            g.suggestion = {"scores": {"shape": .7}, "held": ["성별/성인·아동"]}
        elif g.code == "0003":
            g.fields.update(size="L", gender="여성")
    backend.repository.edit(key, ["0001", "0002", "0003", "0004"], change)
    backend.openWorkspace(key)
    QTest.qWait(100)


def test_only_required_cards_confirm_and_editing_keeps_review_pending(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    window.resize(1000, 720)
    QTest.qWait(100)
    child = visual(window.contentItem(), "gender_0001_combo")
    assert child.property("displayText") == "남아"
    assert not visual(window.contentItem(), "confirm_0003").property("visible")
    pending = visual(window.contentItem(), "confirm_0002")
    field = visual(window.contentItem(), "size_0002_text")
    # The button belongs immediately below the fields, within the existing card.
    field_top = field.mapToScene(QPointF(0, 0)).y()
    confirm_top = pending.mapToScene(QPointF(0, 0)).y()
    assert 0 < confirm_top - field_top < 160
    assert pending.mapToScene(QPointF(0, pending.height())).y() < window.height() - 48
    click(window, field)
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    type_text(window, "XL")
    click(window, pending)
    garment = backend.repository.load(keys[0]).garments[1]
    assert garment.fields["size"] == "XL"
    assert garment.export_ready
    backend.setField("0002", "gender", "남성")
    QTest.qWait(60)
    assert visual(window.contentItem(), "confirm_0002").property("visible")
    click(window, visual(window.contentItem(), "confirm_0001"))
    assert backend.repository.load(keys[0]).garments[0].export_ready
    # A normal populated garment requires no confirmation and no result notice.
    assert backend.repository.load(keys[0]).garments[2].export_ready
    # Leaving a typed field for a photo must save the value and still select it.
    click(window, visual(window.contentItem(), "size_0003_text"))
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    type_text(window, "S")
    click(window, visual(window.contentItem(), "photo_0004"))
    assert backend.repository.load(keys[0]).garments[2].fields["size"] == "S"
    assert backend.selected == {"0004"}
    capture(window, "workspace-1000.png")


def test_three_classification_rows_combine_with_search_and_review(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    initial_height = visual(window.contentItem(), "card_0002").height()
    click(window, window.findChild(QObject, "classificationButton"))
    popup = window.findChild(QObject, "classificationPopup")
    assert popup.property("opened")
    for name, value in (("genderFilter", "여성"), ("sizeFilter", "L"), ("ageFilter", "성인")):
        item = window.findChild(QObject, name)
        index = list(item.property("model")).index(value)
        select_option(window, item, index)
    assert backend.state["classFilters"] == {"gender": "여성", "size": "L", "age": "성인"}
    assert [g.code for g in backend.filtered] == ["0002", "0003"]
    assert abs(visual(window.contentItem(), "card_0002").height() - initial_height) < 2
    capture(window, "classification-1380.png")
    QMetaObject.invokeMethod(popup, "close")
    click(window, window.findChild(QObject, "reviewFilterButton"))
    assert [g.code for g in backend.filtered] == ["0002"]
    search = window.findChild(QObject, "garmentSearch")
    click(window, search)
    type_text(window, "0003")
    assert not backend.filtered
    click(window, window.findChild(QObject, "reviewFilterButton"))
    assert [g.code for g in backend.filtered] == ["0003"]
    click(window, window.findChild(QObject, "photoOnlySwitch"))
    assert backend.mode == "photos"
    click(window, window.findChild(QObject, "photoOnlySwitch"))
    assert backend.mode == "review"
    click(window, window.findChild(QObject, "classificationButton"))
    click(window, window.findChild(QObject, "resetClassFilters"))
    assert backend.state["classFilters"] == {"gender": "", "size": "", "age": ""}


def test_workspace_navigation_is_disabled_while_background_job_is_running(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    backend.jobs[999] = object()
    backend.changed.emit()
    QTest.qWait(70)
    try:
        for name in ("homeButton", "addWorkspaceTab", "workspaceMenuButton", "analyzeButton"):
            assert not window.findChild(QObject, name).property("enabled"), name
        assert not visual(window.contentItem(), "workspaceTab_" + keys[0]).property("enabled")
    finally:
        backend.jobs.pop(999)
        backend.changed.emit()
