"""Проверки одноуровневого отображения путей вольта."""

import json
from pathlib import Path

from syncbase.vault import SyncVault


def test_vault_root_maps_directly_to_app_root(tmp_path: Path):
    vault = SyncVault(tmp_path, "test-token")

    root = vault.create_item("")
    nested = vault.create_item("docs/api.md")

    assert root.local_path == tmp_path
    assert root.cloud_path.as_posix() == "app:"
    assert nested.local_path == tmp_path / "docs/api.md"
    assert nested.cloud_path.as_posix() == "app:/docs/api.md"


def test_nested_folders_are_content_not_project_selectors(tmp_path: Path):
    vault = SyncVault(tmp_path, "test-token")
    item = vault.create_item("Category/Project/file.txt")

    assert item.local_path == tmp_path / "Category/Project/file.txt"
    assert item.cloud_path.as_posix() == "app:/Category/Project/file.txt"


def test_local_scan_never_includes_vault_key_or_cache(tmp_path: Path):
    (tmp_path / ".syncbase").write_text("YANDEX_DISK_TOKEN=secret", encoding="utf-8")
    (tmp_path / ".sync_cache").write_text("{}", encoding="utf-8")
    (tmp_path / ".syncignore").write_text("", encoding="utf-8")
    (tmp_path / "data.txt").write_text("data", encoding="utf-8")

    vault = SyncVault(tmp_path, "test-token")
    vault.local_scan()

    assert "data.txt" in vault.sync_items
    assert ".syncignore" in vault.sync_items
    assert ".syncbase" not in vault.sync_items
    assert ".sync_cache" not in vault.sync_items


def test_cloud_scan_ignores_local_only_service_files(tmp_path: Path, monkeypatch):
    (tmp_path / ".syncignore").write_text("", encoding="utf-8")
    vault = SyncVault(tmp_path, "test-token")
    monkeypatch.setattr(
        vault.yandex_disk_client,
        "list",
        lambda _path: [
            {"name": ".syncbase", "type": "file"},
            {"name": ".sync_cache", "type": "file"},
            {"name": "visible.txt", "type": "file", "md5": "remote"},
        ],
    )

    vault.local_scan()
    vault.cloud_scan()

    assert "visible.txt" in vault.sync_items
    assert ".syncbase" not in vault.sync_items
    assert ".sync_cache" not in vault.sync_items


def test_load_creates_fresh_local_vault_cache(tmp_path: Path, monkeypatch):
    (tmp_path / ".syncbase").write_text("YANDEX_DISK_TOKEN=secret", encoding="utf-8")
    (tmp_path / ".syncignore").write_text("", encoding="utf-8")
    vault = SyncVault(tmp_path, "test-token")
    monkeypatch.setattr(vault.yandex_disk_client, "list", lambda _path: [])

    vault.sync_load()

    cache = json.loads((tmp_path / ".sync_cache").read_text(encoding="utf-8"))
    assert cache["vault_info"]["cache_version"] == "2.0"
    assert cache["vault_info"]["local_path"] == str(tmp_path)
    assert ".syncbase" not in cache["files"]
    assert ".sync_cache" not in cache["files"]
