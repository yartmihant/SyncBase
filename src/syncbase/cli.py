#!/usr/bin/env python3
"""Точка входа CLI для syncbase."""

import os
import sys

from .base import SyncBase
from .resolver import find_storage, STORAGE_KEY_FILE


def main():
    """Главная функция CLI."""
    result = find_storage()

    if result is None:
        print(
            f"❌ Не найден файл хранилища '{STORAGE_KEY_FILE}'.\n"
            f"\n"
            f"   Создайте файл '{STORAGE_KEY_FILE}' в корневой папке хранилища:\n"
            f"\n"
            f"       echo 'YANDEX_DISK_TOKEN=<ваш_oauth_токен>' > .syncbase\n"
            f"\n"
            f"   Папка, содержащая '{STORAGE_KEY_FILE}', станет корнем хранилища.\n"
            f"   Подробнее: см. раздел 'Настройка' в README.md"
        )
        sys.exit(1)

    base_path, token = result

    sync_base = SyncBase(base_path, token)
    cwd_path = os.getcwd()

    if len(sys.argv) < 2:
        sync_base._print_usage()
        sys.exit(1)

    command = sys.argv[1].lower()
    raw_args = sys.argv[2:]

    # Парсим флаг -f / --force (только для save и load)
    force = "-f" in raw_args or "--force" in raw_args
    args = [a for a in raw_args if a not in ("-f", "--force")]

    if command == "list":
        sync_base.cmd_list()
        sys.exit(0)

    if command not in {"status", "save", "load"}:
        print(f"❌ Неизвестная команда: {command}")
        sync_base._print_usage()
        sys.exit(1)

    ctx = sync_base._resolve_context(cwd_path)
    targets = sync_base._select_targets(command, ctx, args)

    if not targets:
        print("⚠️ Не найдено ни одного проекта для обработки.")
        sys.exit(0)

    for category, project in targets:
        if command == "save":
            local_exists = (sync_base.base_path / category / project).is_dir()
            if not local_exists:
                print(
                    f"⚠️ Пропуск save для {category}/{project}:"
                    f" локального проекта нет. Используйте 'load'."
                )
                continue
        if command == "status":
            local_exists = (sync_base.base_path / category / project).is_dir()
            if not local_exists:
                print(
                    f"📊 {category}/{project}: локального проекта нет."
                    f" 💡 Выполните 'load' для восстановления."
                )
                continue

        sync_base._run_for_project(command, category, project, force=force)


if __name__ == "__main__":
    main()
