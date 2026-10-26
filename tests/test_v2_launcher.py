import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "실행2.cmd"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")


def check(launcher: Path, cwd: Path):
    environment = os.environ.copy()
    # A stale inherited value must not make an empty pointer start an old app.
    environment["COATDESK_V2_EXE"] = "wrong-inherited.exe"
    return subprocess.run([environment["COMSPEC"], "/d", "/c", "call", str(launcher), "/check"],
                          cwd=cwd, env=environment, input=b"\r\n", capture_output=True, timeout=15)


def test_launcher_uses_windows_line_endings():
    data = LAUNCHER.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") > 0
    assert not data.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("name", ["normal", "folder with spaces", "한글 공백 경로"])
def test_launcher_resolves_paths_from_a_different_working_directory(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    launcher = root / "실행2.cmd"
    launcher.write_bytes(LAUNCHER.read_bytes())
    relative = Path("dist-releases") / "새 릴리스" / "CoatDesk2.exe"
    executable = root / relative
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"existence probe; /check must not launch this file")
    (root / "dist-releases/latest-v2.txt").write_text(str(relative), encoding="utf-8")
    result = check(launcher, tmp_path)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert "실행 파일 경로를 확인했습니다." in result.stdout.decode("utf-8")


@pytest.mark.parametrize("pointer", [None, "", "dist-releases\\missing.exe"])
def test_missing_or_empty_pointer_reports_failure_without_starting_an_app(tmp_path, pointer):
    launcher = tmp_path / "실행2.cmd"
    launcher.write_bytes(LAUNCHER.read_bytes())
    if pointer is not None:
        (tmp_path / "dist-releases").mkdir()
        (tmp_path / "dist-releases/latest-v2.txt").write_text(pointer, encoding="utf-8")
    result = check(launcher, tmp_path)
    assert result.returncode == 1
    assert not result.stderr
    assert "실행 파일을 찾을 수 없습니다." in result.stdout.decode("utf-8")


def test_current_release_launcher_finds_the_actual_executable(tmp_path):
    if not (ROOT / "dist-releases/latest-v2.txt").is_file():
        pytest.skip("No local packaged release has been built")
    result = check(LAUNCHER, tmp_path)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
