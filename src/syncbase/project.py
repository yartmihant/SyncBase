import concurrent.futures
from collections import deque
import fnmatch
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from .item import SyncItem
from .client import TEMP_UPLOAD_SUFFIX, YandexDiskClient

THREADS_COUNT = 16
LOCAL_ONLY_FILES = {".syncbase", ".sync_cache"}
SYSTEM_FILES = LOCAL_ONLY_FILES | {".syncignore"}


def _is_local_only(name: str) -> bool:
    return name in LOCAL_ONLY_FILES or (
        name.startswith(".sync_cache.") and name.endswith(".tmp")
    )


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

            anchored = line.startswith("/")
            if anchored:
                line = line[1:]

            self.rules.append(
                {
                    "pattern": line,
                    "negate": negate,
                    "is_directory": line.endswith("/"),
                    "absolute": not ("*" in line or "?" in line or "[" in line),
                    "anchored": anchored,
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
                if not rule["anchored"] and "/" not in pattern:
                    matched = matched or pattern in path.split("/")
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
    """Проект внутри одноуровневого вольта."""

    syncignore: SyncIgnore
    yandex_disk_client: YandexDiskClient
    sync_items: Dict[str, SyncItem]
    items_need_for_update: Dict[str, Dict[str, List[SyncItem]]]

    def __init__(self, vault_path: Path | str, project_name: str, token: str):
        self.yandex_disk_client = YandexDiskClient(token)
        self.syncignore = SyncIgnore()

        self.vault_path = Path(vault_path)
        self.name = project_name
        self.local_path = self.vault_path / project_name
        self.cloud_path = Path("app:") / project_name

        # Separate from the saved baseline: status must not acknowledge changes.
        self._local_index: Dict[str, dict] = {}

        self._reset_scan_state()

    def _reset_scan_state(self) -> None:
        """Очистить результаты предыдущего прохода перед новым сканированием."""
        self.sync_items = {}
        self.stale_cloud_temp_paths: List[Path] = []
        self.rename_pairs: List[Tuple[SyncItem, SyncItem]] = []
        self._rename_direction: Optional[Literal["save", "load"]] = None
        self._completed_rename_item_ids: set[int] = set()
        self._force_overwrite_ids: set[int] = set()
        self.items_need_for_update = {
            "empty": {"empty": [], "file": [], "dir": []},
            "file": {"empty": [], "file": [], "dir": []},
            "dir": {"empty": [], "file": [], "dir": []},
        }

    @property
    def token(self) -> str:
        return self.yandex_disk_client.token

    def __str__(self):
        return f"<{self.name}>"

    def __repr__(self):
        return f"<SyncProject {self.name}>"

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
        syncignore_file.local_type = (
            "dir" if syncignore_file.local_path.is_dir()
            else "file" if syncignore_file.local_path.exists() else "empty"
        )

        if syncignore_file.local_type == "empty":
            default_rules = files("syncbase").joinpath(".syncignore.example").read_text(
                encoding="utf-8"
            )
            syncignore_file.local_path.write_text(default_rules, encoding="utf-8")
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
        cache = self.get_cache()
        disk_index = cache.get("local_index", {}) if cache else {}
        self._scan_index = dict(disk_index) if isinstance(disk_index, dict) else {}
        self._scan_index.update(self._local_index)
        self._local_index = {}
        self._scan_local_items("")
        self._scan_index = {}
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
        pending = [current_path]
        while pending:
            folder = pending.pop()
            with os.scandir(self.local_path / folder) as entries:
                for entry in entries:
                    if _is_local_only(entry.name):
                        continue
                    relative_path = os.path.join(folder, entry.name) if folder else entry.name
                    is_dir = entry.is_dir()
                    if self.syncignore.should_ignore(relative_path, is_dir):
                        continue
                    sync_item = self.sync_items.get(relative_path)
                    if sync_item is None:
                        sync_item = self.create_item(relative_path)
                        self.sync_items[relative_path] = sync_item
                    # Windows DirEntry.stat() omits file identity (ino/dev).
                    stat = os.stat(entry.path) if os.name == "nt" and not is_dir else entry.stat()
                    sync_item.calc_local_state(self._scan_index.get(relative_path), stat)
                    if sync_item.local_type == "dir":
                        pending.append(relative_path)
                    elif sync_item.local_type == "file":
                        self._local_index[relative_path] = {
                            "signature": sync_item.local_signature,
                            "md5": sync_item.local_state.md5,
                        }

    def _scan_cloud_items_parallel(self):
        # One pool for the entire tree; children can start as soon as their
        # parent finishes, without waiting for slow siblings on the same level.
        scheduled = {""}
        folders_to_scan = deque([""])

        def process_folder_items(folder_path: str, items: list):
            for item in items:
                item_name = item["name"]
                if _is_local_only(item_name):
                    continue
                relative_path: str = (
                    os.path.join(folder_path, item_name) if folder_path else item_name
                )
                is_dir = item["type"] == "dir"

                # upload() сначала пишет `<target>.tmp`, затем переименовывает.
                # После сетевого сбоя такой cloud-only файл не является данными
                # пользователя и не должен участвовать в защите от перезаписи.
                # Если одноимённый локальный *.tmp существует, считаем его
                # обычным пользовательским файлом и синхронизируем.
                if (
                    not is_dir
                    and item_name.endswith(TEMP_UPLOAD_SUFFIX)
                    and relative_path not in self.sync_items
                ):
                    self.stale_cloud_temp_paths.append(self.cloud_path / relative_path)
                    continue

                if self.syncignore.should_ignore(relative_path, is_dir):
                    continue

                if relative_path not in self.sync_items:
                    self.sync_items[relative_path] = self.create_item(relative_path)
                self.sync_items[relative_path].cloud_state.from_dict(item)

                if is_dir and relative_path not in scheduled:
                    scheduled.add(relative_path)
                    folders_to_scan.append(relative_path)

        total_scanned = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS_COUNT) as executor:
            pending: Dict[concurrent.futures.Future, str] = {}
            while folders_to_scan or pending:
                while folders_to_scan and len(pending) < THREADS_COUNT:
                    folder = folders_to_scan.popleft()
                    pending[executor.submit(
                        self.yandex_disk_client.list, self.cloud_path / folder
                    )] = folder
                done, _ = concurrent.futures.wait(
                    pending, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    folder = pending.pop(future)
                    # An incomplete tree must not be used for destructive sync.
                    process_folder_items(folder, future.result())
                    total_scanned += 1
                if total_scanned % THREADS_COUNT == 0:
                    print(f"    📊 Просканировано папок: {total_scanned}")

        print(f"    ✅ Всего просканировано папок: {total_scanned}")
        if self.stale_cloud_temp_paths:
            print(
                "    🧹 Найдено служебных tmp-файлов: "
                f"{len(self.stale_cloud_temp_paths)}"
            )

    # ------------------------------------------------------------------ #
    #  Распознавание переименований                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _content_key(item: SyncItem, side: Literal["local", "cloud"]):
        state = item.local_state if side == "local" else item.cloud_state
        if not state.md5:
            return None
        return state.md5, state.size

    def detect_renames(
        self,
        direction: Literal["save", "load"],
    ) -> List[Tuple[SyncItem, SyncItem]]:
        """Сопоставить удалённые и добавленные файлы по MD5 и размеру.

        В каждой паре первый элемент — существующий источник, второй — новый
        путь назначения в направлении текущей синхронизации.
        """
        if self._rename_direction == direction:
            return self.rename_pairs

        self._rename_direction = direction
        self.rename_pairs = []
        self._completed_rename_item_ids = set()

        if direction == "save":
            sources = self.items_need_for_update["empty"]["file"]
            targets = self.items_need_for_update["file"]["empty"]
            source_side: Literal["local", "cloud"] = "cloud"
            target_side: Literal["local", "cloud"] = "local"
        else:
            sources = self.items_need_for_update["file"]["empty"]
            targets = self.items_need_for_update["empty"]["file"]
            source_side = "local"
            target_side = "cloud"

        sources_by_content: Dict[tuple, List[SyncItem]] = {}
        targets_by_content: Dict[tuple, List[SyncItem]] = {}

        for item in sources:
            key = self._content_key(item, source_side)
            if key is not None and item.local_path.name not in SYSTEM_FILES:
                sources_by_content.setdefault(key, []).append(item)
        for item in targets:
            key = self._content_key(item, target_side)
            if key is not None and item.local_path.name not in SYSTEM_FILES:
                targets_by_content.setdefault(key, []).append(item)

        for key in sorted(set(sources_by_content) & set(targets_by_content)):
            matching_sources = sorted(
                sources_by_content[key], key=lambda item: str(item.local_path)
            )
            matching_targets = sorted(
                targets_by_content[key], key=lambda item: str(item.local_path)
            )
            self.rename_pairs.extend(zip(matching_sources, matching_targets))

        if self.rename_pairs:
            print(f"🔄 Обнаружено переименований: {len(self.rename_pairs)}")
        return self.rename_pairs

    def _apply_detected_renames(
        self,
        direction: Literal["save", "load"],
    ) -> None:
        self.detect_renames(direction)
        for source, target in self.rename_pairs:
            if direction == "save":
                parent = target.cloud_path.parent
                if not self.yandex_disk_client.exists(parent):
                    if not self.yandex_disk_client.create_dir(parent):
                        raise RuntimeError(
                            f"Не удалось подготовить папку для переименования: {parent}"
                        )
                success = self.yandex_disk_client.move(
                    source.cloud_path,
                    target.cloud_path,
                    overwrite=False,
                )
            else:
                try:
                    target.local_path.parent.mkdir(parents=True, exist_ok=True)
                    if target.local_path.exists():
                        raise FileExistsError(
                            f"целевой путь уже существует: {target.local_path}"
                        )
                    source.local_path.rename(target.local_path)
                    success = True
                except OSError as exc:
                    print(
                        f"❌ Не удалось переименовать локальный файл "
                        f"{source.local_path} → {target.local_path}: {exc}"
                    )
                    success = False

            if not success:
                raise RuntimeError(
                    "Переименование не выполнено; синхронизация остановлена до "
                    "удаления или передачи файлов."
                )

            self._completed_rename_item_ids.update((id(source), id(target)))
            old_path = source.cloud_path if direction == "save" else source.local_path
            new_path = target.cloud_path if direction == "save" else target.local_path
            print(f"   🔄 Переименован: {old_path} → {new_path}")

    def _without_completed_renames(self, *items: SyncItem) -> List[SyncItem]:
        return [
            item for item in items if id(item) not in self._completed_rename_item_ids
        ]

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
                "cache_version": "3.0",
            },
            "files": cache_data_files,
            "dirs": cache_data_dirs,
            "local_index": self._local_index,
            "statistics": {
                "total_files": len(cache_data_files),
                "total_directories": len(cache_data_dirs),
                "total_size": total_size,
            },
        }

        self._write_cache(cache_data)

    def _write_cache(self, cache_data: dict) -> None:
        """Publish a complete cache atomically, keeping the old one on failure."""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.local_path,
                prefix=".sync_cache.", suffix=".tmp", delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(cache_data, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.local_path / ".sync_cache")
        except Exception as e:
            print(f"  ❌ Ошибка создания кэша: {e}")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as e:
                    print(f"  ⚠️ Не удалось удалить временный кэш {temp_path}: {e}")

    def get_cache(self) -> Optional[Dict]:
        cache_file = self.local_path / ".sync_cache"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if not isinstance(cache, dict):
                return None
            info = cache.get("project_info", {})
            if not isinstance(info, dict) or info.get("cache_version") not in ("2.0", "3.0"):
                return None
            for section in ("files", "dirs"):
                entries = cache.get(section)
                if not isinstance(entries, dict) or not all(
                    isinstance(value, dict) for value in entries.values()
                ):
                    return None
            # v2 has no stat fingerprints. Keep its status baseline, but rehash.
            if info["cache_version"] != "3.0" or info.get("local_path") != str(self.local_path):
                cache.pop("local_index", None)
            return cache
        except Exception as e:
            print(f"⚠️ Ошибка чтения кэша проекта {self.local_path}: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Статус                                                              #
    # ------------------------------------------------------------------ #

    def show_status(self):
        print(f"\n📊 Статус проекта {str(self)}...")

        cache = self.get_cache()
        if not cache:
            print("❌ Кэш проекта отсутствует. Выполните 'syncbase save' для его создания")
            return

        self._reset_scan_state()
        self.local_scan()

        # Refresh acceleration data only. The files/dirs baseline still refers
        # to the last save/load, so repeated status continues to report changes.
        cache["local_index"] = self._local_index
        cache["project_info"].update(
            cache_version="3.0", local_path=str(self.local_path)
        )
        self._write_cache(cache)

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

        print("\n💡 Выполните 'syncbase save' для синхронизации изменений")

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
        self.detect_renames(direction)
        rename_source_ids = {id(source) for source, _target in self.rename_pairs}

        def _is_system(item: SyncItem) -> bool:
            return item.local_path.name in SYSTEM_FILES or (
                item.local_type == "empty"
                and item.cloud_type == "file"
                and item.cloud_path.name.endswith(TEMP_UPLOAD_SUFFIX)
            )

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
            if _is_system(item) or id(item) in rename_source_ids:
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
                local_label = item.local_state.modified.strftime("%Y-%m-%d %H:%M:%S")
                cloud_label = item.cloud_state.modified.strftime("%Y-%m-%d %H:%M:%S")
                if direction == "load":
                    print(f"    🔴 {rel}")
                    print(f"         локальный : {local_label}  ← НОВЕЕ (будет ПЕРЕЗАПИСАН)")
                    print(f"         на диске  : {cloud_label}")
                else:
                    print(f"    🔴 {rel}")
                    print(f"         на диске  : {cloud_label}  ← НОВЕЕ (будет ПЕРЕЗАПИСАН)")
                    print(f"         локальный : {local_label}")
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
        print(f"   Пример: syncbase {direction} all -f")
        return False

    # ------------------------------------------------------------------ #
    #  Синхронизация                                                       #
    # ------------------------------------------------------------------ #

    def sync_load(self, force: bool = False):
        """Загрузить состояние проекта из облака в локальную папку."""
        self._reset_scan_state()
        self.local_scan()
        self.cloud_scan()

        if not self.check_overwrite_safety("load", force):
            sys.exit(1)

        self._apply_detected_renames("load")

        def async_remove_local(sync_item: SyncItem):
            sync_item.remove_local()

        self.multythread_operation(
            async_remove_local,
            *self._without_completed_renames(
                *self.items_need_for_update["file"]["empty"],
                *self.items_need_for_update["file"]["dir"],
                *self.items_need_for_update["dir"]["empty"],
                *self.items_need_for_update["dir"]["file"],
            ),
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
            *self._without_completed_renames(
                *self.items_need_for_update["empty"]["file"],
                *self.items_need_for_update["dir"]["file"],
                *self.items_need_for_update["file"]["file"],
            ),
        )

        # Кэш локальный и намеренно не загружается из облака. Формируем его по
        # уже восстановленному дереву, чтобы `status all` сразу был полезен.
        self._reset_scan_state()
        self.local_scan()
        self.set_cache()

    def sync_save(self, force: bool = False):
        """Сохранить локальный проект в облако."""
        self._reset_scan_state()
        print(f"⬆️  Начинаем сохранение проекта {str(self)}...")

        self.local_scan()
        self.cloud_scan()

        if not self.check_overwrite_safety("save", force):
            sys.exit(1)

        self._apply_detected_renames("save")

        # Остатки прерванных загрузок безопасно удаляются без --force. Ошибка
        # очистки не мешает save: при следующем запуске они снова будут найдены.
        for temp_path in self.stale_cloud_temp_paths:
            self.yandex_disk_client.remove(temp_path)

        def async_remove_cloud(sync_item: SyncItem):
            sync_item.remove_cloud()

        self.multythread_operation(
            async_remove_cloud,
            *self._without_completed_renames(
                *self.items_need_for_update["empty"]["file"],
                *self.items_need_for_update["empty"]["dir"],
                *self.items_need_for_update["file"]["dir"],
                *self.items_need_for_update["dir"]["file"],
            ),
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
            *self._without_completed_renames(
                *self.items_need_for_update["file"]["empty"],
                *self.items_need_for_update["file"]["dir"],
                *self.items_need_for_update["file"]["file"],
            ),
        )

        # Обновляем status-кэш только после прохождения защиты и выполнения
        # облачных операций. Заблокированный save не должен скрывать изменения.
        self.set_cache()

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
