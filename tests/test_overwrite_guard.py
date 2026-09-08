"""Unit-тесты защиты проекта от потери данных."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from syncbase.item import SyncItem
from syncbase.project import SyncProject


def _item(
    project: SyncProject,
    name: str,
    local_modified: datetime,
    cloud_modified: datetime,
) -> SyncItem:
    item = project.create_item(name)
    item.local_state.type = "file"
    item.local_state.modified = local_modified
    item.local_state.md5 = "local"
    item.cloud_state.type = "file"
    item.cloud_state.modified = cloud_modified
    item.cloud_state.md5 = "cloud"
    return item


def _conflicting_project(tmp_path: Path, direction: str) -> SyncProject:
    project = SyncProject(tmp_path, "Project", "test-token")
    now = datetime.now()
    old = now - timedelta(hours=1)
    local_modified, cloud_modified = (now, old) if direction == "load" else (old, now)
    project.items_need_for_update["file"]["file"].append(
        _item(project, "conflict.txt", local_modified, cloud_modified)
    )
    return project


@pytest.mark.parametrize("direction", ["load", "save"])
def test_no_changes_are_safe(tmp_path: Path, direction: str):
    project = SyncProject(tmp_path, "Project", "test-token")
    assert project.check_overwrite_safety(direction, force=False) is True


def test_load_blocks_newer_local_file(tmp_path: Path, capsys):
    project = _conflicting_project(tmp_path, "load")
    assert project.check_overwrite_safety("load", force=False) is False
    assert "conflict.txt" in capsys.readouterr().out


def test_save_blocks_newer_cloud_file(tmp_path: Path, capsys):
    project = _conflicting_project(tmp_path, "save")
    assert project.check_overwrite_safety("save", force=False) is False
    assert "conflict.txt" in capsys.readouterr().out


def test_safe_direction_is_not_blocked(tmp_path: Path):
    now = datetime.now()
    old = now - timedelta(hours=1)

    load_project = SyncProject(tmp_path / "load", "Project", "test-token")
    load_project.items_need_for_update["file"]["file"].append(
        _item(load_project, "newer-cloud.txt", old, now)
    )
    assert load_project.check_overwrite_safety("load", force=False) is True

    save_project = SyncProject(tmp_path / "save", "Project", "test-token")
    save_project.items_need_for_update["file"]["file"].append(
        _item(save_project, "newer-local.txt", now, old)
    )
    assert save_project.check_overwrite_safety("save", force=False) is True


@pytest.mark.parametrize(
    ("direction", "local_type", "cloud_type"),
    [("load", "file", "empty"), ("save", "empty", "file")],
)
def test_unique_files_are_protected(
    tmp_path: Path,
    direction: str,
    local_type: str,
    cloud_type: str,
):
    project = SyncProject(tmp_path, "Project", "test-token")
    item = project.create_item("unique.txt")
    item.local_state.type = local_type
    item.cloud_state.type = cloud_type
    project.items_need_for_update[local_type][cloud_type].append(item)

    assert project.check_overwrite_safety(direction, force=False) is False


def test_force_allows_conflict_and_marks_item(tmp_path: Path, capsys):
    project = _conflicting_project(tmp_path, "load")
    project._force_overwrite_ids = set()

    assert project.check_overwrite_safety("load", force=True) is True
    assert len(project._force_overwrite_ids) == 1
    assert "[FORCE]" in capsys.readouterr().out


@pytest.mark.parametrize("name", [".syncbase", ".sync_cache", ".syncignore"])
def test_system_files_never_block(tmp_path: Path, name: str):
    project = SyncProject(tmp_path, "Project", "test-token")
    item = project.create_item(name)
    item.local_state.type = "file"
    item.cloud_state.type = "empty"
    project.items_need_for_update["file"]["empty"].append(item)

    assert project.check_overwrite_safety("load", force=False) is True
