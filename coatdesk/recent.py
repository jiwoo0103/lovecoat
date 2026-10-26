from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .domain import load_task


@dataclass
class RecentTask:
    path: Path
    shooting_date: str
    source_name: str
    count: int
    saved_at: str
    source: str = ""
    error: str = ""


def recent_tasks(directory: Path, known: list[str], limit: int = 30) -> list[RecentTask]:
    candidates = set(directory.glob("*.coat.json"))
    candidates.update(Path(p) for p in known if p)
    dated = []
    for path in candidates:
        try:
            if path.is_file():
                dated.append((path.stat().st_mtime, path.resolve()))
        except OSError:
            continue
    results = []
    seen = set()
    for modified, path in sorted(dated, key=lambda pair: pair[0], reverse=True):
        if path in seen:
            continue
        seen.add(path)
        saved_at = datetime.fromtimestamp(modified, timezone(timedelta(hours=9))).strftime("%m-%d %H:%M")
        try:
            task = load_task(path)
            date = datetime.strptime(task.shooting_date, "%Y%m%d").strftime("%Y-%m-%d")
            results.append(RecentTask(path, date, Path(task.source).name or "사진 폴더",
                                      len(task.garments), saved_at, task.source))
        except (OSError, ValueError, TypeError, KeyError):
            results.append(RecentTask(path, "—", path.stem, 0, saved_at, error="작업 파일 확인 필요"))
        if len(results) >= limit:
            break
    return results
