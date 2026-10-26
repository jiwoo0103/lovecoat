import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from coatdesk.sheets import HEADERS
from coatdesk_v2.domain import Garment
from coatdesk_v2.sheets import Template, assert_new_tab_only, build_plan, inspect_export, next_title, send
from coatdesk_v2.storage import Repository

BOOK = "a" * 24


def entry(value):
    return {"userEnteredValue": {"formulaValue" if str(value).startswith("=") else "numberValue" if isinstance(value, int) else "stringValue": value}}


def template():
    cells = {(1, c): entry(label) for c, label in HEADERS.items()}
    cells[2, 3] = entry('=IF(C3<>"", "0_" & C3, "")')
    for col in range(10, 14):
        cells[2, col] = entry('=IF(B3<>"", 0, "")')
    for r in (2, 3, 4):
        cells[r, 1] = entry(r-1)
        cells[r, 2] = entry("example")
        if r > 2:
            cells[r, 3] = entry("0_wrong")
            for col in range(10, 14):
                cells[r, col] = entry(0)
    cells[2, 4] = {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [
        {"userEnteredValue": s} for s in ("자켓", "코트", "조끼", "기타")]}}}
    cells[2, 8] = {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "M"}]}}}
    cells[2, 15] = entry("helper survives")
    return Template(7, "양식예시(복사해서 사용)", 6, cells)


def setup(tmp_path, count=3):
    repo = Repository(tmp_path / "data")
    garments = [Garment(f"{i:04}", {}, fields={"category": "코트", "gender": "여성", "age": "성인", "size": "M"}, confirmed=True)
                for i in range(1, count+1)]
    key = repo.create("test", "2026-10-24", tmp_path / "photos", 1, count, "", garments)
    api = FakeApi()
    plan = build_plan(repo.load(key), api.sheets[7], api.tabs(BOOK), BOOK, "20261024")
    return repo, key, api, plan


class FakeApi:
    def __init__(self):
        self.sheets = {7: template()}
        self.writes = []
        self.after_error = False
        self.before_error = False
        self.reads = []
    def tabs(self, book):
        return [{"sheetId": s.id, "title": s.title} for s in self.sheets.values()]
    def read(self, book, sheet_id):
        self.reads.append(sheet_id)
        return copy.deepcopy(self.sheets[sheet_id])
    def batch(self, book, requests):
        self.writes.append(copy.deepcopy(requests))
        if self.before_error:
            raise TimeoutError()
        for req in requests:
            kind, body = next(iter(req.items()))
            if kind == "addSheet":
                p = body["properties"]
                self.sheets[p["sheetId"]] = Template(p["sheetId"], p["title"], p["gridProperties"]["rowCount"], {},
                                                      column_count=p["gridProperties"]["columnCount"])
            elif kind == "duplicateSheet":
                s = copy.deepcopy(self.sheets[body["sourceSheetId"]])
                s.id, s.title = body["newSheetId"], body["newSheetName"]
                self.sheets[s.id] = s
            elif kind == "appendDimension":
                self.sheets[body["sheetId"]].row_count += body["length"]
            elif kind == "updateCells":
                area = body["range"]
                s = self.sheets[area["sheetId"]]
                for r in range(area["startRowIndex"], area["endRowIndex"]):
                    for c in range(area["startColumnIndex"], area["endColumnIndex"]):
                        cell = s.cells.setdefault((r, c), {})
                        for field in body["fields"].split(","):
                            cell.pop(field, None)
                for r, row in enumerate(body["rows"], area["startRowIndex"]):
                    for c, cell in enumerate(row["values"], area["startColumnIndex"]):
                        s.cells.setdefault((r, c), {}).update({key: copy.deepcopy(value) for key, value in cell.items()
                                                               if key in body["fields"].split(",")})
            elif kind == "copyPaste":
                s = self.sheets[body["destination"]["sheetId"]]
                field = {"PASTE_FORMAT": "userEnteredFormat", "PASTE_DATA_VALIDATION": "dataValidation"}[body["pasteType"]]
                source, destination = body["source"], body["destination"]
                prototype = [copy.deepcopy(s.cells.get((source["startRowIndex"], c), {}))
                             for c in range(source["startColumnIndex"], source["endColumnIndex"])]
                for r in range(destination["startRowIndex"], destination["endRowIndex"]):
                    for offset, c in enumerate(range(destination["startColumnIndex"], destination["endColumnIndex"])):
                        cell = s.cells.setdefault((r, c), {})
                        if field in prototype[offset]:
                            cell[field] = copy.deepcopy(prototype[offset][field])
                        else:
                            cell.pop(field, None)
            elif kind == "updateDimensionProperties":
                s = self.sheets[body["range"]["sheetId"]]
                area, pixel = body["range"], body["properties"]["pixelSize"]
                if area["dimension"] == "ROWS":
                    s.row_heights = (s.row_heights + [24] * 3)[:3]
                    for r in range(area["startIndex"], min(3, area["endIndex"])):
                        s.row_heights[r] = pixel
                    if area["startIndex"] == 2:
                        s.row_height = pixel
                else:
                    s.dimensions.append({"startColumn": area["startIndex"], "columnMetadata": [{"pixelSize": pixel}]})
            elif kind == "createDeveloperMetadata":
                m = body["developerMetadata"]
                self.sheets[m["location"]["sheetId"]].metadata.append(m)
        if self.after_error:
            raise TimeoutError()


def test_only_new_tab_is_modified_examples_are_cleared_and_codes_keep_zeros(tmp_path):
    repo, key, api, plan = setup(tmp_path, 1)
    before = api.read(BOOK, 7)
    url = send(repo, plan, api)
    assert str(plan.sheet_id) in url
    assert api.read(BOOK, 7) == before
    result = api.read(BOOK, plan.sheet_id)
    assert result.value(2, 2) == "0001"
    assert result.value(2, 3) == '=IF(C3<>"", "0_" & C3, "")'
    assert result.value(2, 10) == '=IF(B3<>"",0,"")'
    assert result.value(3, 2) == result.value(4, 3) == ""
    assert result.value(2, 15) == "helper survives"
    assert repo.export(plan.id)["status"] == "succeeded"
    assert repo.load(key).active[0].code == "0001"
    with pytest.raises(ValueError, match="이미 시도"):
        send(repo, plan, api)
    assert len(api.writes) == 1


def test_excluded_codes_do_not_appear_and_ordinals_are_contiguous(tmp_path):
    repo, key, api, _ = setup(tmp_path, 4)
    repo.update(key, errors=[2, 3])
    plan = build_plan(repo.load(key), api.read(BOOK, 7), api.tabs(BOOK), BOOK, "20261024")
    assert [(r[0], r[1]) for r in plan.rows] == [(1, "0001"), (2, "0004")]


@pytest.mark.parametrize("defect", ["pending_review", "missing", "dropdown", "formula", "protected", "merged", "unknown_formula"])
def test_incomplete_or_unsupported_templates_block_before_any_write(tmp_path, defect):
    repo, key, api, _ = setup(tmp_path)
    w, t = repo.load(key), api.read(BOOK, 7)
    if defect == "pending_review":
        w.garments[0].confirmed = False
        w.garments[0].review_required = ["size"]
        w.garments[0].reviewed_values = {}
    elif defect == "missing":
        w.garments[0].fields.pop("size")
    elif defect == "dropdown":
        w.garments[0].fields["size"] = "XL"
    elif defect == "formula":
        t.cells[2, 10] = entry("=RAND()")
    elif defect == "protected":
        t.protected = [{"range": {"sheetId": 7, "startRowIndex": 2}}]
    elif defect == "merged":
        t.merges = [{"startRowIndex": 2, "endRowIndex": 3}]
    elif defect == "unknown_formula":
        t.cells[2, 5] = entry("=RAND()")
    with pytest.raises(ValueError):
        build_plan(w, t, api.tabs(BOOK), BOOK, "test")
    assert not api.writes


def test_guard_blocks_existing_tab_global_and_unknown_requests(tmp_path):
    _, _, _, plan = setup(tmp_path)
    for request in [{"updateCells": {"range": {"sheetId": 7}}},
                    {"deleteSheet": {"sheetId": 7}}, {"updateCells": {"allSheets": True}},
                    {"updateCells": {"start": {"rowIndex": 0}}}]:
        with pytest.raises(ValueError):
            assert_new_tab_only(plan.requests+[request], 7, plan.sheet_id)


def test_name_collisions_and_extension_are_prepared_on_new_id(tmp_path):
    repo, key, api, _ = setup(tmp_path, 8)
    tabs = api.tabs(BOOK)+[{"sheetId": 8, "title": "20261024"}, {"sheetId": 9, "title": "20261024 (2)"}]
    plan = build_plan(repo.load(key), api.read(BOOK, 7), tabs, BOOK, "20261024")
    assert plan.title == "20261024 (3)"
    assert plan.requests[1] == {"appendDimension": {"sheetId": plan.sheet_id, "dimension": "ROWS", "length": 4}}
    assert len(next(r["updateCells"]["rows"] for r in plan.requests if "updateCells" in r and r["updateCells"]["rows"])) == 8
    assert next_title("x"*100, ["x"*100]).endswith(" (2)")


def test_changed_values_or_template_or_name_invalidate_preflight(tmp_path):
    repo, key, api, plan = setup(tmp_path)
    repo.edit(key, ["0001"], lambda g: g.fields.update(size="L"))
    with pytest.raises(ValueError, match="입력 또는"):
        send(repo, plan, api)
    repo.edit(key, ["0001"], lambda g: g.fields.update(size="M"))
    api.sheets[7].cells[0, 0] = entry("changed")
    with pytest.raises(ValueError, match="양식이 변경"):
        send(repo, plan, api)
    api.sheets[7].cells.pop((0, 0))
    api.sheets[100] = Template(100, plan.title, 1, {})
    with pytest.raises(ValueError, match="사용 중"):
        send(repo, plan, api)
    assert not api.writes


def test_lost_response_after_success_is_verified_without_retry(tmp_path):
    repo, _, api, plan = setup(tmp_path)
    api.after_error = True
    assert "gid=" in send(repo, plan, api)
    assert repo.export(plan.id)["status"] == "succeeded"
    assert len(api.writes) == 1


def test_unknown_result_survives_restart_and_blocks_duplicate_then_settles(tmp_path):
    repo, key, api, plan = setup(tmp_path)
    api.before_error = True
    with pytest.raises(ValueError, match="전송 결과"):
        send(repo, plan, api)
    restored = Repository(repo.directory)
    assert len(restored.unresolved_exports(key)) == 1
    new_plan = build_plan(repo.load(key), api.read(BOOK, 7), api.tabs(BOOK), BOOK, "20261024")
    with pytest.raises(ValueError, match="이전 전송"):
        send(restored, new_plan, api)
    with pytest.raises(ValueError, match="3분"):
        inspect_export(restored, plan.id, api)
    with restored.connection() as db:
        db.execute("UPDATE exports SET updated=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(minutes=4)).isoformat(), plan.id))
    assert inspect_export(restored, plan.id, api) == ""
    assert restored.unresolved_exports(key) == []
    assert len(api.writes) == 1


def test_definite_rejection_does_not_retry(tmp_path):
    repo, _, api, plan = setup(tmp_path)
    class Rejection(Exception):
        resp = SimpleNamespace(status=429)
    def reject(*args):
        raise Rejection()
    api.batch = reject
    with pytest.raises(ValueError, match="429"):
        send(repo, plan, api)
    assert repo.export(plan.id)["status"] == "failed"
