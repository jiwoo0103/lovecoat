from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .photos import digest


def desktop_work_folder() -> Path:
    from PySide6.QtCore import QStandardPaths

    desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    return Path(desktop or Path.home() / "Desktop") / "외투 작업실 작업"


def copy_to_desktop(executable: Path, runtime: Path, destination: Path,
                    progress: Callable[[str], None] = lambda _: None) -> dict:
    """Copy a frozen app, leaving the source and all task JSON files in place."""
    executable, runtime, destination = executable.resolve(), runtime.resolve(), destination.resolve()
    if not executable.is_file() or not runtime.is_dir():
        raise ValueError("실행 파일 또는 필수 실행 폴더를 찾지 못했습니다.")
    if destination.is_relative_to(runtime):
        raise ValueError("바탕화면 저장 폴더를 실행 파일의 _internal 안에 둘 수 없습니다.")
    destination.mkdir(parents=True, exist_ok=True)
    if executable.parent == destination and runtime == destination / "_internal":
        return {"copied": 0, "skipped": 0, "executable": str(executable), "already_here": True}
    files = []
    for path in sorted(runtime.rglob("*")):
        if path.is_symlink():
            raise ValueError("실행 파일에 연결된 링크가 있어 자동 복사를 중단했습니다.")
        if path.is_file():
            files.append((path, destination / "_internal" / path.relative_to(runtime)))
    # Publish the executable after all required runtime files have been copied.
    installed_exe = destination / "외투 작업실.exe"
    files.append((executable, installed_exe))
    copied = skipped = 0
    for index, (source, target) in enumerate(files, 1):
        progress(f"바탕화면에 실행 파일 준비 중 · {index} / {len(files)}개")
        fingerprint = digest(source)
        if target.is_symlink():
            raise ValueError("바탕화면에 같은 이름의 링크 파일이 있어 자동 복사를 중단했습니다.")
        if target.is_file() and digest(target) == fingerprint:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".coatdesk-copy-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output)
                output.flush()
                os.fsync(output.fileno())
            if digest(Path(temporary)) != fingerprint:
                raise OSError("바탕화면에 복사한 실행 파일의 내용 확인에 실패했습니다.")
            try:
                os.replace(temporary, target)
            except PermissionError:
                raise OSError("바탕화면의 외투 작업실이 실행 중이거나 파일을 바꿀 수 없습니다. 해당 앱을 닫고 다시 실행하세요.") from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        copied += 1
    return {"copied": copied, "skipped": skipped, "executable": str(installed_exe), "already_here": False}
