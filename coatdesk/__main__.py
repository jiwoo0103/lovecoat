from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from .window import MainWindow
from .desktop import copy_to_desktop


def main():
    parser = argparse.ArgumentParser(description="외투 작업실")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--smoke-test", type=Path, help="스크린샷 저장 후 자동 종료 (검증용)")
    parser.add_argument("--desktop-copy-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    if not QFontDatabase.families():
        # Qt's offscreen backend has no system font discovery on Windows.
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/malgun.ttf")
    app.setApplicationName("CoatDesk")
    app.setOrganizationName("CoatDesk")
    data_dir = args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    window = MainWindow(data_dir)
    startup_errors = []
    desktop_result = {}
    if args.smoke_test:
        window.error = startup_errors.append
    if args.task:
        window.load(args.task)
    elif not args.smoke_test:
        window.restore_last()
    window.show()
    if getattr(sys, "frozen", False) and (not args.smoke_test or args.desktop_copy_dir):
        def prepare_desktop():
            destination = args.desktop_copy_dir or window.task_dir

            def ready(result):
                desktop_result.update(result)
                window.statusBar().showMessage(f"바탕화면 실행 파일 준비 완료 · {destination}")
            window.run_job(
                "바탕화면에 실행 파일을 준비합니다…",
                lambda progress: copy_to_desktop(Path(sys.executable), Path(sys._MEIPASS), destination, progress),
                ready,
            )
        QTimer.singleShot(0, prepare_desktop)
    if args.smoke_test:
        def capture():
            if window.busy:
                QTimer.singleShot(250, capture)
                return
            report = {"screenshot": window.grab().save(str(args.smoke_test))}
            try:
                import win32crypt
                from googleapiclient.discovery_cache import get_static_doc
                from .recognition import find_tesseract, recognize

                probe = win32crypt.CryptProtectData(b"CoatDesk runtime check", "test", None, None, None, 0)
                report["credential_encryption"] = win32crypt.CryptUnprotectData(probe, None, None, None, 0)[1] == b"CoatDesk runtime check"
                report["sheets_api_available"] = bool(get_static_doc("sheets", "v4"))
                if window.task and window.task.garments:
                    proposal = recognize(window.task.garments[0], window.task.default_roi,
                                         window.task.templates, find_tesseract())
                    report["ocr_values"] = proposal.get("values", {})
                report["ok"] = all(report[k] for k in ("screenshot", "credential_encryption", "sheets_api_available"))
                if args.desktop_copy_dir:
                    report["desktop_copy"] = desktop_result
                    report["ok"] = report["ok"] and bool(desktop_result)
                if startup_errors:
                    report.update(ok=False, startup_errors=startup_errors)
            except Exception as error:
                report.update(ok=False, error=type(error).__name__)
            args.smoke_test.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
            app.exit(0 if report["ok"] else 1)
        QTimer.singleShot(2500, capture)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
