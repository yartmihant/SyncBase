#!/usr/bin/env python3
"""Точка входа одноуровневой CLI для SyncBase."""

import sys
from pathlib import Path
from typing import cast

from .resolver import find_vault, VAULT_KEY_FILE
from .vault import Command, SyncVault


def main():
    """Главная функция CLI."""
    result = find_vault()

    if result is None:
        print(
            f"❌ Не найден ключ вольта '{VAULT_KEY_FILE}'.\n"
            f"\n"
            f"   Создайте файл '{VAULT_KEY_FILE}' в корневой папке вольта:\n"
            f"\n"
            f"       echo 'YANDEX_DISK_TOKEN=<ваш_oauth_токен>' > .syncbase\n"
            f"\n"
            f"   Папка, содержащая '{VAULT_KEY_FILE}', станет корнем вольта.\n"
            f"   Подробнее: см. раздел 'Настройка' в README.md"
        )
        sys.exit(1)

    vault_path, token = result
    vault = SyncVault(vault_path, token)

    if len(sys.argv) < 2:
        _print_usage()
        sys.exit(1)

    command_name = sys.argv[1].lower()
    raw_args = sys.argv[2:]

    # Флаг допустим только для save/load; позиция после команды не важна.
    force = "-f" in raw_args or "--force" in raw_args
    args = [a for a in raw_args if a not in ("-f", "--force")]

    if command_name == "list":
        if args or force:
            _print_usage("list")
            sys.exit(1)
        vault.show_projects()
        sys.exit(0)

    if command_name not in {"status", "save", "load"}:
        print(f"❌ Неизвестная команда: {command_name}")
        _print_usage()
        sys.exit(1)

    command = cast(Command, command_name)

    if len(args) > 1 or (force and command == "status"):
        _print_usage(command)
        sys.exit(1)

    try:
        if args:
            selector = args[0]
            if selector == "all":
                vault.run_all(command, force=force)
            else:
                vault.run_project(command, selector, force=force)
        else:
            project_name = vault.resolve_project(Path.cwd())
            if project_name is None:
                print("❗ Команда без селектора должна запускаться из папки проекта.")
                _print_usage(command)
                sys.exit(1)
            vault.run_project(command, project_name, force=force)
    except ValueError as exc:
        print(f"❌ {exc}")
        _print_usage(command)
        sys.exit(1)


def _print_usage(command: str | None = None) -> None:
    if command:
        print(f"❗ Неверные аргументы для команды '{command}'.")
    print(
        "Использование:\n"
        "  syncbase list\n"
        "  syncbase status [all | <project>]\n"
        "  syncbase save   [all | <project>] [-f | --force]\n"
        "  syncbase load   [all | <project>] [-f | --force]\n\n"
        "Без селектора status/save/load работают с проектом текущей папки.\n"
        "Проекты находятся непосредственно в корне вольта; категорий нет.\n"
        "Флаг -f / --force разрешает потенциально опасную перезапись."
    )


if __name__ == "__main__":
    main()
