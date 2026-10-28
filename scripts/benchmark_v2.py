"""Local, read-only photo benchmark; all generated work lives in --output."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from coatdesk_v2.backend import Backend
from coatdesk_v2.domain import Garment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = json.loads(args.protocol.read_text(encoding="utf-8"))["records"][:600]
    paths = [Path(r["source"]) for r in records]
    source = Path(os.path.commonpath([str(p.parent) for p in paths]))
    app = QGuiApplication([])
    backend = Backend(args.output / "data")
    garments = [Garment(r["code"], {"0": str(p.relative_to(source))}, fields={
        "category": "자켓", "gender": "남성", "age": "성인", "size": "M"}) for r, p in zip(records, paths)]
    keys = [backend.repository.create(f"benchmark-{i}", "", source, 0, 99999, "", garments) for i in range(2)]
    samples = {}
    def measure(label, action, count):
        values = []
        for i in range(count):
            started = time.perf_counter()
            action(i)
            while backend.jobs:
                QTest.qWait(1)
            app.processEvents()
            values.append((time.perf_counter() - started) * 1000)
        samples[label] = {"mean_ms": statistics.mean(values), "p95_ms": sorted(values)[int((len(values)-1)*.95)], "max_ms": max(values)}
    measure("first_open", lambda i: backend.openWorkspace(keys[0]), 1)
    measure("workspace_switch", lambda i: backend.openWorkspace(keys[i % 2]), 12)
    measure("page", lambda i: backend.pageBy(1 if i < 15 else -1), 30)
    measure("field", lambda i: backend.setField(garments[0].code, "size", "M" if i % 2 else "L"), 20)
    measure("state_read", lambda i: backend.state, 30)
    report = {"count": len(garments), "photo_source_exists": all(p.is_file() for p in paths), "samples": samples}
    (args.output / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    backend.shutdown()


if __name__ == "__main__":
    main()
