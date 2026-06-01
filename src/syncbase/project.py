import concurrent.futures
import fnmatch
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Any

from .item import SyncItem
from .client import YandexDiskClient

THREADS_COUNT = 16


def _to_timestamp(dt: datetime) -> float:
    """Конвертирует datetime (naive или aware) в POSIX timestamp для сравнения."""
    if dt.tzinfo is not None:
        return dt.timestamp()
    # naive datetime — интерпретируем как локальное время
    return dt.astimezone().timestamp()


class SyncIgnore:
    """Класс для обработки .syncignore файлов (аналог .gitignore)."""

    def __init__(self, rules_text: str = ""):
        self.rules: List[dict] = []
        self.parse_rules(rules_text)

    def parse_rules(self, rules_text: str):
        self.rules = []
        if not rules_text:
            return

        for line in rules_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            negate = line.startswith("!")
            if negate:
                line = line[1:]

            if line.startswith("/"):
                line = line[1:]

            self.rules.append(
                {
                    "pattern": line,
                    "negate": negate,
                    "is_directory": line.endswith("/"),
                    "absolute": not ("*" in line or "?" in line or "[" in line),
                }
            )

    def should_ignore(self, file_path: str, is_directory: bool = False) -> bool:
        if not self.rules or not file_path:
            return False

        path = file_path.replace("\\", "/").lstrip("/")
        ignored = False

        for rule in self.rules:
            pattern = rule["pattern"]
            if rule["is_directory"] and not is_directory:
                continue
            if pattern.endswith("/"):
                pattern = pattern[:-1]

            matched = False
            if rule["absolute"]:
                matched = path == pattern or path.startswith(pattern + "/")
            else:
                matched = fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(
                    os.path.basename(path), pattern
                )
                path_parts = path.split("/")
                for i in range(len(path_parts)):
                    subpath = "/".join(path_parts[: i + 1])
                    if fnmatch.fnmatch(subpath, pattern):
                        matched = True
                        break

            if matched:
                ignored = False if rule["negate"] else True

        return ignored


class SyncProject:
    """Класс для синхронизации отдельного проекта."""

    syncignore: SyncIgnore
    yandex_disk_client: YandexDiskClient
    sync_items: Dict[str, SyncItem]
    items_need_for_update: Dict[str, Dict[str, List[SyncItem]]]

    def __init__(
        self,
        base_path: Path | str,
        category_name: str,
        project_name: str,
        token: str,
    ):
        relative_path = os.path.join(category_name, project_name)

        self.yandex_disk_client = YandexDiskClient(token)
        self.syncignore = SyncIgnore()
        self.sync_items = {}

        self.local_path = Path(base_path) / relative_path
        self.cloud_path = Path("app:") / relative_path
        self.relative_path = relative_path

        self.items_need_for_update = {
            "empty": {"empty": [], "file": [], "dir": []},
            "file": {"empty": [], "file": [], "dir": []},
            "dir": {"empty": [], "file": [], "dir": []},
        }

    @property
    def token(self) -> str:
        return self.yandex_disk_client.token

    def __str__(self):
        return f"<{self.relative_path}>"

    def __repr__(self):
        return f"<SyncProject {self.relative_path}>"

    def create_item(self, relative_path: str) -> SyncItem:
        if relative_path:
            return SyncItem(
                self.local_path / relative_path,
                self.cloud_path / relative_path,
                self.token,
            )
        return SyncItem(self.local_path, self.cloud_path, self.token)

    # ------------------------------------------------------------------ #
    #  Сканирование                                                        #
    # ------------------------------------------------------------------ #

    def local_scan(self):
        root_dir = self.create_item("")
        root_dir.calc_local_state()

        if root_dir.local_type == "empty":
            root_dir.create_local_dir()
        elif root_dir.local_type == "file":
            raise FileExistsError(f"{root_dir} is file!")

        syncignore_file = self.create_item(".syncignore")
        syncignore_file.calc_local_state()

        if syncignore_file.local_type == "empty":
            syncignore_file.local_path.touch()
            example_path = Path(__file__).parent / ".syncignore.example"
            if example_path.exists():
                syncignore_file.local_path.write_text(
                    example_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            else:
                syncignore_file.local_path.write_text(".git\n", encoding="utf-8")
            syncignore_file.local_type = "file"
        elif syncignore_file.local_type == "dir":
            raise FileExistsError(f"{syncignore_file} is dir!")

        syncignore_text = ""
        if syncignore_file.local_type == "file":
            try:
                syncignore_text = syncignore_file.local_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"⚠️ Не удалось прочитать .syncignore: {e}")
        self.syncignore.parse_rules(syncignore_text)

        print("🔍 Сканируем локальные файлы")
        local_start = time.time()
        self._scan_local_items("")
        local_time = time.time() - local_start
        print(f"  ✅ Локальные файлы просканированы за {local_time:.3f} сек")

    def cloud_scan(self):
        print("🔍 Сканирую удаленные файлы...")
        cloud_start = time.time()
        self._scan_cloud_items_parallel()
        cloud_time = time.time() - cloud_start
        print(f"  ✅ Удаленные файлы просканированы за {cloud_time:.3f} сек")

        for sync_item in self.sync_items.values():
            if (
                sync_item.local_type != sync_item.cloud_type
                or sync_item.local_state.md5 != sync_item.cloud_state.md5
            ):
                self.items_need_for_update[sync_item.local_type][sync_item.cloud_type].append(
                    sync_item
                )

        total_sync_objects = sum(
            len(items)
            for local_type_dict in self.items_need_for_update.values()
            for items in local_type_dict.values()
        )
        print(f"🔢 Всего объектов: {len(self.sync_items)}")
        print(f"📊 Требуют синхронизации: {total_sync_objects}")
        print(f"⚡ Время сканирования по API: {cloud_time:.3f}с")

    def _scan_local_items(self, current_path: str):
        local_dir_path = self.local_path / current_path
        if not local_dir_path.exists():
            return

        for item in local_dir_path.iterdir():
            relative_path = (
                os.path.join(current_path, item.name) if current_path else item.name
            )
            if self.syncignore.should_ignore(relative_path, item.is_dir()):
                continue

            if relative_path not in self.sync_items:
                self.sync_items[relative_path] = self.create_item(relative_path)

            self.sync_items[relative_path].calc_local_state()

            if self.sync_items[relative_path].local_type == "dir":
                self._scan_local_items(relative_path)

    def _scan_cloud_items_parallel(self):
        items_lock = threading.Lock()
        folders_queue_lock = threading.Lock()
        scanned_folders: set = set()
        folders_to_scan: List[str] = []

        def process_folder_items(folder_path: str, items: list):
            with folders_queue_lock:
                if folder_path in scanned_folders:
                    return
                scanned_folders.add(folder_path)

            subfolders_found = []
            for item in items:
                item_name = item["name"]
                relative_path: str = (
                    os.path.join(folder_path, item_name) if folder_path else item_name
                )
                is_dir = item["type"] == "dir"

                if self.syncignore.should_ignore(relative_path, is_dir):
                    continue

                with items_lock:
                    if relative_path not in self.sync_items:
                        self.sync_items[relative_path] = self.create_item(relative_path)
                    self.sync_items[relative_path].cloud_state.from_dict(item)

                if is_dir:
                    subfolders_found.append(relative_path)

            with folders_queue_lock:
                for subfolder in subfolders_found:
                    if subfolder not in scanned_folders:
                        folders_to_scan.append(subfolder)

        def scan_single_folder(folder_path: str) -> bool:
            try:
                items = self.yandex_disk_client.list(self.cloud_path / folder_path)
                process_folder_items(folder_path, items)
                return True
            except Exception as e:
                print(f"    ⚠️ Ошибка сканирования папки {folder_path}: {e}")
                return False

        folders_to_scan.append("")
        total_scanned = 0

        while folders_to_scan:
            with folders_queue_lock:
                current_batch = folders_to_scan[:]
                folders_to_scan.clear()

            if not current_batch:
                break

            max_workers = min(THREADS_COUNT, len(current_batch))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(scan_single_folder, folder) for folder in current_batch
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        if future.result():
                            total_scanned += 1
                    except Exception as e:
                        print(f"    ❌ Ошибка в потоке: {e}")

            print(f"    📊 Просканировано папок: {total_scanned}")

        print(f"    ✅ Всего просканировано папок: {total_scanned}")

    # ------------------------------------------------------------------ #
    #  Кэш                                                                 #
    # ------------------------------------------------------------------ #

    def set_cache(self):
        cache_data_files: Dict[str, dict] = {}
        cache_data_dirs: Dict[str, dict] = {}
        total_size = 0

        for relative_path, sync_item in self.sync_items.items():
            if sync_item.local_type == "file":
                cache_data_files[relative_path] = sync_item.local_state.to_dict()
                total_size += sync_item.local_state.size
            elif sync_item.local_type == "dir":
                cache_data_dirs[relative_path] = sync_item.local_state.to_dict()

        cache_data = {
            "project_info": {
                "local_path": str(self.local_path),
                "cloud_path": str(self.cloud_path),
                "cache_version": "1.0",
            },
            "files": cache_data_files,
            "dirs": cache_data_dirs,
            "statistics": {
                "total_files": len(cache_data_files),
                "total_directories": len(cache_data_dirs),
                "total_size": total_size,
            },
        }

        cache_file_path = self.local_path / ".sync_cache"
        try:
            with open(cache_file_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"  ❌ Ошибка создания кэша: {e}")

    def get_cache(self) -> Optional[Dict]:
        cache_file = self.local_path / ".sync_cache"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка чтения кэша проекта {self.cloud_path}: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Статус                                                              #
    # ------------------------------------------------------------------ #

    def show_status(self):
        print(f"\n📊 Статус проекта {str(self)}...")

        cache = self.get_cache()
        if not cache:
            print("❌ Кэш проекта отсутствует. Сохраните проект на облако для создания кэша")
            return

        self.local_scan()

        cache_files = cache.get("files", {})
        cache_dirs = cache.get("dirs", {})
        current_files: Dict[str, dict] = {}
        current_dirs: Dict[str, dict] = {}
        current_total_size = 0

        for relative_path, sync_item in self.sync_items.items():
            if sync_item.local_type == "file":
                current_files[relative_path] = sync_item.local_state.to_dict()
                current_total_size += sync_item.local_state.size
            elif sync_item.local_type == "dir":
                current_dirs[relative_path] = sync_item.local_state.to_dict()

        new_files = set(current_files) - set(cache_files)
        removed_files = set(cache_files) - set(current_files)
        new_dirs = set(current_dirs) - set(cache_dirs)
        removed_dirs = set(cache_dirs) - set(current_dirs)
        changed_files: set = set()

        for relative_path in set(cache_files) & set(current_files):
            if cache_files[relative_path].get("md5", "") != current_files[relative_path].get("md5", ""):
                changed_files.add(relative_path)

        if not any([new_files, removed_files, new_dirs, removed_dirs, changed_files]):
            print("✅ Проект синхронизирован — изменений не обнаружено")
            return

        print("🔄 Обнаружены изменения:")

        if new_files:
            print(f"\n📁 Новые файлы ({len(new_files)}):")
            for file_path in sorted(new_files):
                print(f"   + {file_path} ({current_files[file_path]['size']} B)")

        if removed_files:
            print(f"\n🗑️ Удаленные файлы ({len(removed_files)}):")
            for file_path in sorted(removed_files):
                print(f"   - {file_path} ({cache_files[file_path]['size']} B)")

        if new_dirs:
            print(f"\n📂 Новые папки ({len(new_dirs)}):")
            for dir_path in sorted(new_dirs):
                print(f"   + {dir_path}/")

        if removed_dirs:
            print(f"\n🗑️ Удаленные папки ({len(removed_dirs)}):")
            for dir_path in sorted(removed_dirs):
                print(f"   - {dir_path}/")

        if changed_files:
            print(f"\n📝 Измененные файлы ({len(changed_files)}):")
            for file_path in sorted(changed_files):
                print(f"   ~ {file_path} ({current_files[file_path]['size']} B)")

        print("\n💡 Сохраните проект на облако для синхронизации изменений")

    # ------------------------------------------------------------------ #
    #  Защита от перезаписи более новых файлов                            #
    # ------------------------------------------------------------------ #

    def check_overwrite_safety(
        self,
        direction: Literal["save", "load"],
        force: bool,
    ) -> bool:
        """
        Проверяет, не будут ли потеряны данные при синхронизации.

        Два класса опасных операций:

        1. Перезапись более новой версии более старой (конфликт версий):
             - load: локальная версия файла НОВЕЕ облачной;
             - save: облачная версия файла НОВЕЕ локальной.

        2. Удаление уникальных данных, существующих только с одной стороны:
             - load: файл есть только локально — будет УДАЛЁН (например, новый
               файл, добавленный в проект перед ошибочным `load`);
             - save: файл есть только в облаке — будет УДАЛЁН с диска.

        Returns:
            True  — безопасно продолжать (или force=True при найденных проблемах).
            False — обнаружена потенциальная потеря данных и force=False.
        """
        _SYSTEM_FILES = {".syncignore", ".sync_cache"}

        def _is_system(item: SyncItem) -> bool:
            return item.local_path.name in _SYSTEM_FILES

        overwrites: List[SyncItem] = []
        deletions: List[SyncItem] = []

        # --- Конфликты версий (файл существует с обеих сторон) ---
        for item in self.items_need_for_update["file"]["file"]:
            if _is_system(item):
                continue
            local_ts = _to_timestamp(item.local_state.modified)
            cloud_ts = _to_timestamp(item.cloud_state.modified)
            if direction == "load" and local_ts > cloud_ts:
                overwrites.append(item)
            elif direction == "save" and cloud_ts > local_ts:
                overwrites.append(item)

        # --- Удаление уникальных файлов ---
        if direction == "load":
            # Файлы, существующие только локально, будут стёрты при load.
            unique_items = self.items_need_for_update["file"]["empty"]
        else:
            # Файлы, существующие только в облаке, будут стёрты при save.
            unique_items = self.items_need_for_update["empty"]["file"]

        for item in unique_items:
            if _is_system(item):
                continue
            deletions.append(item)

        dangerous = overwrites + deletions
        if not dangerous:
            return True

        action_label = (
            "load (диск → локально)" if direction == "load" else "save (локально → диск)"
        )
        print(f"⚠️ Обнаружена потенциальная потеря данных! ⚠️")
        print(f"Команда '{action_label}':\n")

        if overwrites:
            print("  Перезапишет более новые файлы более старыми:")
            for item in sorted(overwrites, key=lambda x: str(x.local_path)):
                rel = os.path.relpath(str(item.local_path), str(self.local_path))
                local_ts = item.local_state.modified.strftime("%Y-%m-%d %H:%M:%S")
                cloud_ts = item.cloud_state.modified.strftime("%Y-%m-%d %H:%M:%S")
                if direction == "load":
                    print(f"    🔴 {rel}")
                    print(f"         локальный : {local_ts}  ← НОВЕЕ (будет ПЕРЕЗАПИСАН)")
                    print(f"         на диске  : {cloud_ts}")
                else:
                    print(f"    🔴 {rel}")
                    print(f"         на диске  : {cloud_ts}  ← НОВЕЕ (будет ПЕРЕЗАПИСАН)")
                    print(f"         локальный : {local_ts}")
            print()

        if deletions:
            where = "локально" if direction == "load" else "в облаке"
            print(f"  Удалит файлы, существующие только {where}:")
            for item in sorted(deletions, key=lambda x: str(x.local_path)):
                rel = os.path.relpath(str(item.local_path), str(self.local_path))
                print(f"    🟠 {rel}  ← будет УДАЛЁН ({where})")
            print()

        if force:
            print("⚠️  [FORCE] Принудительная операция разрешена флагом -f / --force.")
            print("     Затронутые файлы помечены [FORCE] в логе ниже.")
            # Сохраняем id опасных элементов для маркировки в логе
            self._force_overwrite_ids = {id(item) for item in dangerous}
            return True

        print("❌ Операция заблокирована.")
        print(f"   Для принудительной операции используйте флаг -f / --force.")
        print(f"   Пример: syncbase {direction} -f")
        return False

    # ------------------------------------------------------------------ #
    #  Синхронизация                                                       #
    # ------------------------------------------------------------------ #

    def sync_load(self, force: bool = False):
        """Загрузить состояние проекта с облака в локальную папку."""
        self._force_overwrite_ids: set = set()
        self.local_scan()
        self.cloud_scan()

        if not self.check_overwrite_safety("load", force):
            sys.exit(1)

        def async_remove_local(sync_item: SyncItem):
            sync_item.remove_local()

        self.multythread_operation(
            async_remove_local,
            *self.items_need_for_update["file"]["empty"],
            *self.items_need_for_update["file"]["dir"],
            *self.items_need_for_update["dir"]["empty"],
            *self.items_need_for_update["dir"]["file"],
        )

        def async_create_dir_local(sync_item: SyncItem):
            sync_item.create_local_dir()

        self.multythread_operation(
            async_create_dir_local,
            *self.items_need_for_update["empty"]["dir"],
            *self.items_need_for_update["file"]["dir"],
        )

        def asafe_download_file(sync_item: SyncItem):
            if id(sync_item) in self._force_overwrite_ids:
                print(f"⚠️  [FORCE] Перезапись: {sync_item.local_path.name}")
            sync_item.download_file()

        self.multythread_operation(
            asafe_download_file,
            *self.items_need_for_update["empty"]["file"],
            *self.items_need_for_update["dir"]["file"],
            *self.items_need_for_update["file"]["file"],
        )

    def sync_save(self, force: bool = False):
        """Сохранить локальный проект в облако."""
        self._force_overwrite_ids: set = set()
        print(f"⬆️  Начинаем сохранение проекта {str(self)}...")

        self.local_scan()
        self.set_cache()
        self.cloud_scan()

        if not self.check_overwrite_safety("save", force):
            sys.exit(1)

        def async_remove_cloud(sync_item: SyncItem):
            sync_item.remove_cloud()

        self.multythread_operation(
            async_remove_cloud,
            *self.items_need_for_update["empty"]["file"],
            *self.items_need_for_update["empty"]["dir"],
            *self.items_need_for_update["file"]["dir"],
            *self.items_need_for_update["dir"]["file"],
        )

        def async_create_cloud_dir(sync_item: SyncItem):
            sync_item.create_cloud_dir()

        self.multythread_operation(
            async_create_cloud_dir,
            *self.items_need_for_update["dir"]["empty"],
            *self.items_need_for_update["dir"]["file"],
        )

        def async_upload_file(sync_item: SyncItem):
            if id(sync_item) in self._force_overwrite_ids:
                print(f"⚠️  [FORCE] Перезапись: {sync_item.cloud_path.name}")
            sync_item.upload_file()

        self.multythread_operation(
            async_upload_file,
            *self.items_need_for_update["file"]["empty"],
            *self.items_need_for_update["file"]["dir"],
            *self.items_need_for_update["file"]["file"],
        )

    # ------------------------------------------------------------------ #
    #  Многопоточные операции                                             #
    # ------------------------------------------------------------------ #

    def multythread_operation(self, handler: Callable, *items: Any, reverse: bool = False):
        if not items:
            return

        max_workers = min(THREADS_COUNT, len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: List[concurrent.futures.Future] = [
                executor.submit(handler, item)
                for item in sorted(items, key=lambda x: x.cloud_path, reverse=reverse)
            ]
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                future.result()
                completed += 1
                if completed % THREADS_COUNT == 0 or completed == len(futures):
                    print(f"    📊 Обработано объектов: {completed}/{len(futures)}")
