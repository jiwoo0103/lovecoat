"""One-shot local continuation: wait for the four fits, compare, export candidates."""

from __future__ import annotations

import os
import time
import traceback

from scripts.retrain_four_models import ROOT, WORK, save, sha

TASKS = ("size", "shape", "detector", "category")
STATUS = WORK / "pipeline-status.json"


def status(stage, **values):
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), "stage": stage, **values}
    save(STATUS, record)
    print(record, flush=True)


def main():
    import msvcrt

    lock = (WORK / "evaluation.lock").open("a+b")
    lock.seek(0)
    lock.write(b"1")
    lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        while True:
            missing = [task for task in TASKS if not (WORK / "training" / task / "complete.json").exists()]
            if not missing:
                break
            status(
                "waiting_for_four_training_completions",
                missing=missing,
                note="This job never restarts training or infers termination from missing files.",
            )
            time.sleep(30)

        from scripts.retrain_four_evaluation import OUTPUT, candidate_hashes, compare, export

        expected = candidate_hashes()
        evaluator = ROOT / "scripts/retrain_four_evaluation.py"
        evaluator_hash = sha(evaluator)
        frozen_hash = sha(WORK / "frozen-dataset.json")
        folder = WORK / "evaluation/source-snapshot"
        folder.mkdir(parents=True, exist_ok=True)
        code = evaluator.read_bytes()
        destination = folder / (evaluator_hash + ".py")
        if not destination.exists():
            destination.write_bytes(code)
        status(
            "comparison_started",
            candidates=expected,
            evaluator_sha256=evaluator_hash,
            frozen_dataset_sha256=frozen_hash,
        )
        compare()
        if sha(evaluator) != evaluator_hash or sha(WORK / "frozen-dataset.json") != frozen_hash:
            raise ValueError("Evaluation code or frozen dataset changed while comparison was running")
        status("comparison_finished_exporting", candidates=expected)
        export()
        if sha(evaluator) != evaluator_hash or candidate_hashes() != expected:
            raise ValueError("Evaluation implementation or selected candidates changed during export")
        status(
            "comparison_and_export_complete",
            output=str(OUTPUT),
            report_sha256=sha(OUTPUT / "REPORT.md"),
            comparison_sha256=sha(OUTPUT / "comparison.json"),
            note="App model adoption and installer builds are excluded; agent review remains required.",
        )
    except BaseException as error:
        status("failed", error=type(error).__name__, details=str(error))
        traceback.print_exc()
        raise
    finally:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


if __name__ == "__main__":
    main()
