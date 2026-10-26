from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

FIELDS = ("category", "gender", "age", "size")
LABELS = {"category": "카테고리", "gender": "남성/여성", "age": "성인/유아동", "size": "사이즈"}
OPTIONS = {
    "category": ["자켓", "코트", "조끼", "기타"],
    "gender": ["남성", "여성"],
    "age": ["성인", "유아동"],
    "size": ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "60/70", "80/90", "100/110", "120/130"],
}


@dataclass
class Garment:
    code: str
    folder: str
    photos: dict[str, str]
    issues: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    excluded: bool = False
    front: str = "0"
    roi: list[float] | None = None
    suggestion: dict = field(default_factory=dict)

    @property
    def front_path(self) -> str:
        return self.photos.get(self.front, "")


@dataclass
class Task:
    source: str
    garments: list[Garment]
    shooting_date: str = field(default_factory=lambda: date.today().strftime("%Y%m%d"))
    version: int = 1
    page: int = 0
    rows: int = 4
    front_only: bool = False
    photo_zoom: int = 150
    selected: list[int] = field(default_factory=list)
    default_roi: list[float] = field(default_factory=lambda: [0.38, 0.20, 0.65, 0.38])
    templates: list[dict] = field(default_factory=list)
    sheet_url: str = ""
    sheet_tab: str = ""

    def clone(self) -> Task:
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        if data.get("version") != 1:
            raise ValueError("지원하지 않는 작업 파일 버전입니다.")
        data = copy.deepcopy(data)
        data["garments"] = [Garment(**g) for g in data["garments"]]
        task = cls(**data)
        if not 1 <= task.rows <= 8:
            raise ValueError("작업 파일의 표시 줄 수가 올바르지 않습니다.")
        if len({g.code for g in task.garments}) != len(task.garments):
            raise ValueError("중복 외투코드가 있는 작업 파일입니다.")
        if any(not g.code.isascii() or not g.code.isdigit() for g in task.garments):
            raise ValueError("외투코드는 숫자 문자열이어야 합니다.")
        return task

    def ordinals(self) -> dict[str, int]:
        return {g.code: i + 1 for i, g in enumerate(g for g in self.garments if not g.excluded)}


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_task(path: Path, task: Task) -> None:
    atomic_json(path, task.to_dict())


def load_task(path: Path) -> Task:
    return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))


def boundaries(garments: list[Garment]) -> set[int]:
    """Mark both sides. Missing observations do not inherit previous values."""
    result: set[int] = set()
    for index in range(1, len(garments)):
        left, right = garments[index - 1], garments[index]
        a = {**left.suggestion.get("values", {}), **left.fields}
        b = {**right.suggestion.get("values", {}), **right.fields}
        changed = any(a.get(k) and b.get(k) and a[k] != b[k] for k in ("gender", "age", "size"))
        shape_a, shape_b = left.suggestion.get("shape"), right.suggestion.get("shape")
        if shape_a and shape_b and len(shape_a) == len(shape_b):
            distance = sum(x != y for x, y in zip(shape_a, shape_b)) / len(shape_a)
            changed |= distance > 0.23
        if changed:
            result.update((index - 1, index))
    return result
