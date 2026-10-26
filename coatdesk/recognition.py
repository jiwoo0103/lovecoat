from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .domain import OPTIONS, Garment
from .photos import crop_head


def find_tesseract(configured: str = "") -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidates = [configured, str(base / "vendor" / "tesseract" / "tesseract.exe"),
                  shutil.which("tesseract") or "", r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
    return next((p for p in candidates if p and Path(p).is_file()), "")


def shape_feature(image: Image.Image) -> list[int]:
    """Coarse, text-masked placard appearance; deliberately an uncalibrated baseline."""
    bounds = lettering_bounds(image)
    if bounds is None:
        return []
    left, top, right, bottom = bounds
    height = bottom - top
    center = (left + right) / 2
    # Anchor the upper head silhouette to the letters. This removes both the text
    # and the clothing; otherwise coat colours look like changes of mannequin.
    box = (int(center - 1.8 * height), int(top - 2.2 * height),
           int(center + 1.8 * height), int(top))
    canvas = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), "white")
    clipped = (max(0, box[0]), max(0, box[1]), min(image.width, box[2]), min(image.height, box[3]))
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return []
    canvas.paste(image.crop(clipped), (clipped[0] - box[0], clipped[1] - box[1]))
    gray = np.array(ImageOps.grayscale(canvas).filter(ImageFilter.GaussianBlur(.8)))
    ink = (gray < 150).astype(np.uint8)
    if ink.sum() < 30:
        return []
    ink = (np.array(Image.fromarray(ink * 255).resize((48, 64), Image.Resampling.BOX)) >= 128).astype(np.uint8)
    return ink.ravel().tolist()


def lettering_bounds(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Find a horizontal group of dark glyphs inside the placard, excluding its outline."""
    reduced = ImageOps.grayscale(image)
    reduced.thumbnail((600, 600))
    gray = np.array(reduced)
    height, width = gray.shape
    binary = gray < 145
    letters = []
    for x, y, w, h, area in components(binary):
        cx, cy = x + w / 2, y + h / 2
        if (.07 * height < h < .38 * height and .025 * width < w < .55 * width
                and .15 * width < cx < .85 * width and .30 * height < cy < .85 * height
                and area > .12 * w * h):
            letters.append((int(x), int(y), int(w), int(h)))
    if not letters:
        return None
    # A border/hair component is too large; the glyph nearest the face centre anchors the row.
    anchor = min(letters, key=lambda b: abs((b[1] + b[3] / 2) / height - .63))
    line = [b for b in letters if abs((b[1] + b[3] / 2) - (anchor[1] + anchor[3] / 2)) < anchor[3] * .45
            and .45 < b[3] / anchor[3] < 1.7]
    left, top = min(b[0] for b in line), min(b[1] for b in line)
    right, bottom = max(b[0] + b[2] for b in line), max(b[1] + b[3] for b in line)
    sx, sy = image.width / width, image.height / height
    return int(left * sx), int(top * sy), int(right * sx), int(bottom * sy)


def components(binary: np.ndarray):
    """Eight-connected components of a small binary crop, with no native CV runtime."""
    mask = binary.copy()
    height, width = mask.shape
    for start_y, start_x in np.argwhere(mask):
        start_y, start_x = int(start_y), int(start_x)
        if not mask[start_y, start_x]:
            continue
        pending = [(start_y, start_x)]
        mask[start_y, start_x] = False
        left = right = start_x
        top = bottom = start_y
        area = 0
        while pending:
            y, x = pending.pop()
            area += 1
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    if mask[ny, nx]:
                        mask[ny, nx] = False
                        pending.append((ny, nx))
        yield left, top, right - left + 1, bottom - top + 1, area


def lettering_crop(image: Image.Image) -> Image.Image | None:
    bounds = lettering_bounds(image)
    # Add a white border instead of including facial outlines in the OCR image.
    return ImageOps.expand(image.crop(bounds), 18, fill="white") if bounds else None


def classify_shape(feature: list[int], templates: list[dict]) -> tuple[dict, float, str]:
    if not feature or not templates:
        return {}, 0, "머리 기준 사진을 등록하면 분류를 제안합니다."
    scores: dict[tuple, float] = {}
    a = np.array(feature, dtype=np.float32)
    for template in templates:
        b = np.array(template["feature"], dtype=np.float32)
        if a.shape != b.shape or not b.size:
            continue
        union = np.maximum(a, b).sum()
        score = float(np.minimum(a, b).sum() / max(union, 1))
        label = (template["gender"], template["age"])
        scores[label] = max(scores.get(label, 0), score)
    ranking = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    if not ranking:
        return {}, 0, "사용할 수 있는 기준 사진이 없습니다."
    (gender, age), score = ranking[0]
    margin = score - (ranking[1][1] if len(ranking) > 1 else 0)
    if score < 0.72 or margin < 0.12:
        return {}, score, "머리 모양이 불확실합니다. 직접 확인하세요."
    return {"gender": gender, "age": age}, score, "등록한 기준과 비교한 제안입니다. 확인이 필요합니다."


def ocr_size(image: Image.Image, executable: str, allowed: list[str]) -> tuple[str, float]:
    if not executable:
        return "", 0
    best = ("", 0.0)
    votes: dict[str, int] = {}
    glyphs = lettering_crop(image)
    variants = [glyphs] if glyphs is not None else [image]
    for variant in variants:
        variant = ImageOps.grayscale(variant)
        variant.thumbnail((700, 700))
        if glyphs is None and variant.width < 400:
            variant = variant.resize((variant.width * 2, variant.height * 2))
        for angle in (0, -8, 8):
            buffer = io.BytesIO()
            variant.rotate(angle, expand=True, fillcolor=255).save(buffer, format="PNG")
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                [str(Path(executable).resolve()), "stdin", "stdout",
                 "--tessdata-dir", "tessdata", "-l", "eng", "--psm", "7" if glyphs is not None else "11",
                 "-c", "tessedit_char_whitelist=0123456789XSML/", "tsv"],
                input=buffer.getvalue(), capture_output=True, timeout=20, creationflags=flags,
                cwd=Path(executable).resolve().parent,
            )
            if result.returncode:
                raise RuntimeError("OCR 실행에 실패했습니다. Tesseract와 eng 언어 파일을 확인하세요.")
            for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
                value = re.sub(r"\s+", "", row.get("text", "").upper())
                confidence = float(row.get("conf", "-1"))
                if value in allowed and confidence >= 30:
                    votes[value] = votes.get(value, 0) + 1
                    if confidence > best[1]:
                        best = (value, confidence)
    # Conflicting readings stay unresolved, rather than silently voting through them.
    if len(votes) > 1 or best[1] < 55:
        return "", best[1]
    return best


def recognize(garment: Garment, default_roi: list[float], templates: list[dict],
              executable: str, allowed: list[str] | None = None) -> dict:
    if not garment.front_path:
        return {"values": {}, "note": "정면 사진이 없습니다.", "review": True}
    image = crop_head(garment.front_path, garment.roi or default_roi)
    feature = shape_feature(image)
    values, similarity, note = classify_shape(feature, templates)
    size, confidence = ocr_size(image, executable, allowed or OPTIONS["size"])
    if size:
        values["size"] = size
    if not executable:
        note += " OCR 실행 파일이 없어 사이즈 인식은 건너뛰었습니다."
    elif not size:
        note += " 사이즈 글자를 확정하지 못했습니다."
    return {"values": values, "shape": feature, "similarity": round(similarity, 3),
            "ocr_score": round(confidence, 1), "review": True, "note": note}
