from pathlib import Path

import pytest
from PIL import Image

from coatdesk.domain import Task, boundaries, load_task, save_task
from coatdesk.photos import discover, execute_copy, plan_copy


def photo(root, code, angle=0, color="white"):
    folder = root / f"0_{code}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"img_0_0_{angle}.jpg"
    Image.new("RGB", (30, 50), color).save(path)
    return path


def test_discovery_keeps_slots_and_sorts_numbers(tmp_path):
    root = tmp_path / "arbitrary" / "nested"
    photo(root, "0010")
    photo(root, "0002", 0)
    photo(root, "0002", 2)
    photo(root, "0002", 3)
    (root / "0_0002" / "img_0_0_1.jpg").write_bytes(b"broken")
    garments, warnings = discover(tmp_path / "arbitrary")
    assert [g.code for g in garments] == ["0002", "0010"]
    assert set(garments[0].photos) == {"0", "2", "3"}
    assert any("읽을 수 없는" in w for w in warnings)
    assert any("추가 각도" in w for w in warnings)


def test_duplicate_folder_code_rejected(tmp_path):
    photo(tmp_path / "a", "0001")
    photo(tmp_path / "b", "1")
    with pytest.raises(ValueError, match="중복"):
        discover(tmp_path)


def test_copy_is_lossless_idempotent_and_no_overwrite(tmp_path):
    root = tmp_path / "source"
    original = photo(root, "0001")
    original_bytes = original.read_bytes()
    garments, _ = discover(root)
    destination = tmp_path / "flat"
    assert execute_copy(plan_copy(garments, root, destination)) == {"copied": 1, "skipped": 0}
    assert (destination / "0_0001_0.jpg").read_bytes() == original_bytes
    assert execute_copy(plan_copy(garments, root, destination)) == {"copied": 0, "skipped": 1}
    (destination / "0_0001_0.jpg").write_bytes(b"other file")
    with pytest.raises(ValueError, match="이름"):
        execute_copy(plan_copy(garments, root, destination))
    assert original.read_bytes() == original_bytes
    assert (destination / "0_0001_0.jpg").read_bytes() == b"other file"


def test_copy_rechecks_source_and_destination(tmp_path):
    root = tmp_path / "source"
    original = photo(root, "0012")
    garments, _ = discover(root)
    with pytest.raises(ValueError, match="밖"):
        plan_copy(garments, root, root / "output")
    plan = plan_copy(garments, root, tmp_path / "output")
    original.write_bytes(b"changed")
    with pytest.raises(ValueError, match="원본"):
        execute_copy(plan)
    photo(root, "0012")
    plan = plan_copy(garments, root, tmp_path / "output")
    Path(plan[0].target).parent.mkdir()
    Path(plan[0].target).write_bytes(b"new concurrent file")
    with pytest.raises(ValueError, match="충돌"):
        execute_copy(plan)


def test_task_roundtrip_and_daily_ordinal(tmp_path):
    photo(tmp_path / "source", "0248")
    photo(tmp_path / "source", "0250")
    photo(tmp_path / "source", "0253")
    garments, _ = discover(tmp_path / "source")
    garments[1].excluded = True
    garments[0].fields = {"size": "XS", "gender": "여성", "age": "성인"}
    garments[2].fields = {"size": "M", "gender": "여성", "age": "성인"}
    task = Task(str(tmp_path), garments, page=1, selected=[2])
    save_task(tmp_path / "task.coat.json", task)
    restored = load_task(tmp_path / "task.coat.json")
    assert restored.to_dict() == task.to_dict()
    assert restored.ordinals() == {"0248": 1, "0253": 2}
    assert boundaries(garments) == set()  # Unknown middle does not imply a guessed change.
    garments[1].fields = {"size": "XS"}
    assert boundaries(garments) == {1, 2}
