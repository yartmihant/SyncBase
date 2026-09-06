"""Проверки одноуровневого языка команд."""

import sys
from pathlib import Path

import pytest

from syncbase import cli


class DummyVault:
    calls: list[tuple[str, bool]] = []

    def __init__(self, vault_path: Path, token: str):
        self.local_path = vault_path
        self.token = token

    def show_status(self) -> None:
        self.calls.append(("status", False))

    def sync_save(self, force: bool = False) -> None:
        self.calls.append(("save", force))

    def sync_load(self, force: bool = False) -> None:
        self.calls.append(("load", force))


@pytest.fixture(autouse=True)
def fake_vault(tmp_path: Path, monkeypatch):
    DummyVault.calls = []
    monkeypatch.setattr(cli, "find_vault", lambda: (tmp_path, "test-token"))
    monkeypatch.setattr(cli, "SyncVault", DummyVault)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["syncbase", "status", "all"], ("status", False)),
        (["syncbase", "save", "all"], ("save", False)),
        (["syncbase", "save", "-f", "all"], ("save", True)),
        (["syncbase", "load", "all", "--force"], ("load", True)),
    ],
)
def test_supported_commands(monkeypatch, argv: list[str], expected: tuple[str, bool]):
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert DummyVault.calls == [expected]


@pytest.mark.parametrize(
    "argv",
    [
        ["syncbase", "save"],
        ["syncbase", "load", "all", "all"],
        ["syncbase", "save", "Category", "Project"],
        ["syncbase", "status", "all", "--force"],
    ],
)
def test_project_selectors_and_implicit_target_are_rejected(monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert DummyVault.calls == []


def test_missing_vault_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_vault", lambda: None)
    monkeypatch.setattr(sys, "argv", ["syncbase", "save", "all"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "ключ вольта" in capsys.readouterr().out
