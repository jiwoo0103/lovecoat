import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from coatdesk.dialogs import RecentTasksDialog, SheetTransferDialog
from coatdesk.domain import Garment, Task, save_task
from coatdesk.recent import recent_tasks
from coatdesk.sheets import HEADERS, Snapshot
from coatdesk.window import MainWindow


def application():
    return QApplication.instance() or QApplication([])


def wait_for(predicate):
    for _ in range(250):
        QTest.qWait(10)
        if predicate():
            return
    assert predicate(), "UI operation did not finish"


def test_recent_list_recovers_dates_folders_and_legacy_paths_without_modification(tmp_path):
    directory = tmp_path / "Desktop"
    directory.mkdir()
    first = directory / "first.coat.json"
    second = tmp_path / "legacy.coat.json"
    save_task(first, Task("C:/photos/batch1", [Garment("0001", "", {})], shooting_date="20261020"))
    save_task(second, Task("C:/photos/batch2", [], shooting_date="20261021"))
    broken = directory / "broken.coat.json"
    broken.write_text("broken", encoding="utf-8")
    before = first.read_bytes(), second.read_bytes(), broken.read_bytes()
    rows = recent_tasks(directory, [str(second), str(first), str(tmp_path / "gone.json")])
    assert len(rows) == 3
    by_path = {r.path: r for r in rows}
    assert by_path[first].shooting_date == "2026-10-20"
    assert by_path[first].source_name == "batch1"
    assert by_path[first].count == 1
    assert by_path[second].shooting_date == "2026-10-21"
    assert by_path[broken].error
    assert before == (first.read_bytes(), second.read_bytes(), broken.read_bytes())


def test_recent_dialog_selects_task_without_json_browser(tmp_path):
    app = application()
    path = tmp_path / "saved.coat.json"
    save_task(path, Task("C:/photos/clothes", [], shooting_date="20261021"))
    dialog = RecentTasksDialog(None, recent_tasks(tmp_path, []), tmp_path)
    assert dialog.table.item(0, 0).text() == "2026-10-21"
    assert dialog.table.item(0, 1).text() == "clothes"
    dialog.open_selected()
    assert dialog.chosen == path
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()
    app.processEvents()


def test_tabs_panel_collapse_and_view_options_preserve_work(tmp_path):
    app = application()
    window = MainWindow(tmp_path)
    window.task_path = tmp_path / "work.coat.json"
    window.task = Task("photos", [Garment("0001", "", {})], selected=[0])
    window.show()
    window.refresh()
    window.inputs["size"].setCurrentIndex(window.inputs["size"].findData("M"))
    window.tabs.setCurrentIndex(1)
    window.recognition_settings_toggle.setChecked(True)
    app.processEvents()
    assert window.recognition_settings.isVisible()
    window.tabs.setCurrentIndex(0)
    assert window.inputs["size"].currentData() == "M"
    window.apply_fields()
    window.panel_toggle.click()
    assert window.side_scroll.isHidden()
    window.panel_toggle.click()
    assert not window.side_scroll.isHidden()
    window.rows.setValue(2)
    assert window.task.rows == 2
    assert window.task.garments[0].fields["size"] == "M"
    window.task.garments.append(Garment("0002", "", {}))
    window.select(1, Qt.KeyboardModifier.ShiftModifier)
    assert window.apply_button.text() == "선택한 2벌에 적용"
    window.close()


def snapshot():
    cells = {(1, c): {"userEnteredValue": {"stringValue": label}} for c, label in HEADERS.items()}
    for row in range(2, 8):
        cells[row, 3] = {"userEnteredValue": {"formulaValue": f'=IF(C{row+1}<>"", "0_" & C{row+1}, "")'}}
    return Snapshot("20261021", 1, 8, cells)


class FakeSheets:
    def __init__(self):
        self.tab_names = ["20261020", "20261021"]
        self.reads = []
        self.writes = []

    def tabs(self, book_id):
        return self.tab_names

    def read(self, book_id, tab):
        self.reads.append((book_id, tab))
        return copy.deepcopy(snapshot())

    def apply(self, book_id, plan):
        self.writes.append((book_id, plan))
        return "입력 확인 완료"


def transfer(tmp_path):
    app = application()
    api = FakeSheets()
    garments = [Garment("0001", "", {}, fields={"size": "M"}), Garment("0002", "", {}, excluded=True),
                Garment("0003", "", {}, fields={"gender": "여성"})]
    task = Task("photos", garments, shooting_date="20261021", selected=[0],
                sheet_url="https://docs.google.com/spreadsheets/d/" + "a" * 24)
    dialog = SheetTransferDialog(None, task, tmp_path / "token", api_factory=lambda: api)
    dialog.show()
    app.processEvents()
    return app, api, dialog


def test_sheet_dialog_preview_includes_all_active_clothes_and_sends_only_after_confirmation(tmp_path):
    app, api, dialog = transfer(tmp_path)
    dialog.load_tabs()
    wait_for(lambda: dialog.plan is not None and not dialog.busy)
    assert dialog.tab.currentText() == "20261021"
    assert dialog.plan.new_count == 2
    assert {c["code"] for c in dialog.plan.changes} == {"0001", "0003"}
    assert api.writes == []
    assert dialog.table.rowCount() > 0
    dialog.send()
    wait_for(lambda: not dialog.busy)
    assert len(api.writes) == 1
    assert not dialog.send_button.isEnabled()
    dialog.send()
    assert len(api.writes) == 1
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize("changed", ["address", "date"])
def test_changing_sheet_target_invalidates_old_preview(tmp_path, changed):
    app, api, dialog = transfer(tmp_path)
    dialog.load_tabs()
    wait_for(lambda: dialog.plan is not None and not dialog.busy)
    if changed == "address":
        dialog.url.setText("https://docs.google.com/spreadsheets/d/" + "b" * 24)
    else:
        dialog.tab.setCurrentIndex(0)
    assert dialog.plan is None
    assert dialog.table.rowCount() == 0
    assert not dialog.send_button.isEnabled()
    dialog.send()
    assert api.writes == []
    dialog.close()
    app.processEvents()


def test_missing_date_tab_keeps_send_disabled(tmp_path):
    app, api, dialog = transfer(tmp_path)
    api.tab_names = ["20261020"]
    dialog.load_tabs()
    wait_for(lambda: not dialog.busy)
    assert "탭이 없습니다" in dialog.status.text()
    assert dialog.plan is None
    assert api.reads == api.writes == []
    dialog.close()
    app.processEvents()
