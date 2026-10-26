from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .domain import Task
from .recent import RecentTask
from .sheets import HEADERS, GoogleSheets, build_plan, spreadsheet_id
from .widgets import Job


class RecentTasksDialog(QDialog):
    def __init__(self, parent, records: list[RecentTask], directory: Path):
        super().__init__(parent)
        self.setWindowTitle("작업 이어하기")
        self.resize(760, 450)
        self.records = records
        self.chosen: Path | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("날짜와 사진 폴더를 보고 이어서 작업할 항목을 선택하세요."))
        self.table = QTableWidget(len(records), 4)
        self.table.setHorizontalHeaderLabels(["촬영일", "사진 폴더", "옷 수", "최근 저장"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        for row, record in enumerate(records):
            values = [record.shooting_date, record.source_name,
                      f"{record.count}벌" if not record.error else record.error, record.saved_at]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(record.path) + (f"\n{record.error}" if record.error else ""))
                self.table.setItem(row, col, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        if not records:
            layout.addWidget(QLabel("저장된 작업이 없습니다. 사진 폴더를 불러와 새 작업을 시작하세요."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("닫기")
        self.open_button = buttons.addButton("선택한 작업 열기", QDialogButtonBox.ButtonRole.AcceptRole)
        browse = buttons.addButton("다른 작업 파일 찾기", QDialogButtonBox.ButtonRole.ActionRole)

        def browse_file():
            path, _ = QFileDialog.getOpenFileName(self, "기존 작업 파일 열기", str(directory),
                                                "외투 작업 (*.coat.json);;JSON (*.json)")
            if path:
                self.chosen = Path(path)
                self.accept()
        browse.clicked.connect(browse_file)
        buttons.rejected.connect(self.reject)
        self.open_button.clicked.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self.update_selection)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(buttons)
        if records:
            self.table.selectRow(0)
        self.update_selection()

    def update_selection(self):
        row = self.table.currentRow()
        self.open_button.setEnabled(row >= 0 and not self.records[row].error)

    def open_selected(self):
        row = self.table.currentRow()
        if row >= 0 and not self.records[row].error:
            self.chosen = self.records[row].path
            self.accept()


class SheetTransferDialog(QDialog):
    target_selected = Signal(str, str)

    def __init__(self, parent, task: Task, token: Path, api_factory=None):
        super().__init__(parent)
        self.setWindowTitle("시트로 보내기")
        self.resize(960, 670)
        self.task = task.clone()
        self.api_factory = api_factory or (lambda: GoogleSheets(token))
        self.plan = None
        self.book_id = ""
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        layout = QVBoxLayout(self)
        self.scope = QLabel(
            f"촬영일 {task.shooting_date[:4]}-{task.shooting_date[4:6]}-{task.shooting_date[6:]} "
            f"· 전송 대상 {sum(not g.excluded for g in task.garments)}벌 "
            f"· 제외 {sum(g.excluded for g in task.garments)}벌\n"
            "현재 작업의 사용 중인 옷 전체를 확인합니다. 빈 입력 항목은 기존 시트 값을 유지합니다."
        )
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        form = QFormLayout()
        self.url = QLineEdit(task.sheet_url)
        self.url.setPlaceholderText("Google 스프레드시트 주소")
        form.addRow("스프레드시트", self.url)
        self.tab = QComboBox()
        self.tab.setEnabled(False)
        form.addRow("날짜 탭", self.tab)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.load_button = QPushButton("시트 불러오기")
        self.load_button.setDefault(True)
        self.preview_button = QPushButton("미리보기 갱신")
        self.preview_button.setEnabled(False)
        actions.addWidget(self.load_button)
        actions.addWidget(self.preview_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel("주소를 확인하고 시트를 불러오세요. 미리보기만으로는 시트 값이 바뀌지 않습니다.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["시트 행", "열", "외투코드", "현재 값", "입력할 값"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText("닫기")
        self.close_button.setAutoDefault(False)
        self.send_button = buttons.addButton("확인한 내용으로 입력", QDialogButtonBox.ButtonRole.ActionRole)
        self.send_button.setProperty("primary", True)
        self.send_button.setEnabled(False)
        self.send_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        self.load_button.clicked.connect(self.load_tabs)
        self.preview_button.clicked.connect(self.preview)
        self.send_button.clicked.connect(self.send)
        self.url.textChanged.connect(self.address_changed)
        self.tab.currentIndexChanged.connect(self.tab_changed)
        layout.addWidget(buttons)

    def invalidate(self, *_):
        self.plan = None
        self.send_button.setEnabled(False)
        self.table.setRowCount(0)

    def address_changed(self, *_):
        self.invalidate()
        self.book_id = ""
        self.tab.clear()
        self.tab.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.status.setText("주소가 바뀌었습니다. 시트를 다시 불러오세요.")

    def tab_changed(self, *_):
        self.invalidate()
        self.preview_button.setEnabled(bool(self.book_id and self.tab.currentIndex() >= 0 and not self.busy))
        if self.book_id:
            self.status.setText("날짜 탭을 확인하고 미리보기를 갱신하세요.")

    def job(self, message, function, done):
        if self.busy:
            return
        self.busy = True
        self.status.setText(message)
        for widget in (self.url, self.tab, self.load_button, self.preview_button, self.send_button, self.close_button):
            widget.setEnabled(False)
        worker = Job(function)
        self.current_job = worker

        def unlock():
            self.busy = False
            self.url.setEnabled(True)
            self.load_button.setEnabled(True)
            self.close_button.setEnabled(True)
            self.tab.setEnabled(bool(self.book_id and self.tab.count()))
            self.preview_button.setEnabled(bool(self.book_id and self.tab.currentIndex() >= 0))

        def finished(result):
            unlock()
            done(result)

        def failed(message):
            unlock()
            self.invalidate()
            self.status.setText(message)
        worker.signals.done.connect(finished)
        worker.signals.error.connect(failed)
        self.pool.start(worker)

    def load_tabs(self):
        try:
            book_id = spreadsheet_id(self.url.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.invalidate()

        def ready(tabs):
            self.book_id = book_id
            self.tab.blockSignals(True)
            self.tab.clear()
            self.tab.addItems(tabs)
            index = self.tab.findText(self.task.shooting_date)
            self.tab.setCurrentIndex(index)
            self.tab.blockSignals(False)
            self.tab.setEnabled(bool(tabs))
            self.preview_button.setEnabled(index >= 0)
            if index < 0:
                self.status.setText(f"촬영일과 같은 '{self.task.shooting_date}' 탭이 없습니다. 시트에서 해당 날짜의 빈 양식을 준비하세요.")
            else:
                self.preview()
        self.job("날짜 탭을 불러오는 중…", lambda _: self.api_factory().tabs(book_id), ready)

    def preview(self):
        if not self.book_id or self.tab.currentIndex() < 0:
            return
        self.invalidate()
        tab = self.tab.currentText()
        if tab != self.task.shooting_date:
            self.status.setText("선택한 날짜 탭과 촬영일이 다릅니다. 촬영일과 같은 탭을 선택하세요.")
            return
        self.target_selected.emit(self.url.text().strip(), tab)

        def ready(plan):
            self.plan = plan
            self.table.setRowCount(len(plan.changes))
            for row, change in enumerate(plan.changes):
                for col, key in enumerate(("row", "column", "code", "before", "after")):
                    value = str(change[key])
                    if key == "column":
                        value = f"{HEADERS.get(ord(value) - 65, value)} ({value})"
                    self.table.setItem(row, col, QTableWidgetItem(value))
            for col, width in enumerate((75, 145, 95, 175)):
                self.table.setColumnWidth(col, width)
            self.status.setText(
                f"대상 {tab} · 새 옷 {plan.new_count}벌 · 기존 옷 수정 {len({c['code'] for c in plan.changes}) - plan.new_count}벌 "
                f"· 변경 {len(plan.changes)}셀 · 행 확장 {plan.extended_rows}행\n"
                + ("변경할 값이 없습니다." if not plan.changes else "아래 내용을 확인한 뒤 입력하세요. 이미지 수식과 기존 양식을 유지합니다.")
            )
            self.send_button.setEnabled(bool(plan.changes))
        self.job("기존 값과 입력할 내용을 비교하는 중…",
                 lambda _: build_plan(self.api_factory().read(self.book_id, tab), self.task.garments), ready)

    def send(self):
        if not self.plan or not self.send_button.isEnabled() or self.busy:
            return
        plan, book_id = self.plan, self.book_id
        self.send_button.setEnabled(False)

        def ready(result):
            self.plan = None
            self.status.setText(result + "\n다시 보내려면 미리보기를 갱신하세요.")
        self.job("시트 변경 여부를 확인하고 입력하는 중…", lambda _: self.api_factory().apply(book_id, plan), ready)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            event.accept()
