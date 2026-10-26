from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

SHAPES = ["adult_male", "adult_female", "child"]
SIZES = [str(n) for n in range(80, 171, 10)] + ["XS", "S", "M", "L", "XL", "2XL", "3XL"]
SEED = 20261024
SIDE = 512


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_photo(path, limit=1080):
    with Image.open(path) as source:
        source.draft("RGB", (limit, round(limit * 16 / 9)))
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((limit, round(limit * 16 / 9)), Image.Resampling.LANCZOS)
    return image


def letterbox(image, side=SIDE):
    """Keep every input pixel in view: no center crop or random resized crop."""
    image = image.copy().convert("RGB")
    factor = min(side / image.width, side / image.height)
    image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))), Image.Resampling.LANCZOS)
    output = Image.new("RGB", (side, side), (255, 255, 255))
    offset = ((side - image.width) // 2, (side - image.height) // 2)
    output.paste(image, offset)
    return output, {"scale": factor, "offset": offset, "resized_size": image.size, "no_crop": True}


def create_manifest(work):
    original_root = Path(read(".local/gender-label-work/update/manifest.json")["root"])
    original = [json.loads(line) for line in (original_root / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    extra = [json.loads(line) for line in Path("model-results/20261024-extra-validation/labels.jsonl").read_text(encoding="utf-8").splitlines()]
    # Reuse only the documented split and labels, never the retired weights or predictions.
    roles = {r["code"]: r for r in read("model-results/20261024-first/dataset-split.json")}
    inventory = {r["code"]: r for r in read(".local/size-dataset-work/inventory.json")}
    rows = []
    for row in original + extra:
        code = row["code"]
        role = roles[code]["role"] if code in roles else "additional_test"
        shape = "child" if row["age"] == "child" else "adult_" + row["gender"]
        source = Path(inventory[code]["source"])
        if sha(source) != row["front_sha256"]:
            raise ValueError(f"Source photo changed: {code}")
        rows.append({"index": len(rows), "code": code, "source": str(source), "sha256": row["front_sha256"],
                     "shape": shape, "size": row["raw_text"], "shape_index": SHAPES.index(shape),
                     "size_index": SIZES.index(row["raw_text"]), "role": role,
                     "split_group": row.get("split_group", code), "child_gender_provisional": row["age"] == "child"})
    if len(rows) != 1090 or len({r["code"] for r in rows}) != 1090:
        raise ValueError("Expected exactly 1090 separately labeled garment codes")
    groups = {}
    for row in rows:
        existing = groups.setdefault(row["split_group"], row["role"])
        if existing != row["role"]:
            raise ValueError("Related-photo group crosses dataset roles")
    summary = {"count": len(rows), "roles": dict(Counter(r["role"] for r in rows)),
               "shapes": SHAPES, "sizes": SIZES, "rows": rows,
               "no_retired_model_weights_or_predictions_used": True,
               "labels_sha256": {str(original_root / "labels.jsonl"): sha(original_root / "labels.jsonl"),
                                  "model-results/20261024-extra-validation/labels.jsonl": sha("model-results/20261024-extra-validation/labels.jsonl")}}
    save(work / "manifest.json", summary)
    return summary
