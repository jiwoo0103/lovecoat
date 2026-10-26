from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from .backend import Backend
from . import __version__
from .images import Photos


def main():
    parser = argparse.ArgumentParser(description="외투 작업실 2")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--source", type=Path, help="개발·검증용 사진 상위 폴더")
    parser.add_argument("--range-start", type=int, default=1)
    parser.add_argument("--range-end", type=int, default=13)
    parser.add_argument("--smoke-test", type=Path)
    parser.add_argument("--smoke-ocr", action="store_true", help="검증용: 불러온 사진의 OCR도 실행")
    parser.add_argument("--software-rendering", action="store_true", help="하드웨어 가속 없이 실행")
    args = parser.parse_args()
    if args.software_rendering:
        os.environ["QT_QUICK_BACKEND"] = "software"
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = kernel.CreateMutexW(None, False, "Local\\CoatDeskV2Running")
        if mutex:
            atexit.register(kernel.CloseHandle, mutex)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("CoatDesk v2")
    app.setOrganizationName("CoatDesk")
    app.setApplicationVersion(__version__)
    if not QFontDatabase.families():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/malgun.ttf")
    QQuickStyle.setStyle("Basic")
    directory = args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "CoatDesk/v2"
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "app.lock"))
    if not lock.tryLock(100):
        print("같은 작업 저장소를 사용하는 외투 작업실 2가 실행 중입니다.", file=sys.stderr)
        return 2
    backend = Backend(directory)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.addImageProvider("photos", Photos())
    messages = []
    engine.warnings.connect(lambda warnings: messages.extend(w.toString() for w in warnings))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/Main.qml")))
    if not engine.rootObjects():
        print("\n".join(messages), file=sys.stderr)
        lock.unlock()
        return 1
    if args.source:
        backend.createWorkspace("사진 검증", "", str(args.source), str(args.range_start), str(args.range_end), "")
    if args.smoke_test:
        ocr_started = False
        def capture(engine=engine):
            nonlocal ocr_started
            if backend.jobs:
                QTimer.singleShot(200, capture)
                return
            if args.smoke_ocr and not ocr_started and backend.workspace:
                ocr_started = True
                backend.startAnalysis(False)
                QTimer.singleShot(200, capture)
                return
            args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
            success = engine.rootObjects()[0].grabWindow().save(str(args.smoke_test))
            report = {"ok": success and not messages and not backend.error, "warnings": messages,
                      "error": backend.error, "counts": backend.state["counts"], "version": __version__}
            if args.smoke_ocr:
                from .recognition import RecognitionEngine
                report["ocr_runtime"] = bool(RecognitionEngine().executable)
                report["ocr"] = [{"code": g.code, "state": g.analysis_state, "values": g.suggestion.get("values", {})}
                                 for g in backend.workspace.active] if backend.workspace else []
                report["ok"] &= report["ocr_runtime"] and all(g["state"] == "done" for g in report["ocr"]) and bool(report["ocr"])
            args.smoke_test.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            app.exit(0 if report["ok"] else 1)
        QTimer.singleShot(3500, capture)
    app.aboutToQuit.connect(backend.shutdown)
    result = app.exec()
    # Destroy QML before the context property and image provider owners.
    del engine
    lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())
