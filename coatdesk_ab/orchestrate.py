"""Run each independent training stage in its own CUDA process."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .data import read, save, sha


def run(work):
    protocol = read(work / "train-protocol.json")
    for name, fingerprint in protocol["code_sha256"].items():
        if sha(name) != fingerprint:
            raise ValueError(f"Frozen model/data/training code changed: {name}")
    selections, final = {}, {}
    for stage in ("inner", "final"):
        for mode in ("panel", "whole"):
            for task in ("shape", "size"):
                key = f"{mode}-{task}"
                folder = work / "training" / key / stage
                complete = folder / "complete.json"
                if not complete.exists():
                    if folder.exists():
                        raise RuntimeError(f"Incomplete stage needs inspection before retry: {folder}")
                    epochs = selections[key] if stage == "final" else 24
                    source = f"from pathlib import Path; from coatdesk_ab.training import fit; fit(Path({str(work)!r}),{mode!r},{task!r},{stage!r},{epochs})"
                    logfile = work / f"{key}-{stage}-process.log"
                    with logfile.open("x", encoding="utf-8") as stream:
                        process = subprocess.run([sys.executable, "-B", "-c", source], stdout=stream, stderr=subprocess.STDOUT)
                    if process.returncode:
                        raise RuntimeError(f"Worker failed; inspect {logfile}")
                result = read(complete)
                if stage == "inner":
                    selections[key] = result["best_epoch"]
                else:
                    final[key] = {k: result[k] for k in ("fit_count", "epochs", "seconds", "initial_tensor_sha256")}
                print(json.dumps({"completed": key + "/" + stage, "epochs": result["epochs"],
                                  "best_epoch": result["best_epoch"]}), flush=True)
        if stage == "inner" and not (work / "selections.json").exists():
            save(work / "selections.json", selections)
    save(work / "all-training-complete.json", final)
    print(json.dumps({"four_final_models_complete": True}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    run(parser.parse_args().work)
