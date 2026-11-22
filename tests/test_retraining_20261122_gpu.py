"""The two training tasks must never acquire the GPU lease together."""
import json
import sys

import pytest

from scripts import retraining_20261122_gpu as leases

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file-lock implementation")


def test_two_owners_cannot_share_gpu(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "WORK", tmp_path)
    with leases.gpu_lease("category", timeout_seconds=0):
        with pytest.raises(TimeoutError):
            with leases.gpu_lease("size", timeout_seconds=0):
                pytest.fail("A second owner acquired the GPU")


def test_exception_releases_gpu_for_other_task(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "WORK", tmp_path)
    with pytest.raises(ValueError):
        with leases.gpu_lease("category", timeout_seconds=0):
            raise ValueError("training failed")
    with leases.gpu_lease("size", timeout_seconds=0):
        assert json.loads((tmp_path / "gpu-owner.json").read_text())["owner"] == "size"
    assert json.loads((tmp_path / "gpu-owner.json").read_text())["state"] == "released"
