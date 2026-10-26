import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QThread, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from PIL import Image

from coatdesk.domain import Garment, Task, load_task
from coatdesk.window import MainWindow


def test_selection_batch_edit_undo_view_paging_and_save(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.task = Task("", [Garment(f"{i:04}", "", {}) for i in range(1, 14)], selected=[0])
    window.task_path = tmp_path / "ui.coat.json"
    window.show()
    window.refresh()
    app.processEvents()
    window.select(3, Qt.KeyboardModifier.ShiftModifier)
    assert window.task.selected == [0, 1, 2, 3]
    window.inputs["size"].setCurrentIndex(window.inputs["size"].findData("M"))
    window.apply_fields()
    assert all(g.fields == {"size": "M"} for g in window.task.garments[:4])
    window.undo()
    assert not any(g.fields for g in window.task.garments)
    window.mode.setCurrentIndex(1)
    QTest.qWait(50)
    assert len([r for r in window.grid.regions if r[2] == "0"]) == 24  # image + header, 12 garments
    window.mode.setCurrentIndex(0)
    window.change_page(1)
    assert window.task.page == 1
    window.search.setText("0013")
    window.find_code()
    assert window.task.selected == [12]
    assert window.task.page == 3
    window.toggle_excluded()
    assert window.task.garments[12].excluded
    window.save()
    assert load_task(window.task_path).garments[12].excluded
    window.close()


def test_editable_size_accepts_custom_value(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.task = Task("", [Garment("0001", "", {})], selected=[0])
    window.task_path = tmp_path / "custom.coat.json"
    window.refresh()
    window.inputs["size"].setEditText("140/150")
    window.apply_fields()
    assert window.task.garments[0].fields["size"] == "140/150"
    window.close()
    app.processEvents()


def test_background_result_arrives_on_ui_thread(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    results = []
    window.run_job("test", lambda progress: 42,
                   lambda result: results.append((result, QThread.currentThread() == app.thread())))
    for _ in range(100):
        QTest.qWait(10)
        if results:
            break
    assert results == [(42, True)]
    assert not window.busy
    window.close()


def test_zoom_arrows_and_drag_preserve_selection(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.task = Task("", [Garment(f"{i:04}", "", {}) for i in range(13)], selected=[0])
    window.task_path = tmp_path / "navigation.coat.json"
    window.show()
    window.refresh()
    app.processEvents()
    window.photo_zoom.setValue(200)
    assert load_task(window.task_path).photo_zoom == 200
    window.grid.setFocus()
    QTest.keyClick(window.grid, Qt.Key.Key_Right)
    assert window.task.page == 1
    QTest.mousePress(window.grid, Qt.MouseButton.LeftButton, pos=QPoint(240, 100))
    QTest.mouseRelease(window.grid, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    assert window.task.page == 2
    assert window.task.selected == [0]
    QTest.mousePress(window.grid, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mouseRelease(window.grid, Qt.MouseButton.LeftButton, pos=QPoint(240, 100))
    assert window.task.page == 1
    QTest.keyClick(window.grid, Qt.Key.Key_Up)
    QTest.keyClick(window.grid, Qt.Key.Key_Left)
    assert window.task.page == 0
    window.close()


def test_import_autosaves_to_fixed_folder_without_overwriting_old_work(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "photos"
    garment = source / "0_0001"
    garment.mkdir(parents=True)
    for angle in range(3):
        Image.new("RGB", (30, 50), "white").save(garment / f"img_0_0_{angle}.jpg")
    storage = tmp_path / "Desktop" / "외투 작업실 작업"
    window = MainWindow(tmp_path / "settings", task_dir=storage)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(source))

    def unexpected_save_dialog(*args):
        raise AssertionError("Import must not ask the user for a JSON path")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", unexpected_save_dialog)
    window.import_photos()
    for _ in range(200):
        QTest.qWait(10)
        if not window.busy:
            break
    assert window.task is not None
    first = window.task_path
    assert first.parent == storage
    assert load_task(first).garments[0].code == "0001"
    window.task.garments[0].fields["size"] = "M"
    window.save()
    window.import_photos()
    for _ in range(200):
        QTest.qWait(10)
        if not window.busy:
            break
    assert window.task_path != first
    assert window.task_path.parent == storage
    assert len(list(storage.glob("*.coat.json"))) == 2
    assert load_task(first).garments[0].fields["size"] == "M"
    assert not window.task.garments[0].fields
    assert (garment / "img_0_0_0.jpg").exists()
    window.close()
    app.processEvents()
