from pathlib import Path

import pytest

from coatdesk.desktop import copy_to_desktop


def bundle(root: Path):
    root.mkdir()
    exe = root / "CoatDesk.exe"
    exe.write_bytes(b"program")
    runtime = root / "_internal"
    (runtime / "vendor").mkdir(parents=True)
    (runtime / "python.dll").write_bytes(b"runtime")
    (runtime / "vendor" / "ocr.dat").write_bytes(b"ocr")
    return exe, runtime


def test_launch_copy_creates_folder_with_exe_and_runtime_preserving_source(tmp_path):
    exe, runtime = bundle(tmp_path / "downloads")
    destination = tmp_path / "Desktop" / "외투 작업실 작업"
    result = copy_to_desktop(exe, runtime, destination)
    assert result["copied"] == 3
    assert Path(result["executable"]).read_bytes() == exe.read_bytes()
    assert (destination / "_internal/vendor/ocr.dat").read_bytes() == b"ocr"
    assert exe.read_bytes() == b"program"
    assert (runtime / "python.dll").read_bytes() == b"runtime"


def test_repeated_launch_skips_identical_files_and_preserves_saved_work(tmp_path):
    exe, runtime = bundle(tmp_path / "downloads")
    destination = tmp_path / "Desktop"
    destination.mkdir()
    saved = destination / "20261021_task.coat.json"
    saved.write_bytes(b"saved work")
    copy_to_desktop(exe, runtime, destination)
    installed = destination / "외투 작업실.exe"
    before = installed.stat().st_mtime_ns
    result = copy_to_desktop(exe, runtime, destination)
    assert result["copied"] == 0
    assert result["skipped"] == 3
    assert installed.stat().st_mtime_ns == before
    assert saved.read_bytes() == b"saved work"


def test_launch_from_desktop_does_not_copy_onto_itself(tmp_path):
    exe, runtime = bundle(tmp_path / "Desktop")
    result = copy_to_desktop(exe, runtime, exe.parent)
    assert result["already_here"]
    assert result["copied"] == result["skipped"] == 0


def test_new_version_updates_only_changed_program_files(tmp_path):
    exe, runtime = bundle(tmp_path / "downloads")
    destination = tmp_path / "Desktop"
    copy_to_desktop(exe, runtime, destination)
    (destination / "notes.json").write_bytes(b"user file")
    exe.write_bytes(b"new program")
    result = copy_to_desktop(exe, runtime, destination)
    assert result["copied"] == 1
    assert result["skipped"] == 2
    assert (destination / "notes.json").read_bytes() == b"user file"
    assert (destination / "외투 작업실.exe").read_bytes() == b"new program"


def test_failed_runtime_copy_does_not_replace_old_exe(tmp_path, monkeypatch):
    exe, runtime = bundle(tmp_path / "downloads")
    destination = tmp_path / "Desktop"
    copy_to_desktop(exe, runtime, destination)
    exe.write_bytes(b"new program")
    (runtime / "python.dll").write_bytes(b"new runtime")

    def locked(*args):
        raise PermissionError("locked")
    monkeypatch.setattr("coatdesk.desktop.os.replace", locked)
    with pytest.raises(OSError, match="닫고"):
        copy_to_desktop(exe, runtime, destination)
    assert (destination / "외투 작업실.exe").read_bytes() == b"program"
    assert (destination / "_internal/python.dll").read_bytes() == b"runtime"
    assert not list(destination.rglob(".coatdesk-copy-*"))


def test_destination_inside_runtime_is_rejected(tmp_path):
    exe, runtime = bundle(tmp_path / "downloads")
    with pytest.raises(ValueError, match="_internal"):
        copy_to_desktop(exe, runtime, runtime / "nested")
