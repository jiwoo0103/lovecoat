"""Install only into a fresh project-local sandbox and check registry invariance."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import winreg
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from coatdesk_v2 import __version__  # noqa: E402


def registry_snapshot():
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2"
    values = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            for index in range(winreg.QueryInfoKey(handle)[1]):
                values.append(winreg.EnumValue(handle, index))
    except FileNotFoundError:
        return None
    return hashlib.sha256(json.dumps(sorted(values), ensure_ascii=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local"):
        raise ValueError("Installation verification must stay inside project/.local")
    installed = output / "program"
    if installed.exists():
        raise FileExistsError("Use a fresh sandbox installation path")
    installer = args.release.resolve() / f"CoatDesk-{__version__}-Setup.exe"
    before = registry_snapshot()
    process = subprocess.run([str(installer), "/S", "/SANDBOX", f"/D={installed}"], timeout=300, check=True)
    after = registry_snapshot()
    report = {"ok": process.returncode == 0 and before == after and (installed / ".sandbox-install").is_file(),
              "registry_unchanged": before == after, "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
              "exe_sha256": hashlib.sha256((installed / "CoatDesk2.exe").read_bytes()).hexdigest(),
              "executable": str(installed / "CoatDesk2.exe")}
    (output / "installation-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
