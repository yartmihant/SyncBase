"""Incremental hashing, baseline preservation and cache recovery, without network."""

import hashlib
import os

import pytest

from syncbase import item as item_module
from syncbase.project import SyncProject


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Project"
    root.mkdir()
    (root / ".syncignore").write_text("", encoding="utf-8")
    (root / "one.txt").write_bytes(b"one")
    (root / "two.txt").write_bytes(b"two")
    project = SyncProject(tmp_path, "Project", "unused")
    project.local_scan()
    project.set_cache()
    return project


def fresh(project):
    return SyncProject(project.vault_path, project.name, "unused")


@pytest.fixture
def hashed(monkeypatch):
    paths = []
    original = item_module._hash_local_file

    def track(path):
        paths.append(path.name)
        return original(path)

    monkeypatch.setattr(item_module, "_hash_local_file", track)
    return paths


def test_unchanged_files_are_not_read_in_new_process(project, hashed):
    scanned = fresh(project)
    scanned.local_scan()
    assert hashed == []
    assert scanned.sync_items["one.txt"].local_state.md5 == hashlib.md5(b"one").hexdigest()


@pytest.mark.parametrize("change", ["size", "same_size", "nanosecond", "replace"])
def test_changed_files_are_rehashed(project, hashed, change):
    path = project.local_path / "one.txt"
    before = path.stat()
    if change == "replace":
        replacement = project.local_path / "replacement"
        replacement.write_bytes(b"new")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, path)
    else:
        path.write_bytes(b"longer" if change == "size" else b"new")
        delta = 100 if change == "nanosecond" else 1_000_000_000
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + delta))
    scanned = fresh(project)
    scanned.local_scan()
    assert hashed == ["one.txt"]
    assert scanned.sync_items["one.txt"].local_state.md5 == hashlib.md5(path.read_bytes()).hexdigest()


@pytest.mark.skipif(os.name == "nt", reason="Windows ctime is creation time")
def test_restored_mtime_is_invalidated_by_ctime(project, hashed):
    path = project.local_path / "one.txt"
    before = path.stat()
    path.write_bytes(b"new")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    fresh(project).local_scan()
    assert hashed == ["one.txt"]


def test_status_preserves_baseline_and_caches_unsaved_changes(project, hashed, capsys):
    baseline = project.get_cache()["files"]
    (project.local_path / "one.txt").write_bytes(b"changed")
    for _ in range(2):
        fresh(project).show_status()
        assert "~ one.txt" in capsys.readouterr().out
        assert fresh(project).get_cache()["files"] == baseline
    assert hashed == ["one.txt"]


def test_new_removed_and_ignored_files(project, hashed):
    (project.local_path / "one.txt").unlink()
    (project.local_path / "new.txt").write_bytes(b"new")
    (project.local_path / ".syncignore").write_text("ignored/\n", encoding="utf-8")
    ignored = project.local_path / "ignored"
    ignored.mkdir()
    (ignored / "data").write_bytes(b"ignored")
    (project.local_path / ".sync_cache.abcd.tmp").write_bytes(b"partial cache")
    scanned = fresh(project)
    scanned.local_scan()
    scanned.set_cache()
    assert set(hashed) == {".syncignore", "new.txt"}
    assert set(scanned.sync_items) == {".syncignore", "two.txt", "new.txt"}
    assert "one.txt" not in scanned.get_cache()["local_index"]


def test_directory_to_file_transition(project, hashed):
    path = project.local_path / "directory"
    path.mkdir()
    project.local_scan()
    project.set_cache()
    path.rmdir()
    path.write_bytes(b"now a file")
    fresh(project).local_scan()
    assert hashed == ["directory"]


def test_v2_migrates_without_acknowledging_changes(project, hashed, capsys):
    cache = project.get_cache()
    cache["project_info"]["cache_version"] = "2.0"
    cache.pop("local_index")
    project._write_cache(cache)
    (project.local_path / "one.txt").write_bytes(b"changed")
    scanned = fresh(project)
    scanned.show_status()
    assert "~ one.txt" in capsys.readouterr().out
    assert set(hashed) == {"one.txt", "two.txt", ".syncignore"}
    assert scanned.get_cache()["project_info"]["cache_version"] == "3.0"
    assert scanned.get_cache()["files"] == cache["files"]
    hashed.clear()
    fresh(project).local_scan()
    assert hashed == []


@pytest.mark.parametrize("contents", ["{broken", "[]", "null", '{"files": []}'])
def test_corrupt_cache_falls_back_to_full_scan(project, hashed, contents):
    (project.local_path / ".sync_cache").write_text(contents, encoding="utf-8")
    fresh(project).local_scan()
    assert set(hashed) == {"one.txt", "two.txt", ".syncignore"}


@pytest.mark.parametrize("entry", [None, [], {}, {"signature": [], "md5": "bad"}])
def test_invalid_index_entry_is_rehashed(project, hashed, entry):
    cache = project.get_cache()
    cache["local_index"]["one.txt"] = entry
    project._write_cache(cache)
    fresh(project).local_scan()
    assert hashed == ["one.txt"]


def test_atomic_write_failure_preserves_cache(project, monkeypatch):
    path = project.local_path / ".sync_cache"
    original = path.read_bytes()

    def fail(*_args):
        raise OSError("disk error")

    monkeypatch.setattr(os, "replace", fail)
    project._write_cache({"new": "cache"})
    assert path.read_bytes() == original
    assert list(project.local_path.glob(".sync_cache.*.tmp")) == []


def test_file_changed_while_hashing_is_retried(project, monkeypatch):
    path = project.local_path / "one.txt"
    original = item_module._hash_local_file
    attempts = []

    def change_after_read(target):
        digest = original(target)
        if not attempts:
            target.write_bytes(b"changed during read")
        attempts.append(target)
        return digest

    monkeypatch.setattr(item_module, "_hash_local_file", change_after_read)
    item = project.create_item("one.txt")
    item.calc_local_state()
    assert len(attempts) == 2
    assert item.local_state.md5 == hashlib.md5(path.read_bytes()).hexdigest()
    assert item.local_signature == item_module._stat_signature(path.stat())


def test_continuously_changing_file_aborts_index(project, monkeypatch):
    original = item_module._hash_local_file

    def change_after_read(target):
        digest = original(target)
        with target.open("ab") as stream:
            stream.write(b"more")
        return digest

    monkeypatch.setattr(item_module, "_hash_local_file", change_after_read)
    with pytest.raises(RuntimeError, match="изменяется во время индексации"):
        project.create_item("one.txt").calc_local_state()


def test_blocked_save_preserves_baseline(project, monkeypatch):
    path = project.local_path / ".sync_cache"
    original = path.read_bytes()
    (project.local_path / "one.txt").write_bytes(b"changed")
    monkeypatch.setattr(project.yandex_disk_client, "list", lambda _path: [
        {"name": "unique.txt", "type": "file", "md5": "remote", "size": 1},
    ])
    with pytest.raises(SystemExit):
        project.sync_save()
    assert path.read_bytes() == original


def test_load_reuses_hashes_between_its_two_scans(project, hashed, monkeypatch):
    # Start without a disk cache, so the first scan must hash everything.
    (project.local_path / ".sync_cache").unlink()
    scanned = fresh(project)
    remote = [dict(item.local_state.to_dict(), name=name) for name, item in project.sync_items.items()]
    monkeypatch.setattr(scanned.yandex_disk_client, "list", lambda _path: remote)
    scanned.sync_load()
    assert sorted(hashed) == [".syncignore", "one.txt", "two.txt"]
    assert set(scanned.get_cache()["files"]) == {".syncignore", "one.txt", "two.txt"}


def test_load_hashes_downloaded_content(project, hashed, monkeypatch):
    scanned = fresh(project)
    remote = [dict(item.local_state.to_dict(), name=name) for name, item in project.sync_items.items()]
    for entry in remote:
        if entry["name"] == "one.txt":
            entry.update(md5=hashlib.md5(b"downloaded").hexdigest(), size=10)
    monkeypatch.setattr(scanned.yandex_disk_client, "list", lambda _path: remote)
    monkeypatch.setattr(item_module.SyncItem, "download_file", lambda item: item.local_path.write_bytes(b"downloaded"))
    scanned.sync_load(force=True)
    assert hashed == ["one.txt"]
    assert scanned.get_cache()["files"]["one.txt"]["md5"] == hashlib.md5(b"downloaded").hexdigest()
