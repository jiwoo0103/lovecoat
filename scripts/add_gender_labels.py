"""Add reviewed placard gender labels without resampling or changing source photos.

Stage a metadata-only update, validate it against a local dataset copy, then apply
the reviewed payload with hash guards and a complete metadata backup.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from scripts.prepare_size_dataset import SIZES, digest, write_csv, write_extras, write_json

GENDER_FIELDS = {
    "gender", "gender_ko", "gender_label_source", "gender_provisional",
    "gender_reference", "gender_policy_id", "gender_human_verified",
}
METADATA = {
    "labels.jsonl", "labels.csv", "학습용/labels.csv", "검증용/labels.csv", "summary.json",
    "성별기준.json", "성별분포.csv", "성별판독기록.json", "데이터안내.md", "검수보기.html",
    "verification_gender.json", "gender_review_page_checks.json",
    "성별기준사진/A_0035_panel.png", "성별기준사진/B_0001_panel.png",
}


def add_labels(records: list[dict], adult_labels: dict, policy: dict) -> list[dict]:
    adults = {r["code"] for r in records if r["age"] == "adult"}
    if len({r["code"] for r in records}) != len(records):
        raise ValueError("Duplicate garment code")
    if set(adult_labels) != adults:
        raise ValueError("Adult review must cover exactly the existing selected adult garments")
    if policy.get("adult_reference_mapping_confirmed_by_user") is not True:
        raise ValueError("Adult reference mapping has not been confirmed")
    if policy.get("child_rule") != {"gender": "male", "provisional": True, "confirmed_by_user": True}:
        raise ValueError("Expected the user's provisional male child rule")
    result = []
    for old in records:
        if GENDER_FIELDS & old.keys():
            raise ValueError("Existing gender labels must be reviewed before replacement")
        adult = old["age"] == "adult"
        if old["age"] not in {"adult", "child"}:
            raise ValueError("Unknown age")
        gender = adult_labels[old["code"]] if adult else "male"
        if gender not in {"male", "female"}:
            raise ValueError("Unknown gender")
        names = {"adult": {"male": "남성", "female": "여성"}, "child": {"male": "남아", "female": "여아"}}
        result.append({**old, "gender": gender, "gender_ko": names[old["age"]][gender],
                       "gender_label_source": "confirmed_placard_visual" if adult else "user_provisional_rule",
                       "gender_provisional": not adult,
                       "gender_reference": {"male": "A_0035", "female": "B_0001"}[gender] if adult else "child_rule",
                       "gender_policy_id": policy["policy_id"], "gender_human_verified": False})
    return result


def counts(records):
    return {f"{age}_{gender}": sum(r["age"] == age and r["gender"] == gender for r in records)
            for age in ("adult", "child") for gender in ("male", "female")}


def read_records(root):
    return [json.loads(line) for line in (root / "labels.jsonl").read_text(encoding="utf-8").splitlines()]


def allowed_paths(root):
    records = read_records(root)
    return (METADATA | {str(Path(r["front_path"]).parent / "label.json").replace("\\", "/") for r in records}
            | {f"검수이미지/{r['code']}.jpg" for r in records if 951 <= int(r["code"]) <= 981})


def stage(root: Path, review_path: Path, policy_path: Path, output: Path) -> dict:
    root = root.resolve(strict=True)
    if output.exists():
        raise FileExistsError(output)
    old_records = read_records(root)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if review.get("all_selected_adults_reviewed") is not True:
        raise ValueError("Adult visual review is incomplete")
    for reference in policy["adult_references"]:
        if digest(Path(reference["source"])) != reference["sha256"]:
            raise ValueError("Confirmed reference photo changed")
    for row in old_records:
        label = root / Path(row["front_path"]).parent / "label.json"
        if json.loads(label.read_text(encoding="utf-8")) != row:
            raise ValueError(f"Existing folder label differs: {row['code']}")
    records = add_labels(old_records, review["labels"], policy)
    payload = output / "payload"
    payload.mkdir(parents=True)
    for row in records:
        folder = payload / Path(row["front_path"]).parent
        folder.mkdir(parents=True)
        write_json(folder / "label.json", row)
    with (payload / "labels.jsonl").open("x", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_csv(payload / "labels.csv", records)
    for split, dirname in (("train", "학습용"), ("validation", "검증용")):
        write_csv(payload / dirname / "labels.csv", [r for r in records if r["split"] == split])
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    summary.update({"previous_dataset_version": summary["dataset_version"],
                    "dataset_version": "2026-10-24.v2-gender", "gender_counts": dict(Counter(r["gender"] for r in records)),
                    "age_gender_counts": counts(records),
                    "gender_split_counts": {s: counts([r for r in records if r["split"] == s])
                                            for s in ("train", "validation")},
                    "gender_policy_id": policy["policy_id"], "gender_provisional_count": sum(r["gender_provisional"] for r in records),
                    "gender_human_verified": False, "model_training_performed": False})
    write_json(payload / "summary.json", summary)
    write_json(payload / "성별기준.json", policy)
    for reference in policy["adult_references"]:
        target = payload / reference["panel_path"]
        if target.relative_to(payload).as_posix() not in METADATA:
            raise ValueError("Unexpected reference panel path")
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(reference["reviewed_panel_source"], target)
    write_json(payload / "성별판독기록.json", {**review, "age_gender_counts": counts(records),
               "adult_garments_reviewed": len(review["labels"]),
               "child_labels_assigned_by_user_rule": summary["gender_provisional_count"],
               "clipped_preview_originals_rechecked": [r["code"] for r in records if 951 <= int(r["code"]) <= 981],
               "independent_human_review_performed": False})
    distribution = []
    for age, age_ko in (("adult", "성인"), ("child", "아동")):
        for gender, gender_ko in (("male", "남성"), ("female", "여성")):
            for size in SIZES:
                chosen = [r for r in records if (r["age"], r["gender"], r["size_label"]) == (age, gender, size)]
                distribution.append({"age": age, "age_ko": age_ko, "gender": gender, "gender_ko": gender_ko,
                                     "size_label": size, "selected_garments": len(chosen),
                                     "train": sum(r["split"] == "train" for r in chosen),
                                     "validation": sum(r["split"] == "validation" for r in chosen),
                                     "provisional_garments": sum(r["gender_provisional"] for r in chosen)})
    write_csv(payload / "성별분포.csv", distribution)
    mapping = json.loads((root / "사이즈기준.json").read_text(encoding="utf-8"))
    write_extras(payload, records, mapping, summary)
    with (payload / "데이터안내.md").open("a", encoding="utf-8") as stream:
        group = summary["age_gender_counts"]
        stream.write(f"\n## 성별 추가 (2026-10-24)\n\n성인 남성 {group['adult_male']}벌, 성인 여성 {group['adult_female']}벌, "
                     f"아동 남아 {group['child_male']}벌(임시), 아동 여아 {group['child_female']}벌입니다.\n"
                     "기존 사이즈·연령·학습/검증 분할과 원본 사진은 유지했습니다. 성별별 수량을 맞추기 위한 재선별은 하지 않았습니다.\n"
                     "아동 성별은 옷이나 머리 모양으로 남녀 정답을 판독한 것이 아니므로 독립적인 아동 성별 검증 정답으로 취급하지 마세요.\n"
                     "성별 포함 무결성 검사는 `verification_gender_desktop.json`, 필터 실행 검사는 `gender_review_page_checks.json`입니다.\n"
                     "성별 추가 전 메타데이터·변경한 검수용 이미지는 `_라벨보존/20261024_성별추가전`에 보존합니다.\n")
    # These older viewing crops clip the upper hair. Only generated previews are
    # replaced; front_path and every original copied photograph stay untouched.
    for row in records:
        if 951 <= int(row["code"]) <= 981:
            with Image.open(root / row["front_path"]) as original:
                original.draft("RGB", (1080, 1920))
                photo = ImageOps.exif_transpose(original).convert("RGB")
                w, h = photo.size
                photo = photo.crop((int(w * .30), int(h * .23), int(w * .72), int(h * .48)))
                photo.thumbnail((360, 600))
                target = payload / "검수이미지" / (row["code"] + ".jpg")
                target.parent.mkdir(exist_ok=True)
                photo.save(target, quality=94)
    return seal(root, output)


def seal(root: Path, output: Path) -> dict:
    """Seal the staged payload again after adding verification reports."""
    root = root.resolve(strict=True)
    allowed = allowed_paths(root)
    changes = []
    for path in sorted((output / "payload").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(output / "payload").as_posix()
        if relative not in allowed:
            raise ValueError(f"Unexpected payload path: {relative}")
        old = root / relative
        changes.append({"path": relative, "before_sha256": digest(old) if old.exists() else None, "after_sha256": digest(path)})
    result = {"root": str(root), "backup": "_라벨보존/20261024_성별추가전", "changes": changes,
              "unchanged_guards": {name: digest(root / name) for name in ("files_sha256.csv", "사이즈기준.json", "분포.csv")},
              "original_photos_modified": False, "split_membership_modified": False}
    # This is solely the working update manifest, never an existing dataset file.
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"staged_files": len(changes), "root": str(root)}


def apply_update(output: Path, root: Path) -> dict:
    root = root.resolve(strict=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if str(root) != manifest["root"]:
        raise ValueError("Target differs from the reviewed dataset root")
    allowed = allowed_paths(root)
    for name, expected in manifest["unchanged_guards"].items():
        if digest(root / name) != expected:
            raise ValueError(f"Dataset changed after staging: {name}")
    for change in manifest["changes"]:
        relative = change["path"]
        if relative not in allowed:
            raise ValueError(f"Refusing to update original photos or unrelated files: {relative}")
        target = (root / relative).resolve()
        if root not in target.parents:
            raise ValueError("Path escapes dataset")
        if digest(output / "payload" / relative) != change["after_sha256"]:
            raise ValueError(f"Staged payload changed: {relative}")
        actual = digest(target) if target.exists() else None
        if actual != change["before_sha256"]:
            raise ValueError(f"Existing file changed after staging: {relative}")
    backup = root / manifest["backup"]
    backup.mkdir(parents=True, exist_ok=False)
    write_json(backup / "update_manifest.json", manifest)
    for change in manifest["changes"]:
        if change["before_sha256"]:
            relative = change["path"]
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, saved)
            if digest(saved) != change["before_sha256"]:
                raise ValueError(f"Backup verification failed: {relative}")
    changed = []
    try:
        for change in manifest["changes"]:
            relative = change["path"]
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".gender-update-" + uuid4().hex)
            with temp.open("xb") as stream:
                stream.write((output / "payload" / relative).read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            changed.append(change)
            if digest(target) != change["after_sha256"]:
                raise ValueError(f"Written file verification failed: {relative}")
    except Exception:
        for change in reversed(changed):
            target = root / change["path"]
            if change["before_sha256"]:
                shutil.copy2(backup / change["path"], target)
            elif digest(target) == change["after_sha256"]:
                target.unlink()  # Only this update's newly created, hash-matched file.
        raise
    result = {"complete": True, "updated_files": len(changed), "backup": str(backup),
              "backup_hashes_checked": True, "written_hashes_checked": True,
              "original_photos_modified": False, "split_membership_modified": False}
    write_json(backup / "publication.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["stage", "seal", "apply"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args()
    if args.mode == "stage":
        result = stage(args.root, args.review, args.policy, args.output)
    elif args.mode == "seal":
        result = seal(args.root, args.output)
    else:
        result = apply_update(args.output, args.root)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
