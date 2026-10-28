from __future__ import annotations

import json
import copy
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .domain import EDITABLE, Garment, Workspace, parse_errors, shooting_date
from .category import DEFAULT_CATEGORY_THRESHOLDS

SCHEMA_VERSION = 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class Repository:
    """One connection per operation/thread, atomic edits, no tokens in work records."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "workspaces.sqlite3"
        self.initialize()
        self.migrate_v24()

    def migrate_v24(self):
        with self.connection() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='v24_thresholds_migrated'").fetchone():
                return
            if db.execute("SELECT 1 FROM garments LIMIT 1").fetchone():
                backup = self.directory / f"before-v24-{uuid.uuid4().hex[:8]}.sqlite3"
                with sqlite3.connect(backup) as destination:
                    db.backup(destination)
            db.execute("INSERT OR REPLACE INTO settings VALUES('ai_thresholds',?)", (encode({"panel": .9, "shape": .9, "size": .9}),))
            db.execute("INSERT INTO settings VALUES('v24_thresholds_migrated','true')")

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("더 새 버전에서 저장한 작업 DB입니다. 프로그램을 업데이트하세요.")
            if version == SCHEMA_VERSION:
                return
            if self.path.stat().st_size:
                backup = self.directory / f"before-schema-{version}-{uuid.uuid4().hex[:8]}.sqlite3"
                with sqlite3.connect(backup) as destination:
                    db.backup(destination)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, shooting_date TEXT NOT NULL,
                    source TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                    errors TEXT NOT NULL, view TEXT NOT NULL DEFAULT '{}', templates TEXT NOT NULL DEFAULT '[]',
                    revision INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS garments (
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id), code TEXT NOT NULL,
                    number INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (workspace_id,code), UNIQUE(workspace_id,number)
                );
                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    status TEXT NOT NULL, payload TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")

    def create(self, name, date_text, source, start, end, errors, garments) -> str:
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("작업공간 이름을 1~120자로 입력하세요.")
        if start < 0 or end < start:
            raise ValueError("시작·끝 번호를 확인하세요.")
        parsed = parse_errors(errors, start, end)
        day = shooting_date(name, date_text)
        workspace_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO workspaces(id,name,shooting_date,source,start,end,errors,updated) "
                       "VALUES(?,?,?,?,?,?,?,?)", (workspace_id, name, day, str(Path(source).resolve()),
                                                  start, end, encode(parsed), now()))
            db.executemany("INSERT INTO garments VALUES(?,?,?,?)", [
                (workspace_id, g.code, int(g.code), encode(g.to_dict())) for g in garments])
            db.execute("INSERT OR REPLACE INTO settings VALUES('last_workspace',?)", (encode(workspace_id),))
        return workspace_id

    def recent(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT w.*, SUM(CASE WHEN g.number BETWEEN w.start AND w.end "
                "AND COALESCE(json_extract(g.payload,'$.source_present'),1) THEN 1 ELSE 0 END) AS count "
                "FROM workspaces w LEFT JOIN garments g ON w.id=g.workspace_id "
                "GROUP BY w.id ORDER BY w.updated DESC")]

    def load(self, workspace_id: str) -> Workspace:
        with self.connection() as db:
            row = db.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if row is None:
                raise ValueError("작업공간을 찾을 수 없습니다.")
            garments = [Garment(**json.loads(r[0])) for r in db.execute(
                "SELECT payload FROM garments WHERE workspace_id=? ORDER BY number", (workspace_id,))]
            thresholds, category_thresholds = self._thresholds(db)
            for garment in garments:
                if garment.suggestion:
                    garment.rethreshold(thresholds, category_thresholds)
            return Workspace(row["id"], row["name"], row["shooting_date"], row["source"], row["start"], row["end"],
                             json.loads(row["errors"]), garments, json.loads(row["view"]),
                             json.loads(row["templates"]), row["revision"])

    def edit(self, workspace_id: str, codes: list[str], change) -> dict:
        previous = {}
        with self.connection() as db:
            thresholds, category_thresholds = self._thresholds(db)
            for code in dict.fromkeys(codes):
                row = db.execute("SELECT payload FROM garments WHERE workspace_id=? AND code=?",
                                 (workspace_id, code)).fetchone()
                if row is None:
                    raise ValueError(f"옷을 찾을 수 없습니다: {code}")
                garment = Garment(**json.loads(row[0]))
                if garment.suggestion:
                    garment.rethreshold(thresholds, category_thresholds)
                previous[code] = {key: copy.deepcopy(getattr(garment, key)) for key in EDITABLE}
                change(garment)
                db.execute("UPDATE garments SET payload=? WHERE workspace_id=? AND code=?",
                           (encode(garment.to_dict()), workspace_id, code))
            db.execute("UPDATE workspaces SET revision=revision+1,updated=? WHERE id=?", (now(), workspace_id))
        return previous

    def load_garments(self, workspace_id, codes):
        if not codes:
            return []
        with self.connection() as db:
            placeholders = ",".join("?" for _ in codes)
            garments = [Garment(**json.loads(r[0])) for r in db.execute(
                f"SELECT payload FROM garments WHERE workspace_id=? AND code IN ({placeholders})", [workspace_id, *codes])]
            thresholds, category_thresholds = self._thresholds(db)
            for garment in garments:
                if garment.suggestion:
                    garment.rethreshold(thresholds, category_thresholds)
            return garments

    def _thresholds(self, db):
        settings = {r["key"]: json.loads(r["value"]) for r in db.execute(
            "SELECT key,value FROM settings WHERE key IN ('ai_thresholds','category_thresholds')")}
        return settings["ai_thresholds"], settings.get("category_thresholds", dict(DEFAULT_CATEGORY_THRESHOLDS))

    def undo(self, workspace_id: str, previous: dict):
        def restore(garment):
            for key, value in previous[garment.code].items():
                setattr(garment, key, value)
        self.edit(workspace_id, list(previous), restore)

    def update(self, workspace_id: str, **changes):
        if not changes or set(changes) - {"errors", "view", "templates", "source"}:
            raise ValueError("지원하지 않는 작업공간 변경입니다.")
        with self.connection() as db:
            for key, value in changes.items():
                db.execute(f"UPDATE workspaces SET {key}=?,revision=revision+1,updated=? WHERE id=?",
                           (value if key == "source" else encode(value), now(), workspace_id))

    def change_scope(self, workspace_id, start, end, error_text, scanned):
        if start < 0 or end < start or end-start > 100_000:
            raise ValueError("번호 범위는 시작 ≤ 끝, 최대 100,001개로 입력하세요.")
        errors = parse_errors(error_text, start, end)
        found = {int(g.code): g for g in scanned}
        if len(found) != len(scanned) or any(not start <= n <= end for n in found):
            raise ValueError("사진 검사 결과의 번호 범위를 확인하세요.")
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone():
                raise ValueError("작업공간을 찾을 수 없습니다.")
            existing = {r["number"]: Garment(**json.loads(r["payload"])) for r in db.execute(
                "SELECT number,payload FROM garments WHERE workspace_id=?", (workspace_id,))}
            for number, garment in existing.items():
                if start <= number <= end and number not in found:
                    garment.source_present = False
                    db.execute("UPDATE garments SET payload=? WHERE workspace_id=? AND number=?",
                               (encode(garment.to_dict()), workspace_id, number))
            for number, photo_record in found.items():
                garment = existing.get(number)
                if garment is not None:
                    if garment.code != photo_record.code:
                        raise ValueError(f"{number}번의 기존 외투코드와 사진 폴더 표기가 다릅니다. 폴더 이름을 확인하세요.")
                    if garment.photos != photo_record.photos:
                        garment.analysis_key, garment.analysis_state = "", "pending"
                    garment.photos = photo_record.photos
                    garment.issues = photo_record.issues
                    garment.source_present = True
                    db.execute("UPDATE garments SET payload=? WHERE workspace_id=? AND number=?",
                               (encode(garment.to_dict()), workspace_id, number))
                else:
                    db.execute("INSERT INTO garments VALUES(?,?,?,?)",
                               (workspace_id, photo_record.code, number, encode(photo_record.to_dict())))
            db.execute("UPDATE workspaces SET start=?,end=?,errors=?,revision=revision+1,updated=? WHERE id=?",
                       (start, end, encode(errors), now(), workspace_id))

    def setting(self, key: str, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        self.set_settings({key: value})

    def set_settings(self, values):
        with self.connection() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES(?,?)", [(key, encode(value)) for key, value in values.items()])

    def record_export(self, export_id: str, workspace_id: str, status: str, payload: dict):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO exports VALUES(?,?,?,?,?)",
                       (export_id, workspace_id, status, encode(payload), now()))

    def export(self, export_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
            if not row:
                raise ValueError("전송 기록을 찾을 수 없습니다.")
            return {**dict(row), "payload": json.loads(row["payload"])}

    def has_export(self, export_id: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM exports WHERE id=?", (export_id,)).fetchone() is not None

    def unresolved_exports(self, workspace_id: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM exports WHERE workspace_id=? AND status IN ('sending','unknown') "
                              "ORDER BY updated DESC", (workspace_id,))
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
