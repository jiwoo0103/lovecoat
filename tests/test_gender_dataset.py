import json
from pathlib import Path

import pytest

from scripts.add_gender_labels import GENDER_FIELDS, add_labels, apply_update
from scripts.prepare_size_dataset import digest, write_json
from scripts.verify_size_dataset import check_csv_records


def policy():
    return {"policy_id": "confirmed-adult-provisional-child",
            "adult_reference_mapping_confirmed_by_user": True,
            "child_rule": {"gender": "male", "provisional": True, "confirmed_by_user": True}}


def records():
    return [{"code": "0001", "age": "adult", "size_label": "S", "split": "train", "human_verified": False},
            {"code": "0002", "age": "adult", "size_label": "L", "split": "validation", "human_verified": False},
            {"code": "0003", "age": "child", "raw_text": "110", "split": "train", "human_verified": False}]


def test_gender_uses_reviewed_shape_not_size_and_keeps_existing_fields_and_splits():
    before = records()
    result = add_labels(before, {"0001": "male", "0002": "female"}, policy())
    assert [{k: v for k, v in row.items() if k not in GENDER_FIELDS} for row in result] == before
    assert result[0]["gender_reference"] == "A_0035" and result[0]["gender_ko"] == "남성"
    assert result[1]["gender_reference"] == "B_0001" and result[1]["gender_ko"] == "여성"
    assert result[2]["gender"] == "male" and result[2]["gender_ko"] == "남아"
    assert result[2]["gender_provisional"] is True
    assert result[2]["gender_label_source"] == "user_provisional_rule"
    assert all(row["gender_human_verified"] is False for row in result)
    assert all("gender" not in row for row in before)


@pytest.mark.parametrize("review", [{"0001": "male"}, {"0001": "male", "0002": "female", "9999": "male"}])
def test_rejects_partial_or_unselected_adult_annotations(review):
    with pytest.raises(ValueError, match="exactly"):
        add_labels(records(), review, policy())


def test_requires_confirmed_references_and_explicit_provisional_child_rule():
    unconfirmed = {**policy(), "adult_reference_mapping_confirmed_by_user": False}
    with pytest.raises(ValueError, match="not been confirmed"):
        add_labels(records(), {"0001": "male", "0002": "female"}, unconfirmed)
    permanent_child = {**policy(), "child_rule": {"gender": "male", "provisional": False, "confirmed_by_user": True}}
    with pytest.raises(ValueError, match="provisional"):
        add_labels(records(), {"0001": "male", "0002": "female"}, permanent_child)
    labelled = add_labels(records(), {"0001": "male", "0002": "female"}, policy())
    with pytest.raises(ValueError, match="before replacement"):
        add_labels(labelled, {"0001": "female", "0002": "male"}, policy())


def make_update(tmp_path):
    root, output = tmp_path / "dataset", tmp_path / "update"
    root.mkdir()
    (output / "payload").mkdir(parents=True)
    original = b"original photo must never be changed"
    (root / "photo.jpg").write_bytes(original)
    before = {"code": "0001", "front_path": "photo.jpg"}
    (root / "labels.jsonl").write_text(json.dumps(before) + "\n", encoding="utf-8")
    (output / "payload" / "labels.jsonl").write_text(json.dumps({**before, "gender": "female"}) + "\n", encoding="utf-8")
    change = {"path": "labels.jsonl", "before_sha256": digest(root / "labels.jsonl"),
              "after_sha256": digest(output / "payload" / "labels.jsonl")}
    manifest = {"root": str(root.resolve()), "backup": "_label_history/before", "changes": [change], "unchanged_guards": {}}
    write_json(output / "manifest.json", manifest)
    return root, output, manifest, original


def test_apply_saves_recoverable_backup_and_never_writes_original_photographs(tmp_path):
    root, output, manifest, original = make_update(tmp_path)
    before = (root / "labels.jsonl").read_bytes()
    result = apply_update(output, root)
    assert result["complete"] is True
    assert (Path(result["backup"]) / "labels.jsonl").read_bytes() == before
    assert (root / "photo.jpg").read_bytes() == original
    assert digest(root / "labels.jsonl") == manifest["changes"][0]["after_sha256"]


def test_apply_refuses_stale_manual_edits_before_writing_anything(tmp_path):
    root, output, _, original = make_update(tmp_path)
    (root / "labels.jsonl").write_text('{"code":"0001","front_path":"photo.jpg","note":"user edit"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="changed after staging"):
        apply_update(output, root)
    assert not (root / "_label_history").exists()
    assert (root / "photo.jpg").read_bytes() == original


def test_apply_refuses_original_photo_path_even_if_payload_hashes_match(tmp_path):
    root, output, manifest, original = make_update(tmp_path)
    (output / "payload" / "photo.jpg").write_bytes(b"forbidden replacement")
    manifest["changes"] = [{"path": "photo.jpg", "before_sha256": digest(root / "photo.jpg"),
                            "after_sha256": digest(output / "payload" / "photo.jpg")}]
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="original photos"):
        apply_update(output, root)
    assert (root / "photo.jpg").read_bytes() == original


def test_csv_verification_catches_gender_and_provisional_flag_mismatch():
    expected = [{"code": "0001", "gender": "male", "gender_provisional": True}]
    check_csv_records([{"code": "0001", "gender": "male", "gender_provisional": "True"}], expected)
    with pytest.raises(AssertionError):
        check_csv_records([{"code": "0001", "gender": "female", "gender_provisional": "True"}], expected)
    with pytest.raises(AssertionError):
        check_csv_records([{"code": "0001", "gender": "male", "gender_provisional": "False"}], expected)
