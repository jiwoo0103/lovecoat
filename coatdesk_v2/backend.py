from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot

from coatdesk.domain import OPTIONS
from coatdesk.sheets import spreadsheet_id
from . import sheets
from .domain import FIELDS, Workspace, parse_errors, photo_path, shooting_date, validate_fields, validate_roi
from .images import image_url
from .recognition import DEFAULT_THRESHOLDS, RecognitionEngine
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
        # Updating an edited card must not destroy the focused editor/delegate.
        if [r["code"] for r in rows] == [r["code"] for r in self.rows]:
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0), self.index(len(rows)-1), [self.RecordRole])
        else:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()


class Work(QRunnable):
    def __init__(self, job_id, function, emit):
        super().__init__()
        self.job_id, self.function, self.emit = job_id, function, emit

    def run(self):
        try:
            result = self.function(lambda value: self.emit((self.job_id, "progress", value)))
            self.emit((self.job_id, "done", result))
        except Exception as error:
            message = str(error) if isinstance(error, (ValueError, FileNotFoundError)) else f"작업에 실패했습니다 ({type(error).__name__}). 연결 또는 저장 위치를 확인하세요."
            self.emit((self.job_id, "error", message))


class Backend(QObject):
    changed = Signal()
    event = Signal(object)

    def __init__(self, directory: Path, api_factory=None, engine_factory=RecognitionEngine):
        super().__init__()
        self.repository = Repository(directory)
        self.api_factory = api_factory or (lambda authorize=False: sheets.GoogleClient(
            directory, authorize, self.repository.setting("oauth_client", "")))
        self.engine_factory = (lambda: RecognitionEngine(self.repository.setting("ai_thresholds", DEFAULT_THRESHOLDS))) if engine_factory is RecognitionEngine else engine_factory
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
        self.cards = 4
        self.rows = 3
        self.front_only = True
        self.photo_size = 150
        self.undo_stack = []
        self.plan = None
        self.tabs = []
        self.loaded_book = ""
        self.target_url = ""
        self.last_url = ""
        self.message = "작업공간을 만들거나 최근 작업을 여세요."
        self.error = ""
        self.saved = True
        self.recognizing = False
        self.exporting = False
        self.cancel_event = threading.Event()
        self.jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.event.connect(self._event)
        previous = self.repository.setting("last_workspace", "")
        if previous:
            try:
                self.openWorkspace(previous)
            except Exception as error:
                self.error = str(error)

    @Property(QObject, constant=True)
    def pageModel(self):
        return self.model

    @Property("QVariantMap", notify=changed)
    def state(self):
        workspace = self.workspace
        count = workspace.counts if workspace else {"total": 0, "confirmed": 0, "excluded": 0, "missing": 0}
        pages = max(1, (len(self.filtered)+self.page_size-1)//self.page_size)
        current = next((g for g in workspace.garments if g.code == self.current), None) if workspace else None
        return {"hasWorkspace": bool(workspace), "workspaceId": workspace.id if workspace else "",
                "name": workspace.name if workspace else "외투 작업실", "date": workspace.shooting_date if workspace else "",
                "source": workspace.source if workspace else "", "start": workspace.start if workspace else 1,
                "end": workspace.end if workspace else 400, "errors": ", ".join(map(str, workspace.errors)) if workspace else "",
                "counts": count, "recent": self.repository.recent(), "selectedCount": len(self.selected),
                "page": self.page+1, "pages": pages, "visibleCount": len(self.filtered), "mode": self.mode,
                "cards": self.cards, "rows": self.rows, "frontOnly": self.front_only, "photoSize": self.photo_size,
                "filter": self.filter_name, "search": self.search, "current": self._record(current) if current else {},
                "busy": bool(self.jobs), "recognizing": self.recognizing, "exporting": self.exporting,
                "message": self.message, "error": self.error, "saved": self.saved, "canUndo": bool(self.undo_stack),
                "options": OPTIONS, "tabs": self.tabs, "aiThresholds": self.repository.setting("ai_thresholds", DEFAULT_THRESHOLDS), "sheetUrl": self.repository.setting("sheet_url", ""),
                "planReady": self.plan is not None, "planTitle": self.plan.title if self.plan else "",
                "planRows": [[r[0], r[1], r[3], r[5], r[6], r[7]] for r in self.plan.rows] if self.plan else [],
                "lastUrl": self.last_url, "unresolved": len(self.repository.unresolved_exports(workspace.id)) if workspace else 0}

    @property
    def page_size(self):
        return self.cards if self.mode == "review" else self.rows * (3 if self.front_only else 1)

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
                "photos": [{"angle": a, "url": image_url(self.workspace.source, garment.photos.get(a, ""))}
                           for a in sorted(set(garment.photos) | {"0", "1", "2"}, key=int)],
                "issues": " · ".join(garment.issues), "note": garment.suggestion.get("note", ""),
                "analysis": garment.analysis_state, "boundary": False}

    def _refresh(self, reload=True, keep_code=True):
        if self.workspace:
            if reload:
                self.workspace = self.repository.load(self.workspace.id)
            active_codes = {g.code for g in self.workspace.active}
            if self.filter_name != "제외":
                self.selected.intersection_update(active_codes)
            garments = self.workspace.garments if self.filter_name == "제외" else self.workspace.active
            self.filtered = [g for g in garments if (self.filter_name != "제외" or int(g.code) in self.workspace.errors)
                             and (self.filter_name != "미확인" or not g.confirmed)
                             and (self.filter_name != "확인 필요" or g.status == "확인 필요")
                             and (self.filter_name != "확인 완료" or g.status == "확인 완료")
                             and (not self.search or self.search in g.code)]
            codes = [g.code for g in self.filtered]
            if keep_code and self.current in codes:
                self.page = codes.index(self.current)//self.page_size
            self.page = max(0, min(self.page, max(0, (len(codes)-1)//self.page_size)))
            visible = self.filtered[self.page*self.page_size:(self.page+1)*self.page_size]
            if visible and self.current not in [g.code for g in visible]:
                self.current = visible[0].code
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
        self.changed.emit()

    def _view(self):
        if not self.workspace:
            return
        self.repository.update(self.workspace.id, view={"current": self.current, "selected": sorted(self.selected),
            "mode": self.mode, "filter": self.filter_name, "search": self.search, "cards": self.cards,
            "rows": self.rows, "front_only": self.front_only, "photo_size": self.photo_size})

    def _fail(self, error):
        self.error = str(error)
        self.changed.emit()

    def _run(self, label, function, done, kind="normal"):
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = (done, kind)
        self.message, self.error = label, ""
        self.pool.start(Work(job_id, function, self.event.emit))
        self.changed.emit()

    @Slot(object)
    def _event(self, event):
        job_id, action, value = event
        if action == "progress":
            self.message = str(value)
            self._refresh()
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
        self._refresh()

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
    def openWorkspace(self, workspace_id):
        if self.jobs:
            self._fail("진행 중인 작업을 마친 뒤 다른 작업공간을 여세요.")
            return
        try:
            self.workspace = self.repository.load(workspace_id)
            view = self.workspace.view
            self.mode = view.get("mode", "review")
            self.cards = view.get("cards", 4)
            self.rows = view.get("rows", 3)
            self.front_only = view.get("front_only", True)
            self.photo_size = view.get("photo_size", 150)
            self.filter_name = view.get("filter", "전체")
            self.search = view.get("search", "")
            self.current = view.get("current", "")
            self.selected = set(view.get("selected", []))
            self.undo_stack, self.plan, self.tabs, self.last_url = [], None, [], ""
            self.loaded_book = ""
            self.page = 0
            self.repository.set_setting("last_workspace", workspace_id)
            self.message, self.error = "작업공간을 열었습니다. 입력 내용은 자동 저장됩니다.", ""
            self._refresh(False)
        except Exception as error:
            self._fail(error)

    @Slot()
    def dismissError(self):
        self.error = ""
        self.changed.emit()

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
        previous = self.repository.edit(self.workspace.id, codes, change)
        self.undo_stack.append(previous)
        self.undo_stack = self.undo_stack[-100:]
        self.plan, self.error, self.saved = None, "", True
        self.message = "저장 완료"
        self._refresh()

    @Slot(str, str, str)
    def setField(self, code, key, value):
        try:
            validate_fields({key: value.strip()})
            def change(garment):
                if garment.fields.get(key, "") != value.strip():
                    garment.fields[key] = value.strip()
                    garment.confirmed = False
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
                if any(garment.fields.get(k) != v for k, v in values.items()):
                    garment.fields.update(values)
                    garment.confirmed = False
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
                    garment.fields.update(values)
                    garment.confirmed = False
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
                if not garment.complete:
                    raise ValueError(f"{garment.code}: 카테고리·성별·연령·사이즈를 모두 입력하세요.")
                garment.confirmed = True
            self._edit(sorted(self.selected), change)
        except Exception as error:
            self._fail(error)

    @Slot()
    def clearSelected(self):
        try:
            def change(garment):
                garment.fields = {}
                garment.confirmed = False
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
    def setAiThresholds(self, panel, shape, size):
        if self.jobs:
            self._fail("분석을 중단한 뒤 기준을 바꾸세요.")
            return
        if not all(1 <= v <= 100 for v in (panel, shape, size)):
            self._fail("보류 기준은 1~100%로 입력하세요.")
            return
        self.repository.set_setting("ai_thresholds", dict(zip(("panel", "shape", "size"), (panel/100, shape/100, size/100), strict=True)))
        self.message = "AI 보류 기준 저장 완료. 다시 분석하면 새 기준을 적용합니다."
        self.changed.emit()

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
            engine = self.engine_factory()
            completed = 0
            for g in garments:
                if self.cancel_event.is_set():
                    break
                try:
                    key = engine.key(workspace, g)
                    if not force and key == g.analysis_key and g.analysis_state == "done":
                        completed += 1
                        continue
                    suggestion = engine.analyze(workspace, g)
                    state = "done"
                except Exception as error:
                    key, state = "", "failed"
                    suggestion = {"values": {}, "review": True, "note": f"사진 인식 실패 ({type(error).__name__}). 사진과 영역을 확인하세요."}
                def save(item):
                    item.suggestion, item.analysis_key, item.analysis_state = suggestion, key, state
                self.repository.edit(workspace.id, [g.code], save)
                completed += 1
                progress(f"분석 · {completed} / {len(garments)}벌 · {g.code}")
            return completed
        def done(count):
            self.message = f"분석 {'중단' if self.cancel_event.is_set() else '완료'} · {count}벌 처리 · 제안을 확인하고 적용하세요."
        self._run("현재 화면의 옷부터 분석합니다…", analyze, done, "recognition")

    @Slot()
    def cancelAnalysis(self):
        self.cancel_event.set()
        self.message = "현재 옷의 처리를 마친 뒤 중단합니다. 완료한 결과는 저장됩니다."
        self.changed.emit()

    @Slot()
    def invalidatePlan(self):
        if self.plan:
            self.plan = None
            self.changed.emit()

    @Slot(str)
    def sheetTargetChanged(self, url):
        self.target_url = url
        self.plan = None
        try:
            same_book = spreadsheet_id(url) == self.loaded_book
        except ValueError:
            same_book = False
        if not same_book:
            self.tabs = []
            self.loaded_book = ""
        self.changed.emit()

    @Slot(str)
    def setOAuthClient(self, path):
        try:
            self.repository.set_setting("oauth_client", local_path(path))
            self.message = "관리자 연결 설정을 저장했습니다. Google 로그인을 진행하세요."
            self.changed.emit()
        except Exception as error:
            self._fail(error)

    @Slot()
    def login(self):
        if not self.jobs:
            self._run("브라우저에서 Google 로그인을 완료하세요…", lambda progress: bool(self.api_factory(True)),
                      lambda value: setattr(self, "message", "Google 로그인 완료"))

    @Slot(str)
    def loadTabs(self, url):
        if self.jobs:
            return
        try:
            book = spreadsheet_id(url)
            self.target_url = url
            self.repository.set_setting("sheet_url", url)
            self.plan, self.tabs = None, []
            def done(tabs):
                if self.target_url != url:
                    self.message = "주소가 변경되었습니다. 탭을 다시 불러오세요."
                    return
                self.tabs = tabs
                self.loaded_book = book
                self.message = "복제할 양식 탭을 선택하고 미리보기를 만드세요."
            self._run("시트 탭을 불러옵니다…", lambda progress: self.api_factory().tabs(book), done)
        except Exception as error:
            self._fail(error)

    @Slot(str, int, str)
    def preview(self, url, source_id, title):
        if not self.workspace or self.jobs:
            return
        workspace = self.repository.load(self.workspace.id)
        self.plan = None
        try:
            book = spreadsheet_id(url)
            if book != self.loaded_book or source_id not in {t["sheetId"] for t in self.tabs}:
                raise ValueError("대상 파일의 탭을 먼저 다시 불러오세요.")
            def prepare(progress):
                api = self.api_factory()
                return sheets.build_plan(workspace, api.read(book, source_id), api.tabs(book), book, title)
            def done(plan):
                if self.workspace.export_fingerprint() != plan.workspace_digest:
                    raise ValueError("미리보기를 만드는 동안 입력이 바뀌었습니다. 다시 만드세요.")
                self.plan = plan
                self.message = f"새 탭 {plan.title} · {len(plan.rows)}벌 · 확인 후 전송하세요."
            self._run("양식과 전송할 값을 확인합니다…", prepare, done)
        except Exception as error:
            self._fail(error)

    @Slot()
    def send(self):
        if self.jobs or not self.plan:
            return
        plan, self.plan = self.plan, None
        self.exporting = True
        def done(url):
            self.last_url = url
            self.message = "전송 후 입력값을 다시 읽어 확인했습니다. 새 탭을 열 수 있습니다."
        self._run("새 탭에 전송합니다…", lambda progress: sheets.send(self.repository, plan, self.api_factory()), done, "export")

    @Slot()
    def checkPreviousExport(self):
        if self.jobs or not self.workspace:
            return
        records = self.repository.unresolved_exports(self.workspace.id)
        if not records:
            return
        def done(url):
            self.last_url = url
            self.message = "이전 전송 결과를 확인했습니다." if url else "이전 전송의 새 탭이 생성되지 않았습니다. 새 미리보기를 만들 수 있습니다."
        self._run("이전 전송 결과를 조회합니다…", lambda progress: sheets.inspect_export(
            self.repository, records[0]["id"], self.api_factory()), done)

    def shutdown(self):
        self.cancel_event.set()
        self.pool.waitForDone()
