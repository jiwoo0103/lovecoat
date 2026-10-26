from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from .domain import Garment

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FOLDER_PATTERN = re.compile(r"^0_(\d+)$", re.ASCII)
PHOTO_PATTERN = re.compile(r"^img_0_0_(\d+)$", re.IGNORECASE | re.ASCII)
Progress = Callable[[str], None]


def natural_key(value: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", value))


def discover(root: Path, progress: Progress = lambda _: None) -> tuple[list[Garment], list[str]]:
    root = root.resolve(strict=True)
    folders = []
    warnings = []
    for current, directories, _ in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if not Path(current, d).is_symlink()]
        if FOLDER_PATTERN.fullmatch(Path(current).name):
            folders.append(Path(current))
            directories[:] = []
    folders.sort(key=lambda p: (natural_key(p.name), str(p)))
    garments = []
    codes = set()
    for folder in folders:
        code = FOLDER_PATTERN.fullmatch(folder.name)[1]
        # Treat padded and unpadded aliases as a collision, but retain original spelling.
        if int(code) in codes:
            raise ValueError(f"외투코드가 중복됩니다: {code}\n서로 다른 날짜 폴더를 함께 선택했는지 확인하세요.")
        codes.add(int(code))
        photos = {}
        issues = []
        for path in sorted(folder.iterdir(), key=lambda p: natural_key(p.name)):
            if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
                continue
            if path.is_symlink():
                issues.append(f"링크 파일 제외: {path.name}")
                continue
            match = PHOTO_PATTERN.fullmatch(path.stem)
            if not match:
                issues.append(f"알 수 없는 이름: {path.name}")
                continue
            angle = str(int(match[1]))
            if angle in photos:
                raise ValueError(f"{folder.name}: 같은 각도의 사진이 두 장입니다 ({angle}).")
            try:
                with Image.open(path) as image:
                    image.verify()
            except (OSError, ValueError):
                issues.append(f"읽을 수 없는 사진: {path.name}")
                continue
            photos[angle] = str(path.resolve())
        for angle in ("0", "1", "2"):
            if angle not in photos:
                issues.append(f"{angle}번 사진 없음")
        extra = set(photos) - {"0", "1", "2"}
        if extra:
            issues.append(f"추가 각도: {', '.join(sorted(extra, key=int))}")
        garments.append(Garment(code=code, folder=str(folder), photos=photos, issues=issues))
        progress(f"사진 확인 중 · {len(garments)} / {len(folders)}벌")
    if not garments:
        raise ValueError("0_0001 같은 옷 번호 폴더를 찾지 못했습니다. 그 폴더들을 포함하는 상위 폴더를 선택하세요.")
    warnings.extend(f"{g.code}: {issue}" for g in garments for issue in g.issues)
    return garments, warnings


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


@dataclass
class CopyItem:
    source: str
    target: str
    fingerprint: str
    status: str


def plan_copy(garments: list[Garment], source_root: Path, output: Path) -> list[CopyItem]:
    output = output.resolve()
    root = source_root.resolve()
    if output == root or root in output.parents:
        raise ValueError("출력 폴더는 원본 사진 폴더 밖에서 선택하세요.")
    result = []
    for garment in garments:
        for angle, source in sorted(garment.photos.items(), key=lambda pair: int(pair[0])):
            path = Path(source)
            target = output / f"0_{garment.code}_{angle}{path.suffix.lower()}"
            fingerprint = digest(path)
            status = "복사"
            if target.exists():
                status = "동일 · 건너뜀" if target.is_file() and digest(target) == fingerprint else "충돌"
            result.append(CopyItem(str(path), str(target), fingerprint, status))
    return result


def execute_copy(plan: list[CopyItem], progress: Progress = lambda _: None) -> dict:
    if any(item.status == "충돌" for item in plan):
        raise ValueError("이름이 같고 내용이 다른 파일이 있습니다. 다른 출력 폴더를 선택하세요.")
    copied, skipped = 0, 0
    for index, item in enumerate(plan):
        source, target = Path(item.source), Path(item.target)
        if digest(source) != item.fingerprint:
            raise ValueError(f"미리보기 이후 원본이 변경되었습니다: {source.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create also protects against another process creating the target.
        try:
            stream = target.open("xb")
        except FileExistsError:
            if target.is_file() and digest(target) == item.fingerprint:
                skipped += 1
                continue
            raise ValueError(f"복사 중 이름 충돌이 생겼습니다: {target.name}") from None
        try:
            with stream, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, stream)
                stream.flush()
                os.fsync(stream.fileno())
            if digest(target) != item.fingerprint:
                raise OSError("복사된 사진의 검증에 실패했습니다.")
        except Exception:
            # Only our exclusively created incomplete output is removed.
            target.unlink(missing_ok=True)
            raise
        copied += 1
        progress(f"사진 복사 중 · {index + 1} / {len(plan)}장")
    return {"copied": copied, "skipped": skipped}


def read_image(path: str, max_size: int = 1800) -> Image.Image:
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((max_size, max_size))
        return image.copy()


def crop_head(path: str, roi: list[float]) -> Image.Image:
    image = read_image(path, 3000)
    x1, y1, x2, y2 = roi
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("머리 영역이 잘못되었습니다.")
    return image.crop((int(x1 * image.width), int(y1 * image.height),
                       int(x2 * image.width), int(y2 * image.height)))
