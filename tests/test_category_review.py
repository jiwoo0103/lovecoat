import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.category_review import make_record, sha, validate_annotations, write_gallery


def fixture_data(tmp_path):
    source = tmp_path / "original.jpg"
    Image.new("RGB", (90, 160), "ivory").save(source)
    row = {"code": "2764", "source": str(source), "sha256": sha(source), "shape": "adult_male",
           "size": "XL", "split_group": "same-garment", "role": "outer_validation"}
    annotation = {"code": "2764", "draft_category": "코트", "candidate_category": "코트",
                  "needs_review": False, "reason": "사용자가 확정한 기준 사례", "user_confirmed": True}
    manifest, annotations = {"rows": [row]}, {"criterion": {"reference": "2764"}, "entries": [annotation]}
    manifest_path, annotation_path = tmp_path / "manifest.json", tmp_path / "annotations.json"
    for path, value in ((manifest_path, manifest), (annotation_path, annotations)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return row, annotation, manifest, annotations, manifest_path, annotation_path


def test_visual_drafts_and_uncertain_fallback_are_never_training_truth(tmp_path):
    row, entry, *_ = fixture_data(tmp_path)
    uncertain = {**entry, "draft_category": None, "candidate_category": "코트", "user_confirmed": False,
                 "needs_review": True}
    # The record representation does not promote a display fallback to truth.
    result = make_record(uncertain, row)
    assert result["display_category"] == "기타" and result["draft_category"] is None
    assert result["training_label"] is None and result["model_score"] is None
    assert result["review_status"] == "unreviewed"
    clear_draft = make_record({**entry, "user_confirmed": False}, row)
    assert clear_draft["display_category"] == "코트" and clear_draft["training_label"] is None
    confirmed = make_record(entry, row)
    assert confirmed["training_label"] == "코트" and confirmed["review_status"] == "confirmed"


@pytest.mark.parametrize("mutation", [
    "duplicate", "changed_source", "unsafe_code", "unknown_category", "false_truth", "invalid_confirmation",
])
def test_invalid_or_changed_inputs_are_rejected_before_packaging(tmp_path, mutation):
    row, entry, manifest, annotations, *_ = fixture_data(tmp_path)
    if mutation == "duplicate":
        annotations["entries"].append(dict(entry))
    elif mutation == "changed_source":
        Image.new("RGB", (90, 160), "red").save(row["source"])
    elif mutation == "unsafe_code":
        entry["code"] = "../2764"
    elif mutation == "unknown_category":
        entry["draft_category"] = "점퍼"
    elif mutation == "invalid_confirmation":
        entry["user_confirmed"] = "false"
    else:
        entry["draft_category"] = None
        entry["needs_review"] = True
    with pytest.raises(ValueError):
        validate_annotations(manifest, annotations)


def test_packaging_preserves_original_and_metadata_and_rejects_overwrite(tmp_path):
    row, _, _, _, manifest, annotations = fixture_data(tmp_path)
    before = {path: sha(path) for path in (Path(row["source"]), manifest, annotations)}
    output = tmp_path / "review"
    summary = write_gallery(manifest, annotations, output)
    assert summary["count"] == 1 and summary["user_confirmed"] == 1
    assert summary["original_hashes_checked"] == 1 and not summary["category_model_used"]
    assert all(sha(path) == digest for path, digest in before.items())
    data = json.loads((output / "labels-draft.json").read_text(encoding="utf-8"))
    assert data["records"][0]["source_group"] == "same-garment"
    assert data["records"][0]["previous_task_role"] == "outer_validation"
    assert "source" not in data["records"][0]
    with Image.open(output / data["records"][0]["photo"]) as photo:
        assert photo.size == (90, 160)
    with pytest.raises(FileExistsError):
        write_gallery(manifest, annotations, output)


def test_embedded_annotation_text_cannot_escape_json_script_and_csv_is_safe(tmp_path):
    row, entry, _, annotations, manifest_path, annotation_path = fixture_data(tmp_path)
    entry["reason"] = '=HYPERLINK("bad")</script><img src=x onerror=alert(1)>'
    annotations["review_context"] = entry["reason"]
    annotation_path.write_text(json.dumps(annotations, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "review"
    write_gallery(manifest_path, annotation_path, output)
    page = (output / "검수보기.html").read_text(encoding="utf-8")
    assert entry["reason"] not in page
    assert "\\u003c/script>" in page
    assert "__CATEGORY_REVIEW_DATA__" not in page
    csv = (output / "labels-draft.csv").read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in csv and sha(Path(row["source"])) == row["sha256"]


def test_new_validation_batch_has_its_own_context_and_no_invented_confirmations(tmp_path):
    _, entry, _, annotations, manifest_path, annotation_path = fixture_data(tmp_path)
    entry.update(user_confirmed=False, reason="사용자 확인 전 육안 초안")
    annotations.update(title="추가 검증 자료", purpose="new_category_validation",
                       review_context="신규 사진이며 사용자 확인 전까지 학습·평가에서 제외합니다.")
    annotations["criterion"] = {"summary": "엉덩이를 완전히 덮는 기장부터 코트"}
    annotation_path.write_text(json.dumps(annotations, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "validation-review"
    summary = write_gallery(manifest_path, annotation_path, output)
    data = json.loads((output / "labels-draft.json").read_text(encoding="utf-8"))
    assert summary["user_confirmed"] == 0
    assert data["purpose"] == "new_category_validation"
    assert data["review_context"] == annotations["review_context"]
    assert data["records"][0]["training_label"] is None
    assert "2764번 코트만" not in (output / "사용안내.md").read_text(encoding="utf-8")
    assert "최초 생성 시 사용자 확정 0벌·미확정 1벌" in (output / "사용안내.md").read_text(encoding="utf-8")


def test_explicit_attributes_split_and_boundary_survive_packaging(tmp_path):
    row, entry, manifest, annotations, manifest_path, annotation_path = fixture_data(tmp_path)
    row["shape"] = "adult_unknown"
    entry.update(user_confirmed=False, sleeves="yes", length="borderline", dataset_split="test",
                 acceptable_categories=["자켓", "코트"])
    annotations["attribute_review_enabled"] = True
    for path, value in ((manifest_path, manifest), (annotation_path, annotations)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "attribute-review"
    write_gallery(manifest_path, annotation_path, output)
    data = json.loads((output / "labels-draft.json").read_text(encoding="utf-8"))
    assert data["attribute_review_enabled"]
    record = data["records"][0]
    assert record["dataset_split"] == "test" and record["age_group"] == "성인"
    assert record["acceptable_categories"] == ["자켓", "코트"]
    assert record["training_label"] is None
    entry["length"] = "normal"
    with pytest.raises(ValueError, match="acceptable"):
        validate_annotations(manifest, annotations)
