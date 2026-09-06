#!/usr/bin/env python3
"""Точка входа одноуровневой CLI для SyncBase."""

import sys

from .resolver import find_vault, VAULT_KEY_FILE
from .vault import SyncVault


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

    command = sys.argv[1].lower()
    raw_args = sys.argv[2:]

    # Флаг допустим только для save/load; позиция после команды не важна.
    force = "-f" in raw_args or "--force" in raw_args
    args = [a for a in raw_args if a not in ("-f", "--force")]

    if command == "list":
        if args or force:
            _print_usage("list")
            sys.exit(1)
        _print_vault(vault)
        sys.exit(0)

    if command not in {"status", "save", "load"}:
        print(f"❌ Неизвестная команда: {command}")
        _print_usage()
        sys.exit(1)

    if args != ["all"] or (force and command == "status"):
        _print_usage(command)
        sys.exit(1)

    if command == "status":
        vault.show_status()
    elif command == "save":
        vault.sync_save(force=force)
    else:
        vault.sync_load(force=force)


def _print_vault(vault: SyncVault) -> None:
    """Показать одноуровневое содержимое локального и облачного корней."""
    local_items = {
        item.name
        for item in vault.local_path.iterdir()
        if item.name not in {".syncbase", ".sync_cache"}
    }
    cloud_items = {
        item["name"]
        for item in (vault.yandex_disk_client.list(vault.cloud_path) or [])
        if item.get("name") not in {".syncbase", ".sync_cache"}
    }

    print(f"📦 Вольт: {vault.name}")
    print(f"   локально: {vault.local_path}")
    print("   облако: app:/")
    for name in sorted(local_items | cloud_items):
        marks = []
        if name in local_items:
            marks.append("local")
        if name in cloud_items:
            marks.append("cloud")
        print(f"   - {name} [{'/'.join(marks)}]")


def _print_usage(command: str | None = None) -> None:
    if command:
        print(f"❗ Неверные аргументы для команды '{command}'.")
    print(
        "Использование:\n"
        "  syncbase list\n"
        "  syncbase status all\n"
        "  syncbase save   all [-f | --force]\n"
        "  syncbase load   all [-f | --force]\n\n"
        "Каждый .syncbase задаёт один вольт; категории и проекты не поддерживаются.\n"
        "Флаг -f / --force разрешает потенциально опасную перезапись."
    )


if __name__ == "__main__":
    main()
