import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QUrl, QPoint, QPointF, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from coatdesk_v2.backend import Backend
from coatdesk_v2.images import Photos
from coatdesk_v2.scanner import scan
from test_v2_workspace import application, photo
from test_v2_workspace import wait_for
from test_v2_sheets import BOOK, FakeApi


def test_qml_review_photo_zoom_dialogs_and_small_screen_have_no_runtime_warnings(tmp_path):
    app = application()
    QQuickStyle.setStyle("Basic")
    for code in ("0001", "0002", "0003", "0004", "0005"):
        for angle in range(3):
            photo(tmp_path / "photos", code, angle)
    backend = Backend(tmp_path / "data")
    key = backend.repository.create("UI 검증", "", tmp_path / "photos", 1, 5, "", scan(tmp_path / "photos", 1, 5))
    backend.openWorkspace(key)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.addImageProvider("photos", Photos())
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "coatdesk_v2/qml/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    QTest.qWait(250)
    backend.selectPage()
    backend.batchFields("코트", "여성", "성인", "M")
    backend.confirmSelected()
    assert backend.state["counts"]["confirmed"] == 4
    QTest.qWait(50)
    def find_item(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            result = find_item(child, name)
            if result is not None:
                return result
        return None
    picture = find_item(window.contentItem(), "photo_0001")
    assert picture is not None
    point = picture.mapToScene(QPointF(picture.width()/2, picture.height()/2))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(point.x()), int(point.y())))
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert backend.page == 1
    for mode, front in (("review", True), ("photos", True), ("photos", False)):
        backend.setMode(mode)
        backend.viewOptions(4, 2, front, 150)
        window.resize(1000, 720)
        QTest.qWait(100)
        assert not window.grabWindow().isNull()
    for name in ("createDialog", "sourceDialog", "batchDialog", "viewDialog", "headDialog", "zoomDialog", "transferDialog"):
        dialog = window.findChild(QObject, name)
        assert dialog is not None
        QMetaObject.invokeMethod(dialog, "open")
        QTest.qWait(30)
        QMetaObject.invokeMethod(dialog, "close")
    QMetaObject.invokeMethod(window, "showZoom", Q_ARG("QVariant", "0001"))
    QTest.qWait(80)
    backend.nextGarment(1)
    QTest.qWait(80)
    assert backend.current == "0002"
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    QTest.qWait(50)
    backend.shutdown()
    assert app


def test_qml_sheet_dialog_keeps_preview_and_completes_fake_transfer(tmp_path):
    app = application()
    QQuickStyle.setStyle("Basic")
    photo(tmp_path / "photos", "0001")
    api = FakeApi()
    backend = Backend(tmp_path / "data", api_factory=lambda: api)
    key = backend.repository.create("전송 검증", "", tmp_path / "photos", 1, 1, "", scan(tmp_path / "photos", 1, 1))
    backend.openWorkspace(key)
    backend.selectPage()
    backend.batchFields("코트", "여성", "성인", "M")
    backend.confirmSelected()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.addImageProvider("photos", Photos())
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "coatdesk_v2/qml/Main.qml")))
    window = engine.rootObjects()[0]
    backend.loadTabs("https://docs.google.com/spreadsheets/d/"+BOOK)
    wait_for(lambda: not backend.jobs)
    dialog = window.findChild(QObject, "transferDialog")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(50)
    backend.preview("https://docs.google.com/spreadsheets/d/"+BOOK, 7, "20261024")
    wait_for(lambda: not backend.jobs)
    QTest.qWait(50)
    assert backend.plan is not None, backend.error
    backend.send()
    wait_for(lambda: not backend.jobs)
    assert len(api.writes) == 1
    assert backend.last_url.startswith("https://docs.google.com/")
    assert not backend.error
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    QTest.qWait(80)
    backend.shutdown()
    assert app
