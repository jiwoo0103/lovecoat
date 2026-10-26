import copy
import json
from pathlib import Path

import pytest

from coatdesk_v2 import sheets
from coatdesk_v2.domain import Garment
from coatdesk_v2.storage import Repository
from test_v2_sheets import BOOK, FakeApi


def prepare(tmp_path, count=2, empty=False):
    repo = Repository(tmp_path / "workspace")
    garments = [Garment(f"{i:04}", {}, fields={"category": "코트", "gender": "여성", "age": "성인", "size": "M"})
                for i in range(1, count+1)]
    key = repo.create("test", "2026-10-27", tmp_path / "photos", 1, count, "", garments)
    api = FakeApi()
    if empty:
        api.sheets = {}
    plan = sheets.build_plan(repo.load(key), sheets.builtin_template(), api.tabs(BOOK), BOOK, "20261027")
    return repo, key, api, plan


def test_builtin_can_export_without_remote_template_and_keeps_existing_tab(tmp_path):
    repo, _, api, plan = prepare(tmp_path)
    original = copy.deepcopy(api.sheets)
    assert plan.source_id == -1
    assert set(plan.requests[0]) == {"addSheet"}
    assert "gid=" in sheets.send(repo, plan, api)
    assert api.reads == [plan.sheet_id]
    assert all(api.sheets[key] == value for key, value in original.items())
    result = api.sheets[plan.sheet_id]
    assert result.value(0, 2) == '="일일수량: " & COUNT(B:B)'
    assert result.value(1, 2) == "외투코드"
    assert result.value(2, 2) == "0001"
    assert result.value(3, 2) == "0002"
    assert result.value(3, 3) == '=IF(C4<>"", "0_" & C4, "")'
    assert result.value(3, 10) == '=IF(B4<>"",0,"")'
    assert result.value(4, 2) == result.value(4, 3) == ""
    assert result.row_heights == [106, 32, 23]
    assert len(result.dimensions) == 26
    template = sheets.builtin_template()
    assert result.cells[700, 4]["dataValidation"] == template.cells[2, 4]["dataValidation"]
    assert result.cells[700, 2]["userEnteredFormat"] == template.cells[2, 2]["userEnteredFormat"]
    for (r, c), cell in template.cells.items():
        if c >= 15:
            assert result.cells[r, c] == cell


def test_builtin_empty_target_and_name_collision_do_not_need_source_tab(tmp_path):
    repo, key, api, plan = prepare(tmp_path, empty=True)
    sheets.send(repo, plan, api)
    second = sheets.build_plan(repo.load(key), sheets.builtin_template(), api.tabs(BOOK), BOOK, "20261027")
    assert second.title == "20261027 (2)"
    assert len(api.sheets) == 1


def test_builtin_add_sheet_allocates_enough_rows_for_large_export(tmp_path):
    _, _, _, plan = prepare(tmp_path, count=1001, empty=True)
    props = plan.requests[0]["addSheet"]["properties"]
    assert props["gridProperties"] == {"rowCount": 1003, "columnCount": 26}
    assert not any("appendDimension" in r for r in plan.requests)
    assert plan.rows[-1][0:2] == [1001, "1001"]


def test_export_gate_uses_required_review_instead_of_whole_card_confirmation(tmp_path):
    repo, key, api, _ = prepare(tmp_path)
    w = repo.load(key)
    assert not w.active[0].confirmed
    assert w.active[0].export_ready
    w.active[0].review_required = ["size"]
    with pytest.raises(ValueError, match="필수 입력·검수"):
        sheets.build_plan(w, sheets.builtin_template(), api.tabs(BOOK), BOOK, "new")
    w.active[0].confirm_review()
    assert sheets.build_plan(w, sheets.builtin_template(), api.tabs(BOOK), BOOK, "new")


@pytest.mark.parametrize("size,age", [("60/70", "유아동"), ("100/110", "유아동"), ("직접 입력 사이즈", "성인")])
def test_builtin_warning_only_size_list_allows_child_mapping_and_direct_input(tmp_path, size, age):
    repo, key, api, _ = prepare(tmp_path)
    workspace = repo.load(key)
    workspace.garments[0].fields.update(size=size, age=age)
    workspace.garments[0].confirm_review()
    template = sheets.builtin_template()
    assert template.cells[2, 8]["dataValidation"]["strict"] is False
    plan = sheets.build_plan(workspace, template, api.tabs(BOOK), BOOK, "child")
    assert plan.rows[0][7] == size
    # Category, gender and age lists still reject incompatible labels.
    with pytest.raises(ValueError, match="허용 목록"):
        sheets.dropdown(template, 4, "지원하지 않는 카테고리")


def test_builtin_preview_is_invalidated_by_resource_change_before_send(tmp_path, monkeypatch):
    repo, _, api, plan = prepare(tmp_path)
    changed = sheets.builtin_template()
    changed.row_heights[0] += 1
    monkeypatch.setattr(sheets, "builtin_template", lambda: changed)
    with pytest.raises(ValueError, match="양식이 변경"):
        sheets.send(repo, plan, api)
    assert not api.writes
    assert not api.reads


@pytest.mark.parametrize("extra", [
    {"deleteSheet": {"sheetId": 7}},
    {"addSheet": {"properties": {"sheetId": 99}}},
    {"updateCells": {"range": {"sheetId": 7}}},
    {"updateCells": {"range": {"sheetId": 0}, "allSheets": True}},
    {"copyPaste": {"source": {"sheetId": 7}, "destination": {"sheetId": 0}}},
    {"updateSheetProperties": {"properties": {"sheetId": 7}}},
])
def test_builtin_guard_rejects_existing_global_and_additional_tab_writes(tmp_path, extra):
    _, _, _, plan = prepare(tmp_path)
    with pytest.raises(ValueError):
        sheets.assert_new_tab_only(plan.requests + [extra], -1, plan.sheet_id)


def test_builtin_guard_rejects_replaced_first_request_and_mutated_send(tmp_path):
    repo, _, api, plan = prepare(tmp_path)
    with pytest.raises(ValueError):
        sheets.assert_new_tab_only([{"duplicateSheet": {"sourceSheetId": 7, "newSheetId": plan.sheet_id}}], -1, plan.sheet_id)
    plan.requests.append({"updateCells": {"range": {"sheetId": 7}}})
    with pytest.raises(ValueError):
        sheets.send(repo, plan, api)
    assert not api.writes


def test_builtin_lost_response_verifies_without_duplicate_write(tmp_path):
    repo, _, api, plan = prepare(tmp_path, empty=True)
    api.after_error = True
    assert "gid=" in sheets.send(repo, plan, api)
    with pytest.raises(ValueError, match="이미 시도"):
        sheets.send(repo, plan, api)
    assert len(api.writes) == 1


def test_builtin_unknown_result_survives_restart_and_blocks_new_operation(tmp_path):
    repo, key, api, plan = prepare(tmp_path, empty=True)
    api.before_error = True
    with pytest.raises(ValueError, match="전송 결과"):
        sheets.send(repo, plan, api)
    repo = Repository(repo.directory)
    second = sheets.build_plan(repo.load(key), sheets.builtin_template(), [], BOOK, "other")
    with pytest.raises(ValueError, match="이전 전송"):
        sheets.send(repo, second, api)
    assert len(api.writes) == 1


def test_builtin_loader_uses_frozen_resource_and_rejects_example_values(tmp_path, monkeypatch):
    resource = Path(sheets.__file__).resolve().parent.parent / "resources/standard_sheet.json"
    data = json.loads(resource.read_text(encoding="utf-8"))
    destination = tmp_path / "resources/standard_sheet.json"
    destination.parent.mkdir()
    destination.write_text(json.dumps(data), encoding="utf-8")
    original = sheets.builtin_template()
    monkeypatch.setattr(sheets.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert sheets.builtin_template().digest() == original.digest()
    data["cells"].append([3, 2, {"userEnteredValue": {"stringValue": "original-example-code"}}])
    destination.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="내장 표준 양식"):
        sheets.builtin_template()


def google_normalize_formulas(template, payload):
    for r, row in enumerate(payload["rows"], 2):
        template.cells[r, 3]["userEnteredValue"]["formulaValue"] = f'=if(C{r+1}<>"", "0_" & C{r+1}, "")'
        for c in range(10, 14):
            template.cells[r, c]["userEnteredValue"]["formulaValue"] = f'=if(B{r+1}<>"", 0, "")'


def test_send_verifies_google_normalized_formula_whitespace_and_case(tmp_path):
    repo, _, api, plan = prepare(tmp_path)
    batch = api.batch
    def normalizing_batch(book, requests):
        batch(book, requests)
        google_normalize_formulas(api.sheets[plan.sheet_id], plan.payload())
    api.batch = normalizing_batch
    assert "gid=" in sheets.send(repo, plan, api)
    assert repo.export(plan.id)["status"] == "succeeded"
    assert len(api.writes) == 1


def test_read_only_recheck_resolves_unknown_with_normalized_formulas(tmp_path):
    repo, key, api, plan = prepare(tmp_path)
    api.batch(BOOK, plan.requests)
    google_normalize_formulas(api.sheets[plan.sheet_id], plan.payload())
    repo.record_export(plan.id, key, "unknown", plan.payload())
    assert "gid=" in sheets.inspect_export(repo, plan.id, api)
    assert repo.export(plan.id)["status"] == "succeeded"
    assert len(api.writes) == 1


@pytest.mark.parametrize("column,actual", [
    (2, {"stringValue": "0001 "}),
    (4, {"stringValue": " 코트"}),
    (8, {"stringValue": "M "}),
    (3, {"formulaValue": '=IF(C3<>"", "0_ " & C3, "")'}),
    (3, {"formulaValue": '=IF(C4<>"", "0_" & C4, "")'}),
    (3, {"formulaValue": '=RAND()'}),
    (3, {"stringValue": '=IF(C3<>"", "0_" & C3, "")'}),
    (10, {"formulaValue": '=IF(B3<>"", 1, "")'}),
    (10, {"formulaValue": '=IF(B3<>" ", 0, "")'}),
    (10, {"formulaValue": '=IF(B4<>"", 0, "")'}),
    (10, {"stringValue": '=IF(B3<>"",0,"")'}),
])
def test_formula_normalization_still_rejects_changed_data_literals_and_rules(tmp_path, column, actual):
    _, _, api, plan = prepare(tmp_path)
    api.batch(BOOK, plan.requests)
    result = api.sheets[plan.sheet_id]
    google_normalize_formulas(result, plan.payload())
    result.cells[2, column]["userEnteredValue"] = actual
    assert not sheets.matches_export(result, plan.payload())


def test_legacy_supported_image_concatenation_is_normalized_without_broad_formula_acceptance(tmp_path):
    _, _, api, plan = prepare(tmp_path)
    api.batch(BOOK, plan.requests)
    result = api.sheets[plan.sheet_id]
    payload = plan.payload()
    payload["rows"][0][2] = '="0_"&C3'
    result.cells[2, 3]["userEnteredValue"] = {"formulaValue": '= "0_" & c3'}
    assert sheets.matches_export(result, payload)
    payload["rows"][0][2] = '=RAND()'
    result.cells[2, 3]["userEnteredValue"] = {"formulaValue": '=RAND()'}
    assert not sheets.matches_export(result, payload)
