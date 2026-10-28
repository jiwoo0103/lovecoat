"""Compare the actual app inference path against frozen CPU predictions."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coatdesk_v2.category import decode
from coatdesk_v2.domain import Garment, Workspace
from coatdesk_v2.recognition import RecognitionEngine


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    protocol = json.loads((args.experiment / "protocol.json").read_text(encoding="utf-8"))
    references = json.loads((args.experiment / "cpu-test-predictions.json").read_text(encoding="utf-8"))
    sources = {r["code"]: r for r in protocol["records"]}
    engine = RecognitionEngine()
    outputs = []
    started = time.perf_counter()
    for index, reference in enumerate(references):
        source = sources[reference["code"]]
        path = Path(source["source"])
        before = sha(path)
        workspace = Workspace("verification", "verification", "2026-10-28", str(path.parent), 0, 99999, [], [])
        garment = Garment(source["code"], {"0": path.name})
        result = engine.analyze(workspace, garment)
        category = result["category"]
        expected = decode(reference["probabilities"])
        error = max((abs(a-b) for a, b in zip(category.get("probabilities", []), reference["probabilities"])), default=1.)
        outputs.append({"code": garment.code, "category": category, "max_probability_error": error,
                        "same_decision": all(category.get(k) == expected[k] for k in ("candidate", "display_category", "needs_review")),
                        "source_unchanged": before == sha(path), "truth": reference["truth"],
                        "strict_match": category.get("raw_category") == reference["truth"],
                        "boundary_match": category.get("raw_category") in reference["acceptable_categories"]})
        if (index+1) % 10 == 0:
            print(f"{index+1}/{len(references)}", flush=True)
    automatic = [r for r in outputs if not r["category"]["needs_review"]]
    report = {"count": len(outputs), "seconds": time.perf_counter()-started,
              "same_decisions": sum(r["same_decision"] for r in outputs),
              "max_probability_error": max(r["max_probability_error"] for r in outputs),
              "source_unchanged": all(r["source_unchanged"] for r in outputs),
              "automatic": len(automatic), "review": len(outputs)-len(automatic),
              "strict_match": sum(r["strict_match"] for r in outputs),
              "boundary_match": sum(r["boundary_match"] for r in outputs),
              "automatic_strict_match": sum(r["strict_match"] for r in automatic),
              "automatic_boundary_match": sum(r["boundary_match"] for r in automatic)}
    report["ok"] = report["same_decisions"] == len(outputs) and report["source_unchanged"] and report["max_probability_error"] < .002
    (args.output / "predictions.json").write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
