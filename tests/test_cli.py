"""Проверки одноуровневого языка команд."""

import sys
from pathlib import Path

import pytest

from syncbase import cli


class DummyVault:
    calls: list[tuple[str, str | None, bool]] = []

    def __init__(self, vault_path: Path, token: str):
        self.path = vault_path
        self.token = token

    def resolve_project(self, cwd: Path) -> str | None:
        relative = cwd.resolve().relative_to(self.path.resolve())
        return relative.parts[0] if relative.parts else None

    def run_all(self, command: str, force: bool = False) -> None:
        self.calls.append((command, "all", force))

    def run_project(self, command: str, project: str, force: bool = False) -> None:
        self.calls.append((command, project, force))

    def show_projects(self) -> None:
        self.calls.append(("list", None, False))


@pytest.fixture(autouse=True)
def fake_vault(tmp_path: Path, monkeypatch):
    DummyVault.calls = []
    monkeypatch.setattr(cli, "find_vault", lambda: (tmp_path, "test-token"))
    monkeypatch.setattr(cli, "SyncVault", DummyVault)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["syncbase", "status", "all"], ("status", "all", False)),
        (["syncbase", "save", "all"], ("save", "all", False)),
        (["syncbase", "save", "-f", "all"], ("save", "all", True)),
        (["syncbase", "load", "all", "--force"], ("load", "all", True)),
        (["syncbase", "save", "MyProject"], ("save", "MyProject", False)),
    ],
)
def test_supported_selectors(monkeypatch, argv: list[str], expected):
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert DummyVault.calls == [expected]


@pytest.mark.parametrize("command", ["status", "save", "load"])
def test_command_without_selector_uses_current_project(
    fake_vault: Path,
    monkeypatch,
    command: str,
):
    nested = fake_vault / "MyProject" / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setattr(sys, "argv", ["syncbase", command])

    cli.main()

    assert DummyVault.calls == [(command, "MyProject", False)]


def test_force_flag_works_in_project_context(fake_vault: Path, monkeypatch):
    project = fake_vault / "MyProject"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(sys, "argv", ["syncbase", "save", "--force"])

    cli.main()

    assert DummyVault.calls == [("save", "MyProject", True)]


@pytest.mark.parametrize(
    "argv",
    [
        ["syncbase", "save"],
        ["syncbase", "load", "all", "all"],
        ["syncbase", "save", "Category", "Project"],
        ["syncbase", "status", "all", "--force"],
    ],
)
def test_invalid_context_or_two_level_selector_is_rejected(monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert DummyVault.calls == []


def test_list_uses_vault(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["syncbase", "list"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert DummyVault.calls == [("list", None, False)]


def test_missing_vault_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_vault", lambda: None)
    monkeypatch.setattr(sys, "argv", ["syncbase", "save", "all"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "ключ вольта" in capsys.readouterr().out
