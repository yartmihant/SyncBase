from datetime import datetime
import hashlib
import os
import stat as stat_module
from pathlib import Path
from typing import Literal, Optional
import shutil

from .client import YandexDiskClient

ItemType = Literal["file", "dir", "empty"]


def _stat_signature(stat: os.stat_result) -> list[int]:
    """Metadata used to invalidate a cached digest (never atime)."""
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


def _hash_local_file(path: Path) -> str:
    md5 = hashlib.md5()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(chunk)
    return md5.hexdigest()


class ItemState:
    path: str
    modified: datetime
    md5: str
    type: ItemType
    size: int

    def __init__(self):
        self.path = ""
        self.modified = datetime.now()
        self.md5 = ""
        self.type = "empty"
        self.size = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "modified": self.modified.isoformat() if self.modified else None,
            "md5": self.md5,
            "type": self.type,
            "size": self.size,
        }

    def from_dict(self, data: Optional[dict]):
        if data:
            self.md5 = data.get("md5", self.md5)
            self.type = data.get("type", self.type)
            self.size = data.get("size", self.size)

            modified = data.get("modified", self.modified)
            if isinstance(modified, datetime):
                self.modified = modified
            elif isinstance(modified, str):
                try:
                    self.modified = datetime.fromisoformat(modified)
                except Exception:
                    pass

    def size_str(self, size_bytes: float) -> str:
        if size_bytes == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB"]
        unit_index = 0
        while size_bytes >= 1024 and unit_index < len(units) - 1:
            size_bytes /= 1024
            unit_index += 1
        return f"{size_bytes:.1f} {units[unit_index]}"

    def __str__(self):
        return f"[[{self.path}({self.type})]]"


class SyncItem:
    """Класс для синхронизации состояния одного файла или папки."""

    yandex_disk_client: YandexDiskClient
    local_state: ItemState
    cloud_state: ItemState

    @property
    def local_path(self) -> Path:
        return Path(self.local_state.path)

    @local_path.setter
    def local_path(self, value: Path):
        self.local_state.path = str(value)

    @property
    def cloud_path(self) -> Path:
        return Path(self.cloud_state.path)

    @cloud_path.setter
    def cloud_path(self, value: Path):
        self.cloud_state.path = str(value)

    @property
    def local_type(self) -> ItemType:
        return self.local_state.type

    @local_type.setter
    def local_type(self, value: ItemType):
        self.local_state.type = value

    @property
    def cloud_type(self) -> ItemType:
        return self.cloud_state.type

    @cloud_type.setter
    def cloud_type(self, value: ItemType):
        self.cloud_state.type = value

    def __init__(self, local_path: str | Path, cloud_path: str | Path, token: str):
        self.local_state = ItemState()
        self.cloud_state = ItemState()
        self.local_path = Path(local_path)
        self.cloud_path = Path(cloud_path)
        self.yandex_disk_client = YandexDiskClient(token)
        self.local_signature: Optional[list[int]] = None

    def __str__(self):
        return f"SyncItem({self.local_state}, {self.cloud_state})"

    def __repr__(self):
        return f"SyncItem({self.local_state}, {self.cloud_state})"

    def calc_local_state(
        self, cached: Optional[dict] = None, stat: Optional[os.stat_result] = None
    ) -> ItemState:
        self.local_signature = None
        self.local_state.md5 = ""
        self.local_state.size = 0
        try:
            if stat is None:
                stat = self.local_path.stat()
        except FileNotFoundError:
            self.local_state.type = "empty"
            return self.local_state

        self.local_state.type = "dir" if stat_module.S_ISDIR(stat.st_mode) else "file"
        if self.local_state.type == "file":
            signature = _stat_signature(stat)
            digest = cached.get("md5") if isinstance(cached, dict) else None
            if (
                isinstance(cached, dict)
                and cached.get("signature") == signature
                and isinstance(digest, str)
                and len(digest) == 32
                and all(char in "0123456789abcdef" for char in digest)
            ):
                self.local_state.md5 = digest
            else:
                # Never associate a hash of changing content with newer metadata.
                for _attempt in range(3):
                    digest = _hash_local_file(self.local_path)
                    after = self.local_path.stat()
                    if _stat_signature(after) == signature:
                        self.local_state.md5 = digest
                        break
                    stat = after
                    signature = _stat_signature(stat)
                else:
                    raise RuntimeError(f"Файл изменяется во время индексации: {self.local_path}")
            self.local_signature = signature
            self.local_state.size = stat.st_size

        self.local_state.modified = datetime.fromtimestamp(stat.st_mtime)
        return self.local_state

    def calc_cloud_state(self) -> ItemState:
        state = self.yandex_disk_client.get_item_state(self.cloud_path)
        self.cloud_state.from_dict(state)
        return self.cloud_state

    def remove_cloud(self):
        self.yandex_disk_client.remove(self.cloud_path)
        self.cloud_state.type = "empty"

    def remove_local(self):
        try:
            if self.local_state.type == "dir":
                shutil.rmtree(self.local_path)
                self.local_state.type = "empty"
            elif self.local_state.type == "file":
                self.local_path.unlink()
                self.local_state.type = "empty"
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Ошибка удаления файла {self.local_path}: {e}")

    def download_file(self):
        self.yandex_disk_client.download(self.cloud_path, self.local_path)

    def upload_file(self):
        self.yandex_disk_client.upload(self.local_path, self.cloud_path)

    def create_local_dir(self):
        try:
            self.local_path.mkdir(parents=True, exist_ok=True)
            self.local_state.type = "dir"
            print(f"📁 Локальная директория создана: {self.local_path}")
        except Exception as e:
            print(f"❌ Ошибка создания локальной директории {self.local_path}: {e}")

    def create_cloud_dir(self):
        try:
            self.yandex_disk_client.create_dir(self.cloud_path)
            self.cloud_state.type = "dir"
        except Exception as e:
            print(f"❌ Ошибка создания удаленной директории {self.cloud_path}: {e}")

    def remove_local_dir(self):
        try:
            if self.local_path.exists() and self.local_path.is_dir():
                self.local_path.rmdir()
                self.local_state.type = "empty"
        except Exception as e:
            print(f"❌ Ошибка удаления локальной директории {self.local_path}: {e}")

    def remove_cloud_dir(self):
        self.yandex_disk_client.remove(self.cloud_path)
        self.cloud_state.type = "empty"
