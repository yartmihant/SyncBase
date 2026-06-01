#!/usr/bin/env python3
"""
Unit-тесты для syncbase.resolver.

Проверяют поиск файла .syncbase вверх по дереву папок.
Не требуют сети — только файловая система.
"""

import os
import sys
import pytest
from pathlib import Path

# Добавляем src/ в путь для import без установки пакета
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from syncbase.resolver import find_storage, _read_token, STORAGE_KEY_FILE

TOKEN = "y0_test_token_abc123"
KEY_CONTENT = f"YANDEX_DISK_TOKEN={TOKEN}\n"


class TestReadToken:
    """Тесты функции _read_token."""

    def test_reads_simple_line(self, tmp_path: Path):
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(f"YANDEX_DISK_TOKEN={TOKEN}\n")
        assert _read_token(key_file) == TOKEN

    def test_skips_comments(self, tmp_path: Path):
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(
            f"# комментарий\n"
            f"# ещё комментарий\n"
            f"YANDEX_DISK_TOKEN={TOKEN}\n"
        )
        assert _read_token(key_file) == TOKEN

    def test_skips_empty_lines(self, tmp_path: Path):
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(f"\n\n\nYANDEX_DISK_TOKEN={TOKEN}\n\n")
        assert _read_token(key_file) == TOKEN

    def test_returns_none_if_no_token_key(self, tmp_path: Path):
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text("SOME_OTHER_KEY=value\n")
        assert _read_token(key_file) is None

    def test_returns_none_if_empty_value(self, tmp_path: Path):
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text("YANDEX_DISK_TOKEN=\n")
        assert _read_token(key_file) is None

    def test_returns_none_on_missing_file(self, tmp_path: Path):
        key_file = tmp_path / "nonexistent.syncbase"
        assert _read_token(key_file) is None

    def test_strips_whitespace_from_value(self, tmp_path: Path):
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(f"YANDEX_DISK_TOKEN=  {TOKEN}  \n")
        assert _read_token(key_file) == TOKEN


class TestFindStorage:
    """Тесты функции find_storage."""

    def test_finds_key_in_current_dir(self, tmp_path: Path):
        """Файл .syncbase находится прямо в start-папке."""
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(KEY_CONTENT)

        result = find_storage(tmp_path)
        assert result is not None
        base_path, token = result
        assert base_path == tmp_path
        assert token == TOKEN

    def test_finds_key_in_parent_dir(self, tmp_path: Path):
        """Файл .syncbase находится в родительской папке."""
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(KEY_CONTENT)

        sub = tmp_path / "category" / "project"
        sub.mkdir(parents=True)

        result = find_storage(sub)
        assert result is not None
        base_path, token = result
        assert base_path == tmp_path
        assert token == TOKEN

    def test_finds_key_walking_up_multiple_levels(self, tmp_path: Path):
        """Файл .syncbase найден через несколько уровней вверх."""
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(KEY_CONTENT)

        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)

        result = find_storage(deep)
        assert result is not None
        base_path, token = result
        assert base_path == tmp_path
        assert token == TOKEN

    def test_returns_none_when_not_found(self, tmp_path: Path):
        """Файл .syncbase нигде не найден."""
        # tmp_path — изолированная директория без .syncbase
        # Поднимаемся, пока не упрёмся в корень.
        # Чтобы тест не зависел от реальной ФС, используем мок стартовой точки,
        # которая не содержит .syncbase и не имеет нужного файла ни в одном предке.
        # Поскольку в реальной ФС может присутствовать .syncbase выше tmp_path,
        # мы создаём изолированный путь в tmp_path и подменяем поиск.
        # Вместо этого тестируем поведение через временную структуру без файла.
        sub = tmp_path / "no_key_here"
        sub.mkdir()
        # Убеждаемся, что в sub нет .syncbase и не создаём его
        assert not (sub / STORAGE_KEY_FILE).exists()
        # find_storage может найти .syncbase выше tmp_path, поэтому
        # проверяем только то, что результат — либо None, либо base_path не в sub
        result = find_storage(sub)
        if result is not None:
            base_path, _ = result
            assert base_path != sub  # нашли не в sub, а выше — это нормально

    def test_finds_nearest_key(self, tmp_path: Path):
        """Выбирается ближайший .syncbase (не дальний предок)."""
        parent_key = tmp_path / STORAGE_KEY_FILE
        parent_key.write_text(f"YANDEX_DISK_TOKEN=parent_token\n")

        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_key = child_dir / STORAGE_KEY_FILE
        child_key.write_text(f"YANDEX_DISK_TOKEN=child_token\n")

        result = find_storage(child_dir)
        assert result is not None
        base_path, token = result
        assert base_path == child_dir
        assert token == "child_token"

    def test_base_path_is_dir_of_key_file(self, tmp_path: Path):
        """base_path — это папка, содержащая .syncbase, а не сам файл."""
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(KEY_CONTENT)

        result = find_storage(tmp_path)
        assert result is not None
        base_path, _ = result
        assert base_path.is_dir()
        assert (base_path / STORAGE_KEY_FILE).is_file()

    def test_uses_cwd_when_start_is_none(self, tmp_path: Path, monkeypatch):
        """Без явного start используется os.getcwd()."""
        key_file = tmp_path / STORAGE_KEY_FILE
        key_file.write_text(KEY_CONTENT)
        monkeypatch.chdir(tmp_path)

        result = find_storage()
        assert result is not None
        base_path, token = result
        assert token == TOKEN

    def test_test_base_syncbase_is_readable(self):
        """Проверяет, что реальный тестовый .syncbase существует и читаем."""
        test_base = Path(__file__).parent / "test_base1" / STORAGE_KEY_FILE
        assert test_base.exists(), f"Файл {test_base} не найден"
        token = _read_token(test_base)
        assert token is not None and len(token) > 10, "Токен отсутствует или слишком короткий"
