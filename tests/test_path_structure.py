"""Проверки одноуровневой структуры «вольт → проекты»."""

import json
from importlib.resources import files
from pathlib import Path

import pytest

from syncbase.project import SyncIgnore, SyncProject
from syncbase.vault import SyncVault


def test_project_maps_directly_below_app_root(tmp_path: Path):
    project = SyncProject(tmp_path, "MyProject", "test-token")

    root = project.create_item("")
    nested = project.create_item("docs/api.md")

    assert root.local_path == tmp_path / "MyProject"
    assert root.cloud_path.as_posix() == "app:/MyProject"
    assert nested.local_path == tmp_path / "MyProject/docs/api.md"
    assert nested.cloud_path.as_posix() == "app:/MyProject/docs/api.md"


def test_nested_folders_belong_to_same_project(tmp_path: Path):
    vault = SyncVault(tmp_path, "test-token")
    nested = tmp_path / "MyProject" / "src" / "package"
    nested.mkdir(parents=True)

    assert vault.resolve_project(nested) == "MyProject"
    assert vault.resolve_project(tmp_path / "MyProject") == "MyProject"
    assert vault.resolve_project(tmp_path) is None


def test_vault_lists_only_direct_project_directories(tmp_path: Path):
    (tmp_path / "ProjectA" / "nested").mkdir(parents=True)
    (tmp_path / "ProjectB").mkdir()
    (tmp_path / "root-file.txt").write_text("not a project", encoding="utf-8")
    (tmp_path / ".syncignore").write_text("ProjectB/\n", encoding="utf-8")

    vault = SyncVault(tmp_path, "test-token")

    assert vault.get_local_projects() == ["ProjectA"]


def test_run_all_uses_union_of_local_and_cloud_projects(tmp_path: Path, monkeypatch):
    (tmp_path / "LocalProject").mkdir()
    vault = SyncVault(tmp_path, "test-token")
    calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(vault, "get_cloud_projects", lambda: ["CloudProject"])
    monkeypatch.setattr(
        vault,
        "run_project",
        lambda command, name, force=False: calls.append((command, name, force)),
    )

    vault.run_all("load", force=True)

    assert calls == [
        ("load", "CloudProject", True),
        ("load", "LocalProject", True),
    ]


def test_project_scan_never_includes_key_or_cache(tmp_path: Path):
    project_path = tmp_path / "MyProject"
    project_path.mkdir()
    (project_path / ".syncbase").write_text("YANDEX_DISK_TOKEN=secret", encoding="utf-8")
    (project_path / ".sync_cache").write_text("{}", encoding="utf-8")
    (project_path / ".syncignore").write_text("", encoding="utf-8")
    (project_path / "data.txt").write_text("data", encoding="utf-8")

    project = SyncProject(tmp_path, "MyProject", "test-token")
    project.local_scan()

    assert "data.txt" in project.sync_items
    assert ".syncignore" in project.sync_items
    assert ".syncbase" not in project.sync_items
    assert ".sync_cache" not in project.sync_items


def test_new_project_copies_packaged_rules_and_excludes_nested_caches(tmp_path: Path):
    root = tmp_path / "Project"
    ignored_dirs = [".git", ".venv", "src/__pycache__", "web/node_modules", "web/.next"]
    for name in ignored_dirs:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "payload").write_text("generated", encoding="utf-8")
    (root / "src/.DS_Store").write_bytes(b"metadata")
    (root / "src/module.pyc").write_bytes(b"bytecode")
    (root / "src/main.py").write_text("print('hello')", encoding="utf-8")
    (root / ".env").write_text("EXAMPLE=value", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build/report.txt").write_text("keep", encoding="utf-8")

    project = SyncProject(tmp_path, "Project", "unused")
    project.local_scan()

    assert (root / ".syncignore").read_text(encoding="utf-8") == (
        files("syncbase").joinpath(".syncignore.example").read_text(encoding="utf-8")
    )
    assert {Path(name).as_posix() for name in project.sync_items} == {
        ".syncignore", ".env", "src", "src/main.py", "web", "build", "build/report.txt",
    }


@pytest.mark.parametrize("rules", ["", "# My rules\ncustom/\n"])
def test_existing_project_rules_are_not_replaced(tmp_path: Path, rules: str):
    root = tmp_path / "Project"
    (root / ".venv").mkdir(parents=True)
    (root / ".syncignore").write_text(rules, encoding="utf-8")
    project = SyncProject(tmp_path, "Project", "unused")

    project.local_scan()

    assert (root / ".syncignore").read_text(encoding="utf-8") == rules
    assert ".venv" in project.sync_items


def test_explicit_root_rule_does_not_exclude_nested_directory():
    rules = SyncIgnore("/cache/\n")
    assert rules.should_ignore("cache", is_directory=True)
    assert not rules.should_ignore("src/cache", is_directory=True)


def test_negated_name_can_restore_nested_file():
    rules = SyncIgnore("Thumbs.db\n!docs/Thumbs.db\n")
    assert rules.should_ignore("assets/Thumbs.db")
    assert not rules.should_ignore("docs/Thumbs.db")


def test_cloud_scan_is_scoped_to_project(tmp_path: Path, monkeypatch):
    project_path = tmp_path / "MyProject"
    project_path.mkdir()
    (project_path / ".syncignore").write_text("", encoding="utf-8")
    project = SyncProject(tmp_path, "MyProject", "test-token")
    requested_paths: list[str] = []

    def fake_list(path):
        requested_paths.append(path.as_posix())
        return [{"name": "visible.txt", "type": "file", "md5": "remote"}]

    monkeypatch.setattr(project.yandex_disk_client, "list", fake_list)
    project.local_scan()
    project.cloud_scan()

    assert requested_paths == ["app:/MyProject"]
    assert "visible.txt" in project.sync_items


def test_stale_cloud_tmp_files_do_not_block_save(tmp_path: Path, monkeypatch):
    project_path = tmp_path / "MyProject"
    project_path.mkdir()
    (project_path / ".syncignore").write_text("", encoding="utf-8")
    (project_path / "report.pdf").write_bytes(b"report")
    (project_path / "notes.tmp").write_text("valuable", encoding="utf-8")
    project = SyncProject(tmp_path, "MyProject", "test-token")
    monkeypatch.setattr(
        project.yandex_disk_client,
        "list",
        lambda _path: [
            {"name": "report.pdf.tmp", "type": "file"},
            {"name": "orphan.tmp", "type": "file"},
            {
                "name": "notes.tmp",
                "type": "file",
                "md5": project.sync_items["notes.tmp"].local_state.md5,
            },
        ],
    )

    project.local_scan()
    project.cloud_scan()

    assert "report.pdf.tmp" not in project.sync_items
    assert "orphan.tmp" not in project.sync_items
    assert "notes.tmp" in project.sync_items
    assert sorted(path.as_posix() for path in project.stale_cloud_temp_paths) == [
        "app:/MyProject/orphan.tmp",
        "app:/MyProject/report.pdf.tmp",
    ]
    assert project.check_overwrite_safety("save", force=False) is True


def test_save_cleans_stale_cloud_tmp_without_force(tmp_path: Path, monkeypatch):
    project_path = tmp_path / "MyProject"
    project_path.mkdir()
    (project_path / ".syncignore").write_text("", encoding="utf-8")
    project = SyncProject(tmp_path, "MyProject", "test-token")
    removed: list[str] = []
    monkeypatch.setattr(
        project.yandex_disk_client,
        "list",
        lambda _path: [{"name": "interrupted.bin.tmp", "type": "file"}],
    )
    monkeypatch.setattr(
        project.yandex_disk_client,
        "remove",
        lambda path: removed.append(path.as_posix()) or True,
    )
    monkeypatch.setattr(project, "multythread_operation", lambda *_args, **_kwargs: None)

    project.sync_save(force=False)

    assert removed == ["app:/MyProject/interrupted.bin.tmp"]


def test_load_creates_project_cache(tmp_path: Path, monkeypatch):
    project_path = tmp_path / "MyProject"
    project_path.mkdir()
    (project_path / ".syncignore").write_text("", encoding="utf-8")
    project = SyncProject(tmp_path, "MyProject", "test-token")
    monkeypatch.setattr(project.yandex_disk_client, "list", lambda _path: [])

    project.sync_load()

    cache = json.loads((project_path / ".sync_cache").read_text(encoding="utf-8"))
    assert cache["project_info"]["cache_version"] == "3.0"
    assert cache["project_info"]["local_path"] == str(project_path)
    assert ".syncbase" not in cache["files"]
    assert ".sync_cache" not in cache["files"]
