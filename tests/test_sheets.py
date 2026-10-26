import copy

import pytest

from coatdesk.domain import Garment
from coatdesk.sheets import GoogleSheets, HEADERS, Snapshot, build_plan, translated_formula


def entry(value):
    if isinstance(value, int):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"formulaValue" if value.startswith("=") else "stringValue": value}}


def sheet(row_count=12):
    cells = {(1, col): entry(label) for col, label in HEADERS.items()}
    cells[0, 2] = entry('="일일수량: " & COUNT(B:B)')
    for row in range(2, row_count):
        cells[row, 3] = entry(f'=IF(C{row + 1}<>"", "0_" & C{row + 1}, "")')
        cells[row, 8] = {"dataValidation": {"condition": {"type": "ONE_OF_LIST",
                          "values": [{"userEnteredValue": v} for v in ["XS", "S", "M", "L"]]}}}
    return Snapshot("20261020", 42, row_count, cells)


def garment(code, **fields):
    return Garment(code, "", {}, fields=fields)


def apply_fake(snapshot, plan):
    result = copy.deepcopy(snapshot)
    result.row_count += plan.extended_rows
    for position, value in plan.expected.items():
        result.cells.setdefault(position, {}).update(entry(value))
    return result


def test_new_codes_keep_zeros_and_skip_excluded():
    snapshot = sheet()
    items = [garment("0248", size="XS"), garment("0249"), garment("0250", gender="여성")]
    items[1].excluded = True
    original = copy.deepcopy(snapshot)
    plan = build_plan(snapshot, items)
    assert plan.expected[2, 1] == 1
    assert plan.expected[3, 1] == 2
    assert plan.expected[3, 2] == "0250"
    assert plan.expected[2, 2] == "0248"
    assert plan.new_count == 2
    assert snapshot == original
    assert all(r.get("updateCells", {}).get("fields") == "userEnteredValue" for r in plan.requests)
    assert not any((r, 3) in plan.expected for r in range(snapshot.row_count))
    assert build_plan(apply_fake(snapshot, plan), items).changes == []


def test_existing_rows_update_only_requested_values():
    snapshot = sheet()
    snapshot.cells[2, 1] = entry(114)
    snapshot.cells[2, 2] = entry("0248")
    snapshot.cells[2, 4] = entry("코트")
    snapshot.cells[2, 9] = entry("보존할 설명")
    snapshot.cells[3, 1] = entry(115)
    snapshot.cells[3, 2] = entry("0250")
    plan = build_plan(snapshot, [garment("0248", size="L"), garment("0255", size="M")])
    assert (2, 4) not in plan.expected
    assert (2, 9) not in plan.expected
    assert (2, 1) not in plan.expected
    assert plan.expected[4, 1] == 116
    assert plan.expected[4, 2] == "0255"


def test_numeric_existing_codes_match_padded_codes():
    snapshot = sheet()
    snapshot.cells[2, 1] = entry(1)
    snapshot.cells[2, 2] = entry(1)
    plan = build_plan(snapshot, [garment("0001", size="M")])
    assert plan.new_count == 0
    assert plan.expected == {(2, 8): "M"}


@pytest.mark.parametrize("defect", ["header", "duplicate", "formula", "protected", "merged", "dropdown", "partial"])
def test_dangerous_sheet_conditions_block_writes(defect):
    snapshot = sheet()
    items = [garment("0001", size="M")]
    if defect == "header":
        snapshot.cells[1, 2] = entry("다른열")
    elif defect == "duplicate":
        for row in (2, 3):
            snapshot.cells[row, 1] = entry(row - 1)
            snapshot.cells[row, 2] = entry("0001")
    elif defect == "formula":
        snapshot.cells[2, 1] = entry(1)
        snapshot.cells[2, 2] = entry("0001")
        snapshot.cells[2, 8] = entry("=A1")
    elif defect == "protected":
        snapshot.protected = [{"range": {"startRowIndex": 2, "endRowIndex": 3}}]
    elif defect == "merged":
        snapshot.merges = [{"startRowIndex": 2, "endRowIndex": 4}]
    elif defect == "dropdown":
        items[0].fields["size"] = "XXXXL"
    elif defect == "partial":
        snapshot.cells[2, 9] = entry("다른 사람의 작업")
    with pytest.raises(ValueError):
        build_plan(snapshot, items)


def test_extension_copies_formats_rules_and_image_formula_only():
    snapshot = sheet(3)
    snapshot.cells[2, 1] = entry(1)
    snapshot.cells[2, 2] = entry("0001")
    snapshot.row_heights[2] = 38
    snapshot.conditional_formats = [{"ranges": [{"sheetId": 42, "startRowIndex": 2, "endRowIndex": 3}],
                                     "booleanRule": {"condition": {"type": "BLANK"}, "format": {}}}]
    plan = build_plan(snapshot, [garment("0002", size="L"), garment("0004")])
    assert plan.extended_rows == 2
    assert plan.requests[0] == {"appendDimension": {"sheetId": 42, "dimension": "ROWS", "length": 2}}
    types = [r["copyPaste"]["pasteType"] for r in plan.requests if "copyPaste" in r]
    assert set(types) == {"PASTE_FORMAT", "PASTE_DATA_VALIDATION", "PASTE_FORMULA"}
    assert plan.expected[3, 3] == '=IF(C4<>"", "0_" & C4, "")'
    assert plan.expected[4, 1] == 3
    assert any("updateConditionalFormatRule" in r for r in plan.requests)
    assert any("updateDimensionProperties" in r for r in plan.requests)


def test_unknown_image_formula_blocked():
    with pytest.raises(ValueError):
        translated_formula("=IMPORTRANGE(A1,A2)", 2, 4)


class FakeRequest:
    def __init__(self, callback):
        self.callback = callback

    def execute(self, **kwargs):
        return self.callback()


class FakeApi:
    def __init__(self, callback):
        self.callback = callback
        self.writes = 0

    def spreadsheets(self):
        return self

    def batchUpdate(self, **kwargs):
        self.writes += 1
        return FakeRequest(self.callback)


def test_concurrent_edit_blocks_send():
    snapshot = sheet()
    plan = build_plan(snapshot, [garment("0001")])
    fresh = copy.deepcopy(snapshot)
    fresh.cells[2, 4] = entry("코트")
    api = GoogleSheets.__new__(GoogleSheets)
    api.api = FakeApi(lambda: None)
    api.read = lambda *_: fresh
    with pytest.raises(ValueError, match="변경"):
        api.apply("test", plan)
    assert api.api.writes == 0


def test_timeout_after_success_does_not_retry():
    snapshot = sheet()
    plan = build_plan(snapshot, [garment("0001")])
    responses = iter([snapshot, apply_fake(snapshot, plan)])

    def timeout():
        raise TimeoutError()
    api = GoogleSheets.__new__(GoogleSheets)
    api.api = FakeApi(timeout)
    api.read = lambda *_: next(responses)
    assert "확인" in api.apply("test", plan)
    assert api.api.writes == 1
