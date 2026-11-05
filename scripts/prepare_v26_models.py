"""Stage the three approved checkpoints with a previous release's category bundle."""
import argparse
import json
import shutil
from pathlib import Path

import torch

from coatdesk_ab.data import SHAPES, SIZES, sha
from coatdesk_v2 import __version__


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--installed-assets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    candidate = read_json(args.candidate / "manifest.json")
    installed = read_json(args.installed_assets / "manifest.json")
    category = read_json(args.installed_assets / "category/manifest.json")
    sizes = ["70", *SIZES]
    if candidate["sizes"] != sizes or candidate["shapes"] != SHAPES:
        raise ValueError("Candidate class order does not match the approved model contract")

    models = {}
    contracts = (
        ("detector", "detector.pt", 448, "ssdlite_mobilenet_v3_large_448"),
        ("shape", "panel-shape.pt", 512, "resnet18_stride16_attention"),
        ("size", "panel-size.pt", 512, "resnet18_stride16_attention"),
    )
    for task, target, side, architecture in contracts:
        source = args.candidate / f"{task}.pt"
        if sha(source) != candidate["files"][source.name]:
            raise ValueError(f"Candidate hash mismatch: {source.name}")
        checkpoint = torch.load(source, map_location="cpu", weights_only=True)
        actual = (checkpoint["task"], checkpoint["input_side"], checkpoint["architecture"])
        if actual != (task, side, architecture) or checkpoint["sizes"] != sizes or checkpoint["shapes"] != SHAPES:
            raise ValueError(f"Unexpected checkpoint metadata: {source.name}")
        models[target] = checkpoint["state_dict"]

    for name, expected in category["files"].items():
        if sha(args.installed_assets / "category" / name) != expected:
            raise ValueError(f"Previous category asset hash mismatch: {name}")
    mapping = args.installed_assets / "size_mapping.json"
    if sha(mapping) != installed["files"][mapping.name]:
        raise ValueError("Previous size mapping hash mismatch")

    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.installed_assets / "category", output / "category")
    shutil.copy2(mapping, output / mapping.name)
    shutil.copy2(args.installed_assets / "TORCHVISION_LICENSE", output / "TORCHVISION_LICENSE")
    for name, weights in models.items():
        torch.save(weights, output / name)
    manifest = {
        "version": __version__,
        "size_classes": sizes,
        "files": {name: sha(output / name) for name in (*models, mapping.name)},
        "candidate_source_hashes": {f"{task}.pt": candidate["files"][f"{task}.pt"] for task, *_ in contracts},
        "category_model_sha256": category["files"]["model.pt"],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
