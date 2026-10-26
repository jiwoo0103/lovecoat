from __future__ import annotations

import copy
import json
import os
import re
import secrets
import sys
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

from coatdesk.sheets import HEADERS, SCOPES, cell_value, spreadsheet_id, translated_formula
from .domain import Workspace, fingerprint, validate_fields
from .storage import Repository


@dataclass
class Template:
    id: int
    title: str
    row_count: int
    cells: dict[tuple[int, int], dict]
    protected: list = field(default_factory=list)
    merges: list = field(default_factory=list)
    conditional: list = field(default_factory=list)
    row_height: int = 24
    metadata: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)
    column_count: int = 26
    row_heights: list[int] = field(default_factory=list)

    def value(self, row, col):
        return cell_value(self.cells.get((row, col), {}))

    def digest(self):
        return fingerprint({"id": self.id, "title": self.title, "rows": self.row_count,
                            "cells": [[r, c, v] for (r, c), v in sorted(self.cells.items())],
                            "protected": self.protected, "merges": self.merges,
                            "conditional": self.conditional, "height": self.row_height,
                            "dimensions": self.dimensions, "columns": self.column_count,
                            "row_heights": self.row_heights})


def builtin_template() -> Template:
    """Load the sanitized, versioned template bundled with the application."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    try:
        data = json.loads((root / "resources/standard_sheet.json").read_text(encoding="utf-8"))
        widths, heights = data["column_widths"], data["row_heights"]
        if data["version"] != 1 or len(heights) != 3 or not widths:
            raise ValueError
        if not all(type(v) is int and v > 0 for v in widths + heights):
            raise ValueError
        template = Template(-1, "내장 표준 양식", data["row_count"],
                            {(r, c): cell for r, c, cell in data["cells"]}, row_height=heights[2],
                            dimensions=[{"startColumn": 0, "columnMetadata": [{"pixelSize": w} for w in widths]}],
                            column_count=len(widths), row_heights=heights)
        # The packaged resource must never contain original/example garment data.
        for (r, c), cell in template.cells.items():
            if r >= 2 and c < 15:
                value = cell.get("userEnteredValue", {})
                if value and not (r == 2 and (c == 3 or 10 <= c < 14) and "formulaValue" in value):
                    raise ValueError
        validate_template(template)
        return template
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("내장 표준 양식을 읽을 수 없습니다. 프로그램 설치 파일을 확인하세요.") from None


def parse_template(sheet: dict) -> Template:
    cells = {}
    height = 24
    dimensions = []
    for block in sheet.get("data", []):
        for r, row in enumerate(block.get("rowData", []), block.get("startRow", 0)):
            for c, cell in enumerate(row.get("values", []), block.get("startColumn", 0)):
                item = {k: cell[k] for k in ("userEnteredValue", "userEnteredFormat", "dataValidation", "note") if k in cell}
                if item:
                    cells[r, c] = item
        dimensions.append({k: block[k] for k in ("startColumn", "columnMetadata") if k in block})
        for r, meta in enumerate(block.get("rowMetadata", []), block.get("startRow", 0)):
            if r == 2:
                height = meta.get("pixelSize", 24)
    p = sheet["properties"]
    return Template(p["sheetId"], p["title"], p["gridProperties"]["rowCount"], cells,
                    sheet.get("protectedRanges", []), sheet.get("merges", []),
                    sheet.get("conditionalFormats", []), height, sheet.get("developerMetadata", []), dimensions)


def grid(sheet_id, row, col, end_row, end_col):
    return {"sheetId": sheet_id, "startRowIndex": row, "endRowIndex": end_row,
            "startColumnIndex": col, "endColumnIndex": end_col}


def next_title(base: str, titles: list[str]) -> str:
    base = base.strip()
    if not base or len(base) > 100 or re.search(r"[\[\]:*?/\\]", base):
        raise ValueError("새 탭 이름을 1~100자로 입력하세요. [ ] : * ? / \\ 는 사용할 수 없습니다.")
    title, suffix = base, 2
    while title in titles:
        tail = f" ({suffix})"
        title = base[:100 - len(tail)] + tail
        suffix += 1
    return title


def dimension_formula(value: str, row: int) -> str:
    compact = re.sub(r"\s+", "", value).upper()
    if compact != f'=IF(B{row + 1}<>"",0,"")':
        raise ValueError("K:N 실측 수식이 지원하는 양식과 다릅니다.")
    return f'=IF(B{row + 1}<>"",0,"")'


def validate_template(template: Template):
    if template.row_count < 3:
        raise ValueError("양식에 3행 입력 기준이 없습니다.")
    for col, label in HEADERS.items():
        if str(template.value(1, col)).replace(" ", "") != label:
            raise ValueError(f"{chr(65 + col)}2 헤더는 '{label}'이어야 합니다.")
    translated_formula(str(template.value(2, 3)), 2, 2)
    for col in range(10, 14):
        dimension_formula(str(template.value(2, col)), 2)
    for (r, c), cell in template.cells.items():
        if r < 2 or not 1 <= c < 14:
            continue
        formula = cell.get("userEnteredValue", {}).get("formulaValue")
        if formula:
            if c == 3:
                translated_formula(formula, r, r)
            elif 10 <= c < 14:
                dimension_formula(formula, r)
            else:
                raise ValueError(f"{chr(65+c)}{r+1}에 지원하지 않는 수식이 있습니다.")
    for area in template.merges + [p.get("range", {}) for p in template.protected]:
        if area.get("endRowIndex", template.row_count) > 2 and area.get("endColumnIndex", 14) > 1 and area.get("startColumnIndex", 0) < 14:
            raise ValueError("입력 영역에 병합 또는 보호 범위가 있어 전송할 수 없습니다.")


def dropdown(template: Template, col: int, value: str):
    validation = template.cells.get((2, col), {}).get("dataValidation", {})
    condition = validation.get("condition", {})
    if condition and condition.get("type") != "ONE_OF_LIST":
        raise ValueError(f"{chr(65+col)}열은 고정 목록 유효성 검사만 지원합니다.")
    if template.id == -1 and col == 8 and validation.get("strict") is False:
        # Excel lists adult sizes as suggestions. The app also supports child
        # mappings and direct size input; preserve that warning-only validation.
        return
    if condition and value not in [v.get("userEnteredValue") for v in condition.get("values", [])]:
        raise ValueError(f"{chr(65+col)}열 허용 목록에 '{value}'가 없습니다.")


@dataclass
class ExportPlan:
    id: str
    workspace_id: str
    workspace_digest: str
    book_id: str
    source_id: int
    source_digest: str
    sheet_id: int
    title: str
    requests: list[dict]
    rows: list[list]
    counts: dict

    def payload(self):
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def assert_new_tab_only(requests: list[dict], source_id: int, new_id: int):
    if new_id == source_id or not requests:
        raise ValueError("새 탭 전용 전송 규칙을 위반했습니다.")
    if type(new_id) is not int or not 0 <= new_id < 2**31:
        raise ValueError("새 탭 ID가 올바르지 않습니다.")
    if source_id == -1:
        if set(requests[0]) != {"addSheet"} or set(requests[0]["addSheet"]) != {"properties"}:
            raise ValueError("내장 양식의 새 탭 생성 요청이 올바르지 않습니다.")
        properties = requests[0]["addSheet"]["properties"]
        if properties.get("sheetId") != new_id or set(properties) - {"sheetId", "title", "sheetType", "gridProperties"}:
            raise ValueError("내장 양식의 새 탭 생성 요청이 올바르지 않습니다.")
    else:
        first = requests[0].get("duplicateSheet", {})
        if len(requests[0]) != 1 or first.get("sourceSheetId") != source_id or first.get("newSheetId") != new_id:
            raise ValueError("양식 복제 요청이 올바르지 않습니다.")
    allowed = {"appendDimension", "copyPaste", "updateCells", "updateDimensionProperties",
               "updateConditionalFormatRule", "createDeveloperMetadata"}

    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "sheetId" and item != new_id:
                    raise ValueError("기존 탭을 수정하는 요청을 차단했습니다.")
                if key in {"spreadsheet", "allSheets", "sourceSheetId"}:
                    raise ValueError("전역 변경 요청을 차단했습니다.")
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)

    if source_id == -1:
        inspect(requests[0])
    for request in requests[1:]:
        if len(request) != 1 or next(iter(request)) not in allowed:
            raise ValueError("지원하지 않는 전송 요청입니다.")
        kind, body = next(iter(request.items()))
        if kind in {"appendDimension", "updateConditionalFormatRule"}:
            destinations = [body.get("sheetId")]
        elif kind == "copyPaste":
            destinations = [body.get(k, {}).get("sheetId") for k in ("source", "destination")]
        elif kind == "createDeveloperMetadata":
            destinations = [body.get("developerMetadata", {}).get("location", {}).get("sheetId")]
        else:
            destinations = [body.get("range", {}).get("sheetId")]
        if any(target != new_id for target in destinations):
            raise ValueError("새 탭 ID가 없는 쓰기 요청을 차단했습니다.")
        inspect(request)


def _builtin_requests(template: Template, new_id: int, title: str, total_rows: int) -> list[dict]:
    requests = [{"addSheet": {"properties": {"sheetId": new_id, "title": title, "sheetType": "GRID",
                                             "gridProperties": {"rowCount": total_rows, "columnCount": template.column_count}}}}]
    last_row = max(r for r, _ in template.cells) + 1
    rows = [{"values": [copy.deepcopy(template.cells.get((r, c), {})) for c in range(template.column_count)]}
            for r in range(last_row)]
    requests.append({"updateCells": {"range": grid(new_id, 0, 0, last_row, template.column_count), "rows": rows,
                                     "fields": "userEnteredValue,userEnteredFormat,dataValidation,note"}})
    for dimension in template.dimensions:
        start = dimension.get("startColumn", 0)
        for offset, item in enumerate(dimension.get("columnMetadata", [])):
            requests.append({"updateDimensionProperties": {"range": {"sheetId": new_id, "dimension": "COLUMNS",
                "startIndex": start + offset, "endIndex": start + offset + 1},
                "properties": {"pixelSize": item["pixelSize"]}, "fields": "pixelSize"}})
    for r, height in enumerate(template.row_heights):
        requests.append({"updateDimensionProperties": {"range": {"sheetId": new_id, "dimension": "ROWS",
            "startIndex": r, "endIndex": r + 1 if r < 2 else total_rows},
            "properties": {"pixelSize": height}, "fields": "pixelSize"}})
    return requests


def build_plan(workspace: Workspace, template: Template, tabs: list[dict], book: str, base_title: str) -> ExportPlan:
    active = workspace.active
    if not active:
        raise ValueError("전송할 옷이 없습니다.")
    invalid = [g.code for g in active if not g.export_ready]
    if invalid:
        raise ValueError("필수 입력·검수가 필요한 외투코드: " + ", ".join(invalid[:30]))
    validate_template(template)
    if template.id == -1 and template.digest() != builtin_template().digest():
        raise ValueError("내장 양식이 변경되었습니다. 미리보기를 다시 만드세요.")
    existing_ids = {t["sheetId"] for t in tabs}
    new_id = secrets.randbelow(2**31 - 1)
    while new_id in existing_ids:
        new_id = secrets.randbelow(2**31 - 1)
    title = next_title(base_title, [t["title"] for t in tabs])
    operation = uuid.uuid4().hex
    end = len(active) + 2
    total_rows = max(end, template.row_count)
    builtin = template.id == -1
    requests = (_builtin_requests(template, new_id, title, total_rows) if builtin else
                [{"duplicateSheet": {"sourceSheetId": template.id, "newSheetId": new_id, "newSheetName": title}}])
    if not builtin and end > template.row_count:
        requests.append({"appendDimension": {"sheetId": new_id, "dimension": "ROWS", "length": end-template.row_count}})
    for paste in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
        requests.append({"copyPaste": {"source": grid(new_id, 2, 1, 3, 14),
                                       "destination": grid(new_id, 2, 1, total_rows if builtin else end, 14), "pasteType": paste}})
    # Only B:N below the headers is the input table. P:R helper lists stay intact.
    requests.append({"updateCells": {"range": grid(new_id, 2, 1, total_rows, 14),
                                     "rows": [], "fields": "userEnteredValue"}})
    rows, encoded = [], []
    for i, garment in enumerate(active, 3):
        validate_fields(garment.fields)
        for key, col in (("category", 4), ("gender", 6), ("age", 7), ("size", 8)):
            dropdown(template, col, garment.fields[key])
        image = translated_formula(str(template.value(2, 3)), 2, i-1)
        values = [i-2, garment.code, image, garment.fields["category"], "", garment.fields["gender"],
                  garment.fields["age"], garment.fields["size"], ""] + [f'=IF(B{i}<>"",0,"")'] * 4
        rows.append(values)
        encoded.append({"values": [{"userEnteredValue": {
            "formulaValue" if col in (2, 9, 10, 11, 12) else "numberValue" if isinstance(value, int) else "stringValue": value,
        }} for col, value in enumerate(values)]})
    requests.append({"updateCells": {"range": grid(new_id, 2, 1, end, 14), "rows": encoded, "fields": "userEnteredValue"}})
    requests.append({"updateDimensionProperties": {"range": {"sheetId": new_id, "dimension": "ROWS", "startIndex": 2, "endIndex": end},
                                                    "properties": {"pixelSize": template.row_height}, "fields": "pixelSize"}})
    if end > template.row_count:
        for index, rule in enumerate(template.conditional):
            updated = copy.deepcopy(rule)
            for area in updated.get("ranges", []):
                area["sheetId"] = new_id
                if area.get("endRowIndex") == template.row_count:
                    area["endRowIndex"] = end
            requests.append({"updateConditionalFormatRule": {"sheetId": new_id, "index": index, "rule": updated}})
    requests.append({"createDeveloperMetadata": {"developerMetadata": {
        "metadataKey": "coatdesk_export", "metadataValue": operation,
        "location": {"sheetId": new_id}, "visibility": "DOCUMENT"}}})
    assert_new_tab_only(requests, template.id, new_id)
    return ExportPlan(operation, workspace.id, workspace.export_fingerprint(), spreadsheet_id(book),
                      template.id, template.digest(), new_id, title, requests, rows, workspace.counts)


def _browser_candidates() -> list[Path]:
    """Known installed-browser locations, never shell commands or PATH entries."""
    locations = ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe",
                 "Mozilla Firefox/firefox.exe")
    result = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            for location in locations:
                path = Path(root) / location
                if path.is_file() and path not in result:
                    result.append(path)
    return result


def _windows_default_browser_executable() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            program = winreg.QueryValueEx(key, "ProgId")[0]
        for prefix, name in (("ChromeHTML", "chrome.exe"), ("MSEdgeHTM", "msedge.exe"), ("FirefoxURL", "firefox.exe")):
            if program.startswith(prefix):
                return name
    except OSError:
        pass
    return ""


class _BrowserLaunchError(ValueError):
    pass


class _LoginBrowser:
    def open(self, url, new=1, autoraise=True):
        candidates = _browser_candidates()
        preferred = _windows_default_browser_executable()
        # For known Windows defaults, launch that browser directly. os.startfile
        # can report success without opening a window in some desktop runners.
        chosen = next((p for p in candidates if p.name.lower() == preferred), None)
        controllers = []
        if chosen:
            controllers.append(webbrowser.BackgroundBrowser(str(chosen)))
        else:
            try:
                default = webbrowser.get()
                if not isinstance(default, _LoginBrowser):
                    controllers.append(default)
            except webbrowser.Error:
                pass
        controllers.extend(webbrowser.BackgroundBrowser(str(p)) for p in candidates if p != chosen)
        for browser in controllers:
            try:
                if browser.open(url, new=new, autoraise=autoraise):
                    return True
            except (OSError, webbrowser.Error):
                continue
        raise _BrowserLaunchError("Google 로그인 브라우저를 열지 못했습니다. Chrome·Edge·Firefox 중 설치된 브라우저와 기본 브라우저 설정을 확인하세요.")


class GoogleClient:
    def __init__(self, directory: Path, authorize=False, client_path: str = ""):
        import httplib2
        import win32crypt
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_httplib2 import AuthorizedHttp
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        token = directory / "google-token.dpapi"
        credentials = None
        if token.exists() and not authorize:
            try:
                raw = win32crypt.CryptUnprotectData(token.read_bytes(), None, None, None, 0)[1]
                credentials = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
                if credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
            except Exception:
                raise ValueError("Google 로그인을 갱신하지 못했습니다. 다시 로그인하세요.") from None
        if authorize:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            config_path = Path(client_path) if client_path else base / "resources/google_oauth.json"
            if not config_path.is_file():
                raise ValueError("배포용 Google OAuth 설정이 준비되지 않았습니다. 관리자 설정에서 연결 JSON을 지정하세요.")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            installed = config.get("installed")
            if not isinstance(installed, dict) or not installed.get("client_id") or any(
                    key in value for value in (config, installed)
                    for key in ("refresh_token", "access_token", "private_key")):
                raise ValueError("사용자 토큰이 아닌 데스크톱 앱용 OAuth 연결 JSON을 선택하세요.")
            flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
            webbrowser.register("coatdesk-google-login", None, _LoginBrowser())
            try:
                credentials = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=180, open_browser=True,
                    browser="coatdesk-google-login", authorization_prompt_message="",
                    success_message="로그인이 완료되었습니다. 외투 작업실로 돌아가세요.")
            except Exception as error:
                if type(error).__name__ == "WSGITimeoutError":
                    raise ValueError("Google 로그인 시간이 지났습니다. 브라우저 창을 확인하고 Google 로그인을 다시 누르세요.") from None
                if isinstance(error, _BrowserLaunchError):
                    raise
                raise ValueError("Google 로그인을 완료하지 못했습니다. 테스트 사용자 등록·권한 승인·연결 설정을 확인하고 다시 로그인하세요.") from None
        if not credentials or not credentials.valid:
            raise ValueError("먼저 Google에 로그인하세요.")
        encrypted = win32crypt.CryptProtectData(credentials.to_json().encode(), "CoatDesk v2", None, None, None, 0)
        temporary = token.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(token)
        self.api = build("sheets", "v4", http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)), cache_discovery=False)

    def tabs(self, book: str) -> list[dict]:
        result = self.api.spreadsheets().get(spreadsheetId=book, fields="sheets.properties").execute(num_retries=0)
        return [s["properties"] for s in result["sheets"]]

    def read(self, book: str, sheet_id: int) -> Template:
        result = self.api.spreadsheets().getByDataFilter(spreadsheetId=book,
            body={"dataFilters": [{"gridRange": {"sheetId": sheet_id}}], "includeGridData": True},
            fields="sheets(properties,data(startRow,startColumn,rowData(values(userEnteredValue,userEnteredFormat,dataValidation,note)),rowMetadata(pixelSize),columnMetadata(pixelSize)),merges,protectedRanges,conditionalFormats,developerMetadata)"
        ).execute(num_retries=0)
        matches = [s for s in result.get("sheets", []) if s["properties"]["sheetId"] == sheet_id]
        if not matches:
            raise ValueError("시트 탭을 찾을 수 없습니다.")
        return parse_template(matches[0])

    def batch(self, book, requests):
        return self.api.spreadsheets().batchUpdate(spreadsheetId=book, body={"requests": requests}).execute(num_retries=0)


def _export_formula_key(value: str, row: int, col: int) -> str:
    if not isinstance(value, str):
        raise ValueError("수식이 아닙니다.")
    if col == 3:
        translated_formula(value, row, row)
    else:
        dimension_formula(value, row)
    # Google may insert spaces and change function/reference casing. Spaces
    # inside quoted literals affect the result and must remain significant.
    parts = re.split(r'("(?:[^"]|"")*")', value)
    return "".join(part if i % 2 else re.sub(r"\s+", "", part).upper()
                   for i, part in enumerate(parts))


def matches_export(template: Template, payload: dict) -> bool:
    identity = any(m.get("metadataKey") == "coatdesk_export" and m.get("metadataValue") == payload["id"] for m in template.metadata)
    if not identity:
        return False
    for r, row in enumerate(payload["rows"], 2):
        for c, value in enumerate(row, 1):
            if c == 3 or 10 <= c < 14:
                actual = template.cells.get((r, c), {}).get("userEnteredValue", {}).get("formulaValue")
                try:
                    if _export_formula_key(actual, r, c) != _export_formula_key(value, r, c):
                        return False
                except ValueError:
                    return False
            elif template.value(r, c) != value:
                return False
    return all(template.value(r, 2) == "" for r in range(len(payload["rows"])+2, template.row_count))


def inspect_export(repository: Repository, export_id: str, api) -> str:
    record = repository.export(export_id)
    payload = record["payload"]
    tabs = api.tabs(payload["book_id"])
    if not any(t["sheetId"] == payload["sheet_id"] for t in tabs):
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(record["updated"])).total_seconds()
        if elapsed < 190:
            raise ValueError("아직 새 탭이 보이지 않습니다. 서버 처리가 끝날 수 있도록 3분 후 결과 확인을 다시 실행하세요.")
        repository.record_export(export_id, record["workspace_id"], "failed", payload)
        return ""
    tab = api.read(payload["book_id"], payload["sheet_id"])
    if not matches_export(tab, payload):
        raise ValueError("전송 결과가 예상과 다릅니다. 재전송하지 말고 생성된 탭을 확인하세요.")
    repository.record_export(export_id, record["workspace_id"], "succeeded", payload)
    return f"https://docs.google.com/spreadsheets/d/{payload['book_id']}/edit#gid={payload['sheet_id']}"


def send(repository: Repository, plan: ExportPlan, api) -> str:
    if repository.has_export(plan.id):
        raise ValueError("이미 시도한 전송입니다. 결과를 조회하거나 새 미리보기를 만드세요.")
    existing = repository.unresolved_exports(plan.workspace_id)
    if existing:
        raise ValueError("결과를 확인하지 못한 전송이 있습니다. 먼저 이전 전송 결과를 확인하세요.")
    if repository.load(plan.workspace_id).export_fingerprint() != plan.workspace_digest:
        raise ValueError("입력 또는 제외번호가 바뀌었습니다. 미리보기를 다시 만드세요.")
    fresh = builtin_template() if plan.source_id == -1 else api.read(plan.book_id, plan.source_id)
    if fresh.digest() != plan.source_digest:
        raise ValueError("양식이 변경되었습니다. 미리보기를 다시 만드세요.")
    tabs = api.tabs(plan.book_id)
    if any(t["title"] == plan.title or t["sheetId"] == plan.sheet_id for t in tabs):
        raise ValueError("새 탭 이름 또는 ID가 사용 중입니다. 미리보기를 다시 만드세요.")
    assert_new_tab_only(plan.requests, plan.source_id, plan.sheet_id)
    payload = plan.payload()
    repository.record_export(plan.id, plan.workspace_id, "sending", payload)
    try:
        api.batch(plan.book_id, plan.requests)
    except Exception as error:
        status = getattr(getattr(error, "resp", None), "status", 0)
        # A definite rejection is safe to preview anew; a lost response is not.
        rejected = 400 <= status < 500 and status != 408
        repository.record_export(plan.id, plan.workspace_id, "failed" if rejected else "unknown", payload)
        if rejected:
            raise ValueError(f"Google에서 요청을 거절했습니다 (HTTP {status}). 권한·한도·탭 이름을 확인하고 미리보기를 다시 만드세요.") from None
    try:
        return inspect_export(repository, plan.id, api)
    except Exception:
        repository.record_export(plan.id, plan.workspace_id, "unknown", payload)
        raise ValueError("전송 결과를 확인하지 못했습니다. '이전 전송 결과 확인'을 사용하세요. 새 탭을 반복 생성하지 않습니다.") from None
