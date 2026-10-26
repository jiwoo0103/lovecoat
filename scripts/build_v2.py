"""Build into a new release directory. No install or global configuration changes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from coatdesk_v2 import __version__  # noqa: E402 - direct script invocation needs the repository root


def desktop_config(path):
    """Validate without printing credentials or accepting user/service-account tokens."""
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        installed = config.get("installed", {})
        required = ("client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris")
        forbidden = {"refresh_token", "access_token", "private_key", "private_key_id", "id_token"}
        def unsafe(value):
            if isinstance(value, dict):
                return bool(forbidden.intersection(value)) or any(unsafe(v) for v in value.values())
            return isinstance(value, list) and any(unsafe(v) for v in value)
        if not all(installed.get(key) for key in required) or unsafe(config) or "web" in config:
            raise ValueError()
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError("유효한 Desktop OAuth 클라이언트 설정만 포함할 수 있습니다. 사용자 토큰·서비스 계정 키는 금지합니다.") from None
    return {"installed": installed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--makensis", type=Path)
    parser.add_argument("--oauth-client", type=Path, help="배포용 Desktop OAuth 클라이언트 설정 (사용자 토큰 금지)")
    args = parser.parse_args()
    release = ROOT / "dist-releases" / ("v2-"+uuid.uuid4().hex[:12])
    release.mkdir(parents=True)
    stage = release / "build-input"
    stage.mkdir()
    licenses = stage / "licenses"
    for distribution in importlib.metadata.distributions():
        for file in distribution.files or []:
            if ".dist-info/licenses/" in str(file).replace("\\", "/"):
                source = distribution.locate_file(file)
                if source.is_file():
                    target = licenses / distribution.metadata["Name"] / str(file).split("licenses/", 1)[-1]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    runtime = ROOT / "resources/ai"
    if not (runtime / "manifest.json").is_file():
        raise SystemExit("학습 모델 resources/ai를 먼저 준비하세요.")
    if "+cpu" not in importlib.metadata.version("torch"):
        raise SystemExit("CPU 전용 PyTorch 빌드 환경을 사용하세요.")
    additions = [(runtime, "resources/ai"), (ROOT / "resources/standard_sheet.json", "resources"),
                 (ROOT / "coatdesk_v2/qml", "coatdesk_v2/qml"),
                 (licenses, "licenses"), (ROOT / "README.md", "."), (ROOT / "THIRD_PARTY_NOTICES.md", ".")]
    if args.oauth_client:
        config = desktop_config(args.oauth_client)
        resource = stage / "resources"
        resource.mkdir()
        (resource / "google_oauth.json").write_text(json.dumps(config), encoding="utf-8")
        additions.append((resource, "resources"))
    environment = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
                                          str(windows / "System32"), str(windows)])
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--name", "CoatDesk2",
               "--workpath", str(ROOT / "build/v2-ai"), "--specpath", str(stage), "--distpath", str(release),
               "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtPdf",
               "--exclude-module", "cv2", "--exclude-module", "matplotlib", "--exclude-module", "scipy",
               "--exclude-module", "pandas", "--exclude-module", "IPython", "--exclude-module", "pytest",
               "--exclude-module", "tensorboard", "--exclude-module", "tkinter", "--collect-all", "torchvision"]
    for source, destination in additions:
        command.extend(["--add-data", f"{source}{os.pathsep}{destination}"])
    command.append(str(ROOT / "run_v2.py"))
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    payload = release / "CoatDesk2"
    shutil.copy2(ROOT / "README.md", payload / "사용법.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", payload / "THIRD_PARTY_NOTICES.md")
    files = sorted(p for p in payload.rglob("*") if p.is_file())
    manifest = stage / "uninstall-files.nsh"
    lines = []
    for path in files:
        relative = str(path.relative_to(payload)).replace("/", "\\").replace("$", "$$")
        if '"' in relative:
            raise SystemExit("설치파일 경로에 큰따옴표가 있습니다.")
        lines.append(f'Delete "$INSTDIR\\{relative}"')
    directories = sorted((p for p in payload.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for path in directories:
        relative = str(path.relative_to(payload)).replace("/", "\\").replace("$", "$$")
        lines.append(f'RMDir "$INSTDIR\\{relative}"')
    manifest.write_text("\n".join(lines)+"\n", encoding="utf-8-sig")
    compiler = args.makensis or (Path(shutil.which("makensis")) if shutil.which("makensis") else None)
    installer = release / f"CoatDesk-{__version__}-Setup.exe"
    if compiler:
        subprocess.run([str(compiler.resolve()), "/INPUTCHARSET", "UTF8", f"/DPAYLOAD={payload}", f"/DOUTPUT={installer}",
                        f"/DUNINSTALL_MANIFEST={manifest}", str(ROOT / "installer/CoatDesk2.nsi")], cwd=ROOT, check=True)
    report = {"version": __version__, "executable": str(payload / "CoatDesk2.exe"), "trained_models": 3,
              "category_ai": False, "embedded_sheet_template": True,
              "installer": str(installer) if installer.exists() else None,
              "google_configured": bool(args.oauth_client), "files": len(files),
              "bytes": sum(p.stat().st_size for p in files),
              "dependencies": {p: importlib.metadata.version(p) for p in ("PySide6-Essentials", "PyInstaller", "numpy", "Pillow")}}
    if installer.exists():
        report["installer_sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
    (release / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # This pointer is separate from the original prototype's latest.txt.
    (ROOT / "dist-releases/latest-v2.txt").write_text(str(payload.relative_to(ROOT) / "CoatDesk2.exe"), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not compiler:
        print("NSIS was not found; onedir is ready, installer compilation was skipped.")


if __name__ == "__main__":
    main()
