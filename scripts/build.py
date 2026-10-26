"""Build a portable folder. No installer or global/system modifications."""
import importlib.metadata
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "dist-releases"
DIST = RELEASES / uuid.uuid4().hex[:12]

if __name__ == "__main__":
    if not (ROOT / "vendor/tesseract/tessdata/eng.traineddata").is_file():
        raise SystemExit("vendor/tesseract의 실행 파일과 eng.traineddata를 먼저 준비하세요. README 참고.")
    licenses = ROOT / "build/licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        for file in distribution.files or []:
            if ".dist-info/licenses/" in str(file).replace("\\", "/"):
                source = distribution.locate_file(file)
                target = licenses / name / Path(str(file)).name
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    build_environment = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    # A developer's PATH may contain Poppler/Conda ICU with a different ABI.
    # Qt uses the Windows ICU API; do not package an unrelated DLL from PATH.
    build_environment["PATH"] = os.pathsep.join([
        str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
        str(windows / "System32"), str(windows),
    ])
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
        "--workpath", "build/isolated",
        "--distpath", str(DIST),
        "--name", "CoatDesk", "--add-data", "vendor/tesseract:vendor/tesseract",
        "--add-data", "build/licenses:licenses",
        "--add-data", "README.md:.", "--add-data", "THIRD_PARTY_NOTICES.md:.",
        "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "cv2",
        "run_app.py",
    ], cwd=ROOT, env=build_environment, check=True)
    shutil.copy2(ROOT / "README.md", DIST / "CoatDesk/사용법.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", DIST / "CoatDesk/THIRD_PARTY_NOTICES.md")
    # Publish only a complete build. Running copies may keep their DLLs locked.
    pointer = RELEASES / "latest.txt"
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(str((DIST / "CoatDesk/CoatDesk.exe").relative_to(ROOT)), encoding="ascii")
    temporary.replace(pointer)
    print(f"Ready: {DIST / 'CoatDesk/CoatDesk.exe'}")
