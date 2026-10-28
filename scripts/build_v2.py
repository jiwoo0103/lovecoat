"""Build into a new release directory. No install or global configuration changes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import ssl
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from coatdesk_v2 import __version__  # noqa: E402 - direct script invocation needs the repository root
from coatdesk_v2.category import POLICY_VERSION, DEFAULT_CATEGORY_THRESHOLDS  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--makensis", type=Path)
    args = parser.parse_args()
    release = ROOT / "dist-releases" / ("v2-"+uuid.uuid4().hex[:12])
    release.mkdir(parents=True)
    stage = release / "build-input"
    stage.mkdir()
    licenses = stage / "licenses"
    retired = {"google-api-python-client", "google-auth", "google-auth-httplib2", "google-auth-oauthlib",
               "httplib2", "oauthlib", "requests-oauthlib", "pywin32"}
    for distribution in importlib.metadata.distributions():
        if distribution.metadata["Name"].lower().replace("_", "-") in retired:
            continue
        for file in distribution.files or []:
            normalized = str(file).replace("\\", "/")
            legacy_notice = ".dist-info/" in normalized and Path(normalized).name.lower().startswith(("license", "copying", "notice"))
            if ".dist-info/licenses/" in normalized or legacy_notice:
                source = distribution.locate_file(file)
                if source.is_file():
                    relative = normalized.split("licenses/", 1)[-1] if "/licenses/" in normalized else Path(normalized).name
                    target = licenses / distribution.metadata["Name"] / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    runtime = ROOT / "resources/ai"
    if not (runtime / "manifest.json").is_file():
        raise SystemExit("학습 모델 resources/ai를 먼저 준비하세요.")
    category = runtime / "category"
    category_manifest = json.loads((category / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in category_manifest["files"].items():
        if hashlib.sha256((category / name).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"카테고리 실행 자산 해시 불일치: {name}")
    if "+cpu" not in importlib.metadata.version("torch"):
        raise SystemExit("CPU 전용 PyTorch 빌드 환경을 사용하세요.")
    additions = [(runtime, "resources/ai"), (ROOT / "resources/standard_sheet.json", "resources"),
                 (ROOT / "coatdesk_v2/qml", "coatdesk_v2/qml"),
                 (licenses, "licenses"), (ROOT / "README.md", "."), (ROOT / "THIRD_PARTY_NOTICES.md", ".")]
    # Some build runtimes load an additional distrust bundle next to ssl.py.
    # Preserve that policy in the frozen app, where ssl.__file__ is in _internal.
    distrust_bundle = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if distrust_bundle.is_file():
        additions.append((distrust_bundle, "."))
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    environment = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
                                          str(windows / "System32"), str(windows)])
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--name", "CoatDesk2",
               "--workpath", str(ROOT / "build/v2-ai"), "--specpath", str(stage), "--distpath", str(release),
               "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtPdf",
               "--exclude-module", "matplotlib", "--exclude-module", "scipy",
               "--exclude-module", "pandas", "--exclude-module", "IPython", "--exclude-module", "pytest",
               "--exclude-module", "tensorboard", "--exclude-module", "tkinter", "--collect-all", "torchvision"]
    for module in ("google", "googleapiclient", "google_auth_httplib2", "google_auth_oauthlib", "httplib2",
                   "oauthlib", "requests_oauthlib", "win32crypt"):
        command.extend(["--exclude-module", module])
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
    report = {"version": __version__, "executable": str(payload / "CoatDesk2.exe"), "trained_models": 4,
              "category_ai": True, "category_policy": POLICY_VERSION, "category_thresholds": DEFAULT_CATEGORY_THRESHOLDS,
              "category_assets": category_manifest, "embedded_sheet_template": True, "excel_export": True,
              "installer": str(installer) if installer.exists() else None,
              "google_configured": False, "files": len(files),
              "bytes": sum(p.stat().st_size for p in files),
              "dependencies": {p: importlib.metadata.version(p) for p in ("PySide6-Essentials", "PyInstaller", "numpy", "Pillow", "openpyxl")}}
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
