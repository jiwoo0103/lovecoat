"""One process at a time may use the GPU for the two authorized retraining runs."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".local/retraining-20261122"


@contextmanager
def gpu_lease(owner, timeout_seconds=14400, poll_seconds=5):
    """An OS file lock is released on process exit, including abnormal termination."""
    import msvcrt

    WORK.mkdir(parents=True, exist_ok=True)
    lock_path = WORK / "gpu.lock"
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        started = time.monotonic()
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError(f"GPU lease timed out: {owner}") from error
                time.sleep(poll_seconds)
        record = {"owner": owner, "pid": os.getpid(), "state": "acquired",
                  "updated_at_utc": datetime.now(timezone.utc).isoformat()}
        (WORK / "gpu-owner.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        try:
            yield record
        finally:
            record.update(state="released", updated_at_utc=datetime.now(timezone.utc).isoformat())
            (WORK / "gpu-owner.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
