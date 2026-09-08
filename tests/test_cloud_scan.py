"""Cloud tree scheduling and error propagation, without API access."""

import threading

import pytest

from syncbase.project import SyncProject


def test_child_starts_before_slow_sibling_finishes(tmp_path, monkeypatch):
    project = SyncProject(tmp_path, "Project", "unused")
    child_started = threading.Event()
    sibling_overlapped = []
    requested = []
    lock = threading.Lock()
    monkeypatch.setattr("syncbase.project.THREADS_COUNT", 2)

    def listing(path):
        relative = path.relative_to(project.cloud_path).as_posix()
        with lock:
            requested.append(relative)
        if relative == ".":
            return [{"name": name, "type": "dir"} for name in ("slow", "fast")]
        if relative == "slow":
            sibling_overlapped.append(child_started.wait(timeout=5))
            return []
        if relative == "fast":
            return [{"name": "child", "type": "dir"}]
        if relative == "fast/child":
            child_started.set()
            return [{"name": "file.txt", "type": "file", "md5": "hash"}]
        raise AssertionError(relative)

    monkeypatch.setattr(project.yandex_disk_client, "list", listing)
    project.cloud_scan()
    assert sibling_overlapped == [True]
    assert sorted(requested) == [".", "fast", "fast/child", "slow"]
    assert any(item.cloud_path.name == "file.txt" for item in project.sync_items.values())


def test_failed_cloud_scan_aborts_before_any_mutation(tmp_path, monkeypatch):
    project = SyncProject(tmp_path, "Project", "unused")
    operations = []

    def listing(_path):
        raise OSError("offline")

    monkeypatch.setattr(project.yandex_disk_client, "list", listing)
    monkeypatch.setattr(project, "multythread_operation", lambda *_args: operations.append(True))
    with pytest.raises(OSError, match="offline"):
        project.sync_save(force=True)
    assert operations == []
    assert not (project.local_path / ".sync_cache").exists()
