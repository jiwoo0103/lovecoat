from __future__ import annotations

import json
import sys
from pathlib import Path

from coatdesk.photos import digest

from .domain import Garment, Workspace, fingerprint, photo_path

DEFAULT_THRESHOLDS = {"panel": .50, "shape": .95, "size": .95}
ENGINE_VERSION = "three-model-cpu-448-v2.2-autofill"


class RecognitionEngine:
    version = ENGINE_VERSION

    executable = "three-trained-models"

    def __init__(self, thresholds=None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        directory = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "resources/ai"
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            raise RuntimeError("학습 모델이 없습니다. AI 모델 포함 설치파일로 다시 설치하세요.")
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        for name, expected in metadata["files"].items():
            if digest(directory/name) != expected:
                raise RuntimeError("학습 모델 파일이 손상되었습니다. 설치파일로 다시 설치하세요.")
        self.model_key = fingerprint(metadata)
        from .ai_runtime import Models
        self.models = Models(directory)

    def key(self, workspace: Workspace, garment: Garment) -> str:
        relative = garment.photos.get(garment.front, "")
        path = photo_path(workspace.source, relative) if relative else None
        return fingerprint({"engine": self.version, "models": self.model_key, "thresholds": self.thresholds,
                            "file": digest(path) if path and path.is_file() else "missing", "front": garment.front,
                            "manual_roi": garment.roi if garment.roi_manual else None})

    def analyze(self, workspace: Workspace, garment: Garment) -> dict:
        relative = garment.photos.get(garment.front, "")
        if not relative:
            return {"values": {}, "review": True, "note": "정면 사진이 없습니다.", "held": ["사진"]}
        result = self.models.analyze(photo_path(workspace.source, relative), self.thresholds, garment.roi if garment.roi_manual else None)
        result.update(engine_version=self.version, front=garment.front, thresholds=self.thresholds)
        return result

    def template(self, workspace, garment):
        raise ValueError("새 모델은 학습된 판넬 모양을 사용합니다. 머리 기준 등록은 필요하지 않습니다.")
