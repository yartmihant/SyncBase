import requests
import time
import random
from pathlib import Path
from tqdm import tqdm
from typing import Optional

PROGRESS_BAR_FILESIZE = 1024 * 1024
CHUNK_SIZE = 64 * 1024

_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def _fmt_size(size_bytes: float) -> str:
    """Форматирует размер файла с автоматическим выбором единицы (B/KB/MB/GB)."""
    if size_bytes == 0:
        return "0 B"
    i = 0
    while size_bytes >= 1024 and i < len(_SIZE_UNITS) - 1:
        size_bytes /= 1024
        i += 1
    precision = 0 if size_bytes >= 100 else 1 if size_bytes >= 10 else 2
    return f"{size_bytes:.{precision}f} {_SIZE_UNITS[i]}"


def _fmt_speed(bytes_per_sec: float) -> str:
    """Форматирует скорость передачи с автоматическим выбором единицы (B/s ... GB/s)."""
    return _fmt_size(bytes_per_sec) + "/s"


class YandexDiskClient:
    """
    Клиент для работы с Яндекс.Диском через API с автоматическим обходом антивирусной проверки.

    Основные возможности:
    - 🚀 Автоматический обход антивируса для всех файлов (.tmp + переименование)
    - 📊 Прогресс-бар для файлов > 1 MB
    - 🔄 Универсальный HTTP клиент с retry и rate limiting
    - 📁 CRUD операции для файлов и папок
    - 📄 Автоматическая пагинация для больших списков
    - 📊 Экспоненциальный backoff с jitter
    """

    def _normalize_path(self, path: str | Path) -> Path:
        if isinstance(path, str):
            return Path(path)
        return path

    def __init__(self, token: str):
        self.token = token
        self.api_base = "https://cloud-api.yandex.net/v1/disk/resources"
        self.headers = {"Authorization": f"OAuth {self.token}"}

    def _make_request(
        self,
        method: str,
        url: str = "/",
        params=None,
        max_retries: int = 3,
        is_api_call: bool = True,
        **kwargs,
    ):
        """
        Универсальный HTTP запрос с механизмом повторных попыток.
        """
        if params is not None and isinstance(params, dict):
            params = {k: v.as_posix() if isinstance(v, Path) else v for k, v in params.items()}
            params = {
                k: (v if not (isinstance(v, str) and v.endswith(":")) else v + "/")
                for k, v in params.items()
            }
            kwargs["params"] = params

        retryable_errors = (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.HTTPError,
        )

        for attempt in range(max_retries + 1):
            try:
                if "timeout" not in kwargs:
                    kwargs["timeout"] = 60 if not is_api_call else 30

                if is_api_call:
                    full_url = self.api_base + url
                    request_headers = self.headers
                    operation_type = "API"
                else:
                    full_url = url
                    request_headers = kwargs.pop("headers", {})
                    operation_type = "Upload/Download"

                response = requests.request(method, full_url, headers=request_headers, **kwargs)

                if response.status_code == 429:
                    if attempt < max_retries:
                        try:
                            retry_after = int(response.headers.get("Retry-After", "1"))
                        except (ValueError, TypeError):
                            retry_after = 1
                        wait_time = min(retry_after, 60)
                        print(
                            f"🕐 {operation_type} rate limit (429), ждем {wait_time}с..."
                            f" (попытка {attempt + 1}/{max_retries + 1})"
                        )
                        time.sleep(wait_time)
                        continue

                if response.status_code >= 500:
                    if attempt < max_retries:
                        wait = self._calculate_backoff_time(attempt)
                        print(
                            f"🔄 {operation_type} серверная ошибка {response.status_code},"
                            f" повтор через {wait:.1f}с... (попытка {attempt + 1}/{max_retries + 1})"
                        )
                        time.sleep(wait)
                        continue

                return response

            except retryable_errors as e:
                if attempt < max_retries:
                    wait = self._calculate_backoff_time(attempt)
                    print(
                        f"🔄 {operation_type} сетевая ошибка: {type(e).__name__},"
                        f" повтор через {wait:.1f}с... (попытка {attempt + 1}/{max_retries + 1})"
                    )
                    time.sleep(wait)
                    continue
                else:
                    print(f"❌ Критическая {operation_type.lower()} ошибка после {max_retries} попыток: {e}")
                    return None

            except requests.exceptions.RequestException as e:
                print(f"❌ Ошибка {operation_type.lower()} запроса: {e}")
                return None

        print(f"❌ Исчерпаны все {max_retries} попытки для {method} {url}")
        return None

    def _calculate_backoff_time(self, attempt: int) -> float:
        base_delay = min(2**attempt, 32)
        jitter = random.uniform(0.1, 0.5) * base_delay
        return base_delay + jitter

    def list(self, cloud_path: str | Path, limit: int = 10000):
        """Получить полный список файлов и папок с автоматической пагинацией."""
        cloud_path = self._normalize_path(cloud_path)

        all_items = []
        offset = 0
        page_limit = min(limit, 10000)

        while True:
            params = {"path": cloud_path, "limit": page_limit, "offset": offset}
            response = self._make_request("GET", "/", params=params)
            if not response or response.status_code != 200:
                break

            data = response.json()
            embedded = data.get("_embedded", {})
            items = embedded.get("items", [])

            if not items:
                break

            all_items.extend(items)
            total = embedded.get("total", 0)
            current_count = len(all_items)

            if total > 1000:
                print(f"  📄 Получено элементов: {current_count}/{total}")

            if current_count >= total or len(items) < page_limit:
                break

            offset += len(items)

        if len(all_items) > 100:
            print(f"  ✅ Итого элементов получено: {len(all_items)}")
        return all_items

    def exists(self, cloud_path: str | Path) -> bool:
        """Проверить, существует ли файл или папка."""
        cloud_path = self._normalize_path(cloud_path)
        params = {"path": cloud_path, "fields": "path,type,name"}
        response = self._make_request("GET", "/", params=params)
        return response is not None and response.status_code == 200

    def get_item_state(self, cloud_path: str | Path) -> Optional[dict]:
        """Получить метаданные файла/папки или None если не существует."""
        cloud_path = self._normalize_path(cloud_path)
        params = {"path": cloud_path, "fields": "name,type,size,md5,modified"}
        response = self._make_request("GET", "/", params=params)
        if response and response.status_code == 200:
            return response.json()
        return None

    def create_dir(self, cloud_path: str | Path, create_parent: bool = True) -> bool:
        """Создать папку."""
        cloud_path = self._normalize_path(cloud_path)
        params = {"path": cloud_path}
        response = self._make_request("PUT", "/", params=params)

        if response.status_code == 201:
            print(f"📁 Папка создана: {cloud_path}")
            return True
        elif response.status_code == 409:
            if self.exists(cloud_path):
                print(f"📁 Папка уже существует: {cloud_path}")
                return True
            elif create_parent:
                return self.create_dir(cloud_path.parent) and self.create_dir(cloud_path, create_parent=False)
        print(f"❌ Не удалось создать папку: {cloud_path}, {response}")
        return False

    def remove(self, cloud_path: str | Path, permanently: bool = True) -> bool:
        """Удалить файл или папку."""
        cloud_path = self._normalize_path(cloud_path)
        params = {"path": cloud_path, "permanently": str(permanently).lower()}
        response = self._make_request("DELETE", "/", params=params)
        if response and response.status_code in (202, 204):
            print(f"🗑️  Удалено: {cloud_path}")
            return True
        print(f"❌ Не удалось удалить: {cloud_path}")
        return False

    def download(self, cloud_path: str | Path, local_path: str | Path) -> bool:
        """Скачать файл с Яндекс.Диска."""
        cloud_path = self._normalize_path(cloud_path)
        params = {"path": cloud_path}
        response = self._make_request("GET", "/download", params=params)
        if not response:
            print(f"❌ Не удалось получить ссылку для скачивания: {cloud_path}")
            return False
        try:
            download_url = response.json()["href"]
            print(f"⬇️  Начинаем скачивание: {cloud_path}")
            r = self._make_request("GET", download_url, is_api_call=False, stream=True)
            if r and r.status_code == 200:
                total_size = int(r.headers.get("content-length", 0))
                show_progress = total_size > PROGRESS_BAR_FILESIZE

                with open(local_path, "wb") as f:
                    if show_progress:
                        with tqdm(
                            total=total_size,
                            unit="B",
                            unit_scale=True,
                            desc=f"⬇️ {cloud_path.name}",
                            leave=False,
                        ) as pbar:
                            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    pbar.update(len(chunk))
                    else:
                        f.write(r.content)

                print(f"⬇️  Скачан: {cloud_path}")
                return True
            else:
                print(f"❌ Ошибка скачивания: {cloud_path} (статус: {r.status_code if r else 'None'})")
                return False
        except Exception as e:
            print(f"❌ Ошибка при скачивании файла {cloud_path}: {e}")
            return False

    def upload(
        self,
        local_path: str | Path,
        cloud_path: str | Path,
        overwrite: bool = True,
        create_parent: bool = True,
    ) -> bool:
        """
        Загрузить файл на Яндекс.Диск с автоматическим обходом антивируса.

        Стратегия: загружает как .tmp, затем переименовывает на сервере.
        """
        local_path = self._normalize_path(local_path)
        cloud_path = self._normalize_path(cloud_path)

        file_size = local_path.stat().st_size
        print(f"⬆️  Загружаем файл: {local_path.name} ({_fmt_size(file_size)})")

        if create_parent:
            cloud_path_parent = cloud_path.parent
            if not self.exists(cloud_path_parent):
                if not self.create_dir(cloud_path_parent):
                    print(f"❌ Не удалось создать папку: {cloud_path_parent}")
                    return False

        tmp_cloud_path = cloud_path.as_posix() + ".tmp"
        start_time = time.time()

        try:
            success = self._upload_file_with_progress(local_path, tmp_cloud_path, overwrite, create_parent=False)
            if not success:
                print(f"❌ Ошибка загрузки файла {tmp_cloud_path}")
                return False

            upload_time = time.time() - start_time
            upload_speed = file_size / upload_time if upload_time > 0 else 0

            if not self.move(tmp_cloud_path, cloud_path, overwrite):
                print("❌ Ошибка переименования файла")
                try:
                    self.remove(tmp_cloud_path)
                except Exception:
                    pass
                return False

            print(f"   {cloud_path} загружен за {upload_time:.1f}с ({_fmt_speed(upload_speed)})")
            return True

        except Exception as e:
            print(f"❌ Ошибка при загрузке файла {cloud_path}: {e}")
            try:
                self.remove(tmp_cloud_path)
            except Exception:
                pass
            return False

    def _upload_file_with_progress(
        self, local_path: Path, cloud_path: str, overwrite: bool, create_parent: bool
    ) -> bool:
        """Внутренний метод загрузки файла с прогресс-баром."""
        file_size = local_path.stat().st_size
        show_progress = file_size > PROGRESS_BAR_FILESIZE

        params = {"path": cloud_path, "overwrite": str(overwrite).lower()}
        response = self._make_request("GET", "/upload", params=params)

        if response is None or response.status_code >= 400:
            status_info = response.status_code if response is not None else "None"
            print(f"❌ Не удалось получить ссылку для загрузки: {cloud_path} (статус: {status_info})")
            return False

        try:
            upload_url = response.json()["href"]

            with open(local_path, "rb") as f:
                if show_progress:
                    with tqdm(
                        total=file_size,
                        unit="B",
                        unit_scale=True,
                        desc=f"⬆️ {local_path.name}",
                        leave=False,
                    ) as pbar:

                        class ProgressFileReader:
                            def __init__(self, file_obj, progress_bar):
                                self.file_obj = file_obj
                                self.progress_bar = progress_bar

                            def read(self, size=None):
                                chunk = self.file_obj.read(size or CHUNK_SIZE)
                                if chunk:
                                    self.progress_bar.update(len(chunk))
                                return chunk

                        progress_reader = ProgressFileReader(f, pbar)
                        response = requests.put(
                            upload_url,
                            data=progress_reader,
                            timeout=300,
                            headers={"Content-Type": "application/octet-stream"},
                        )
                else:
                    response = requests.put(upload_url, files={"file": f}, timeout=120)

            if response.status_code in (201, 202):
                return True
            else:
                print(f"❌ Ошибка загрузки: {response.status_code}")
                return False

        except Exception as e:
            print(f"❌ Ошибка при загрузке: {e}")
            return False

    def move(self, from_cloud_path: str | Path, to_cloud_path: str | Path, overwrite: bool = True) -> bool:
        """Переместить файл или папку."""
        from_cloud_path = self._normalize_path(from_cloud_path)
        to_cloud_path = self._normalize_path(to_cloud_path)

        params = {
            "from": from_cloud_path,
            "path": to_cloud_path,
            "overwrite": str(overwrite).lower(),
        }
        response = self._make_request("POST", "/move", params=params)
        if response and response.status_code in (201, 202):
            return True
        print(f"❌ Не удалось переместить: {from_cloud_path} → {to_cloud_path}")
        return False

    def copy(self, from_cloud_path: str | Path, to_cloud_path: str | Path, overwrite: bool = True) -> bool:
        """Скопировать файл или папку."""
        from_cloud_path = self._normalize_path(from_cloud_path)
        to_cloud_path = self._normalize_path(to_cloud_path)

        params = {
            "from": from_cloud_path,
            "path": to_cloud_path,
            "overwrite": str(overwrite).lower(),
        }
        response = self._make_request("POST", "/copy", params=params)
        if response and response.status_code in (201, 202):
            print(f"📄 Скопировано: {from_cloud_path} → {to_cloud_path}")
            return True
        print(f"❌ Не удалось скопировать: {from_cloud_path} → {to_cloud_path}")
        return False
