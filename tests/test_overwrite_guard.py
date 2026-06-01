#!/usr/bin/env python3
"""
Unit/интеграционные тесты защиты от перезаписи более новых файлов.

Использует реальное тестовое хранилище на Яндекс.Диске.
Токен берётся из tests/test_base1/.syncbase.

Структура теста:
  - Создаём временную категорию/проект в BASE_PATH.
  - Загружаем (save) на облако.
  - Искусственно создаём конфликт времён модификации.
  - Проверяем, что check_overwrite_safety блокирует или разрешает операцию.
"""

import sys
import os
import time
import shutil
import pytest
from pathlib import Path
from datetime import datetime, timedelta

# Добавляем src/ в путь
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from syncbase.resolver import find_storage, STORAGE_KEY_FILE
from syncbase.project import SyncProject
from syncbase.item import SyncItem, ItemState

# ------------------------------------------------------------------ #
#  Фикстуры                                                           #
# ------------------------------------------------------------------ #

TEST_BASE = Path(__file__).parent / "test_base1"


def get_token() -> str:
    result = find_storage(TEST_BASE)
    if result is None:
        pytest.skip(f"Не найден {STORAGE_KEY_FILE} в {TEST_BASE}")
    _, token = result
    return token


@pytest.fixture
def token() -> str:
    return get_token()


@pytest.fixture
def temp_project(tmp_path: Path, token: str):
    """
    Создаёт временный SyncProject с тестовыми файлами.
    tmp_path используется как base_path; проект не заливается на диск.
    """
    category = "tmp_test_guard"
    project_name = f"guard_{int(time.time())}"
    project_dir = tmp_path / category / project_name
    project_dir.mkdir(parents=True)

    # Создаём тестовый файл
    test_file = project_dir / "test_file.txt"
    test_file.write_text("initial content")

    sp = SyncProject(tmp_path, category, project_name, token)
    return sp, project_dir, test_file


# ------------------------------------------------------------------ #
#  Вспомогательная функция: подготовка SyncItem с нужными временами  #
# ------------------------------------------------------------------ #

def _make_sync_item_with_dates(
    sp: SyncProject,
    rel_path: str,
    local_modified: datetime,
    cloud_modified: datetime,
    md5_differ: bool = True,
) -> SyncItem:
    """Создаёт SyncItem с искусственно заданными метаданными."""
    item = sp.create_item(rel_path)
    item.local_state.type = "file"
    item.local_state.modified = local_modified
    item.local_state.md5 = "local_hash_aaa" if md5_differ else "same_hash"
    item.local_state.size = 100

    item.cloud_state.type = "file"
    item.cloud_state.modified = cloud_modified
    item.cloud_state.md5 = "cloud_hash_bbb" if md5_differ else "same_hash"
    item.cloud_state.size = 100

    return item


# ------------------------------------------------------------------ #
#  Тесты check_overwrite_safety                                       #
# ------------------------------------------------------------------ #

class TestOverwriteGuard:
    """Тесты метода SyncProject.check_overwrite_safety()."""

    def _setup_project_with_conflict(
        self, tmp_path: Path, token: str, direction: str
    ) -> SyncProject:
        """
        Создаёт SyncProject и добавляет в items_need_for_update['file']['file']
        один элемент с временным конфликтом.
        """
        sp = SyncProject(tmp_path, "cat", "proj", token)

        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)

        if direction == "load":
            # load: локальный НОВЕЕ облачного — опасно
            local_mod = now
            cloud_mod = one_hour_ago
        else:
            # save: облачный НОВЕЕ локального — опасно
            local_mod = one_hour_ago
            cloud_mod = now

        item = _make_sync_item_with_dates(sp, "conflict.txt", local_mod, cloud_mod)
        sp.sync_items["conflict.txt"] = item
        sp.items_need_for_update["file"]["file"].append(item)
        return sp

    def test_no_block_when_no_file_file_conflicts(self, tmp_path: Path, token: str):
        """Нет конфликтных файлов → check_overwrite_safety возвращает True."""
        sp = SyncProject(tmp_path, "cat", "proj", token)
        # items_need_for_update['file']['file'] пуст
        assert sp.check_overwrite_safety("load", force=False) is True
        assert sp.check_overwrite_safety("save", force=False) is True

    def test_no_block_when_same_md5(self, tmp_path: Path, token: str):
        """Одинаковый md5 → файл не попадает в items_need_for_update['file']['file']."""
        sp = SyncProject(tmp_path, "cat", "proj", token)
        now = datetime.now()
        item = _make_sync_item_with_dates(sp, "same.txt", now, now - timedelta(hours=1), md5_differ=False)
        # С одинаковым md5 файл не попадает в items_need_for_update через cloud_scan,
        # но мы можем его добавить — check_overwrite_safety проверяет дату, не md5
        # (разный md5 — обязательное условие попадания в список)
        sp.items_need_for_update["file"]["file"].append(item)
        # Одинаковый md5 означает, что содержимое одинаково, даже если даты разные.
        # В реальном коде этот файл сюда не попадёт. Тест проверяет только даты.
        # local_mod == cloud_mod (нет разницы) — не опасно
        item.local_state.modified = now
        item.cloud_state.modified = now
        assert sp.check_overwrite_safety("load", force=False) is True

    def test_blocks_load_when_local_newer(self, tmp_path: Path, token: str, capsys):
        """load: локальный файл новее облачного → метод возвращает False."""
        sp = self._setup_project_with_conflict(tmp_path, token, "load")
        result = sp.check_overwrite_safety("load", force=False)
        assert result is False
        captured = capsys.readouterr()
        assert "потеря данных" in captured.out
        assert "conflict.txt" in captured.out

    def test_blocks_save_when_cloud_newer(self, tmp_path: Path, token: str, capsys):
        """save: облачный файл новее локального → метод возвращает False."""
        sp = self._setup_project_with_conflict(tmp_path, token, "save")
        result = sp.check_overwrite_safety("save", force=False)
        assert result is False
        captured = capsys.readouterr()
        assert "потеря данных" in captured.out
        assert "conflict.txt" in captured.out

    def test_no_block_when_cloud_newer_but_direction_load(self, tmp_path: Path, token: str):
        """
        load: облачный файл НОВЕЕ локального — это безопасно
        (мы перезапишем более старый локальный более новым облачным).
        """
        sp = SyncProject(tmp_path, "cat", "proj", token)
        now = datetime.now()
        item = _make_sync_item_with_dates(
            sp, "ok.txt",
            local_modified=now - timedelta(hours=1),  # локальный старее
            cloud_modified=now,                        # облачный новее
        )
        sp.items_need_for_update["file"]["file"].append(item)
        assert sp.check_overwrite_safety("load", force=False) is True

    def test_no_block_when_local_newer_but_direction_save(self, tmp_path: Path, token: str):
        """
        save: локальный файл НОВЕЕ облачного — это безопасно
        (мы перезапишем более старый облачный более новым локальным).
        """
        sp = SyncProject(tmp_path, "cat", "proj", token)
        now = datetime.now()
        item = _make_sync_item_with_dates(
            sp, "ok.txt",
            local_modified=now,                        # локальный новее
            cloud_modified=now - timedelta(hours=1),   # облачный старее
        )
        sp.items_need_for_update["file"]["file"].append(item)
        assert sp.check_overwrite_safety("save", force=False) is True

    def test_force_allows_overwrite(self, tmp_path: Path, token: str, capsys):
        """С флагом force=True метод возвращает True, даже если есть конфликт."""
        sp = self._setup_project_with_conflict(tmp_path, token, "load")
        result = sp.check_overwrite_safety("load", force=True)
        assert result is True
        captured = capsys.readouterr()
        assert "[FORCE]" in captured.out

    def test_force_logs_warning_icon(self, tmp_path: Path, token: str, capsys):
        """С force=True в выводе есть маркер [FORCE]."""
        sp = self._setup_project_with_conflict(tmp_path, token, "save")
        sp.check_overwrite_safety("save", force=True)
        captured = capsys.readouterr()
        assert "[FORCE]" in captured.out

    def test_force_saves_dangerous_item_ids(self, tmp_path: Path, token: str):
        """С force=True заполняется _force_overwrite_ids."""
        sp = self._setup_project_with_conflict(tmp_path, token, "load")
        sp._force_overwrite_ids = set()
        sp.check_overwrite_safety("load", force=True)
        assert len(sp._force_overwrite_ids) > 0

    def test_multiple_conflicts_all_reported(self, tmp_path: Path, token: str, capsys):
        """Все конфликтующие файлы перечислены в выводе."""
        sp = SyncProject(tmp_path, "cat", "proj", token)
        now = datetime.now()
        old = now - timedelta(hours=1)

        for name in ("file_a.txt", "file_b.txt", "file_c.txt"):
            item = _make_sync_item_with_dates(sp, name, local_modified=now, cloud_modified=old)
            sp.items_need_for_update["file"]["file"].append(item)

        result = sp.check_overwrite_safety("load", force=False)
        assert result is False
        captured = capsys.readouterr()
        assert "file_a.txt" in captured.out
        assert "file_b.txt" in captured.out
        assert "file_c.txt" in captured.out


# ------------------------------------------------------------------ #
#  Интеграционный тест: реальная синхронизация с блокировкой         #
# ------------------------------------------------------------------ #

class TestOverwriteGuardIntegration:
    """
    Проверяет блокировку на реальном хранилище.
    Использует tmp_path как BASE_PATH, облако — реальный Яндекс.Диск.
    """

    @pytest.fixture(autouse=True)
    def setup_project(self, tmp_path: Path, token: str):
        self.token = token
        self.base_path = tmp_path
        self.category = "tmp_guard_test"
        self.project_name = f"guard_{int(time.time())}"
        self.project_dir = tmp_path / self.category / self.project_name
        self.project_dir.mkdir(parents=True)

        # Создаём файл
        self.test_file = self.project_dir / "data.txt"
        self.test_file.write_text("version 1")

        yield

        # Очистка: удаляем временный проект из облака
        try:
            from syncbase.client import YandexDiskClient
            client = YandexDiskClient(self.token)
            cloud_path = f"app:/{self.category}/{self.project_name}"
            client.remove(cloud_path)
        except Exception:
            pass

    def test_sync_save_then_modify_then_load_is_blocked(self):
        """
        Сценарий: save → изменить локально → load без --force → блок.
        """
        sp = SyncProject(self.base_path, self.category, self.project_name, self.token)
        sp.sync_save(force=False)  # первый save, конфликтов нет

        # Небольшая пауза, чтобы mtime гарантированно стал новее
        time.sleep(1)

        # Изменяем файл локально (он станет новее облачной версии)
        self.test_file.write_text("version 2 — local changes")
        os.utime(self.test_file, None)  # обновляем mtime

        # Создаём новый SyncProject (чистый, без кэша сканирования)
        sp2 = SyncProject(self.base_path, self.category, self.project_name, self.token)
        sp2.local_scan()
        sp2.cloud_scan()

        # check_overwrite_safety для load должна вернуть False
        result = sp2.check_overwrite_safety("load", force=False)
        assert result is False, (
            "Ожидалась блокировка: локальный файл новее облачного, "
            "а команда load перезапишет его"
        )

    def test_force_flag_bypasses_block(self):
        """С force=True блокировка снимается."""
        sp = SyncProject(self.base_path, self.category, self.project_name, self.token)
        sp.sync_save(force=False)

        time.sleep(1)
        self.test_file.write_text("version 2 — local changes")
        os.utime(self.test_file, None)

        sp2 = SyncProject(self.base_path, self.category, self.project_name, self.token)
        sp2.local_scan()
        sp2.cloud_scan()

        result = sp2.check_overwrite_safety("load", force=True)
        assert result is True
