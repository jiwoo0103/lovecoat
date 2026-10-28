"""Check a local V2.4 payload without executing or uploading its contents."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args()
    payload = args.payload or args.release / "CoatDesk2"
    internal = payload / "_internal"
    ai = internal / "resources/ai"
    errors = []
    for directory in (ai, ai / "category"):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            if sha(directory / name) != expected:
                errors.append(f"model asset mismatch: {name}")
    for source in (ROOT / "coatdesk_v2/qml").glob("*.qml"):
        if sha(source) != sha(internal / "coatdesk_v2/qml" / source.name):
            errors.append(f"QML mismatch: {source.name}")
    if sha(ROOT / "resources/standard_sheet.json") != sha(internal / "resources/standard_sheet.json"):
        errors.append("sheet template mismatch")
    models = list(ai.rglob("*.pt"))
    if len(models) != 4:
        errors.append("expected four models")
    policy = json.loads((ai / "category/policy.json").read_text(encoding="utf-8"))
    if policy["thresholds"] != {"sleeve": .9, "length": .9}:
        errors.append("category threshold mismatch")
    bad_files = []
    for path in payload.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        relative = path.relative_to(payload).as_posix()
        if name.endswith((".sqlite3", ".db", ".dpapi", ".xlsx")) or name.startswith(("client_secret", ".env")):
            bad_files.append(relative)
        if name.endswith(".dll") and any(word in name for word in ("cudart", "cublas", "cudnn")):
            bad_files.append(relative)
        if name.endswith((".jpg", ".jpeg", ".png")) and "resources/ai/category/references/" not in relative:
            # Qt/torchvision can carry their own public assets. Only reject
            # photographs in the application-owned resource directories.
            if relative.startswith("_internal/resources/"):
                bad_files.append(relative)
    errors.extend(f"unexpected private/GPU asset: {name}" for name in bad_files)
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(payload / "CoatDesk2.exe"))
    pyz_name = next(name for name in archive.toc if name.endswith(".pyz"))
    python_archive = archive.open_embedded_archive(pyz_name)
    modules = python_archive.toc
    if any(name.startswith(("google_auth", "googleapiclient", "scripts.category_")) for name in modules):
        errors.append("retired authentication or training modules bundled")
    if "coatdesk_v2.category" not in modules or not (internal / "cv2/cv2.pyd").is_file():
        errors.append("category runtime missing")
    backend_code = python_archive.extract("coatdesk_v2.backend")
    if "ThreadPoolExecutor" not in backend_code.co_names:
        errors.append("persistent Python inference worker missing")
    notices = [p.relative_to(internal / "licenses").as_posix() for p in (internal / "licenses").rglob("*") if p.is_file()]
    if not any(name.lower().startswith("opencv-python-headless/") for name in notices):
        errors.append("OpenCV notices missing")
    report = {"ok": not errors, "errors": errors, "models": len(models), "policy": policy,
              "exe_sha256": sha(payload / "CoatDesk2.exe"), "qml_files": len(list((ROOT / "coatdesk_v2/qml").glob("*.qml"))),
              "cpu_only": not bad_files, "private_assets_found": bad_files, "license_files": len(notices)}
    target = args.release / ("installed-content-audit.json" if args.payload else "content-audit.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
