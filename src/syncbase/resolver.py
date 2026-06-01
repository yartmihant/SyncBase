"""Поиск файла хранилища .syncbase вверх по дереву файловой системы."""

from pathlib import Path
from typing import Optional, Tuple

STORAGE_KEY_FILE = ".syncbase"
_TOKEN_KEY = "YANDEX_DISK_TOKEN"


def find_storage(start: Optional[Path | str] = None) -> Optional[Tuple[Path, str]]:
    """
    Ищет файл `.syncbase` начиная с `start` (по умолчанию — cwd)
    и поднимается вверх по дереву папок до корня файловой системы.

    Папка, в которой найден `.syncbase`, становится корнем хранилища (BASE_PATH).

    Returns:
        Кортеж (base_path, token), если файл найден и содержит токен.
        None, если файл нигде не найден.

    Raises:
        ничего — все ошибки подавляются, при проблеме возвращается None.
    """
    current = Path(start).resolve() if start else Path.cwd().resolve()

    while True:
        candidate = current / STORAGE_KEY_FILE
        if candidate.is_file():
            token = _read_token(candidate)
            if token:
                return current, token
        parent = current.parent
        if parent == current:
            # Достигли корня файловой системы
            return None
        current = parent


def _read_token(key_file: Path) -> Optional[str]:
    """
    Читает токен из файла `.syncbase` в формате KEY=VALUE.

    Пример содержимого файла:
        # SyncBase storage key
        YANDEX_DISK_TOKEN=y0__xCPz...
    """
    try:
        for line in key_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                if key.strip() == _TOKEN_KEY:
                    value = value.strip()
                    return value if value else None
    except OSError:
        pass
    return None
