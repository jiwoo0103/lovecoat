"""Exercise real CPU model reuse, cancellation, resume and forced inference."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication

from coatdesk_v2.backend import Backend
from coatdesk_v2.domain import Garment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    app = QGuiApplication([])
    backend = Backend(args.output / "data")
    garments = [Garment(f"{i:04}", {"0": f"0_{i:04}/img_0_0_0.jpg"}) for i in range(1, 7)]
    garments[0].set_field("size", "L")
    key = backend.repository.create("repeat inference", "", args.source, 1, 6, "", garments)
    backend.openWorkspace(key)
    rounds = []
    for index, force in enumerate((False, False, False, True, False)):
        if index == 4:
            backend.setAiThresholds(91, 90, 90)
        loop = QEventLoop()
        def progress(event):
            if index == 0 and event[1] == "progress":
                backend.cancelAnalysis()
        def finished():
            if not backend.jobs:
                loop.quit()
        backend.event.connect(progress)
        backend.changed.connect(finished)
        started = time.perf_counter()
        backend.startAnalysis(force)
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        backend.event.disconnect(progress)
        backend.changed.disconnect(finished)
        if backend.jobs:
            print("real inference exceeded 60 seconds", flush=True)
            os._exit(1)
        workspace = backend.repository.load(key)
        rounds.append({"round": index, "seconds": time.perf_counter()-started,
                       "done": sum(g.analysis_state == "done" for g in workspace.garments),
                       "engine_id": id(backend._recognition_engine), "error": backend.error,
                       "first_key": workspace.garments[0].analysis_key,
                       "manual_size": workspace.garments[0].fields.get("size")})
        print(json.dumps(rounds[-1]), flush=True)
    ok = (0 < rounds[0]["done"] < 6 and all(r["done"] == 6 for r in rounds[1:])
          and len({r["engine_id"] for r in rounds}) == 1
          and all(not r["error"] and r["manual_size"] == "L" for r in rounds)
          and rounds[3]["first_key"] != rounds[4]["first_key"])
    backend.shutdown()
    restored = Backend(args.output / "data")
    restored.openWorkspace(key)
    ok &= all(g.analysis_state == "done" for g in restored.workspace.garments)
    restored.shutdown()
    report = {"ok": bool(ok), "rounds": rounds, "restart_preserves_results": True}
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert app
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
