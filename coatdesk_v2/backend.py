from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot

from . import excel
from .domain import FIELDS, OPTIONS, Workspace, parse_errors, photo_path, shooting_date, validate_fields, validate_roi
from .images import image_url
from .recognition import DEFAULT_THRESHOLDS, RecognitionEngine
from .category import DEFAULT_CATEGORY_THRESHOLDS
from .scanner import scan
from .storage import Repository


def local_path(value: str) -> str:
    return QUrl(value).toLocalFile() if value.startswith("file:") else value


class PageModel(QAbstractListModel):
    RecordRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return {self.RecordRole: b"record"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role):
        return self.rows[index.row()] if index.isValid() and role == self.RecordRole else None

    def replace(self, rows):
        # Reuse card delegates when paging, rather than rebuilding every control.
        if rows == self.rows:
            return
        overlap = min(len(rows), len(self.rows))
        if len(rows) < len(self.rows):
            self.beginRemoveRows(QModelIndex(), len(rows), len(self.rows)-1)
            del self.rows[len(rows):]
            self.endRemoveRows()
        self.rows[:overlap] = rows[:overlap]
        if overlap:
            self.dataChanged.emit(self.index(0), self.index(overlap-1), [self.RecordRole])
        if len(rows) > len(self.rows):
            first = len(self.rows)
            self.beginInsertRows(QModelIndex(), first, len(rows)-1)
            self.rows.extend(rows[first:])
            self.endInsertRows()


class Work(QRunnable):
    def __init__(self, job_id, function, emit):
        super().__init__()
        self.job_id, self.function, self.emit = job_id, function, emit

    def run(self):
        run_job(self.job_id, self.function, self.emit)


def run_job(job_id, function, emit):
    try:
        result = function(lambda value: emit((job_id, "progress", value)))
        emit((job_id, "done", result))
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, FileNotFoundError)) else f"작업에 실패했습니다 ({type(error).__name__}). 사진 또는 저장 위치를 확인하세요."
        emit((job_id, "error", message))


class Backend(QObject):
    changed = Signal()
    event = Signal(object)
    scopeChanged = Signal()

    def __init__(self, directory: Path, engine_factory=RecognitionEngine):
        super().__init__()
        self.repository = Repository(directory)
        self.thresholds = self.repository.setting("ai_thresholds", DEFAULT_THRESHOLDS)
        self.category_thresholds = self.repository.setting("category_thresholds", dict(DEFAULT_CATEGORY_THRESHOLDS))
        self.excel_path = self.repository.setting("excel_path", "")
        self.recent = self.repository.recent()
        self._state_cache = None
        self._workspace_summary = None
        self._open_request = 0
        self.engine_factory = (lambda: RecognitionEngine(self.thresholds, self.category_thresholds)) if engine_factory is RecognitionEngine else engine_factory
        self._recognition_engine = None
        self.model = PageModel(self)
        self.workspace: Workspace | None = None
        self.selected = set()
        self.anchor = ""
        self.current = ""
        self.page = 0
        self.filtered = []
        self.mode = "review"
        self.filter_name = "전체"
        self.search = ""
        self.class_filters = {"gender": "", "size": "", "age": ""}
        self.show_home = True
        self.opened = self.repository.setting("open_workspaces", [])
        self.workspace_undo = {}
        self.cards = 4
        self.rows = 3
        self.front_only = True
        self.photo_size = 150
        self.layouts = {"review": [2, 2], "photos": [2, 2]}
        self.fast_input = False
        self.undo_stack = []
        self.plan = None
        self.target_path = ""
        self.last_path = ""
        self.message = "작업공간을 만들거나 최근 작업을 여세요."
        self.error = ""
        self.saved = True
        self.recognizing = False
        self.exporting = False
        self.cancel_event = threading.Event()
        self.jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        # Keep model construction and all CPU inference on one persistent
        # thread. Reconstructing PyTorch models on another pool thread can
        # stall native CPU allocations after a previous inference run.
        self.recognition_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="CoatDeskAI")
        self.event.connect(self._event)

    @Property(QObject, constant=True)
    def pageModel(self):
        return self.model

    @Slot(QObject)
    def attachWindow(self, window):
        from .navigation import NavigationKeys
        self.navigation = NavigationKeys(self, window)

    @Property("QVariantMap", notify=changed)
    def state(self):
        if self._state_cache is not None:
            return self._state_cache
        workspace = self.workspace
        if workspace and self._workspace_summary is None:
            active = workspace.active
            ready = {g.code for g in active if g.export_ready}
            present = {int(g.code) for g in workspace.in_range}
            self._workspace_summary = ({"total": len(active), "confirmed": len(ready),
                "excluded": len(present.intersection(workspace.errors)),
                "missing": workspace.end-workspace.start+1-len(present)},
                sum(g.code not in ready and bool(g.fields or g.suggestion or g.issues) for g in active))
        count, review_count = self._workspace_summary if workspace else ({"total": 0, "confirmed": 0, "excluded": 0, "missing": 0}, 0)
        pages = max(1, (len(self.filtered)+self.page_size-1)//self.page_size)
        current = next((g for g in workspace.garments if g.code == self.current), None) if workspace else None
        recent = self.recent
        self._state_cache = {"hasWorkspace": bool(workspace), "home": self.show_home,
                "reviewCount": review_count,
                "readyCount": count["confirmed"],
                "openWorkspaces": [r for key in self.opened for r in recent if r["id"] == key],
                "classFilters": self.class_filters, "workspaceId": workspace.id if workspace else "",
                "name": workspace.name if workspace else "외투 작업실", "date": workspace.shooting_date if workspace else "",
                "source": workspace.source if workspace else "", "start": workspace.start if workspace else 1,
                "end": workspace.end if workspace else 400, "errors": ", ".join(map(str, workspace.errors)) if workspace else "",
                "emptyPhotoCodes": workspace.empty_photo_codes if workspace else [],
                "counts": count, "recent": recent, "selectedCount": len(self.selected),
                "page": self.page+1, "pages": pages, "visibleCount": len(self.filtered), "mode": self.mode,
                "cards": self.cards, "rows": self.rows, "frontOnly": self.front_only, "photoSize": self.photo_size,
                "gridRows": self.layouts[self.mode][0], "gridColumns": self.layouts[self.mode][1], "fastInput": self.fast_input,
                "filter": self.filter_name, "search": self.search,
                "current": next((r for r in self.model.rows if r["code"] == self.current), self._record(current) if current and not any(r["code"] == self.current for r in self.model.rows) else {}),
                "busy": bool(self.jobs), "recognizing": self.recognizing, "exporting": self.exporting,
                "navigationBlocked": any(kind != "load" for _, kind in self.jobs.values()),
                "loadingWorkspace": any(kind == "load" for _, kind in self.jobs.values()),
                "scopeChanging": any(kind == "scope" for _, kind in self.jobs.values()),
                "message": self.message, "error": self.error, "saved": self.saved, "canUndo": bool(self.undo_stack),
                "options": OPTIONS, "aiThresholds": self.thresholds,
                "categoryThresholds": self.category_thresholds,
                "excelPath": self.excel_path,
                "planReady": self.plan is not None, "planTitle": self.plan.title if self.plan else "",
                "planPath": self.plan.path if self.plan else "",
                "planAddsToFile": self.plan is not None and self.plan.file_digest is not None,
                "planRows": [[r[0], r[1], r[3], r[5], r[6], r[7]] for r in self.plan.rows] if self.plan else [],
                "lastFileUrl": QUrl.fromLocalFile(self.last_path).toString() if self.last_path else ""}
        return self._state_cache

    def _notify(self):
        self._state_cache = None
        self.changed.emit()

    @property
    def page_size(self):
        rows, columns = self.layouts[self.mode]
        return rows * columns

    def _record(self, garment):
        excluded = int(garment.code) in self.workspace.errors
        values = garment.suggestion.get("values", {})
        return {"code": garment.code, "fields": garment.fields, "suggestions": values,
                "status": "제외" if excluded else garment.status, "excluded": excluded,
                "selected": garment.code in self.selected, "front": garment.front, "roi": garment.roi,
                "photo": image_url(self.workspace.source, garment.photos.get(garment.front, "")),
                "head": image_url(self.workspace.source, garment.photos.get(garment.front, ""), garment.roi if garment.roi_manual else (garment.suggestion.get("roi") or garment.roi)),
                "aiScores": garment.suggestion.get("scores", {}), "aiCandidates": garment.suggestion.get("candidates", {}),
                "held": garment.suggestion.get("held", []), "manualRoi": garment.roi_manual,
                "categoryAnalysis": garment.suggestion.get("category", {}),
                "fieldSources": garment.field_sources,
                "invalidSize": bool(garment.fields.get("size") and garment.fields["size"] not in OPTIONS["size"]),
                "pendingReview": garment.pending_review, "needsReview": bool(garment.pending_review),
                "photos": [{"angle": a, "url": image_url(self.workspace.source, garment.photos.get(a, ""))}
                           for a in sorted(set(garment.photos) | {"0", "1", "2"}, key=int)],
                "issues": " · ".join(garment.issues), "note": garment.suggestion.get("note", ""),
                "analysis": garment.analysis_state, "boundary": False}

    def _refresh(self, reload=True, keep_code=True):
        if self.workspace:
            if reload:
                self.workspace = self.repository.load(self.workspace.id)
                self._workspace_summary = None
            active_codes = {g.code for g in self.workspace.active}
            if self.filter_name != "제외":
                self.selected.intersection_update(active_codes)
            garments = self.workspace.in_range if self.filter_name == "제외" else self.workspace.active
            self.filtered = [g for g in garments if (self.filter_name != "제외" or int(g.code) in self.workspace.errors)
                             and (self.filter_name != "미확인" or not g.confirmed)
                             and (self.filter_name != "확인 필요" or g.status == "확인 필요")
                             and (self.filter_name != "확인 완료" or g.status == "확인 완료")
                             and (not self.search or self.search in g.code)
                             and all(not value or g.fields.get(key) == value for key, value in self.class_filters.items())]
            codes = [g.code for g in self.filtered]
            if keep_code and self.current in codes:
                self.page = codes.index(self.current)//self.page_size
            self.page = max(0, min(self.page, max(0, (len(codes)-1)//self.page_size)))
            visible = self.filtered[self.page*self.page_size:(self.page+1)*self.page_size]
            if visible and self.current not in [g.code for g in visible]:
                self.current = visible[0].code
            if not visible:
                self.current = ""
            boundaries = set()
            active = self.workspace.active
            for left, right in zip(active, active[1:]):
                a = {**left.suggestion.get("values", {}), **left.fields}
                b = {**right.suggestion.get("values", {}), **right.fields}
                changed = any(a.get(k) and b.get(k) and a[k] != b[k] for k in ("gender", "age", "size"))
                sa, sb = left.suggestion.get("shape", []), right.suggestion.get("shape", [])
                if sa and sb and len(sa) == len(sb):
                    changed |= sum(x != y for x, y in zip(sa, sb))/len(sa) > .23
                if changed:
                    boundaries.update((left.code, right.code))
            records = [self._record(g) for g in visible]
            for record in records:
                record["boundary"] = record["code"] in boundaries
            self.model.replace(records)
        self._notify()

    def _view(self):
        if not self.workspace:
            return
        self.workspace.view.update(layouts=copy.deepcopy(self.layouts), fast_input=self.fast_input)
        self.repository.update(self.workspace.id, view=self.workspace.view | {
            "current": self.current, "selected": sorted(self.selected), "mode": self.mode,
            "filter": self.filter_name, "search": self.search, "cards": self.cards, "rows": self.rows,
            "front_only": self.front_only, "photo_size": self.photo_size, "class_filters": self.class_filters})

    def _fail(self, error):
        self.error = str(error)
        self._notify()

    def _run(self, label, function, done, kind="normal"):
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = (done, kind)
        self.message, self.error = label, ""
        if kind == "recognition":
            self.recognition_pool.submit(run_job, job_id, function, self.event.emit)
        else:
            self.pool.start(Work(job_id, function, self.event.emit))
        self._notify()

    @Slot(object)
    def _event(self, event):
        job_id, action, value = event
        if action == "progress":
            if isinstance(value, dict):
                self.message = value["message"]
                if self.workspace and self.workspace.id == value["workspace"]:
                    changes = {g.code: g for g in value["garments"]}
                    self.workspace.garments = [changes.get(g.code, g) for g in self.workspace.garments]
                    self._workspace_summary = None
                self._refresh(False)
            else:
                self.message = str(value)
                self._notify()
            return
        done, kind = self.jobs.pop(job_id)
        if kind == "recognition":
            self.recognizing = False
        if kind == "export":
            self.exporting = False
        if action == "error":
            self._fail(value)
        else:
            try:
                done(value)
                self.saved = True
            except Exception as error:
                self._fail(error)
        if kind != "load":
            self._refresh()
        else:
            self._notify()

    @Slot(str, str, str, str, str, str)
    def createWorkspace(self, name, day, source, start_text, end_text, errors):
        if self.jobs:
            self._fail("진행 중인 작업이 끝난 뒤 작업공간을 만드세요.")
            return
        try:
            start, end = int(start_text), int(end_text)
            parse_errors(errors, start, end)
            shooting_date(name.strip(), day)
            source = local_path(source)
            def create(progress):
                garments = scan(Path(source), start, end, progress)
                return self.repository.create(name, day, source, start, end, errors, garments)
            self._run("사진 폴더를 검사합니다…", create, self.openWorkspace)
        except Exception as error:
            self._fail(error)

    @Slot(str)
    def openWorkspace(self, workspace_id, loaded=None):
        if any(kind != "load" for _, kind in self.jobs.values()):
            self._fail("진행 중인 작업을 마친 뒤 다른 작업공간을 여세요.")
            return
        try:
            if self.workspace:
                self._view()
                self.workspace_undo[self.workspace.id] = self.undo_stack
            self.workspace = loaded if loaded is not None else self.repository.load(workspace_id)
            self._workspace_summary = None
            self.recent = self.repository.recent()
            self.show_home = False
            if workspace_id not in self.opened:
                self.opened.append(workspace_id)
                self.repository.set_setting("open_workspaces", self.opened)
            view = self.workspace.view
            self.mode = view.get("mode", "review")
            self.cards = view.get("cards", 4)
            self.rows = view.get("rows", 3)
            self.front_only = view.get("front_only", True)
            self.photo_size = view.get("photo_size", 150)
            self.layouts = copy.deepcopy(view.get("layouts", {
                "review": [1 if self.cards == 2 else 2, 2],
                "photos": [max(1, min(4, self.rows)), 3 if self.front_only else 1]})) if view else {"review": [2, 2], "photos": [2, 2]}
            self.fast_input = view.get("fast_input", False)
            self.filter_name = view.get("filter", "전체")
            self.search = view.get("search", "")
            self.class_filters = {"gender": "", "size": "", "age": "", **view.get("class_filters", {})}
            self.current = view.get("current", "")
            self.anchor = ""
            self.selected = set(view.get("selected", []))
            self.undo_stack, self.plan, self.last_path = self.workspace_undo.get(workspace_id, []), None, ""
            self.page = 0
            self.repository.set_setting("last_workspace", workspace_id)
            self.message, self.error = "작업공간을 열었습니다. 입력 내용은 자동 저장됩니다.", ""
            self._refresh(False)
        except Exception as error:
            self._fail(error)

    @Slot(str)
    def requestWorkspace(self, workspace_id):
        if any(kind != "load" for _, kind in self.jobs.values()):
            self._fail("진행 중인 작업을 마친 뒤 다른 작업공간을 여세요.")
            return
        if self.workspace:
            self._view()
        self._open_request += 1
        request = self._open_request
        def done(workspace):
            if request == self._open_request:
                self.openWorkspace(workspace_id, workspace)
        self._run("작업공간을 여는 중…", lambda progress: self.repository.load(workspace_id), done, "load")

    @Slot()
    def goHome(self):
        if any(kind != "load" for _, kind in self.jobs.values()):
            return
        self._open_request += 1
        try:
            self._view()
            self.show_home = True
            self._notify()
        except Exception as error:
            self.saved = False
            self._fail(error)

    @Slot(str, str, str)
    def setClassificationFilters(self, gender, size, age):
        self.class_filters = {"gender": gender, "size": size, "age": age}
        self.current = ""
        self.page = 0
        self._preference()

    @Slot()
    def dismissError(self):
        self.error = ""
        self._notify()

    @Slot(str, int)
    def select(self, code, modifiers=0):
        codes = [g.code for g in self.filtered]
        if code not in codes:
            return
        if modifiers & int(Qt.KeyboardModifier.ShiftModifier.value) and self.anchor in codes:
            a, b = sorted((codes.index(self.anchor), codes.index(code)))
            self.selected.update(codes[a:b+1])
        elif modifiers & int(Qt.KeyboardModifier.ControlModifier.value):
            self.selected.symmetric_difference_update({code})
            self.anchor = code
        else:
            self.selected = {code}
            self.anchor = code
        self.current = code
        try:
            self._view()
            self._refresh(False)
        except Exception as error:
            self._fail(error)

    @Slot()
    def selectPage(self):
        self.selected.update(r["code"] for r in self.model.rows)
        self._preference()

    @Slot(str)
    def setMode(self, mode):
        if mode in {"review", "photos"}:
            self.mode = mode
            self._preference()

    @Slot(str)
    def setFilter(self, value):
        if value in {"전체", "미확인", "확인 필요", "확인 완료", "제외"}:
            self.filter_name = value
            self._preference()

    @Slot(str)
    def setSearch(self, value):
        self.search = value.strip()
        self._preference()

    @Slot(int, int, bool, int)
    def viewOptions(self, cards, rows, front_only, photo_size):
        self.cards = 2 if cards == 2 else 4
        self.rows = max(1, min(8, rows))
        self.front_only = front_only
        self.photo_size = max(100, min(250, photo_size))
        self.layouts["review"] = [1 if self.cards == 2 else 2, 2]
        self.layouts["photos"] = [max(1, min(4, rows)), 3 if front_only else 1]
        self._preference()

    @Slot(int, int)
    def setLayout(self, rows, columns):
        if 1 <= rows <= 4 and 1 <= columns <= 4:
            self.layouts[self.mode] = [rows, columns]
            self._preference()

    @Slot(bool)
    def setFastInput(self, value):
        self.fast_input = value
        self._preference()

    @Slot(bool)
    def setFrontOnly(self, value):
        self.front_only = value
        self._preference()

    @Slot(int)
    def setPhotoSize(self, value):
        self.photo_size = max(100, min(250, value))
        self._preference()

    def _preference(self):
        try:
            self._refresh(False)
            self._view()
        except Exception as error:
            self._fail(error)

    @Slot(int)
    def pageBy(self, delta):
        self.page += delta
        self.current = ""
        self._refresh(False, False)
        try:
            self._view()
        except Exception as error:
            self._fail(error)

    @Slot(int)
    def nextGarment(self, delta):
        codes = [g.code for g in self.filtered]
        if not codes:
            return
        index = codes.index(self.current) if self.current in codes else 0
        self.focusGarment(codes[max(0, min(len(codes)-1, index+delta))])

    @Slot(str)
    def focusGarment(self, code):
        if code not in {g.code for g in self.filtered}:
            return
        self.current = code
        self._preference()

    def _edit(self, codes, change):
        if not self.workspace or self.exporting:
            return
        if not codes:
            raise ValueError("먼저 옷을 선택하세요.")
        if any(kind == "scope" for _, kind in self.jobs.values()):
            raise ValueError("사진 범위를 검사하는 동안 잠시 기다리세요.")
        previous = self.repository.edit(self.workspace.id, codes, change)
        self.undo_stack.append(previous)
        self.undo_stack = self.undo_stack[-100:]
        self.plan, self.error, self.saved = None, "", True
        self.message = "저장 완료"
        changes = {g.code: g for g in self.repository.load_garments(self.workspace.id, codes)}
        self.workspace.garments = [changes.get(g.code, g) for g in self.workspace.garments]
        self._workspace_summary = None
        self._refresh(False)

    @Slot(str, str, str)
    def setField(self, code, key, value):
        try:
            validate_fields({key: value.strip()})
            def change(garment):
                garment.set_field(key, value.strip())
            self._edit([code], change)
        except Exception as error:
            self.saved = False
            self._fail(error)

    @Slot(str, str, str, str)
    def batchFields(self, category, gender, age, size):
        try:
            values = {k: v.strip() for k, v in zip(FIELDS, (category, gender, age, size)) if v.strip()}
            validate_fields(values)
            if not values:
                raise ValueError("일괄 적용할 값을 선택하세요.")
            def change(garment):
                for key, value in values.items():
                    garment.set_field(key, value)
            self._edit(sorted(self.selected), change)
        except Exception as error:
            self._fail(error)

    @Slot()
    def applySuggestions(self):
        try:
            def change(garment):
                values = {k: v for k, v in garment.suggestion.get("values", {}).items() if k in FIELDS and not garment.fields.get(k)}
                if values:
                    validate_fields(values)
                    garment.apply_analysis()
            self._edit(sorted(self.selected), change)
        except Exception as error:
            self._fail(error)

    @Slot()
    def confirmSelected(self):
        try:
            active = {g.code for g in self.workspace.active} if self.workspace else set()
            if self.selected - active:
                raise ValueError("제외한 옷은 확인 완료 대상이 아닙니다.")
            def change(garment):
                garment.confirm_review()
            self._edit(sorted(self.selected), change)
        except Exception as error:
            self._fail(error)

    @Slot(str)
    def confirmGarment(self, code):
        try:
            if not self.workspace or code not in {g.code for g in self.workspace.active}:
                raise ValueError("현재 작업 대상의 옷을 확인하세요.")
            self._edit([code], lambda garment: garment.confirm_review())
        except Exception as error:
            self._fail(error)

    @Slot()
    def clearSelected(self):
        try:
            def change(garment):
                garment.fields = {}
                garment.confirmed = False
                garment.reviewed_values = {}
                garment.field_sources = {}
            self._edit(sorted(self.selected), change)
        except Exception as error:
            self._fail(error)

    @Slot()
    def undo(self):
        if not self.undo_stack or self.exporting:
            return
        try:
            self.repository.undo(self.workspace.id, self.undo_stack[-1])
            self.undo_stack.pop()
            self.plan = None
            self._refresh()
        except Exception as error:
            self._fail(error)

    @Slot(str)
    def setErrors(self, text):
        if not self.workspace or self.exporting:
            return
        try:
            errors = parse_errors(text, self.workspace.start, self.workspace.end)
            self.repository.update(self.workspace.id, errors=errors)
            self.cancel_event.set()
            self.plan = None
            self._refresh()
        except Exception as error:
            self._fail(error)

    @Slot(str, str, str)
    def updateWorkspaceScope(self, start_text, end_text, error_text):
        if not self.workspace or self.jobs:
            self._fail("진행 중인 작업을 마친 뒤 사진 범위를 바꾸세요.")
            return
        try:
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                raise ValueError("시작·끝 번호를 숫자로 입력하세요.") from None
            if start < 0 or end < start or end-start > 100_000:
                raise ValueError("번호 범위는 시작 ≤ 끝, 최대 100,001개로 입력하세요.")
            parse_errors(error_text, start, end)
            workspace_id, source = self.workspace.id, self.workspace.source
            def change(progress):
                scanned = scan(Path(source), start, end, progress)
                self.repository.change_scope(workspace_id, start, end, error_text, scanned)
                return len(scanned)
            def done(count):
                self.plan = None
                self.recent = self.repository.recent()
                self.message = f"사진 범위 {start}–{end}번 저장 · {count}개 폴더 확인 · 기존 입력은 보존했습니다."
                self._refresh()
                self.scopeChanged.emit()
            self._run("사진 범위를 검사합니다…", change, done, "scope")
        except ValueError as error:
            self._fail(str(error) if str(error) else "시작·끝 번호를 숫자로 입력하세요.")

    @Slot(str)
    def reconnect(self, root):
        if not self.workspace or self.jobs:
            return
        try:
            source = Path(local_path(root)).resolve(strict=True)
            missing = sum(not photo_path(str(source), p).is_file() for g in self.workspace.garments for p in g.photos.values())
            if missing:
                raise ValueError(f"새 폴더에서 사진 {missing}장을 찾지 못했습니다. 같은 번호 구조의 상위 폴더를 선택하세요.")
            self.repository.update(self.workspace.id, source=str(source))
            self.plan = None
            self._refresh()
        except Exception as error:
            self._fail(error)

    @Slot(str, str)
    def setFront(self, code, angle):
        if self.recognizing:
            self._fail("분석을 중단한 뒤 정면 사진을 바꾸세요.")
            return
        try:
            def change(g):
                if angle not in g.photos:
                    raise ValueError("선택한 각도의 사진이 없습니다.")
                g.front, g.confirmed, g.analysis_key = angle, False, ""
            self._edit([code], change)
        except Exception as error:
            self._fail(error)

    @Slot(str, float, float, float, float)
    def setRoi(self, code, x1, y1, x2, y2):
        if self.recognizing:
            self._fail("분석을 중단한 뒤 머리 영역을 바꾸세요.")
            return
        try:
            roi = [x1, y1, x2, y2]
            validate_roi(roi)
            def change(g):
                g.roi, g.confirmed, g.analysis_key = roi, False, ""
                g.roi_manual = True
            self._edit([code], change)
        except Exception as error:
            self._fail(error)

    @Slot()
    def applyRoiToSelected(self):
        if self.recognizing:
            self._fail("분석을 중단한 뒤 영역을 적용하세요.")
            return
        try:
            roi = copy.copy(next(g for g in self.workspace.garments if g.code == self.current).roi)
            def change(g):
                g.roi, g.confirmed, g.analysis_key = roi, False, ""
                g.roi_manual = True
            self._edit(sorted(self.selected), change)
        except Exception as error:
            self._fail(error)

    @Slot()
    def registerTemplate(self):
        if not self.workspace or self.jobs:
            return
        workspace = self.repository.load(self.workspace.id)
        garment = next((g for g in workspace.garments if g.code == self.current), None)
        if not garment:
            return
        def done(template):
            self.repository.update(workspace.id, templates=workspace.templates+[template])
            self.message = f"{garment.code}의 머리 모양을 기준으로 등록했습니다."
        self._run("머리 기준을 등록합니다…", lambda progress: self.engine_factory().template(workspace, garment), done)

    @Slot(int, int, int)
    @Slot(int, int, int, int)
    def setAiThresholds(self, panel, shape, size, sleeve=None):
        if self.jobs:
            self._fail("분석을 중단한 뒤 기준을 바꾸세요.")
            return
        if not all(1 <= v <= 100 for v in ((panel, shape, size) if sleeve is None else (panel, shape, size, sleeve))):
            self._fail("보류 기준은 1~100%로 입력하세요.")
            return
        settings = {"ai_thresholds": dict(zip(("panel", "shape", "size"), (panel/100, shape/100, size/100), strict=True))}
        if sleeve is not None:
            settings["category_thresholds"] = {**self.category_thresholds, "sleeve": sleeve/100}
        self.repository.set_settings(settings)
        self.thresholds = self.repository.setting("ai_thresholds")
        self.category_thresholds = self.repository.setting("category_thresholds", dict(DEFAULT_CATEGORY_THRESHOLDS))
        self.plan = None
        self._workspace_summary = None
        if self.workspace:
            for garment in self.workspace.garments:
                garment.rethreshold(self.thresholds, self.category_thresholds)
        self.message = "AI 기준 저장 완료. 저장된 점수의 확인 상태를 갱신했습니다. 새 분석에도 적용됩니다."
        self._refresh(False)

    @Slot()
    def automaticPanel(self):
        if self.recognizing or not self.current:
            return
        def change(g):
            g.roi_manual, g.analysis_key, g.confirmed = False, "", False
        self._edit([self.current], change)

    @Slot(bool)
    def startAnalysis(self, force=False):
        if not self.workspace or self.jobs:
            return
        workspace = self.repository.load(self.workspace.id)
        priority = {r["code"]: i for i, r in enumerate(self.model.rows)}
        garments = sorted(workspace.active, key=lambda g: (priority.get(g.code, 10**9), int(g.code)))
        self.cancel_event.clear()
        self.recognizing = True
        def analyze(progress):
            if self._recognition_engine is None:
                self._recognition_engine = self.engine_factory()
            engine = self._recognition_engine
            if isinstance(engine, RecognitionEngine):
                engine.thresholds = dict(self.thresholds)
                engine.category_thresholds = dict(self.category_thresholds)
            completed = 0
            changes = []
            import time
            last_progress = time.monotonic()
            def report(garment):
                nonlocal last_progress
                changes.extend(self.repository.load_garments(workspace.id, [garment.code]))
                if time.monotonic() - last_progress >= .08:
                    progress({"message": f"분석 · {completed} / {len(garments)}벌 · {garment.code}",
                              "workspace": workspace.id, "garments": changes.copy()})
                    changes.clear()
                    last_progress = time.monotonic()
            for g in garments:
                if self.cancel_event.is_set():
                    break
                try:
                    key = engine.key(workspace, g)
                    if not force and key == g.analysis_key and g.analysis_state == "done":
                        self.repository.edit(workspace.id, [g.code], lambda item: item.apply_analysis())
                        completed += 1
                        report(g)
                        continue
                    suggestion = engine.analyze(workspace, g)
                    state = "failed" if suggestion.get("category", {}).get("error") or suggestion.get("review_fields") else "done"
                except Exception as error:
                    key, state = "", "failed"
                    suggestion = {"values": {}, "review": True, "review_fields": list(FIELDS),
                                  "category": {"error": type(error).__name__, "needs_review": True},
                                  "note": f"사진 인식 실패 ({type(error).__name__}). 사진과 영역을 확인하세요."}
                def save(item):
                    item.suggestion, item.analysis_key, item.analysis_state = suggestion, key, state
                    item.apply_analysis()
                self.repository.edit(workspace.id, [g.code], save)
                completed += 1
                report(g)
            if changes:
                progress({"message": f"분석 · {completed} / {len(garments)}벌", "workspace": workspace.id, "garments": changes})
            return completed
        def done(count):
            self.message = f"분석 {'중단' if self.cancel_event.is_set() else '완료'} · {count}벌 처리 · 빈 항목에 입력했습니다. 확인 필요 항목을 검수하세요."
        self._run("현재 화면의 옷부터 분석합니다…", analyze, done, "recognition")

    @Slot()
    def cancelAnalysis(self):
        self.cancel_event.set()
        self.message = "현재 옷의 처리를 마친 뒤 중단합니다. 완료한 결과는 저장됩니다."
        self._notify()

    @Slot()
    def invalidatePlan(self):
        if self.plan:
            self.plan = None
            self._notify()

    @Slot(str)
    def excelTargetChanged(self, path):
        self.target_path = local_path(path)
        self.plan = None
        self._notify()

    @Slot(str, str)
    def previewExcel(self, path, title):
        if not self.workspace or self.jobs:
            return
        self.plan = None
        try:
            workspace = self.repository.load(self.workspace.id)
            target = local_path(path.strip())
            self.target_path = target
            def done(plan):
                if self.target_path != target:
                    self.message = "저장 위치가 바뀌었습니다. 미리보기를 다시 만드세요."
                    return
                if self.workspace.export_fingerprint() != plan.workspace_digest:
                    raise ValueError("미리보기를 만드는 동안 입력이 바뀌었습니다. 다시 만드세요.")
                self.plan = plan
                self.repository.set_setting("excel_path", plan.path)
                self.excel_path = plan.path
                self.message = f"새 시트 {plan.title} · {len(plan.rows)}벌 · 확인 후 저장하세요."
            self._run("양식과 저장할 값을 확인합니다…",
                      lambda progress: excel.build_plan(workspace, target, title), done)
        except Exception as error:
            self._fail(error)

    @Slot()
    def saveExcel(self):
        if self.jobs or not self.plan:
            return
        plan, self.plan = self.plan, None
        self.exporting = True
        def done(path):
            self.last_path = str(path)
            self.message = f"엑셀의 새 시트 {plan.title}에 저장하고 입력값을 다시 읽어 확인했습니다."
        self._run("엑셀 파일에 새 시트를 저장합니다…",
                  lambda progress: excel.save(self.repository, plan), done, "export")

    def shutdown(self):
        self.cancel_event.set()
        self.recognition_pool.shutdown(wait=True)
        self.pool.waitForDone()
