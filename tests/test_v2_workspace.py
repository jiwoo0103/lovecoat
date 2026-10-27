import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PIL import Image
from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from coatdesk_v2.backend import Backend
from coatdesk_v2.domain import Garment, parse_errors, photo_path, shooting_date
from coatdesk_v2.scanner import scan
from coatdesk_v2.storage import Repository


def application():
    return QApplication.instance() or QApplication([])


def photo(root, code, angle=0):
    folder = root / f"0_{code}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"img_0_0_{angle}.jpg"
    Image.new("RGB", (32, 48), "white").save(path)
    return path


def workspace(tmp_path, count=4):
    repo = Repository(tmp_path / "data")
    garments = [Garment(f"{i:04}", {}) for i in range(1, count+1)]
    key = repo.create("20261024", "", tmp_path / "photos", 1, count, "", garments)
    return repo, key


def wait_for(predicate, milliseconds=6000):
    for _ in range(milliseconds//10):
        QTest.qWait(10)
        if predicate():
            return
    raise AssertionError("백그라운드 작업이 완료되지 않았습니다.")


def test_range_filter_counts_and_workspace_isolation(tmp_path):
    for i in range(1, 401):
        photo(tmp_path / "photos", f"{i:04}")
    # Out-of-range photographs must not be opened or merged into this workspace.
    broken = tmp_path / "photos/0_2000"
    broken.mkdir()
    (broken / "img_0_0_0.jpg").write_text("damaged")
    garments = scan(tmp_path / "photos", 1, 400)
    repo = Repository(tmp_path / "data")
    key = repo.create("test", "2026-10-24", tmp_path / "photos", 1, 400, "212,300", garments)
    w = repo.load(key)
    assert w.counts == {"total": 398, "confirmed": 0, "excluded": 2, "missing": 0}
    assert w.active[0].code == "0001"
    assert all(g.photos["0"].startswith("0_") for g in w.garments)
    other = repo.create("other", "", tmp_path / "photos", 1, 400, "", garments)
    assert len(repo.load(other).active) == 400
    with pytest.raises(ValueError, match="범위 밖"):
        parse_errors("980", 1, 400)
    assert repo.load(other).errors == []


def test_scanner_preserves_missing_slots_detects_duplicates_and_counts_missing(tmp_path):
    photo(tmp_path, "0001", 0)
    photo(tmp_path, "0001", 2)
    photo(tmp_path, "0003", 0)
    garments = scan(tmp_path, 1, 4)
    assert garments[0].photos.keys() == {"0", "2"}
    assert "1번 사진 없음" in garments[0].issues
    repo = Repository(tmp_path / "data")
    key = repo.create("test", "", tmp_path, 1, 4, "3", garments)
    assert repo.load(key).counts == {"total": 1, "confirmed": 0, "excluded": 1, "missing": 2}
    photo(tmp_path, "1")
    with pytest.raises(ValueError, match="중복"):
        scan(tmp_path, 1, 4)


def test_dates_paths_and_errors_are_validated(tmp_path):
    assert shooting_date("20261024") == "2026-10-24"
    with pytest.raises(ValueError):
        shooting_date("20260230")
    assert parse_errors("001, 1 2", 1, 2) == [1, 2]
    with pytest.raises(ValueError):
        photo_path(str(tmp_path), "../outside.jpg")
    with pytest.raises(ValueError):
        photo_path(str(tmp_path), str(tmp_path / "absolute.jpg"))


def test_atomic_edits_preserve_previous_values_on_failure_and_reopen(tmp_path):
    repo, key = workspace(tmp_path)
    def edit(g):
        g.fields["size"] = "M"
        if g.code == "0002":
            raise ValueError("fail batch")
    with pytest.raises(ValueError):
        repo.edit(key, ["0001", "0002"], edit)
    assert all(not g.fields for g in Repository(tmp_path / "data").load(key).garments)
    repo.update(key, view={"current": "0003", "selected": ["0003"]})
    assert repo.load(key).view["current"] == "0003"
    with repo.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_backend_selection_batch_confirmation_undo_and_reopen(tmp_path):
    app = application()
    repo, key = workspace(tmp_path, 9)
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    backend.select("0001", 0)
    backend.select("0003", Qt.KeyboardModifier.ShiftModifier.value)
    assert backend.selected == {"0001", "0002", "0003"}
    backend.batchFields("코트", "여성", "성인", "M")
    assert backend.state["counts"]["confirmed"] == 3
    backend.confirmSelected()
    assert backend.state["counts"]["confirmed"] == 3
    backend.setField("0001", "size", "L")
    assert backend.state["counts"]["confirmed"] == 3
    backend.undo()
    assert backend.state["counts"]["confirmed"] == 3
    backend.setMode("photos")
    backend.viewOptions(2, 1, False, 170)
    backend.nextGarment(1)
    current = backend.current
    restored = Backend(repo.directory)
    assert restored.state["home"]
    restored.openWorkspace(key)
    assert restored.current == current
    assert restored.mode == "photos"
    assert restored.selected == backend.selected
    assert restored.workspace.active[0].fields["size"] == "M"
    backend.shutdown()
    restored.shutdown()
    assert app


def test_confirmation_is_atomic_and_save_failure_is_visible(tmp_path, monkeypatch):
    app = application()
    repo, key = workspace(tmp_path)
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    backend.selectPage()
    backend.confirmSelected()
    assert "모두 입력" in backend.error
    assert repo.load(key).counts["confirmed"] == 0
    def failed(*args):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(backend.repository, "edit", failed)
    backend.setField("0001", "size", "M")
    assert not backend.saved
    assert backend.error
    assert repo.load(key).garments[0].fields == {}
    backend.shutdown()
    assert app


def test_worker_results_are_queued_to_ui_and_analysis_resumes_without_overwrite(tmp_path):
    app = application()
    repo, key = workspace(tmp_path, 3)
    calls = []
    class Engine:
        def key(self, w, g):
            return "key-"+g.code
        def analyze(self, w, g):
            calls.append(g.code)
            return {"values": {"size": "S", "gender": "여성", "age": "성인"}, "review": True}
    backend = Backend(repo.directory, engine_factory=Engine)
    backend.openWorkspace(key)
    backend.setField("0001", "size", "L")
    threads = []
    backend.changed.connect(lambda: threads.append(QThread.currentThread()))
    backend.startAnalysis(False)
    wait_for(lambda: not backend.jobs)
    assert len(calls) == 3
    assert all(t == app.thread() for t in threads)
    backend.select("0001", 0)
    backend.applySuggestions()
    g = repo.load(key).garments[0]
    assert g.fields["size"] == "L"
    assert g.fields["gender"] == "여성"
    assert not g.confirmed
    backend.startAnalysis(False)
    wait_for(lambda: not backend.jobs)
    assert len(calls) == 3
    backend.startAnalysis(True)
    wait_for(lambda: not backend.jobs)
    assert len(calls) == 6
    assert repo.load(key).garments[0].fields["size"] == "L"
    backend.shutdown()


def test_cancel_stores_completed_garment_and_keeps_remaining_pending(tmp_path):
    app = application()
    repo, key = workspace(tmp_path, 6)
    class Engine:
        def key(self, w, g):
            return g.code
        def analyze(self, w, g):
            backend.cancel_event.set()
            return {"values": {"size": "M"}}
    backend = Backend(repo.directory, engine_factory=Engine)
    backend.openWorkspace(key)
    backend.startAnalysis(False)
    wait_for(lambda: not backend.jobs)
    garments = repo.load(key).garments
    assert sum(g.analysis_state == "done" for g in garments) == 1
    assert sum(g.analysis_state == "pending" for g in garments) == 5
    backend.shutdown()
    assert app


def test_changing_excel_path_or_input_discards_preview(tmp_path):
    app = application()
    repo, key = workspace(tmp_path, 1)
    backend = Backend(repo.directory)
    backend.openWorkspace(key)
    backend.selectPage()
    backend.batchFields("자켓", "여성", "성인", "M")
    path = tmp_path / "외투목록.xlsx"
    backend.previewExcel(str(path), "20261024")
    wait_for(lambda: not backend.jobs)
    assert backend.plan is not None, backend.error
    assert backend.state["planPath"] == str(path)
    backend.excelTargetChanged(str(tmp_path / "다른 파일.xlsx"))
    assert backend.plan is None
    backend.previewExcel(str(path), "20261024")
    wait_for(lambda: not backend.jobs)
    assert backend.plan is not None, backend.error
    backend.setField("0001", "size", "L")
    assert backend.plan is None
    assert not path.exists()
    backend.shutdown()
    assert app


def test_schema_backup_and_future_version_preserve_database(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    with sqlite3.connect(directory / "workspaces.sqlite3") as db:
        db.execute("CREATE TABLE old_marker (value TEXT)")
        db.execute("INSERT INTO old_marker VALUES('keep')")
    repo = Repository(directory)
    backups = list(directory.glob("before-schema-0-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT value FROM old_marker").fetchone()[0] == "keep"
    with repo.connection() as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="더 새 버전"):
        Repository(directory)
    with sqlite3.connect(repo.path) as db:
        assert db.execute("SELECT value FROM old_marker").fetchone()[0] == "keep"
