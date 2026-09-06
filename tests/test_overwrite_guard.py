"""Unit-тесты защиты вольта от потери данных."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from syncbase.item import SyncItem
from syncbase.vault import SyncVault


def _item(
    vault: SyncVault,
    name: str,
    local_modified: datetime,
    cloud_modified: datetime,
) -> SyncItem:
    item = vault.create_item(name)
    item.local_state.type = "file"
    item.local_state.modified = local_modified
    item.local_state.md5 = "local"
    item.cloud_state.type = "file"
    item.cloud_state.modified = cloud_modified
    item.cloud_state.md5 = "cloud"
    return item


def _conflicting_vault(tmp_path: Path, direction: str) -> SyncVault:
    vault = SyncVault(tmp_path, "test-token")
    now = datetime.now()
    old = now - timedelta(hours=1)
    local_modified, cloud_modified = (now, old) if direction == "load" else (old, now)
    vault.items_need_for_update["file"]["file"].append(
        _item(vault, "conflict.txt", local_modified, cloud_modified)
    )
    return vault


@pytest.mark.parametrize("direction", ["load", "save"])
def test_no_changes_are_safe(tmp_path: Path, direction: str):
    vault = SyncVault(tmp_path, "test-token")
    assert vault.check_overwrite_safety(direction, force=False) is True


def test_load_blocks_newer_local_file(tmp_path: Path, capsys):
    vault = _conflicting_vault(tmp_path, "load")
    assert vault.check_overwrite_safety("load", force=False) is False
    assert "conflict.txt" in capsys.readouterr().out


def test_save_blocks_newer_cloud_file(tmp_path: Path, capsys):
    vault = _conflicting_vault(tmp_path, "save")
    assert vault.check_overwrite_safety("save", force=False) is False
    assert "conflict.txt" in capsys.readouterr().out


def test_safe_direction_is_not_blocked(tmp_path: Path):
    now = datetime.now()
    old = now - timedelta(hours=1)

    load_vault = SyncVault(tmp_path / "load", "test-token")
    load_vault.items_need_for_update["file"]["file"].append(
        _item(load_vault, "newer-cloud.txt", old, now)
    )
    assert load_vault.check_overwrite_safety("load", force=False) is True

    save_vault = SyncVault(tmp_path / "save", "test-token")
    save_vault.items_need_for_update["file"]["file"].append(
        _item(save_vault, "newer-local.txt", now, old)
    )
    assert save_vault.check_overwrite_safety("save", force=False) is True


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
    vault = SyncVault(tmp_path, "test-token")
    item = vault.create_item("unique.txt")
    item.local_state.type = local_type
    item.cloud_state.type = cloud_type
    vault.items_need_for_update[local_type][cloud_type].append(item)

    assert vault.check_overwrite_safety(direction, force=False) is False


def test_force_allows_conflict_and_marks_item(tmp_path: Path, capsys):
    vault = _conflicting_vault(tmp_path, "load")
    vault._force_overwrite_ids = set()

    assert vault.check_overwrite_safety("load", force=True) is True
    assert len(vault._force_overwrite_ids) == 1
    assert "[FORCE]" in capsys.readouterr().out


@pytest.mark.parametrize("name", [".syncbase", ".sync_cache", ".syncignore"])
def test_system_files_never_block(tmp_path: Path, name: str):
    vault = SyncVault(tmp_path, "test-token")
    item = vault.create_item(name)
    item.local_state.type = "file"
    item.cloud_state.type = "empty"
    vault.items_need_for_update["file"]["empty"].append(item)

    assert vault.check_overwrite_safety("load", force=False) is True
