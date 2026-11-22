"""Stage only the selected size model over an existing release's frozen assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import torch

from coatdesk_ab.network import PhotoClassifier
from coatdesk_v2 import __version__
from coatdesk_v2.ai_runtime import SIZE_CLASSES_20


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage(baseline, selection, output):
    decision = read(selection)
    checkpoint = Path(decision["selected_checkpoint"])
    if sha(checkpoint) != decision["selected_sha256"] or decision["classes"] != SIZE_CLASSES_20:
        raise ValueError("Selected size checkpoint or class order changed")
    metadata = read(checkpoint.parent / "candidate-manifest.json")
    if metadata["sha256"] != decision["selected_sha256"] or metadata["classes"] != SIZE_CLASSES_20:
        raise ValueError("Candidate metadata does not match the selected model")
    model = PhotoClassifier(len(SIZE_CLASSES_20))
    model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True), strict=True)
    manifest = read(baseline / "manifest.json")
    category = read(baseline / "category/manifest.json")
    for folder, contract in ((baseline, manifest), (baseline / "category", category)):
        for name, expected in contract["files"].items():
            if sha(folder / name) != expected:
                raise ValueError("Baseline asset hash mismatch: " + name)
    # Refuse overwrite: old releases and selected checkpoints are never mutated.
    shutil.copytree(baseline, output)
    shutil.copy2(checkpoint, output / "panel-size.pt")
    manifest["version"] = __version__
    manifest["size_classes"] = SIZE_CLASSES_20
    manifest["files"]["panel-size.pt"] = sha(output / "panel-size.pt")
    manifest.setdefault("candidate_source_hashes", {})["size.pt"] = decision["selected_sha256"]
    manifest["size_model_source"] = "20261122-size-retrain/size20.pt"
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    preserved = {}
    for source in baseline.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(baseline)
        if relative.as_posix() in ("panel-size.pt", "manifest.json"):
            continue
        expected = sha(source)
        if sha(output / relative) != expected:
            raise AssertionError("Non-size asset changed: " + str(relative))
        preserved[relative.as_posix()] = expected
    report = {"version": __version__, "size_checkpoint_sha256": decision["selected_sha256"],
              "baseline": str(baseline.resolve()), "runtime": str(output.resolve()),
              "classes": SIZE_CLASSES_20, "unchanged_assets": preserved,
              "changed_runtime_files": ["panel-size.pt", "manifest.json"]}
    (output.parent / "size20-runtime-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(args.baseline, args.selection, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
