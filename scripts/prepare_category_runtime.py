"""Copy frozen, local inference assets without changing experiment outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coatdesk_v2.category import POLICY_VERSION, DEFAULT_CATEGORY_THRESHOLDS  # noqa: E402

MODEL_SHA256 = "d48df504df994fa3414dba4e4efd979cd0080162883e7dd66b703e3d63b569fa"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = args.model_dir / "sleeve-length-efficientnet-b0.pt"
    if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("Unexpected category checkpoint")
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(model, args.output / "model.pt")
    refs = args.output / "references"
    refs.mkdir()
    metadata = json.loads((args.references / "references.json").read_text(encoding="utf-8"))
    for row in metadata["records"]:
        for suffix in (".png", "-mask.png"):
            name = row["code"] + suffix
            shutil.copy2(args.references / name, refs / name)
    shutil.copy2(args.references / "references.json", refs / "references.json")
    policy = {"version": POLICY_VERSION, "thresholds": DEFAULT_CATEGORY_THRESHOLDS,
              "input_side": 320, "normalization_mean": [.485, .456, .406], "normalization_std": [.229, .224, .225],
              "preprocessing": "whole-panel-template-v2-mask8px-at1080-letterbox320-v1",
              "training_policy_unchanged": True, "scores_calibrated": False}
    (args.output / "policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    manifest = {"version": "2.4.0", "files": {p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(args.output.rglob("*")) if p.is_file()}}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"model_sha256": MODEL_SHA256, "files": len(manifest["files"])}))


if __name__ == "__main__":
    main()
