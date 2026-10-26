from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from coatdesk.photos import EXTENSIONS, FOLDER_PATTERN, PHOTO_PATTERN, natural_key
from .domain import Garment


def scan(root: Path, start: int, end: int, progress=lambda message: None) -> list[Garment]:
    if start < 0 or end < start or end - start > 100_000:
        raise ValueError("번호 범위는 시작 ≤ 끝, 최대 100,001개로 입력하세요.")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("사진 상위 폴더를 선택하세요.")
    folders: dict[int, tuple[str, Path]] = {}
    # Enumerate directory names first; do not decode images outside this workspace.
    for current, directories, _ in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if not (Path(current) / d).is_symlink()]
        match = FOLDER_PATTERN.fullmatch(Path(current).name)
        if not match:
            continue
        directories[:] = []
        code = match[1]
        number = int(code)
        if not start <= number <= end:
            continue
        if number in folders:
            raise ValueError(f"같은 번호의 폴더가 중복됩니다: {folders[number][0]}, {code}")
        folders[number] = code, Path(current)
    garments = []
    for code, folder in (folders[n] for n in sorted(folders)):
        photos, issues = {}, []
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
                raise ValueError(f"{code}: {angle}번 각도의 사진이 중복됩니다.")
            try:
                with Image.open(path) as image:
                    image.verify()
            except (OSError, ValueError):
                issues.append(f"손상된 사진: {path.name}")
                continue
            photos[angle] = path.relative_to(root).as_posix()
        issues.extend(f"{angle}번 사진 없음" for angle in ("0", "1", "2") if angle not in photos)
        if set(photos) - {"0", "1", "2"}:
            issues.append("추가 각도 사진 있음")
        garments.append(Garment(code, photos, issues))
        progress(f"사진 검사 · {len(garments)} / {len(folders)}벌")
    if not garments:
        raise ValueError("지정한 범위의 0_0001 형식 옷 폴더가 없습니다.")
    return garments
