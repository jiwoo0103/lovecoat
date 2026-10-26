from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .domain import Garment

COLUMNS = {"category": 4, "gender": 6, "age": 7, "size": 8}
HEADERS = {1: "번호", 2: "외투코드", 3: "이미지", 4: "카테고리", 6: "남성/여성", 7: "성인/유아동", 8: "사이즈"}
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def spreadsheet_id(text: str) -> str:
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", text)
    value = match[1] if match else text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
        raise ValueError("올바른 Google 스프레드시트 주소를 입력하세요.")
    return value


def cell_value(cell: dict):
    value = cell.get("userEnteredValue", {})
    if "formulaValue" in value:
        return value["formulaValue"]
    return value.get("stringValue", value.get("numberValue", value.get("boolValue", "")))


def canonical_code(value) -> str:
    text = str(value).strip()
    if isinstance(value, (float, int)) and float(value).is_integer():
        text = str(int(value))
    if not re.fullmatch(r"\d+", text, re.ASCII):
        raise ValueError(f"숫자가 아닌 외투코드가 있습니다: {text[:30]}")
    return str(int(text))


@dataclass
class Snapshot:
    title: str
    sheet_id: int
    row_count: int
    cells: dict[tuple[int, int], dict]
    protected: list[dict] = field(default_factory=list)
    merges: list[dict] = field(default_factory=list)
    conditional_formats: list[dict] = field(default_factory=list)
    row_heights: dict[int, int] = field(default_factory=dict)

    def cell(self, row: int, col: int) -> dict:
        return self.cells.get((row, col), {})

    def value(self, row: int, col: int):
        return cell_value(self.cell(row, col))

    def fingerprint(self) -> str:
        data = {
            "title": self.title, "sheet": self.sheet_id, "rows": self.row_count,
            "cells": [[r, c, v] for (r, c), v in sorted(self.cells.items())],
            "protected": self.protected, "merges": self.merges,
            "conditional": self.conditional_formats, "heights": self.row_heights,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


@dataclass
class SheetPlan:
    snapshot: Snapshot
    requests: list[dict]
    changes: list[dict]
    expected: dict[tuple[int, int], object]
    new_count: int
    extended_rows: int


def in_range(row: int, col: int, area: dict) -> bool:
    return (area.get("startRowIndex", 0) <= row < area.get("endRowIndex", 10**9)
            and area.get("startColumnIndex", 0) <= col < area.get("endColumnIndex", 10**9))


def assert_writable(snapshot: Snapshot, row: int, col: int) -> None:
    if any(in_range(row, col, area) for area in snapshot.merges):
        raise ValueError(f"{row + 1}행에 병합 셀이 있어 자동 입력을 중단했습니다.")
    for protected in snapshot.protected:
        # Named range protections without a resolved range are conservatively blocked.
        area = protected.get("range", {})
        if in_range(row, col, area):
            if not any(in_range(row, col, free) for free in protected.get("unprotectedRanges", [])):
                raise ValueError(f"{row + 1}행에 보호 범위가 있습니다. 시트에서 입력 가능 범위를 확인하세요.")


def grid_range(sheet_id: int, row: int, col: int, end_row: int | None = None, end_col: int | None = None) -> dict:
    return {"sheetId": sheet_id, "startRowIndex": row, "endRowIndex": end_row or row + 1,
            "startColumnIndex": col, "endColumnIndex": end_col or col + 1}


def validate_dropdown(snapshot: Snapshot, row: int, col: int, value: str) -> None:
    rule = snapshot.cell(row, col).get("dataValidation", {})
    condition = rule.get("condition", {})
    if condition.get("type") == "ONE_OF_LIST":
        allowed = [v["userEnteredValue"] for v in condition.get("values", [])]
        if value not in allowed:
            raise ValueError(f"{row + 1}행의 허용 목록에 '{value}'가 없습니다. 시트의 목록을 먼저 확인하세요.")
    elif condition:
        # Resolve dropdowns pointing to ranges before authorizing writes.
        raise ValueError(f"{row + 1}행에 수식/범위 기반 유효성 검사가 있습니다. 이 버전은 고정 목록만 자동 입력합니다.")


def translated_formula(formula: str, source_row: int, destination_row: int) -> str:
    # Only the known image formula contract is automatically propagated.
    compact = re.sub(r"\s+", "", formula).upper()
    ref = f"C{source_row + 1}"
    if compact not in {f'=IF({ref}<>"","0_"&{ref},"")', f'="0_"&{ref}'}:
        raise ValueError("이미지 수식이 예상한 규칙과 다릅니다. 기존 양식의 D열 수식을 확인하세요.")
    return formula.replace(f"C{source_row + 1}", f"C{destination_row + 1}").replace(
        f"c{source_row + 1}", f"C{destination_row + 1}")


def build_plan(snapshot: Snapshot, garments: list[Garment]) -> SheetPlan:
    for col, label in HEADERS.items():
        if str(snapshot.value(1, col)).replace(" ", "") != label:
            raise ValueError(f"서식 확인 실패: {chr(65 + col)}2는 '{label}'이어야 합니다.")
    active = [g for g in garments if not g.excluded]
    if len({canonical_code(g.code) for g in active}) != len(active):
        raise ValueError("작업에 중복 외투코드가 있습니다.")
    existing = {}
    ordinals = set()
    last_data_row = 1
    for row in range(2, snapshot.row_count):
        code = snapshot.value(row, 2)
        number = snapshot.value(row, 1)
        if code != "":
            key = canonical_code(code)
            if key in existing:
                raise ValueError(f"시트에 중복 외투코드가 있습니다: {code}")
            existing[key] = row
            if not str(number).isdigit() or int(number) < 1 or int(number) in ordinals:
                raise ValueError(f"{row + 1}행의 기존 번호가 없거나 중복됩니다. 시트에서 확인하세요.")
            ordinals.add(int(number))
            last_data_row = row
        elif number != "":
            raise ValueError(f"{row + 1}행은 번호만 있고 외투코드가 비어 있습니다.")
    next_number = max(ordinals, default=0) + 1
    templates = [r for r in range(2, snapshot.row_count)
                 if "formulaValue" in snapshot.cell(r, 3).get("userEnteredValue", {})]
    if not templates:
        raise ValueError("D열에서 기존 이미지 수식을 찾지 못했습니다.")
    template = templates[-1]
    translated_formula(str(snapshot.value(template, 3)), template, template)
    requests: list[dict] = []
    changes = []
    expected = {}
    next_row = last_data_row + 1
    new_rows = []

    def put(row: int, col: int, value, code: str):
        if snapshot.value(row, col) == value:
            return
        assert_writable(snapshot, row, col)
        old = snapshot.cell(row, col).get("userEnteredValue", {})
        if "formulaValue" in old:
            raise ValueError(f"{row + 1}행 {chr(65 + col)}열에 기존 수식이 있어 덮어쓸 수 없습니다.")
        if col in COLUMNS.values():
            validate_dropdown(snapshot, row if row < snapshot.row_count else template, col, str(value))
        requests.append({"updateCells": {
            "range": grid_range(snapshot.sheet_id, row, col),
            "rows": [{"values": [{"userEnteredValue": {
                "numberValue" if isinstance(value, int) else "stringValue": value,
            }}]}], "fields": "userEnteredValue",
        }})
        changes.append({"row": row + 1, "column": chr(65 + col), "code": code,
                        "before": snapshot.value(row, col), "after": value})
        expected[row, col] = value

    for garment in active:
        key = canonical_code(garment.code)
        if key in existing:
            row = existing[key]
        else:
            row = next_row
            # Never fill deleted gaps or overwrite partially prepared rows.
            if any(snapshot.value(row, col) != "" for col in range(1, 14) if col != 3):
                raise ValueError(f"{row + 1}행에 기존 내용이 있습니다. 연속된 빈 입력 행을 준비하세요.")
            new_rows.append(row)
            next_row += 1
            put(row, 1, next_number, garment.code)
            put(row, 2, garment.code, garment.code)
            next_number += 1
            image_formula = snapshot.value(row, 3)
            if image_formula:
                translated_formula(str(image_formula), row, row)
            else:
                assert_writable(snapshot, row, 3)
                formula = translated_formula(str(snapshot.value(template, 3)), template, row)
                requests.append({"copyPaste": {
                    "source": grid_range(snapshot.sheet_id, template, 3),
                    "destination": grid_range(snapshot.sheet_id, row, 3),
                    "pasteType": "PASTE_FORMULA",
                }})
                expected[row, 3] = formula
                changes.append({"row": row + 1, "column": "D", "code": garment.code,
                                "before": "", "after": formula})
        for key, col in COLUMNS.items():
            if garment.fields.get(key):
                put(row, col, garment.fields[key], garment.code)
    extra = max(0, next_row - snapshot.row_count)
    if extra:
        # Extension is explicit in preview; copy styles/validation only, not old values.
        for col in range(1, 14):
            assert_writable(snapshot, snapshot.row_count, col)
        # Unknown formulas outside D need a deliberate template decision.
        if any("formulaValue" in snapshot.cell(template, col).get("userEnteredValue", {})
               for col in range(1, 14) if col != 3):
            raise ValueError("행 확장 기준에 다른 수식이 있습니다. 시트에서 미리 빈 행을 준비하세요.")
        prelude = [{"appendDimension": {"sheetId": snapshot.sheet_id, "dimension": "ROWS", "length": extra}}]
        for paste_type in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
            prelude.append({"copyPaste": {
                "source": grid_range(snapshot.sheet_id, template, 1, end_col=14),
                "destination": grid_range(snapshot.sheet_id, snapshot.row_count, 1,
                                          end_row=next_row, end_col=14), "pasteType": paste_type,
            }})
        if template in snapshot.row_heights:
            prelude.append({"updateDimensionProperties": {
                "range": {"sheetId": snapshot.sheet_id, "dimension": "ROWS",
                          "startIndex": snapshot.row_count, "endIndex": next_row},
                "properties": {"pixelSize": snapshot.row_heights[template]}, "fields": "pixelSize",
            }})
        for index, rule in enumerate(snapshot.conditional_formats):
            updated = copy.deepcopy(rule)
            for area in updated.get("ranges", []):
                if area.get("endRowIndex") == snapshot.row_count:
                    area["endRowIndex"] = next_row
            if updated != rule:
                prelude.append({"updateConditionalFormatRule": {
                    "sheetId": snapshot.sheet_id, "index": index, "rule": updated,
                }})
        requests = prelude + requests
    return SheetPlan(snapshot, requests, changes, expected, len(new_rows), extra)


def snapshot_from_response(response: dict) -> Snapshot:
    sheet = response["sheets"][0]
    properties = sheet["properties"]
    cells = {}
    heights = {}
    for block in sheet.get("data", []):
        for i, row in enumerate(block.get("rowData", []), block.get("startRow", 0)):
            for j, cell in enumerate(row.get("values", []), block.get("startColumn", 0)):
                # Effective cached formula results can change without an edit.
                relevant = {k: cell[k] for k in ("userEnteredValue", "dataValidation") if k in cell}
                if relevant:
                    cells[i, j] = relevant
        for i, meta in enumerate(block.get("rowMetadata", []), block.get("startRow", 0)):
            if "pixelSize" in meta:
                heights[i] = meta["pixelSize"]
    return Snapshot(properties["title"], properties["sheetId"], properties["gridProperties"]["rowCount"],
                    cells, sheet.get("protectedRanges", []), sheet.get("merges", []),
                    sheet.get("conditionalFormats", []), heights)


class GoogleSheets:
    """Created and used entirely inside one worker; no shared httplib2 transport."""

    def __init__(self, token_path: Path, client_path: str = "", authorize: bool = False):
        import httplib2
        import win32crypt
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_httplib2 import AuthorizedHttp
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        credentials = None
        if token_path.exists() and not authorize:
            try:
                raw = win32crypt.CryptUnprotectData(token_path.read_bytes(), None, None, None, 0)[1]
                credentials = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
            except Exception:
                raise ValueError("저장된 Google 로그인을 읽지 못했습니다. 연결 설정에서 다시 로그인하세요.") from None
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            if not authorize or not client_path:
                raise ValueError("먼저 Google 연결 설정에서 로그인하세요.")
            flow = InstalledAppFlow.from_client_secrets_file(client_path, SCOPES)
            credentials = flow.run_local_server(
                host="127.0.0.1", port=0, timeout_seconds=180, open_browser=True,
                authorization_prompt_message="", success_message="로그인이 완료되었습니다. 외투 작업실로 돌아가세요.",
            )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = win32crypt.CryptProtectData(credentials.to_json().encode(), "CoatDesk", None, None, None, 0)
        temporary = token_path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(token_path)
        self.api = build("sheets", "v4", http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)),
                         cache_discovery=False)

    def tabs(self, book_id: str) -> list[str]:
        response = self.api.spreadsheets().get(
            spreadsheetId=book_id, fields="sheets.properties").execute(num_retries=0)
        return [s["properties"]["title"] for s in response["sheets"]]

    def read(self, book_id: str, tab: str) -> Snapshot:
        title = "'" + tab.replace("'", "''") + "'"
        response = self.api.spreadsheets().get(
            spreadsheetId=book_id, ranges=[f"{title}!A:R"], includeGridData=True,
            fields="sheets(properties,data(startRow,startColumn,rowData(values(userEnteredValue,dataValidation)),"
                   "rowMetadata(pixelSize)),merges,protectedRanges,conditionalFormats)",
        ).execute(num_retries=0)
        return snapshot_from_response(response)

    def apply(self, book_id: str, plan: SheetPlan) -> str:
        fresh = self.read(book_id, plan.snapshot.title)
        if fresh.fingerprint() != plan.snapshot.fingerprint():
            raise ValueError("미리보기 이후 시트가 변경되었습니다. 다시 미리보기를 만들어 확인하세요.")
        if not plan.requests:
            return "변경할 값이 없습니다."
        try:
            self.api.spreadsheets().batchUpdate(
                spreadsheetId=book_id, body={"requests": plan.requests},
            ).execute(num_retries=0)
        except Exception:
            # A timeout can occur AFTER an atomic batch succeeds. Never retry a write blindly.
            try:
                after = self.read(book_id, plan.snapshot.title)
                if all(after.value(r, c) == value for (r, c), value in plan.expected.items()):
                    return "통신이 끊겼지만 다시 읽어 모든 입력값의 반영을 확인했습니다."
            except Exception:
                pass
            raise ValueError("전송 결과를 확인하지 못했습니다. 재전송 전에 새 미리보기를 만드세요. 중복 추가는 외투코드로 검사합니다.") from None
        after = self.read(book_id, plan.snapshot.title)
        if not all(after.value(r, c) == value for (r, c), value in plan.expected.items()):
            raise ValueError("입력 후 값이 달라졌습니다. 공동 편집 여부를 확인하고 다시 미리보기를 만드세요.")
        return f"{len(plan.changes)}개 셀의 입력을 확인했습니다."
