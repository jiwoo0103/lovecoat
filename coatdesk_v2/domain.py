from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from coatdesk.domain import FIELDS, LABELS, OPTIONS

DEFAULT_ROI = [0.38, 0.20, 0.65, 0.38]
EDITABLE = ("fields", "confirmed", "front", "roi", "roi_manual")


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def shooting_date(name: str, supplied: str = "") -> str:
    value = supplied.strip() or (name if re.fullmatch(r"\d{8}", name, re.ASCII) else date.today().isoformat())
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("촬영일을 YYYY-MM-DD 형식으로 입력하세요.") from None


def parse_errors(text: str, start: int, end: int) -> list[int]:
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if any(not p.isascii() or not p.isdigit() for p in parts):
        raise ValueError("에러번호는 쉼표 또는 공백으로 구분한 숫자로 입력하세요.")
    numbers = sorted({int(p) for p in parts})
    outside = [p for p in numbers if not start <= p <= end]
    if outside:
        raise ValueError("작업 범위 밖 에러번호: " + ", ".join(map(str, outside[:20])))
    return numbers


def photo_path(root: str, relative: str) -> Path:
    base = Path(root).resolve()
    path = (base / relative).resolve()
    if not relative or Path(relative).is_absolute() or not path.is_relative_to(base):
        raise ValueError("원본 폴더 밖의 사진 경로입니다.")
    return path


def validate_roi(roi: list[float]) -> None:
    if len(roi) != 4 or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
        raise ValueError("사진 안에서 머리 영역을 지정하세요.")


@dataclass
class Garment:
    code: str
    photos: dict[str, str]
    issues: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    confirmed: bool = False
    front: str = "0"
    roi: list[float] = field(default_factory=lambda: DEFAULT_ROI.copy())
    roi_manual: bool = False
    suggestion: dict = field(default_factory=dict)
    analysis_key: str = ""
    analysis_state: str = "pending"

    @property
    def complete(self) -> bool:
        return all(self.fields.get(key, "").strip() for key in FIELDS)

    @property
    def status(self) -> str:
        if self.confirmed and self.complete:
            return "확인 완료"
        return "확인 필요" if self.fields or self.suggestion or self.issues else "미확인"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Workspace:
    id: str
    name: str
    shooting_date: str
    source: str
    start: int
    end: int
    errors: list[int]
    garments: list[Garment]
    view: dict = field(default_factory=dict)
    templates: list[dict] = field(default_factory=list)
    revision: int = 0

    @property
    def active(self) -> list[Garment]:
        errors = set(self.errors)
        return [g for g in self.garments if self.start <= int(g.code) <= self.end and int(g.code) not in errors]

    @property
    def counts(self) -> dict:
        present = {int(g.code) for g in self.garments}
        active = self.active
        return {"total": len(active), "confirmed": sum(g.confirmed and g.complete for g in active),
                "excluded": len(present.intersection(self.errors)),
                "missing": self.end - self.start + 1 - len(present)}

    def export_fingerprint(self) -> str:
        return fingerprint({"workspace": self.id, "date": self.shooting_date, "range": [self.start, self.end],
                            "errors": self.errors, "items": [{"code": g.code, "fields": g.fields,
                            "confirmed": g.confirmed} for g in self.active]})


def validate_fields(fields: dict) -> None:
    for key, value in fields.items():
        if key not in FIELDS or not isinstance(value, str):
            raise ValueError("올바르지 않은 입력 항목입니다.")
        if value and key != "size" and value not in OPTIONS[key]:
            raise ValueError(f"{LABELS[key]}의 허용 목록에 없는 값입니다.")
        if len(value) > 100:
            raise ValueError("입력값이 너무 깁니다.")
