"""Compare painted UI and photo readiness against a saved source snapshot."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from PySide6.QtCore import QUrl, QObject, QMetaObject, Qt, QEventLoop, QTimer
    from PySide6.QtGui import QGuiApplication, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from coatdesk_v2.backend import Backend
    from coatdesk_v2.domain import Garment
    from coatdesk_v2.images import Photos
    args.output.mkdir(parents=True, exist_ok=False)
    rows = json.loads(args.protocol.read_text(encoding="utf-8"))["records"][:600]
    paths = [Path(r["source"]) for r in rows]
    source = Path(os.path.commonpath([str(p.parent) for p in paths]))
    app = QGuiApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/malgun.ttf")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    QQuickStyle.setStyle("Basic")
    backend = Backend(args.output / "data")
    garments = [Garment(r["code"], {str(a): str(p.with_name(f"img_0_0_{a}.jpg").relative_to(source)) for a in range(3)
                                  if p.with_name(f"img_0_0_{a}.jpg").exists()}, fields={
        "category": "자켓", "gender": "남성", "age": "성인", "size": "M"}) for r, p in zip(rows, paths)]
    keys = [backend.repository.create(f"600벌 검증 {i+1}", "2026-10-28", source, 0, 99999, "", garments) for i in range(2)]
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.addImageProvider("photos", Photos())
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.load(QUrl.fromLocalFile(str(args.source_root.resolve() / "coatdesk_v2/qml/Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError(warnings)
    window = engine.rootObjects()[0]
    QTest.qWait(120)
    def descendants(item):
        for child in item.childItems():
            yield child
            yield from descendants(child)
    def photos_loading():
        return any(i.isVisible() and i.metaObject().className().startswith("QQuickImage") and not i.property("source").isEmpty() and i.property("progress") < 1
                   for i in descendants(window.contentItem()))
    def wait_ready():
        deadline = time.monotonic()+10
        while photos_loading() and time.monotonic() < deadline:
            QTest.qWait(2)
        return not photos_loading()
    def open_workspace(key):
        if hasattr(backend, "requestWorkspace"):
            backend.requestWorkspace(key)
        else:
            backend.openWorkspace(key)
        if backend.jobs:
            loop = QEventLoop()
            def finished():
                if not backend.jobs:
                    loop.quit()
            backend.changed.connect(finished)
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            backend.changed.disconnect(finished)
            if backend.jobs:
                raise TimeoutError("workspace load")
    def paint():
        # frameSwapped measures the displayed response; grabWindow adds a
        # synchronous pixel readback that is not part of normal interaction.
        loop = QEventLoop()
        window.frameSwapped.connect(loop.quit)
        QTimer.singleShot(5000, loop.quit)
        window.update()
        loop.exec()
        window.frameSwapped.disconnect(loop.quit)
    started = time.perf_counter()
    open_workspace(keys[0])
    paint()
    first_frame = (time.perf_counter()-started)*1000
    ready = wait_ready()
    first_photos = (time.perf_counter()-started)*1000
    samples = {}
    def measure(label, action, count):
        values = []
        for index in range(count):
            started = time.perf_counter()
            action(index)
            paint()
            values.append((time.perf_counter()-started)*1000)
            wait_ready()
        samples[label] = {"mean_ms": statistics.mean(values), "p95_ms": sorted(values)[int((len(values)-1)*.95)], "max_ms": max(values)}
    measure("workspace", lambda i: open_workspace(keys[i%2]), 10)
    measure("page", lambda i: backend.pageBy(1), 20)
    current = backend.model.rows[0]["code"]
    measure("field", lambda i: backend.setField(current, "size", "M" if i%2 else "L"), 12)
    dialog = window.findChild(QObject, "viewDialog")
    measure("popup", lambda i: QMetaObject.invokeMethod(dialog, "open" if i%2 == 0 else "close"), 10)
    QMetaObject.invokeMethod(dialog, "close")
    backend.pageBy(-1000)
    QTest.qWait(40)
    if hasattr(backend, "setLayout"):
        for width, height in ((1380, 920), (1000, 720)):
            window.resize(width, height)
            for fast in (False, True):
                backend.setFastInput(fast)
                for count in (2, 4):
                    backend.setLayout(count, count)
                    QTest.qWait(70)
                    wait_ready()
                    window.grabWindow().save(str(args.output / f"review-{width}-{count}x{count}-fast{int(fast)}.png"))
    report = {"count": len(rows), "first_frame_ms": first_frame, "first_photos_ready_ms": first_photos,
              "photos_ready": ready, "warnings": warnings, "samples": samples}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    window.close()
    backend.shutdown()
    engine.deleteLater()
    QTest.qWait(50)


if __name__ == "__main__":
    main()
