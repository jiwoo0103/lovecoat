from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from coatdesk.domain import FIELDS, LABELS, OPTIONS as LEGACY_OPTIONS
from .category import DEFAULT_CATEGORY_THRESHOLDS, decode

OPTIONS = {**LEGACY_OPTIONS, "size": ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL"]}

DEFAULT_ROI = [0.38, 0.20, 0.65, 0.38]
EDITABLE = ("fields", "confirmed", "front", "roi", "roi_manual", "review_required", "reviewed_values", "field_sources")


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
    review_required: list[str] = field(default_factory=list)
    reviewed_values: dict[str, str] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    analysis_review_required: list[str] | None = None
    source_present: bool = True

    def __post_init__(self):
        # Older payloads stored a whole-card confirmation. Preserve that review.
        if self.confirmed and not self.reviewed_values:
            self.reviewed_values = self.fields.copy()
        if not self.field_sources:
            self.field_sources = {key: "legacy" for key in self.fields}
        if self.analysis_review_required is None:
            # Only retire legacy reasons explained by the saved analysis.
            derived = self.analysis_review_keys()
            self.review_required = sorted(set(self.review_required) - derived)
            self.analysis_review_required = sorted(derived)

    def rethreshold(self, thresholds, category_thresholds=None):
        scores = self.suggestion.get("scores", {})
        held = list(self.suggestion.get("held", []))
        for key, reason in (("panel", "판넬 위치"), ("shape", "성별/성인·아동"), ("size", "사이즈")):
            if scores.get(key) is not None:
                held = [r for r in held if r != reason]
                if scores[key] < thresholds[key]:
                    held.append(reason)
        self.suggestion.update(thresholds=dict(thresholds), held=held)
        category = self.suggestion.get("category", {})
        if category.get("probabilities"):
            settings = category_thresholds or DEFAULT_CATEGORY_THRESHOLDS
            old_note = ", ".join(category.get("review_reasons", []))
            panel_note = self.suggestion.get("panel_note", self.suggestion.get("note", "").replace(old_note, "").strip(" ·") if old_note else self.suggestion.get("note", ""))
            category.update(decode(category["probabilities"], settings["sleeve"], settings["length"]))
            self.suggestion.setdefault("values", {})["category"] = category["display_category"]
            self.suggestion.setdefault("candidates", {})["category"] = category["candidate"]
            self.suggestion.update(panel_note=panel_note, note=" · ".join(filter(None, (panel_note, ", ".join(category["review_reasons"])))))
            if self.fields.get("category") and self.reviewed_values.get("category") == self.fields["category"]:
                self.field_sources["category"] = "manual"
            if self.field_sources.get("category") in {"ai", "default"}:
                self.set_field("category", category["display_category"], "ai")
        self.analysis_review_required = sorted(self.analysis_review_keys())

    def analysis_review_keys(self) -> set[str]:
        required = set(self.suggestion.get("review_fields", []))
        held = " ".join(self.suggestion.get("held", []))
        scores = self.suggestion.get("scores", {})
        thresholds = self.suggestion.get("thresholds", {"panel": .9, "shape": .9, "size": .9})
        def low(key):
            return key in scores and scores[key] is not None and scores[key] < thresholds.get(key, .95)
        if "판넬" in held or low("panel"):
            required.update(("gender", "age", "size"))
        if "성별" in held or "연령" in held or low("shape"):
            required.update(("gender", "age"))
        if "사이즈" in held or low("size"):
            required.add("size")
        category = self.suggestion.get("category", {})
        if category.get("needs_review") or category.get("error"):
            required.add("category")
        return required

    @property
    def pending_review(self) -> list[str]:
        required = set(self.review_required) | self.analysis_review_keys()
        if self.fields.get("age") == "유아동":
            required.add("gender")
        if self.field_sources.get("category") == "default":
            required.add("category")
        invalid = {k for k, value in self.fields.items() if value and value not in OPTIONS.get(k, [])}
        required.update(invalid)
        return [key for key in FIELDS if key in required and
                (key in invalid or not self.fields.get(key) or self.reviewed_values.get(key) != self.fields.get(key))]

    @property
    def export_ready(self) -> bool:
        return self.complete and not self.pending_review

    def set_field(self, key: str, value: str, source: str = "manual"):
        # Re-selecting the same default value is still an explicit human choice.
        self.field_sources[key] = source
        if self.fields.get(key, "") != value:
            self.fields[key] = value
            self.reviewed_values.pop(key, None)
            self.confirmed = False
            if key == "age":
                self.reviewed_values.pop("gender", None)
        if self.fields.get("age") == "유아동" and not self.fields.get("gender"):
            self.fields["gender"] = "남성"
            self.field_sources["gender"] = "child_default"
            self.review_required = sorted(set(self.review_required) | {"gender"})

    def apply_analysis(self):
        values = {**self.suggestion.get("candidates", {}), **self.suggestion.get("values", {})}
        category = self.suggestion.get("category", {})
        if category.get("display_category") and (not self.fields.get("category") or self.field_sources.get("category") in {"default", "ai"}):
            self.set_field("category", category["display_category"], "ai")
        for key in ("gender", "age", "size"):
            value = values.get(key)
            if value and not self.fields.get(key):
                validate_fields({key: value})
                self.set_field(key, value, "ai")
        self.analysis_review_required = sorted(self.analysis_review_keys())

    def confirm_review(self):
        if not self.complete:
            raise ValueError(f"{self.code}: 카테고리·성별·연령·사이즈를 모두 입력하세요.")
        validate_fields(self.fields)
        self.reviewed_values.update(self.fields)
        if self.field_sources.get("category") in {"default", "ai"}:
            self.field_sources["category"] = "manual"
        self.confirmed = True

    @property
    def complete(self) -> bool:
        return all(self.fields.get(key, "") in OPTIONS[key] for key in FIELDS)

    @property
    def status(self) -> str:
        if self.export_ready:
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
    def in_range(self) -> list[Garment]:
        return [g for g in self.garments if g.source_present and self.start <= int(g.code) <= self.end]

    @property
    def empty_photo_codes(self) -> list[str]:
        return [g.code for g in self.in_range if not g.photos]

    @property
    def active(self) -> list[Garment]:
        errors = set(self.errors)
        return [g for g in self.in_range if int(g.code) not in errors]

    @property
    def counts(self) -> dict:
        present = {int(g.code) for g in self.in_range}
        active = self.active
        return {"total": len(active), "confirmed": sum(g.export_ready for g in active),
                "excluded": len(present.intersection(self.errors)),
                "missing": self.end - self.start + 1 - len(present)}

    def export_fingerprint(self) -> str:
        return fingerprint({"workspace": self.id, "date": self.shooting_date, "range": [self.start, self.end],
                            "errors": self.errors, "items": [{"code": g.code, "fields": g.fields,
                            "confirmed": g.confirmed, "review_required": g.review_required,
                            "reviewed_values": g.reviewed_values, "pending_review": g.pending_review}
                            for g in self.active]})


def validate_fields(fields: dict) -> None:
    for key, value in fields.items():
        if key not in FIELDS or not isinstance(value, str):
            raise ValueError("올바르지 않은 입력 항목입니다.")
        if value and value not in OPTIONS[key]:
            raise ValueError(f"{LABELS[key]}의 허용 목록에 없는 값입니다.")
        if len(value) > 100:
            raise ValueError("입력값이 너무 깁니다.")
