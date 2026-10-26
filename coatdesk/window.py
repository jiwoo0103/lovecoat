from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QDate, Qt, QThreadPool
from PySide6.QtGui import QAction, QImageReader, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget,
    QVBoxLayout, QWidget, QWidgetAction,
)

from .domain import FIELDS, LABELS, OPTIONS, Task, atomic_json, boundaries, load_task, save_task
from .desktop import desktop_work_folder
from .dialogs import RecentTasksDialog, SheetTransferDialog
from .recent import recent_tasks
from .photos import crop_head, discover, execute_copy, plan_copy
from .recognition import find_tesseract, recognize, shape_feature
from .sheets import GoogleSheets
from .widgets import CropPicker, Job, PhotoGrid, PreviewDialog, show_zoom

STYLE = """
QMainWindow, QDialog { background: #f6f8fc; }
QWidget { font-family: 'Malgun Gothic'; font-size: 13px; color: #1f3048; }
QPushButton { background: white; border: 1px solid #ccd5e2; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #eaf0fe; border-color: #6993eb; }
QPushButton:disabled { color: #9aa5b5; background: #f3f5f8; }
QPushButton[primary="true"] { background: #2563eb; color: white; border-color: #2563eb; }
QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit {
 background: white; border: 1px solid #ccd5e2; border-radius: 4px; padding: 6px;
}
QLabel#title { font-size: 22px; font-weight: bold; color: #153458; }
QLabel#muted { color: #64748b; }
QLabel#selection { font-size: 17px; font-weight: bold; }
QFrame#panel { background: white; border-radius: 8px; }
QTabWidget::pane { border: 0; background: white; }
QTabBar::tab { padding: 11px 21px; background: #edf2f8; color: #607188; }
QTabBar::tab:selected { background: white; color: #2563eb; border-bottom: 2px solid #2563eb; }
QMenu { background: white; padding: 5px; border: 1px solid #d3dce8; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: #eaf0fe; }
QTableWidget { background: white; border: 1px solid #d3dce8; gridline-color: #edf1f7; }
QStatusBar { background: #edf2fa; padding: 3px; }
"""


def button(text, callback, primary=False):
    result = QPushButton(text)
    result.setProperty("primary", primary)
    result.clicked.connect(callback)
    return result


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, task_dir: Path | None = None):
        super().__init__()
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.task_dir = task_dir if task_dir is not None else desktop_work_folder()
        self.task: Task | None = None
        self.task_path: Path | None = None
        self.undo_stack = []
        self.anchor = 0
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.preferences = {}
        self.preference_path = self.data_dir / "settings.json"
        if self.preference_path.exists():
            try:
                self.preferences = json.loads(self.preference_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        self.setWindowTitle("외투 작업실")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(STYLE)
        self.build_ui()
        self.statusBar().showMessage("사진 폴더를 불러오면 번호별 사진을 바로 볼 수 있습니다.")
        self.refresh()

    def build_ui(self):
        self.body = QWidget()
        self.setCentralWidget(self.body)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(10)
        top = QHBoxLayout()
        title = QLabel("외투 작업실")
        title.setObjectName("title")
        top.addWidget(title)
        top.addSpacing(12)
        top.addWidget(button("사진 불러오기", self.import_photos))
        top.addWidget(button("작업 열기", self.open_task))
        top.addStretch()
        top.addWidget(QLabel("촬영일"))
        self.date = QDateEdit(QDate.currentDate())
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setCalendarPopup(True)
        self.date.dateChanged.connect(self.change_date)
        top.addWidget(self.date)
        self.sheet_button = button("시트로 보내기", self.sheet_preview, True)
        top.addWidget(self.sheet_button)
        more = QPushButton("더보기")
        menu = QMenu(more)
        menu.addAction("사진 한 폴더로 내보내기", self.copy_photos)
        menu.addAction("지금 저장", self.save)
        menu.addSeparator()
        menu.addAction("Google 연결 설정", self.google_settings)
        menu.addAction("도움말", self.help)
        more.setMenu(menu)
        top.addWidget(more)
        layout.addLayout(top)

        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["한 벌씩 3장", "정면만 모아보기"])
        self.mode.setMinimumWidth(155)
        self.mode.currentIndexChanged.connect(self.change_view)
        controls.addWidget(self.mode)
        self.search = QLineEdit()
        self.search.setPlaceholderText("외투코드 검색 · Enter")
        self.search.setMaximumWidth(220)
        self.search.returnPressed.connect(self.find_code)
        controls.addWidget(self.search)
        self.view_button = QPushButton("보기 설정")
        view_menu = QMenu(self.view_button)
        view_options = QWidget()
        view_form = QFormLayout(view_options)
        view_form.setContentsMargins(14, 12, 14, 12)
        self.rows = QSpinBox()
        self.rows.setRange(1, 8)
        self.rows.setValue(4)
        self.rows.valueChanged.connect(self.change_view)
        view_form.addRow("화면 줄 수", self.rows)
        self.photo_zoom = QSpinBox()
        self.photo_zoom.setRange(100, 250)
        self.photo_zoom.setSingleStep(25)
        self.photo_zoom.setSuffix("%")
        self.photo_zoom.setValue(150)
        self.photo_zoom.setToolTip("100%: 전체 사진 · 확대하면 가장자리가 가려질 수 있습니다. 더블클릭하면 전체 사진을 크게 볼 수 있습니다.")
        self.photo_zoom.valueChanged.connect(self.change_zoom)
        view_form.addRow("사진 크기", self.photo_zoom)
        options_action = QWidgetAction(view_menu)
        options_action.setDefaultWidget(view_options)
        view_menu.addAction(options_action)
        self.view_button.setMenu(view_menu)
        controls.addWidget(self.view_button)
        self.panel_toggle = button("입력 패널 접기", self.toggle_panel)
        self.panel_toggle.setCheckable(True)
        controls.addWidget(self.panel_toggle)
        controls.addStretch()
        self.previous_boundary = button("이전 경계", lambda: self.jump_boundary(-1))
        self.next_boundary = button("다음 경계", lambda: self.jump_boundary(1))
        controls.addWidget(self.previous_boundary)
        controls.addWidget(self.next_boundary)
        layout.addLayout(controls)

        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.grid = PhotoGrid()
        self.grid.selected.connect(self.select)
        self.grid.opened.connect(self.open_photo)
        self.grid.page_requested.connect(self.change_page)
        self.splitter.addWidget(self.grid)
        self.panel = QFrame()
        self.panel.setObjectName("panel")
        self.panel.setMinimumWidth(280)
        self.panel.setMaximumWidth(360)
        editor = QVBoxLayout(self.panel)
        editor.setContentsMargins(14, 15, 14, 12)
        self.selection_label = QLabel("사진을 선택하세요")
        self.selection_label.setObjectName("selection")
        editor.addWidget(self.selection_label)
        self.details = QLabel("Shift: 연속 선택 · Ctrl: 여러 벌 선택")
        self.details.setWordWrap(True)
        self.details.setObjectName("muted")
        editor.addWidget(self.details)
        self.tabs = QTabWidget()
        editor.addWidget(self.tabs, 1)
        info_page = QWidget()
        info = QVBoxLayout(info_page)
        info.setContentsMargins(0, 16, 0, 0)
        info.setSpacing(12)
        info_hint = QLabel("변경할 항목만 선택하세요.\n빈 항목은 기존 값을 유지합니다.")
        info_hint.setWordWrap(True)
        info_hint.setObjectName("muted")
        info.addWidget(info_hint)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.inputs = {}
        for key in FIELDS:
            combo = QComboBox()
            combo.addItem("— 변경하지 않음 —", "")
            for value in OPTIONS[key]:
                combo.addItem(value, value)
            combo.setEditable(key == "size")
            self.inputs[key] = combo
            form.addRow(LABELS[key], combo)
        info.addLayout(form)
        self.apply_button = button("선택한 옷에 적용", self.apply_fields, True)
        info.addWidget(self.apply_button)
        self.previous_button = button("이전 옷 값 적용", self.apply_previous)
        info.addWidget(self.previous_button)
        row = QHBoxLayout()
        self.undo_button = button("되돌리기", self.undo)
        self.undo_button.setToolTip("Ctrl+Z · 앱에서 입력한 내용 되돌리기")
        row.addWidget(self.undo_button)
        self.selection_actions = QPushButton("선택 작업")
        selection_menu = QMenu(self.selection_actions)
        selection_menu.addAction("제외 / 제외 해제", self.toggle_excluded)
        selection_menu.addAction("선택한 옷의 입력값 비우기", self.clear_fields)
        self.selection_actions.setMenu(selection_menu)
        row.addWidget(self.selection_actions)
        info.addLayout(row)
        info.addStretch()
        info.addWidget(QLabel("파랑: 선택 · 주황: 경계 후보 · 회색: 제외"))
        self.tabs.addTab(info_page, "옷 정보")

        recognition_page = QWidget()
        recognition = QVBoxLayout(recognition_page)
        recognition.setContentsMargins(0, 16, 0, 0)
        recognition.setSpacing(12)
        recognition_hint = QLabel("사진에서 성별·연령·사이즈를 제안합니다.\n확인 후 적용해야 입력값에 반영됩니다.")
        recognition_hint.setWordWrap(True)
        recognition_hint.setObjectName("muted")
        recognition.addWidget(recognition_hint)
        row = QHBoxLayout()
        self.recognize_button = button("선택 인식", lambda: self.recognize_items(False), True)
        row.addWidget(self.recognize_button)
        row.addWidget(button("전체 인식", lambda: self.recognize_items(True)))
        recognition.addLayout(row)
        self.suggestion = QPlainTextEdit()
        self.suggestion.setReadOnly(True)
        self.suggestion.setMinimumHeight(145)
        self.suggestion.setMaximumHeight(240)
        self.suggestion.setPlaceholderText("옷을 선택하고 인식을 실행하세요.\n직접 입력한 값은 유지됩니다.")
        recognition.addWidget(self.suggestion)
        self.accept_button = button("제안 확인하고 적용", self.accept_suggestions)
        recognition.addWidget(self.accept_button)
        self.recognition_settings_toggle = QPushButton("인식 설정 열기")
        self.recognition_settings_toggle.setCheckable(True)
        recognition.addWidget(self.recognition_settings_toggle)
        self.recognition_settings = QWidget()
        settings = QVBoxLayout(self.recognition_settings)
        settings.setContentsMargins(0, 0, 0, 0)
        settings_hint = QLabel("머리 위치나 모양이 달라졌을 때 설정하세요.")
        settings_hint.setWordWrap(True)
        settings.addWidget(settings_hint)
        settings.addWidget(button("머리 영역 지정", self.select_crop))
        settings.addWidget(button("현재 머리를 기준으로 등록", self.register_template))
        row = QHBoxLayout()
        row.addWidget(button("정면 사진 변경", self.set_front))
        row.addWidget(button("머리 확대", self.zoom_head))
        settings.addLayout(row)
        recognition.addWidget(self.recognition_settings)
        self.recognition_settings.hide()
        self.recognition_settings_toggle.toggled.connect(self.toggle_recognition_settings)
        recognition.addStretch()
        self.tabs.addTab(recognition_page, "사진 인식")

        self.side_scroll = QScrollArea()
        self.side_scroll.setWidget(self.panel)
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.side_scroll.setMinimumWidth(300)
        self.side_scroll.setMaximumWidth(370)
        self.splitter.addWidget(self.side_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setSizes([1060, 320])
        layout.addWidget(self.splitter, 1)
        footer = QHBoxLayout()
        self.task_info = QLabel("")
        self.task_info.setObjectName("muted")
        footer.addWidget(self.task_info)
        footer.addStretch()
        self.previous_page = button("‹ 이전", lambda: self.change_page(-1))
        self.next_page = button("다음 ›", lambda: self.change_page(1))
        footer.addWidget(self.previous_page)
        self.page_label = QLabel("0 / 0")
        self.page_label.setMinimumWidth(65)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.addWidget(self.page_label)
        footer.addWidget(self.next_page)
        footer.addStretch()
        self.save_state = QLabel("사진을 불러오세요")
        self.save_state.setObjectName("muted")
        footer.addWidget(self.save_state)
        layout.addLayout(footer)
        for key, callback in [
            ("Ctrl+S", self.save), ("Ctrl+Z", self.undo),
            ("PgDown", lambda: self.change_page(1)), ("PgUp", lambda: self.change_page(-1)),
            ("Ctrl+F", self.search.setFocus),
        ]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)

    def toggle_panel(self, checked):
        self.side_scroll.setVisible(not checked)
        self.panel_toggle.setText("입력 패널 열기" if checked else "입력 패널 접기")
        self.grid.setFocus()

    def toggle_recognition_settings(self, checked):
        self.recognition_settings.setVisible(checked)
        self.recognition_settings_toggle.setText("인식 설정 닫기" if checked else "인식 설정 열기")

    def error(self, message):
        QMessageBox.warning(self, "확인이 필요합니다", message)

    def run_job(self, message, function, done):
        if self.busy:
            return
        self.busy = True
        self.body.setEnabled(False)
        self.statusBar().showMessage(message)
        job = Job(function)
        self.current_job = job

        def finish(result):
            self.busy = False
            self.body.setEnabled(True)
            self.statusBar().showMessage("완료")
            done(result)

        def failed(text):
            self.busy = False
            self.body.setEnabled(True)
            self.statusBar().showMessage("작업 중단 · 표시된 내용을 확인하세요.")
            self.error(text)

        job.signals.done.connect(finish)
        job.signals.error.connect(failed)
        job.signals.progress.connect(self.statusBar().showMessage)
        self.pool.start(job)

    def remember(self):
        if self.task:
            self.undo_stack.append(self.task.clone())
            self.undo_stack = self.undo_stack[-30:]

    def save_preferences(self):
        atomic_json(self.preference_path, self.preferences)

    def save(self, *_):
        if not self.task or not self.task_path:
            return True
        try:
            save_task(self.task_path, self.task)
            self.preferences["last_task"] = str(self.task_path)
            self.preferences["recent_tasks"] = [str(self.task_path)] + [
                p for p in self.preferences.get("recent_tasks", []) if p != str(self.task_path)
            ][:29]
            self.save_preferences()
            self.save_state.setText("● 자동 저장됨")
            self.save_state.setToolTip(str(self.task_path))
            return True
        except OSError as error:
            self.save_state.setText("저장 확인 필요")
            self.error(f"작업을 저장하지 못했습니다. 다른 작업을 열거나 종료하기 전에 저장 위치를 확인하세요.\n{error}")
            return False

    def changed(self):
        self.save()
        self.refresh()

    def restore_last(self):
        path = self.preferences.get("last_task")
        if path and Path(path).is_file():
            self.load(Path(path))

    def load(self, path: Path):
        try:
            task = load_task(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(f"작업 파일을 열지 못했습니다.\n{error}")
            return
        self.task, self.task_path = task, path
        self.undo_stack = []
        self.anchor = task.selected[0] if task.selected else 0
        self.refresh()
        self.save_state.setText("● 작업 불러옴")
        self.save_state.setToolTip(str(path))

    def import_photos(self):
        if not self.save():
            return
        source = QFileDialog.getExistingDirectory(self, "번호 폴더들을 포함하는 상위 폴더 선택")
        if not source:
            return
        selected_date = self.date.date().toString("yyyyMMdd")

        def done(result):
            garments, warnings = result
            new_task = Task(source, garments, shooting_date=selected_date, selected=[0])
            folder_name = Path(source).name[:40]
            target = self.task_dir / f"{selected_date}_{folder_name}_{uuid.uuid4().hex[:12]}.coat.json"
            try:
                save_task(target, new_task)
            except OSError as error:
                self.error(f"바탕화면에 작업 파일을 저장하지 못했습니다. 저장 폴더를 확인하세요.\n{self.task_dir}\n{error}")
                return
            self.task = new_task
            self.task_path = target
            self.undo_stack = []
            self.anchor = 0
            self.changed()
            if warnings:
                PreviewDialog(self, "사진 검사 결과", "누락된 칸은 그대로 두고 나머지 사진을 표시합니다.",
                              ["확인할 항목"], [[w] for w in warnings], "확인").exec()
        self.run_job("사진 폴더를 확인하는 중…", lambda progress: discover(Path(source), progress), done)

    def open_task(self):
        if not self.save():
            return
        known = list(self.preferences.get("recent_tasks", []))
        if self.preferences.get("last_task"):
            known.append(self.preferences["last_task"])

        def show(records):
            dialog = RecentTasksDialog(self, records, self.task_dir)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen:
                self.load(dialog.chosen)
        self.run_job("저장된 작업 목록을 불러옵니다…", lambda _: recent_tasks(self.task_dir, known), show)

    def refresh(self):
        task = self.task
        self.panel.setEnabled(bool(task))
        for widget in (self.sheet_button, self.view_button, self.mode, self.search, self.panel_toggle):
            widget.setEnabled(bool(task))
        if not task:
            self.grid.set_task(None)
            self.task_info.setText("사진 폴더를 불러와 시작하세요")
            for widget in (self.previous_page, self.next_page, self.previous_boundary, self.next_boundary):
                widget.setEnabled(False)
            return
        per_page = task.rows * (3 if task.front_only else 1)
        pages = max(1, math.ceil(len(task.garments) / per_page))
        task.page = min(max(task.page, 0), pages - 1)
        task.selected = [i for i in task.selected if 0 <= i < len(task.garments)]
        for widget in (self.mode, self.rows, self.date, self.photo_zoom):
            widget.blockSignals(True)
        self.mode.setCurrentIndex(int(task.front_only))
        self.rows.setValue(task.rows)
        self.photo_zoom.setValue(task.photo_zoom)
        self.date.setDate(QDate.fromString(task.shooting_date, "yyyyMMdd"))
        for widget in (self.mode, self.rows, self.date, self.photo_zoom):
            widget.blockSignals(False)
        self.page_label.setText(f"{task.page + 1} / {pages}")
        self.previous_page.setEnabled(task.page > 0)
        self.next_page.setEnabled(task.page < pages - 1)
        has_boundaries = bool(boundaries(task.garments))
        self.previous_boundary.setEnabled(has_boundaries)
        self.next_boundary.setEnabled(has_boundaries)
        self.grid.set_task(task)
        count = len(task.selected)
        self.selection_label.setText(f"외투 {task.garments[task.selected[0]].code}" if count == 1 else f"{count}벌 선택")
        self.apply_button.setText(f"선택한 {count}벌에 적용" if count else "옷을 선택하세요")
        self.apply_button.setEnabled(count > 0)
        self.recognize_button.setText(f"선택 {count}벌 인식")
        self.recognize_button.setEnabled(count > 0)
        self.selection_actions.setEnabled(count > 0)
        self.undo_button.setEnabled(bool(self.undo_stack))
        self.previous_button.setEnabled(bool(count and min(task.selected) > 0
                                              and task.garments[min(task.selected) - 1].fields))
        self.recognition_settings.setEnabled(count == 1)
        self.accept_button.setEnabled(any(
            any(not g.fields.get(key) for key in g.suggestion.get("values", {})) for g in self.selected_items()
        ))
        for key, combo in self.inputs.items():
            values = {task.garments[i].fields.get(key, "") for i in task.selected}
            value = values.pop() if len(values) == 1 else ""
            found = combo.findData(value)
            combo.setCurrentIndex(found if found >= 0 else 0)
            if found < 0 and combo.isEditable():
                combo.setEditText(value)
        included = sum(not g.excluded for g in task.garments)
        complete = sum(not g.excluded and all(g.fields.get(key) for key in FIELDS) for g in task.garments)
        self.task_info.setText(f"사용 {included}벌 · 선택 {count}벌 · 입력 완료 {complete}벌")
        self.task_info.setToolTip(f"전체 {len(task.garments)}벌 · 경계 후보 {len(boundaries(task.garments))}벌 · 머리 기준 {len(task.templates)}개")
        if count == 1:
            garment = task.garments[task.selected[0]]
            issue = "\n".join(garment.issues)
            self.details.setText(f"정면: {garment.front}번 · {'제외됨' if garment.excluded else '사용'}\n"
                                 + (issue or "Shift: 연속 선택 · Ctrl: 여러 벌 선택"))
            proposal = garment.suggestion
            if proposal:
                values = " / ".join(f"{LABELS[k]}: {v}" for k, v in proposal.get("values", {}).items())
                self.suggestion.setPlainText((values or "제안할 값을 찾지 못했습니다.") + "\n\n" + proposal.get("note", ""))
            else:
                self.suggestion.clear()
        else:
            self.details.setText("적용할 항목만 선택하세요.\n빈 항목은 기존 값을 유지합니다.")
            self.suggestion.setPlainText("선택한 옷들의 제안은 적용 전에 표로 확인할 수 있습니다.")

    def selected_items(self):
        return [self.task.garments[i] for i in self.task.selected] if self.task else []

    def select(self, index, modifiers):
        if not self.task:
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self.task.selected = list(range(min(self.anchor, index), max(self.anchor, index) + 1))
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            selected = set(self.task.selected)
            selected.symmetric_difference_update({index})
            self.task.selected = sorted(selected)
            self.anchor = index
        else:
            self.task.selected = [index]
            self.anchor = index
        self.changed()

    def change_view(self, *_):
        if self.task:
            first = self.task.page * self.task.rows * (3 if self.task.front_only else 1)
            self.task.rows = self.rows.value()
            self.task.front_only = bool(self.mode.currentIndex())
            self.task.page = first // (self.task.rows * (3 if self.task.front_only else 1))
            self.changed()

    def change_date(self, value):
        if self.task:
            self.remember()
            self.task.shooting_date = value.toString("yyyyMMdd")
            self.changed()

    def change_zoom(self, value):
        if self.task:
            self.task.photo_zoom = value
            self.changed()

    def change_page(self, direction):
        if self.task and not self.busy:
            self.task.page += direction
            self.refresh()
            self.save()
            self.grid.setFocus()

    def goto(self, index):
        self.task.selected = [index]
        self.anchor = index
        self.task.page = index // (self.task.rows * (3 if self.task.front_only else 1))
        self.changed()
        self.grid.setFocus()

    def find_code(self):
        if not self.task:
            return
        value = self.search.text().strip()
        if value.isascii() and value.isdigit():
            for i, garment in enumerate(self.task.garments):
                if int(garment.code) == int(value):
                    self.goto(i)
                    return
        self.statusBar().showMessage("해당 외투코드가 없습니다.")

    def jump_boundary(self, direction):
        if not self.task:
            return
        current = self.task.selected[0] if self.task.selected else -1
        candidates = [i for i in boundaries(self.task.garments) if (i - current) * direction > 0]
        if candidates:
            self.goto(min(candidates) if direction > 0 else max(candidates))
        else:
            self.statusBar().showMessage("그 방향에 경계 후보가 없습니다.")

    def apply_fields(self):
        if not self.selected_items():
            return
        values = {}
        for key, combo in self.inputs.items():
            value = combo.currentData()
            if combo.isEditable() and combo.currentText() != combo.itemText(combo.currentIndex()):
                value = combo.currentText().strip()
            if value:
                values[key] = value
        if not values:
            return
        self.remember()
        for garment in self.selected_items():
            garment.fields.update(values)
        self.changed()
        self.grid.setFocus()

    def apply_previous(self):
        if not self.task or not self.task.selected or min(self.task.selected) == 0:
            return
        previous = self.task.garments[min(self.task.selected) - 1].fields
        self.remember()
        for garment in self.selected_items():
            garment.fields.update(previous)
        self.changed()
        self.grid.setFocus()

    def clear_fields(self):
        if self.selected_items():
            self.remember()
            for garment in self.selected_items():
                garment.fields.clear()
            self.changed()

    def toggle_excluded(self):
        items = self.selected_items()
        if items:
            self.remember()
            value = not all(g.excluded for g in items)
            for garment in items:
                garment.excluded = value
            self.changed()

    def undo(self):
        if self.undo_stack and not self.busy:
            self.task = self.undo_stack.pop()
            self.changed()

    def open_photo(self, index, angle):
        path = self.task.garments[index].photos.get(angle)
        if not path:
            return
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        pixmap = QPixmap.fromImage(reader.read())
        if pixmap.isNull():
            self.error("사진 파일을 열지 못했습니다. 원본 경로를 확인하세요.")
            return
        show_zoom(self, pixmap, f"{self.task.garments[index].code} · {angle}번 사진")

    def first_item(self):
        items = self.selected_items()
        if len(items) != 1:
            self.error("이 기능은 옷 한 벌을 선택한 뒤 사용할 수 있습니다.")
            return None
        return items[0]

    def set_front(self):
        garment = self.first_item()
        if not garment:
            return
        choices = sorted(garment.photos, key=int)
        if not choices:
            return
        angle, ok = QInputDialog.getItem(self, "정면 사진 변경", "정면에 해당하는 사진 번호", choices, editable=False)
        if ok:
            self.remember()
            garment.front = angle
            garment.suggestion = {}
            garment.roi = None
            self.changed()

    def zoom_head(self):
        garment = self.first_item()
        if garment and garment.front_path:
            try:
                image = crop_head(garment.front_path, garment.roi or self.task.default_roi)
                show_zoom(self, QPixmap.fromImage(ImageQt(image)), f"{garment.code} 머리 영역")
            except (OSError, ValueError) as error:
                self.error(str(error))

    def select_crop(self):
        garment = self.first_item()
        if not garment or not garment.front_path:
            return
        reader = QImageReader(garment.front_path)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            self.error("사진을 읽지 못했습니다.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("머리와 글자가 모두 들어가도록 드래그하세요")
        layout = QVBoxLayout(dialog)
        picker = CropPicker(image)
        layout.addWidget(picker)
        common = QCheckBox("개별 영역을 지정하지 않은 다른 옷에도 이 위치 적용")
        common.setChecked(True)
        layout.addWidget(common)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and picker.roi:
            self.remember()
            garment.roi = picker.roi
            garment.suggestion = {}
            if common.isChecked():
                self.task.default_roi = picker.roi
                for item in self.task.garments:
                    if item.roi is None:
                        item.suggestion = {}
            self.changed()

    def register_template(self):
        garment = self.first_item()
        if not garment or not garment.front_path:
            return
        if not garment.fields.get("gender") or not garment.fields.get("age"):
            self.error("먼저 이 옷의 남성/여성, 성인/유아동을 확인하고 '값 적용'을 눌러주세요.")
            return
        try:
            feature = shape_feature(crop_head(garment.front_path, garment.roi or self.task.default_roi))
        except (ValueError, OSError) as error:
            self.error(str(error))
            return
        if not feature:
            self.error("머리 모양을 찾지 못했습니다. 머리 영역을 다시 지정하세요.")
            return
        self.remember()
        self.task.templates = [t for t in self.task.templates if t["code"] != garment.code]
        self.task.templates.append({"code": garment.code, "gender": garment.fields["gender"],
                                    "age": garment.fields["age"], "feature": feature})
        self.changed()
        self.statusBar().showMessage("머리 기준을 등록했습니다. 다른 모양도 확인 후 각각 등록하세요.")

    def recognize_items(self, all_items):
        if not self.task:
            return
        indices = list(range(len(self.task.garments))) if all_items else list(self.task.selected)
        if not indices:
            return
        task = self.task.clone()
        executable = find_tesseract(self.preferences.get("tesseract", ""))

        def work(progress):
            results = {}
            for number, index in enumerate(indices, 1):
                garment = task.garments[index]
                progress(f"머리와 사이즈 확인 중 · {number} / {len(indices)}벌 · {garment.code}")
                try:
                    results[index] = recognize(garment, task.default_roi, task.templates, executable)
                except (OSError, ValueError, RuntimeError) as error:
                    results[index] = {"values": {}, "review": True, "note": str(error)}
            return results

        def done(results):
            self.remember()
            for index, result in results.items():
                self.task.garments[index].suggestion = result
            self.changed()
            self.statusBar().showMessage("인식 완료 · 제안을 확인하고 적용하세요. 기존 입력값은 유지했습니다.")
        self.run_job("인식을 시작합니다…", work, done)

    def accept_suggestions(self):
        items = self.selected_items()
        proposed = [(g, k, v) for g in items for k, v in g.suggestion.get("values", {}).items()
                    if not g.fields.get(k)]
        if not proposed:
            self.error("적용할 새 제안이 없습니다. 직접 입력한 항목은 제안으로 덮어쓰지 않습니다.")
            return
        dialog = PreviewDialog(self, "인식 제안 확인",
                               "사진을 확인한 값만 적용하세요. 제안은 미입력 항목에만 적용됩니다.",
                               ["외투코드", "항목", "제안"], [[g.code, LABELS[k], v] for g, k, v in proposed],
                               "확인한 제안 적용")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.remember()
            for garment, key, value in proposed:
                garment.fields[key] = value
            self.changed()
            self.grid.setFocus()

    def copy_photos(self):
        if not self.task:
            self.error("먼저 사진 폴더를 불러오세요.")
            return
        folder = QFileDialog.getExistingDirectory(self, "모든 사진을 복사할 폴더 · 원본 폴더 밖에 선택")
        if not folder:
            return
        task = self.task.clone()

        def preview(plan):
            conflicts = sum(i.status == "충돌" for i in plan)
            dialog = PreviewDialog(
                self, "사진 복사 미리보기",
                f"총 {len(plan)}장 · 충돌 {conflicts}장\n제외한 옷도 사진은 함께 보관합니다. 원본 사진은 변경하지 않습니다.",
                ["원본", "새 이름", "상태"],
                [[f"{Path(i.source).parent.name}/{Path(i.source).name}", Path(i.target).name, i.status] for i in plan],
                "복사 실행", conflicts == 0 and bool(plan),
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.run_job("사진을 복사합니다…", lambda progress: execute_copy(plan, progress),
                             lambda result: QMessageBox.information(
                                 self, "사진 복사 완료",
                                 f"{result['copied']}장 복사 · 동일 파일 {result['skipped']}장 건너뜀\n{folder}",
                             ))
        self.run_job("사진 이름과 중복을 검사합니다…",
                     lambda _: plan_copy(task.garments, Path(task.source), Path(folder)), preview)

    def google_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Google 연결")
        dialog.resize(580, 330)
        layout = QVBoxLayout(dialog)
        guide = QLabel(
            "처음 한 번 Google Cloud에서 Sheets API를 사용 설정하고,\n"
            "OAuth 클라이언트 유형을 '데스크톱 앱'으로 만든 JSON 파일이 필요합니다.\n"
            "사진 인식은 로컬에서 처리합니다. Google에는 확인한 셀 값만 전송합니다.\n"
            "로그인 정보는 이 Windows 사용자만 읽을 수 있도록 암호화해 저장합니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)
        path = QLineEdit(self.preferences.get("client_path", ""))
        path.setPlaceholderText("OAuth 데스크톱 클라이언트 JSON 파일")
        layout.addWidget(path)

        def choose():
            filename, _ = QFileDialog.getOpenFileName(dialog, "OAuth JSON 선택", "", "JSON (*.json)")
            if filename:
                path.setText(filename)
        layout.addWidget(button("JSON 파일 선택", choose))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("브라우저에서 로그인", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        client_path = path.text().strip()
        if not Path(client_path).is_file():
            self.error("OAuth JSON 파일을 선택하세요.")
            return

        def done(_):
            self.preferences["client_path"] = client_path
            self.save_preferences()
            QMessageBox.information(self, "연결 완료", "Google 로그인이 완료되었습니다. 시트 입력 미리보기를 사용할 수 있습니다.")
        self.run_job("브라우저에서 로그인을 완료하세요 · 최대 3분",
                     lambda _: GoogleSheets(self.data_dir / "google-token.dpapi", client_path, True), done)

    def sheet_preview(self):
        if not self.task:
            return
        dialog = SheetTransferDialog(self, self.task, self.data_dir / "google-token.dpapi")
        dialog.target_selected.connect(self.remember_sheet_target)
        dialog.exec()

    def remember_sheet_target(self, url, tab):
        if self.task:
            self.task.sheet_url, self.task.sheet_tab = url, tab
            self.save()

    def help(self):
        QMessageBox.information(
            self, "사용 방법",
            "1. 사진 폴더를 불러오고 촬영일을 확인합니다.\n"
            "2. 한 벌씩 3장 또는 정면만 보기로 확인합니다. 줄 수와 크기는 '보기 설정'에서 바꿉니다.\n"
            "3. '옷 정보' 탭에서 값을 입력하고 적용합니다. Shift는 연속 선택, Ctrl은 여러 벌 선택입니다.\n"
            "4. '사진 인식' 탭에서 인식 제안을 확인합니다. 머리 설정은 '인식 설정 열기'에 있습니다.\n"
            "5. 상단 '시트로 보내기'에서 주소·날짜·변경값을 한 번에 확인하고 전송합니다.\n"
            "6. '작업 열기'에서는 날짜와 폴더 이름으로 저장된 작업을 고를 수 있습니다.\n\n"
            "사진을 따로 모아 전달할 때는 '더보기 → 사진 한 폴더로 내보내기'를 사용하세요.\n"
            "작업은 자동 저장됩니다. Ctrl+Z는 앱 안의 입력만 취소하며 이미 전송한 시트는 되돌리지 않습니다.\n"
            "시트에 이미 있는 옷을 앱에서 제외해도 시트의 기존 행을 삭제하지 않습니다.\n"
            "이미지는 기존 시트처럼 0_외투코드 수식으로 입력하며 사진 파일을 업로드하지 않습니다.",
        )

    def closeEvent(self, event):
        if self.busy:
            self.statusBar().showMessage("현재 작업이 끝난 뒤 종료할 수 있습니다.")
            event.ignore()
            return
        if self.save():
            self.grid.pool.waitForDone(5000)
            event.accept()
        else:
            event.ignore()
