"""Проверки распознавания и выполнения переименований по хэшу."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from syncbase.project import SyncProject


def _save_rename_pair(project: SyncProject, md5: str = "same-md5"):
    source = project.create_item("old/name.txt")
    source.local_state.type = "empty"
    source.cloud_state.type = "file"
    source.cloud_state.md5 = md5
    source.cloud_state.size = 128

    target = project.create_item("new/name.txt")
    target.local_state.type = "file"
    target.local_state.md5 = md5
    target.local_state.size = 128
    target.cloud_state.type = "empty"

    project.sync_items = {"old/name.txt": source, "new/name.txt": target}
    project.items_need_for_update["empty"]["file"].append(source)
    project.items_need_for_update["file"]["empty"].append(target)
    return source, target


def test_save_rename_is_detected_and_does_not_require_force(tmp_path: Path):
    project = SyncProject(tmp_path, "Project", "test-token")
    source, target = _save_rename_pair(project)

    assert project.detect_renames("save") == [(source, target)]
    assert project.check_overwrite_safety("save", force=False) is True


def test_different_hash_is_not_treated_as_rename(tmp_path: Path):
    project = SyncProject(tmp_path, "Project", "test-token")
    _source, target = _save_rename_pair(project)
    target.local_state.md5 = "different-md5"

    assert project.detect_renames("save") == []
    assert project.check_overwrite_safety("save", force=False) is False


def test_save_uses_cloud_move_instead_of_delete_and_upload(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "Project").mkdir()
    project = SyncProject(tmp_path, "Project", "test-token")
    move = Mock(return_value=True)
    monkeypatch.setattr(project.yandex_disk_client, "exists", lambda _path: True)
    monkeypatch.setattr(project.yandex_disk_client, "move", move)

    def local_scan():
        _source, target = _save_rename_pair(project)
        project.sync_items = {"new/name.txt": target}
        project.items_need_for_update["empty"]["file"].clear()

    def cloud_scan():
        source = project.create_item("old/name.txt")
        source.local_state.type = "empty"
        source.cloud_state.type = "file"
        source.cloud_state.md5 = "same-md5"
        source.cloud_state.size = 128
        project.sync_items["old/name.txt"] = source
        project.items_need_for_update["empty"]["file"].append(source)

    operation_sizes: list[int] = []
    monkeypatch.setattr(project, "local_scan", local_scan)
    monkeypatch.setattr(project, "cloud_scan", cloud_scan)
    monkeypatch.setattr(
        project,
        "multythread_operation",
        lambda _handler, *items, **_kwargs: operation_sizes.append(len(items)),
    )

    project.sync_save(force=False)

    move.assert_called_once()
    assert move.call_args.args[0].as_posix() == "app:/Project/old/name.txt"
    assert move.call_args.args[1].as_posix() == "app:/Project/new/name.txt"
    assert operation_sizes == [0, 0, 0]


def test_load_renames_local_file_without_downloading(tmp_path: Path):
    project_path = tmp_path / "Project"
    old_path = project_path / "old/name.txt"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("same content", encoding="utf-8")
    project = SyncProject(tmp_path, "Project", "test-token")

    source = project.create_item("old/name.txt")
    source.calc_local_state()
    source.cloud_state.type = "empty"
    target = project.create_item("new/name.txt")
    target.local_state.type = "empty"
    target.cloud_state.type = "file"
    target.cloud_state.md5 = source.local_state.md5
    target.cloud_state.size = source.local_state.size
    project.items_need_for_update["file"]["empty"].append(source)
    project.items_need_for_update["empty"]["file"].append(target)

    assert project.check_overwrite_safety("load", force=False) is True
    project._apply_detected_renames("load")

    assert not old_path.exists()
    assert (project_path / "new/name.txt").read_text(encoding="utf-8") == "same content"


def test_failed_cloud_move_stops_before_delete_or_upload(tmp_path: Path, monkeypatch):
    project = SyncProject(tmp_path, "Project", "test-token")
    _save_rename_pair(project)
    monkeypatch.setattr(project.yandex_disk_client, "exists", lambda _path: True)
    monkeypatch.setattr(project.yandex_disk_client, "move", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="синхронизация остановлена"):
        project._apply_detected_renames("save")

    assert project._completed_rename_item_ids == set()
