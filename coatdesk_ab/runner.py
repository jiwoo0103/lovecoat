from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
import torchvision

from .data import SEED, SIDE, read, save, sha
from .locator import VERSION
from .training import BATCH, MAX_EPOCHS, fit


def train_all(work):
    protocol = work / "train-protocol.json"
    code_hashes = {str(p): sha(p) for p in sorted(Path("coatdesk_ab").glob("*.py"))}
    if not protocol.exists():
        save(protocol, {"four_independent_models": ["panel-shape", "panel-size", "whole-shape", "whole-size"],
             "side": SIDE, "seed": SEED, "batch": BATCH, "maximum_inner_epochs": MAX_EPOCHS,
             "selection": "Highest inner-dev target accuracy, then lowest inner-dev cross entropy; final fit on all 645 for selected epoch count",
             "fit_inner": 516, "dev_inner": 129, "fit_final": 645, "held_out": 445,
             "held_out_not_used_for_model_selection": True, "labels_manifest_sha256": sha(work / "manifest.json"),
             "prepared_manifest_sha256": sha(work / "prepared.json"), "locator_version": VERSION,
             "classes": {k: read(work / "manifest.json")[k] for k in ("shapes", "sizes")},
             "torch": torch.__version__, "torchvision": torchvision.__version__, "gpu": torch.cuda.get_device_name(0),
             "base_weights": str(work.parent / "ab-assets" / "resnet18-f37072fd.pth"),
             "base_sha256": sha(work.parent / "ab-assets" / "resnet18-f37072fd.pth"),
             "code_sha256": code_hashes, "retired_models_used": False,
             "whole_mode_preprocessing": "Resize preserving aspect ratio + white letterbox. No automatic crop, center crop, or random crop.",
             "augmentation": "Brightness plus bounded whole-frame shrink/pad/translation, no pixels removed; no text flipping",
             "limitations": ["17 observed size labels only, not general OCR", "Child male is a provisional user rule, not learned sex", "Same original photo collection; labels are not independently human verified"]})
    else:
        for name, fingerprint in read(protocol)["code_sha256"].items():
            if sha(name) != fingerprint:
                raise ValueError(f"Training code changed after protocol freeze: {name}")
    selections = {}
    for mode in ("panel", "whole"):
        for task in ("shape", "size"):
            result = fit(work, mode, task, "inner")
            selections[f"{mode}-{task}"] = result["best_epoch"]
    if not (work / "selections.json").exists():
        save(work / "selections.json", selections)
    final = {}
    for mode in ("panel", "whole"):
        for task in ("shape", "size"):
            key = f"{mode}-{task}"
            final[key] = fit(work, mode, task, "final", selections[key])
    if not (work / "all-training-complete.json").exists():
        save(work / "all-training-complete.json", {key: {k: r[k] for k in ("fit_count", "epochs", "seconds", "initial_tensor_sha256")} for key, r in final.items()})
    print(json.dumps({"four_final_models_complete": True, "epochs": selections}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=Path(".local/ab-work-20261024-r2"))
    train_all(parser.parse_args().work)


if __name__ == "__main__":
    main()
