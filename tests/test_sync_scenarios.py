#!/usr/bin/env python3
"""
Интеграционные сценарные тесты двусторонней синхронизации.

Моделируют типичные ситуации работы с двумя локальными копиями одного
облачного проекта (как `tests/test_base1` и `tests/test_base2`, которые
ведут к одному и тому же хранилищу, но локально различны).

Каждый тест создаёт ВРЕМЕННЫЙ уникальный проект в облаке и удаляет его
после завершения, поэтому реальные `test_project` хранилищ не затрагиваются.

Требует:
  - сети и валидного токена (берётся из tests/test_base1/.syncbase);
  - реального доступа к Яндекс.Диску.

Проверяемые сценарии:
  1. Round-trip: save из A → load в B даёт идентичное дерево.
  2. Изменение файла в A распространяется в B (cloud новее → безопасный load).
  3. Удаление файла в A распространяется в B (через save -f и load -f:
     удалённый на одной стороне файл локально неотличим от нового, поэтому
     load защищает его и требует -f для осознанного распространения).
  4. НОВЫЙ локальный файл в B защищён от стирания при ошибочном `load`
     (главный сценарий потери данных): без -f операция блокируется,
     файл сохраняется; с -f файл удаляется.
  5. Конфликт версий: локальный файл в B новее облачного — load без -f
     блокируется, с -f перезаписывается.
  6. Конфликт версий: облачный файл новее локального — save без -f
     блокируется.
  7. Системные файлы (.syncignore / .sync_cache) не блокируют load,
     даже если локально новее.
"""

import os
import sys
import time
import uuid
import pytest
from pathlib import Path

# Добавляем src/ в путь для import без установки пакета
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from syncbase.resolver import find_storage, STORAGE_KEY_FILE
from syncbase.project import SyncProject
from syncbase.client import YandexDiskClient

TEST_BASE = Path(__file__).parent / "test_base1"
SYSTEM_FILES = {".syncignore", ".sync_cache"}


# ------------------------------------------------------------------ #
#  Вспомогательные функции                                            #
# ------------------------------------------------------------------ #

def _get_token() -> str:
    result = find_storage(TEST_BASE)
    if result is None:
        pytest.skip(f"Не найден {STORAGE_KEY_FILE} в {TEST_BASE}")
    _, token = result
    return token


def _write(base_dir: Path, rel: str, content: str, mtime: float | None = None) -> Path:
    """Создаёт файл внутри проекта с указанным содержимым (и опц. mtime)."""
    path = base_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _list_files(proj_dir: Path) -> dict[str, str]:
    """Возвращает {относительный_путь: содержимое} без системных файлов."""
    result: dict[str, str] = {}
    for p in proj_dir.rglob("*"):
        if p.is_file() and p.name not in SYSTEM_FILES:
            result[str(p.relative_to(proj_dir))] = p.read_text(encoding="utf-8")
    return result


# ------------------------------------------------------------------ #
#  Фикстура окружения: токен, два локальных base, уникальный облачный  #
#  проект и его очистка после теста.                                   #
# ------------------------------------------------------------------ #

class Env:
    def __init__(self, token: str, base_a: Path, base_b: Path,
                 category: str, project: str):
        self.token = token
        self.base_a = base_a
        self.base_b = base_b
        self.category = category
        self.project = project
        self.proj_a = base_a / category / project
        self.proj_b = base_b / category / project

    def make_a(self) -> SyncProject:
        return SyncProject(self.base_a, self.category, self.project, self.token)

    def make_b(self) -> SyncProject:
        return SyncProject(self.base_b, self.category, self.project, self.token)


@pytest.fixture
def env(tmp_path: Path):
    token = _get_token()
    base_a = tmp_path / "base_a"
    base_b = tmp_path / "base_b"
    base_a.mkdir()
    base_b.mkdir()

    category = "tmp_scenarios"
    project = f"sc_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    e = Env(token, base_a, base_b, category, project)
    try:
        yield e
    finally:
        # Удаляем временный облачный проект целиком
        client = YandexDiskClient(token)
        cloud_project = Path("app:") / category / project
        try:
            client.remove(cloud_project)
        except Exception as exc:  # pragma: no cover - очистка best-effort
            print(f"⚠️ Не удалось удалить облачный проект {cloud_project}: {exc}")


# ------------------------------------------------------------------ #
#  1. Round-trip: save A → load B                                      #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_roundtrip_save_then_load(env: Env):
    _write(env.proj_a, "README.md", "hello world")
    _write(env.proj_a, "src/main.py", "print('hi')")
    _write(env.proj_a, "docs/api.md", "# API")

    env.make_a().sync_save()
    env.make_b().sync_load()

    files_a = _list_files(env.proj_a)
    files_b = _list_files(env.proj_b)

    assert files_b == files_a
    assert files_b["README.md"] == "hello world"
    assert files_b["src/main.py"] == "print('hi')"


# ------------------------------------------------------------------ #
#  2. Изменение файла в A распространяется в B                         #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_modification_propagates_to_b(env: Env):
    _write(env.proj_a, "file.txt", "version 1")
    env.make_a().sync_save()
    env.make_b().sync_load()
    assert _list_files(env.proj_b)["file.txt"] == "version 1"

    # Изменяем файл в A (делая его новее), сохраняем
    future = time.time() + 5
    _write(env.proj_a, "file.txt", "version 2", mtime=future)
    env.make_a().sync_save()

    # В B файл старый → облако новее → load безопасен (без -f)
    env.make_b().sync_load()
    assert _list_files(env.proj_b)["file.txt"] == "version 2"


# ------------------------------------------------------------------ #
#  3. Удаление файла в A распространяется в B (через -f)               #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_deletion_propagates_to_b(env: Env):
    _write(env.proj_a, "keep.txt", "keep")
    _write(env.proj_a, "remove.txt", "remove")
    env.make_a().sync_save()
    env.make_b().sync_load()
    assert "remove.txt" in _list_files(env.proj_b)

    # Удаляем файл в A локально и сохраняем.
    # На облаке файл существует → save удалит его (защита от удаления
    # уникального облачного файла требует -f для осознанного действия).
    (env.proj_a / "remove.txt").unlink()
    env.make_a().sync_save(force=True)

    # Теперь в B файл remove.txt существует только локально (на облаке его
    # больше нет). С точки зрения B это неотличимо от нового локального
    # файла, поэтому load без -f защищает его от удаления.
    with pytest.raises(SystemExit):
        env.make_b().sync_load()
    assert "remove.txt" in _list_files(env.proj_b)

    # Осознанное распространение удаления — load с -f.
    env.make_b().sync_load(force=True)
    files_b = _list_files(env.proj_b)
    assert "remove.txt" not in files_b
    assert files_b.get("keep.txt") == "keep"


# ------------------------------------------------------------------ #
#  4. ГЛАВНЫЙ СЦЕНАРИЙ: новый локальный файл защищён при load          #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_new_local_file_blocks_load(env: Env):
    _write(env.proj_a, "main.py", "code")
    env.make_a().sync_save()
    env.make_b().sync_load()

    # Пользователь добавил НОВЫЙ файл в B и ошибочно делает load.
    new_file = _write(env.proj_b, "new_work.py", "важная новая работа")

    # Без -f операция должна блокироваться, а файл — сохраниться.
    with pytest.raises(SystemExit):
        env.make_b().sync_load()
    assert new_file.exists(), "Новый локальный файл не должен быть удалён без -f"
    assert new_file.read_text(encoding="utf-8") == "важная новая работа"

    # С -f пользователь осознанно удаляет уникальный файл.
    env.make_b().sync_load(force=True)
    assert not new_file.exists()


# ------------------------------------------------------------------ #
#  5. Конфликт: локальный файл в B новее облачного → load блокируется  #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_newer_local_conflict_blocks_load(env: Env):
    _write(env.proj_a, "doc.txt", "cloud content")
    env.make_a().sync_save()
    env.make_b().sync_load()

    # Изменяем файл в B и делаем его новее облачного.
    future = time.time() + 100
    _write(env.proj_b, "doc.txt", "local newer content", mtime=future)

    # load без -f должен блокироваться, локальные изменения сохранены.
    with pytest.raises(SystemExit):
        env.make_b().sync_load()
    assert _list_files(env.proj_b)["doc.txt"] == "local newer content"

    # load с -f перезаписывает локальную версию облачной.
    env.make_b().sync_load(force=True)
    assert _list_files(env.proj_b)["doc.txt"] == "cloud content"


# ------------------------------------------------------------------ #
#  6. Конфликт: облачный файл новее локального → save блокируется      #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_newer_cloud_conflict_blocks_save(env: Env):
    _write(env.proj_a, "doc.txt", "v1")
    env.make_a().sync_save()
    env.make_b().sync_load()

    # A обновляет файл (новее) и сохраняет — облако становится новее.
    future = time.time() + 100
    _write(env.proj_a, "doc.txt", "cloud v2", mtime=future)
    env.make_a().sync_save()

    # В B меняем тот же файл, но делаем его СТАРЕЕ облачного.
    past = time.time() - 10_000
    _write(env.proj_b, "doc.txt", "local stale", mtime=past)

    # save из B без -f должен блокироваться (затёр бы более новое облако).
    with pytest.raises(SystemExit):
        env.make_b().sync_save()


# ------------------------------------------------------------------ #
#  7. Системные файлы не блокируют load даже если новее               #
# ------------------------------------------------------------------ #

@pytest.mark.integration
def test_system_files_do_not_block_load(env: Env):
    _write(env.proj_a, "main.py", "code")
    env.make_a().sync_save()
    env.make_b().sync_load()

    # Делаем .syncignore в B новее и с другим содержимым.
    future = time.time() + 100
    syncignore_b = env.proj_b / ".syncignore"
    syncignore_b.write_text("# изменено локально\n*.tmp\n", encoding="utf-8")
    os.utime(syncignore_b, (future, future))

    # load НЕ должен блокироваться из-за системного файла.
    env.make_b().sync_load()  # отсутствие SystemExit = успех
