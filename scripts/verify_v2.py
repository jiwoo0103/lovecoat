"""Read-only source validation and CPU baseline. Writes only to a new output directory."""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coatdesk.photos import digest
from coatdesk_v2.recognition import RecognitionEngine
from coatdesk_v2.scanner import scan
from coatdesk_v2.storage import Repository


def peak_memory_mb():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
             "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return round(counters.PeakWorkingSetSize / 1024**2, 1)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=600)
    parser.add_argument("--errors", default="")
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    if output.is_relative_to(source) or output.exists():
        parser.error("검증 출력은 원본 밖의 새 폴더를 지정하세요.")
    output.mkdir(parents=True)
    report = {"source": str(source), "range": [args.start, args.end], "ocr": args.ocr}
    begin = time.perf_counter()
    garments = scan(source, args.start, args.end)
    report["scan_seconds"] = round(time.perf_counter()-begin, 3)
    paths = [source / p for g in garments for p in g.photos.values()]
    before = {str(p.relative_to(source)): digest(p) for p in paths}
    print(f"Scanned {len(garments)} garments, {len(paths)} photographs; source hashes captured.", flush=True)
    repository = Repository(output / "data")
    workspace_id = repository.create("CPU 검증", "", source, args.start, args.end, args.errors, garments)
    workspace = repository.load(workspace_id)
    report["counts"] = workspace.counts
    report["photos"] = len(paths)
    report["issues"] = [{"code": g.code, "issues": g.issues} for g in garments if g.issues]
    if args.ocr:
        engine = RecognitionEngine()
        start = time.perf_counter()
        results = []
        for index, garment in enumerate(workspace.active, 1):
            try:
                key = engine.key(workspace, garment)
                result = engine.analyze(workspace, garment)
                state = "done"
            except Exception as error:
                key, result, state = "", {"values": {}, "note": type(error).__name__}, "failed"
            def save(item):
                item.suggestion, item.analysis_key, item.analysis_state = result, key, state
            repository.edit(workspace_id, [garment.code], save)
            results.append({"code": garment.code, "values": result.get("values", {}), "state": state})
            if index % 25 == 0 or index == len(workspace.active):
                print(f"OCR {index}/{len(workspace.active)}: {time.perf_counter()-start:.1f}s", flush=True)
        report["ocr_seconds"] = round(time.perf_counter()-start, 3)
        report["results"] = results
        report["sizes"] = dict(Counter(r["values"].get("size", "unresolved") for r in results))
        restored = Repository(output / "data").load(workspace_id)
        report["persisted_results"] = sum(g.analysis_state == "done" for g in restored.active)
        report["cache_keys_match"] = all(g.analysis_key == engine.key(restored, g) for g in restored.active if g.analysis_state == "done")
    report["source_unchanged"] = all(digest(source / p) == original for p, original in before.items())
    report["peak_memory_mb"] = peak_memory_mb()
    report["total_seconds"] = round(time.perf_counter()-begin, 3)
    report["ok"] = report["source_unchanged"] and (not args.ocr or all(r["state"] == "done" for r in report["results"]))
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"results", "issues"}}, ensure_ascii=True), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
