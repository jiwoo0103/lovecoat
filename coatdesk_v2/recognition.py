from __future__ import annotations

import json
import sys
from pathlib import Path

from coatdesk.photos import digest

from .domain import Garment, Workspace, fingerprint, photo_path
from .category import DEFAULT_CATEGORY_THRESHOLDS, decode

DEFAULT_THRESHOLDS = {"panel": .90, "shape": .90, "size": .90}
ENGINE_VERSION = "four-model-cpu-v2.4.1-configurable-category"


class RecognitionEngine:
    version = ENGINE_VERSION

    executable = "four-trained-models"

    def __init__(self, thresholds=None, category_thresholds=None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.category_thresholds = {**DEFAULT_CATEGORY_THRESHOLDS, **(category_thresholds or {})}
        directory = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "resources/ai"
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            raise RuntimeError("학습 모델이 없습니다. AI 모델 포함 설치파일로 다시 설치하세요.")
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        category_directory = directory / "category"
        for name, expected in metadata["files"].items():
            if digest(directory/name) != expected:
                raise RuntimeError("학습 모델 파일이 손상되었습니다. 설치파일로 다시 설치하세요.")
        from .ai_runtime import Models
        self.models = Models(directory)
        from .category import CategoryModel
        self.category_error = ""
        category_metadata = {}
        try:
            category_metadata = json.loads((category_directory / "manifest.json").read_text(encoding="utf-8"))
            for name, expected in category_metadata["files"].items():
                if digest(category_directory / name) != expected:
                    raise ValueError("카테고리 모델 파일이 손상되었습니다.")
            self.category_model = CategoryModel(category_directory)
        except Exception as error:
            self.category_model = None
            self.category_error = type(error).__name__
        self.model_key = fingerprint({"panel": metadata, "category": category_metadata, "category_error": self.category_error})

    def key(self, workspace: Workspace, garment: Garment) -> str:
        relative = garment.photos.get(garment.front, "")
        path = photo_path(workspace.source, relative) if relative else None
        return fingerprint({"engine": self.version, "models": self.model_key, "thresholds": self.thresholds,
                            "category_thresholds": self.category_thresholds,
                            "file": digest(path) if path and path.is_file() else "missing", "front": garment.front,
                            "manual_roi": garment.roi if garment.roi_manual else None})

    def analyze(self, workspace: Workspace, garment: Garment) -> dict:
        relative = garment.photos.get(garment.front, "")
        if not relative:
            return {"values": {}, "review": True, "note": "정면 사진이 없습니다.", "held": ["사진"],
                    "review_fields": ["gender", "age", "size"],
                    "category": {"error": "MissingPhoto", "needs_review": True, "review_reasons": ["정면 사진 없음"]}}
        path = photo_path(workspace.source, relative)
        try:
            result = self.models.analyze(path, self.thresholds, garment.roi if garment.roi_manual else None)
        except Exception as error:
            result = {"values": {}, "candidates": {}, "review_fields": ["gender", "age", "size"],
                      "note": f"판넬 분석 실패 ({type(error).__name__})", "held": ["판넬 분석 실패"]}
        try:
            from coatdesk_ab.data import load_photo
            if self.category_model is None:
                raise ValueError(self.category_error)
            category = self.category_model.analyze(load_photo(path))
            category.update(decode(category["probabilities"], self.category_thresholds["sleeve"], self.category_thresholds["length"]))
        except Exception as error:
            category = {"needs_review": True, "error": type(error).__name__, "review_reasons": ["카테고리 분석 실패 · 사진과 안내판을 확인하세요."]}
        result["category"] = category
        if category.get("display_category"):
            result.setdefault("values", {})["category"] = category["display_category"]
        if category.get("candidate"):
            result.setdefault("candidates", {})["category"] = category["candidate"]
        result.setdefault("scores", {}).update(category=category.get("score"), sleeve=category.get("sleeve_score"), length=category.get("length_score"))
        if category.get("needs_review"):
            result["panel_note"] = result.get("note", "")
            result["note"] = " · ".join(filter(None, (result.get("note"), ", ".join(category["review_reasons"]))))
        result.update(engine_version=self.version, front=garment.front, thresholds=self.thresholds)
        return result

    def template(self, workspace, garment):
        raise ValueError("새 모델은 학습된 판넬 모양을 사용합니다. 머리 기준 등록은 필요하지 않습니다.")
