"""Package visually annotated category drafts into a private, offline review gallery."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

CATEGORIES = ("자켓", "코트", "조끼", "기타")
SLEEVES = ("yes", "no", "unclear")
LENGTHS = ("short", "normal", "borderline", "long", "very_long", "unclear")
TEMPLATE = Path(__file__).with_name("category_review_template.html")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def validate_annotations(manifest, annotations):
    sources = {row["code"]: row for row in manifest["rows"]}
    entries = annotations["entries"]
    seen = set()
    if not entries or not annotations.get("criterion"):
        raise ValueError("Annotations and the agreed criterion are required")
    if not isinstance(annotations["criterion"], dict):
        raise ValueError("Criterion must be an object")
    for field in ("title", "review_context", "purpose"):
        if field in annotations and not isinstance(annotations[field], str):
            raise ValueError(f"Review {field} must be text")
    if not isinstance(annotations.get("attribute_review_enabled", False), bool):
        raise ValueError("Attribute review flag must be boolean")
    for entry in entries:
        code = entry["code"]
        if not re.fullmatch(r"[0-9]{4,}", code) or code not in sources or code in seen:
            raise ValueError(f"Unknown, unsafe or duplicate garment code: {code}")
        seen.add(code)
        for field in ("draft_category", "candidate_category"):
            if entry.get(field) not in (*CATEGORIES, None):
                raise ValueError(f"Invalid category for {code}")
        if any(not isinstance(entry.get(field, False), bool) for field in ("user_confirmed", "needs_review")):
            raise ValueError("Confirmation and review flags must be booleans")
        if entry.get("user_confirmed") and entry.get("draft_category") is None:
            raise ValueError("An uncertain draft cannot be confirmed as a training label")
        if entry.get("draft_category") is None and not entry.get("needs_review"):
            raise ValueError("An uncertain draft must be marked for review")
        if not entry.get("reason"):
            raise ValueError(f"A visual annotation reason is required: {code}")
        if "dataset_split" in entry and entry["dataset_split"] not in ("train", "dev", "test"):
            raise ValueError("Invalid dataset split")
        if annotations.get("attribute_review_enabled"):
            if entry.get("sleeves") not in SLEEVES or entry.get("length") not in LENGTHS:
                raise ValueError("Explicit sleeve and length attributes are required")
            allowed = entry.get("acceptable_categories")
            if (not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed)
                    or any(c not in CATEGORIES for c in allowed) or entry.get("draft_category") not in allowed
                    or len(allowed) > 1 and (set(allowed) != {"자켓", "코트"} or entry["length"] != "borderline")):
                raise ValueError("Invalid acceptable categories")
        row = sources[code]
        if row.get("shape") not in ("adult_male", "adult_female", "adult_unknown", "child"):
            raise ValueError(f"Unrecognized age metadata for {code}")
        path = Path(row["source"])
        if not path.is_file() or sha(path) != row["sha256"]:
            raise ValueError(f"Original photograph is missing or changed: {code}")
    return sources


def make_record(entry, source):
    confirmed = entry.get("user_confirmed") is True
    category = entry.get("draft_category")
    record = {
        "code": entry["code"], "folder_id": "0_" + entry["code"],
        "photo": "photos/" + entry["code"] + ".jpg",
        "age_group": "아동" if source["shape"] == "child" else "성인",
        "raw_size": source.get("size", ""),
        "source_sha256": source["sha256"],
        "source_group": source.get("split_group", entry["code"]),
        "previous_task_role": source.get("role", ""),
        "draft_category": category,
        "display_category": category or "기타",
        "candidate_category": entry.get("candidate_category") or category,
        "sleeves": entry.get("sleeves", "unclear"),
        "length": entry.get("length", "unclear"),
        "reason": entry["reason"], "needs_review": bool(entry.get("needs_review")),
        "hold_reason": entry.get("hold_reason", ""),
        "model_score": None,
        "annotation_method": "user_confirmed" if confirmed else "codex_visual_draft",
        "review_status": "confirmed" if confirmed else "unreviewed",
        "training_label": category if confirmed else None,
    }
    for key in ("dataset_split", "acceptable_categories", "selection_cohort"):
        if key in entry:
            record[key] = entry[key]
    return record


def write_gallery(manifest_path: Path, annotation_path: Path, output: Path):
    if output.exists():
        raise FileExistsError("Choose a new output directory; existing reviews are never overwritten")
    manifest_hash, annotation_hash = sha(manifest_path), sha(annotation_path)
    manifest, annotations = read(manifest_path), read(annotation_path)
    sources = validate_annotations(manifest, annotations)
    records = sorted((make_record(entry, sources[entry["code"]]) for entry in annotations["entries"]),
                     key=lambda row: int(row["code"]))
    dataset_id = hashlib.sha256((manifest_hash + annotation_hash).encode()).hexdigest()[:20]
    bundle = {
        "schema_version": "category-review-v1", "dataset_id": dataset_id,
        "title": annotations.get("title", "옷 카테고리 라벨 검수"),
        "review_context": annotations.get("review_context", ""),
        "purpose": annotations.get("purpose", "category_label_review"),
        "attribute_review_enabled": annotations.get("attribute_review_enabled", False),
        "criterion": annotations["criterion"], "categories": CATEGORIES,
        "annotation_method": "codex_visual_draft", "trained_category_model_used": False,
        "model_scores_available": False, "training_performed": False,
        "records": records,
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__CATEGORY_REVIEW_DATA__") != 1:
        raise ValueError("Invalid review template")
    safe_json = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c")
    page = template.replace("__CATEGORY_REVIEW_DATA__", safe_json)
    output.mkdir(parents=True, exist_ok=False)
    (output / "photos").mkdir()
    for record in records:
        source = Path(sources[record["code"]]["source"])
        with Image.open(source) as opened:
            opened.draft("RGB", (1080, 1920))
            image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((1080, 1920), Image.Resampling.LANCZOS)
        image.save(output / record["photo"], quality=92)
    with (output / "검수보기.html").open("x", encoding="utf-8") as stream:
        stream.write(page)
    dump(output / "labels-draft.json", bundle)
    with (output / "labels-draft.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        fields = ("folder_id", "age_group", "raw_size", "display_category", "candidate_category",
                  "draft_category", "needs_review", "review_status", "training_label", "reason")
        if bundle["attribute_review_enabled"]:
            fields += ("dataset_split", "sleeves", "length", "acceptable_categories")
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            # Keep arbitrary annotation text from becoming a spreadsheet formula.
            writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                             else value for key, value in row.items()})
    for record in records:
        if sha(Path(sources[record["code"]]["source"])) != record["source_sha256"]:
            raise ValueError("A source photograph changed during packaging")
    if sha(manifest_path) != manifest_hash or sha(annotation_path) != annotation_hash:
        raise ValueError("Existing metadata changed during packaging")
    summary = {
        "count": len(records), "by_category": dict(Counter(r["display_category"] for r in records)),
        "by_age": dict(Counter(r["age_group"] for r in records)),
        "flagged_for_review": sum(r["needs_review"] for r in records),
        "user_confirmed": sum(r["review_status"] == "confirmed" for r in records),
        "original_hashes_checked": len(records), "source_manifest_unchanged": True,
        "annotations_sha256": annotation_hash, "source_manifest_sha256": manifest_hash,
        "dataset_id": dataset_id, "category_model_used": False, "training_performed": False,
        "artifacts": {name: sha(output / name) for name in ("검수보기.html", "labels-draft.json", "labels-draft.csv")},
    }
    dump(output / "verification.json", summary)
    with (output / "사용안내.md").open("x", encoding="utf-8") as stream:
        confirmed_count = sum(record["review_status"] == "confirmed" for record in records)
        stream.write(
            f"# {bundle['title']}\n\n총 {len(records)}벌. `검수보기.html`을 브라우저에서 여세요.\n\n"
            f"{bundle['review_context']}\n\n"
            "분류 버튼과 성인/아동 필터로 사진을 모아 볼 수 있습니다. 사진 클릭 시 확대합니다.\n"
            "분류 변경 후 ‘분류 확정’을 눌러야 정답으로 구분됩니다. 미확정 초안은 학습 정답이 아닙니다.\n"
            "‘기타·판독 보류’는 실제 기타 정답과 구분됩니다. 원래 후보와 보류 이유를 확인하세요.\n\n"
            "수정은 현재 브라우저에 저장됩니다. ‘검수 결과 저장’으로 JSON을 내보내 보관하세요.\n"
            "같은 자료의 검수 결과만 불러올 수 있습니다. 원본·기존 라벨·기존 모델은 수정하지 않습니다.\n"
            "이 자료는 Codex의 육안 판독 초안입니다. 학습한 카테고리 모델의 추론 점수는 없습니다.\n"
            f"최초 생성 시 사용자 확정 {confirmed_count}벌·미확정 {len(records) - confirmed_count}벌입니다.\n"
        )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_gallery(args.manifest, args.annotations, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
