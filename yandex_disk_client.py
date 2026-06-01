import requests
import time
import random
from pathlib import Path
from tqdm import tqdm
import os
from typing import Optional

PROGRESS_BAR_FILESIZE = 1024 * 1024
CHUNK_SIZE = 64*1024

class YandexDiskClient:
    """
    Клиент для работы с Яндекс.Диском через API с автоматическим обходом антивирусной проверки.
    
    Основные возможности:
    - 🚀 **Автоматический обход антивируса** для всех файлов (.tmp + переименование)
    - 📊 **Прогресс-бар** для файлов > 1MB
    - 🔄 Универсальный HTTP клиент с retry механизмом и rate limiting
    - 📁 CRUD операции для файлов и папок
    - 📄 Автоматическая пагинация для больших списков
    - ⬆️⬇️ Специализированная обработка upload/download операций
    - 📊 Экспоненциальный backoff с jitter для стабильности
    - 🎯 Адаптивные timeout'ы в зависимости от типа операции
    - 📝 Детальное логирование с эмодзи для мониторинга
    
    Архитектурные особенности:
    - **Умная стратегия загрузки**: автоматически обходит антивирусную проверку
    - **Прогресс-бар**: показывает загрузку больших файлов в реальном времени
    - **Разделение API вызовов и прямых HTTP запросов**
    - **Адаптивные timeout'ы** в зависимости от типа операции
    - **Детальное логирование** с эмодзи для мониторинга

    version 0.0.6
    """
    def _normalize_path(self, path: str | Path) -> Path:
        """
        Внутренний метод: приводит путь к типу Path, если передан str.
        """
        if isinstance(path, str):
            return Path(path)
        return path

    def __init__(self, token: str):
        self.token = token
        self.api_base = "https://cloud-api.yandex.net/v1/disk/resources"
        self.headers = {"Authorization": f"OAuth {self.token}"}

    def _make_request(self, method: str, url: str="/", params=None, max_retries: int = 3, is_api_call: bool = True, **kwargs):
        """
        Универсальный HTTP запрос с механизмом повторных попыток
        
        Args:
            method: HTTP метод (GET, POST, PUT, DELETE)
            url: URL endpoint (для API) или полный URL (для upload/download)
            max_retries: Максимальное количество повторных попыток
            is_api_call: True для API вызовов (добавляет base URL + headers), False для прямых HTTP
            **kwargs: Дополнительные параметры для requests (включая stream для download)
            
        Returns:
            Response объект или None при критической ошибке
        """

        # Преобразуем значения params из Path в str, если нужно
        if params is not None and isinstance(params, dict):
            # Приводим Path к строке с прямыми слэшами (универсально для Windows и Unix)
            params = {k: v.as_posix() if isinstance(v, Path) else v for k, v in params.items()}
            params = {k: (v if not (type(v) == str and v.endswith(':')) else v+'/') for k, v in params.items()}
            kwargs['params'] = params

        # Типы ошибок, которые стоит повторять
        retryable_errors = (
            requests.exceptions.ConnectionError,    # Network is unreachable
            requests.exceptions.Timeout,            # Timeout errors
            requests.exceptions.ChunkedEncodingError,  # Incomplete read
            requests.exceptions.HTTPError           # HTTP errors
        )
        
        for attempt in range(max_retries + 1):
            try:
                # Добавляем timeout для предотвращения зависания
                if 'timeout' not in kwargs:
                    kwargs['timeout'] = 60 if not is_api_call else 30  # Увеличенный timeout для upload/download
                
                # Определяем URL и headers в зависимости от типа запроса
                if is_api_call:
                    full_url = self.api_base + url
                    request_headers = self.headers
                    operation_type = "API"
                else:
                    full_url = url
                    request_headers = kwargs.pop('headers', {})  # Для upload/download headers могут быть в kwargs
                    operation_type = "Upload/Download"
                
                response = requests.request(method, full_url, headers=request_headers, **kwargs)
                
                # Проверяем статус коды, которые стоит повторить
                if response.status_code == 429:  # Too Many Requests
                    if attempt < max_retries:
                        try:
                            retry_after = int(response.headers.get('Retry-After', '1'))
                        except (ValueError, TypeError):
                            retry_after = 1
                        wait_time = min(retry_after, 60)  # Максимум 60 секунд
                        print(f"🕐 {operation_type} rate limit (429), ждем {wait_time}с... (попытка {attempt + 1}/{max_retries + 1})")
                        time.sleep(wait_time)
                        continue
                
                if response.status_code >= 500:  # Server errors
                    if attempt < max_retries:
                        server_wait_time: float = self._calculate_backoff_time(attempt)
                        print(f"🔄 {operation_type} серверная ошибка {response.status_code}, повтор через {server_wait_time:.1f}с... (попытка {attempt + 1}/{max_retries + 1})")
                        time.sleep(server_wait_time)
                        continue
                
                # print(f'<{method}:{url}{kwargs.get("params", {})} -> {response.status_code}>')
                return response
                
            except retryable_errors as e:
                if attempt < max_retries:
                    network_wait_time: float = self._calculate_backoff_time(attempt)
                    print(f"🔄 {operation_type} сетевая ошибка: {type(e).__name__}, повтор через {network_wait_time:.1f}с... (попытка {attempt + 1}/{max_retries + 1})")
                    time.sleep(network_wait_time)
                    continue
                else:
                    print(f"❌ Критическая {operation_type.lower()} ошибка после {max_retries} попыток: {e}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                # Некритичные ошибки, которые не стоит повторять
                print(f"❌ Ошибка {operation_type.lower()} запроса: {e}")
                return None
        
        # Если дошли сюда, значит все попытки исчерпаны
        print(f"❌ Исчерпаны все {max_retries} попытки для {method} {url}")
        return None
    
    def _calculate_backoff_time(self, attempt: int) -> float:
        """
        Рассчитывает время ожидания с экспоненциальным backoff + jitter
        
        Args:
            attempt: Номер попытки (0, 1, 2, ...)
            
        Returns:
            Время ожидания в секундах
        """
        # Экспоненциальный backoff: 1s, 2s, 4s, 8s...
        base_delay = min(2 ** attempt, 32)  # Максимум 32 секунды
        
        # Добавляем jitter (случайность) для избежания thundering herd
        jitter = random.uniform(0.1, 0.5) * base_delay
        
        return base_delay + jitter
    
    def list(self, cloud_path: str|Path, limit: int = 10000):
        """
        Получить полный список файлов и папок с автоматической пагинацией.
        
        Автоматически обрабатывает большие папки, получая все элементы
        через несколько API вызовов. Показывает прогресс для папок >1000 элементов.
        
        Args:
            cloud_path: Путь к папке в облаке
            limit: Максимальное количество элементов на страницу (максимум 10000)
            
        Returns:
            Полный список всех элементов в папке (файлы + папки)
            
        Note:
            Для больших папок (>1000 элементов) выводит прогресс в консоль
        """
        cloud_path = self._normalize_path(cloud_path)

        all_items = []
        offset = 0
        
        # Ограничиваем limit максимумом API
        page_limit = min(limit, 10000)
        
        while True:
            params = {
                "path": cloud_path,
                "limit": page_limit,
                "offset": offset
            }
            response = self._make_request("GET", "/", params=params)
            if not response or response.status_code != 200:
                break
                
            data = response.json()
            embedded = data.get("_embedded", {})
            items = embedded.get("items", [])
            
            if not items:
                break  # Больше элементов нет
            
            all_items.extend(items)
            
            # Проверяем, получили ли все элементы
            total = embedded.get("total", 0)
            current_count = len(all_items)
            
            # Для больших папок показываем прогресс
            if total > 1000:
                print(f"  📄 Получено элементов: {current_count}/{total}")
            
            # Если получили все элементы или достигли лимита страницы
            if current_count >= total or len(items) < page_limit:
                break
                
            # Переходим к следующей странице
            offset += len(items)
        
        # Показываем итог только для больших папок
        if len(all_items) > 100:
            print(f"  ✅ Итого элементов получено: {len(all_items)}")
        return all_items

    def exists(self, cloud_path: str|Path):
        """Проверить, существует ли файл или папка"""
        cloud_path = self._normalize_path(cloud_path)

        params = {"path": cloud_path, "fields": "path,type,name"}
        response = self._make_request("GET", "/", params=params)
        return response is not None and response.status_code == 200

    def get_item_state(self, cloud_path: str|Path):
        """Получить информацию о файле/папке или None если не существует"""
        cloud_path = self._normalize_path(cloud_path)

        params = {"path": cloud_path, "fields": "name,type,size,md5,modified"}
        response = self._make_request("GET", "/", params=params)
        
        if response and response.status_code == 200:
            return response.json()
        return None

    def create_dir(self, cloud_path: str|Path, create_parent=True):
        """Создать папку"""
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
                cloud_path_parent = cloud_path.parent
                return self.create_dir(cloud_path_parent) and self.create_dir(cloud_path, create_parent=False)
        print(f"❌ Не удалось создать папку: {cloud_path}, {response}")
        return False

    def remove(self, cloud_path: str|Path, permanently: bool = True):
        """Удалить файл или папку"""
        cloud_path = self._normalize_path(cloud_path)

        params = {"path": cloud_path, "permanently": str(permanently).lower()}
        response = self._make_request("DELETE", "/", params=params)
        if response and response.status_code in (202, 204):
            print(f"🗑️  Удалено: {cloud_path}")
            return True
        print(f"❌ Не удалось удалить: {cloud_path}")
        return False

    def download(self, cloud_path: str|Path, local_path: str|Path):
        """Скачать файл с Яндекс.Диска с прогресс-баром"""

        cloud_path = self._normalize_path(cloud_path)

        params = {"path": cloud_path}
        response = self._make_request("GET", "/download", params=params)
        if not response:
            print(f"❌ Не удалось получить ссылку для скачивания: {cloud_path}")
            return False
        try:
            download_url = response.json()["href"]
            # Используем универсальный retry механизм для download URL
            print(f"⬇️  Начинаем скачивание: {cloud_path}")
            r = self._make_request("GET", download_url, is_api_call=False, stream=True)
            if r and r.status_code == 200:
                # Получаем размер файла из заголовков
                total_size = int(r.headers.get('content-length', 0))
                show_progress = total_size > PROGRESS_BAR_FILESIZE  # Показываем прогресс для файлов > 1MB
                
                with open(local_path, "wb") as f:
                    if show_progress:
                        # Создаем прогресс-бар для больших файлов
                        with tqdm(
                            total=total_size,
                            unit='B',
                            unit_scale=True,
                            desc=f"⬇️ Скачивание {cloud_path.name}",
                            leave=False
                        ) as pbar:
                            # Скачиваем файл чанками с прогресс-баром
                            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    pbar.update(len(chunk))
                    else:
                        # Для маленьких файлов скачиваем без прогресс-бара
                        f.write(r.content)
                
                print(f"⬇️  Скачан: {cloud_path}")
                return True
            else:
                print(f"❌ Ошибка скачивания: {cloud_path} (статус: {r.status_code if r else 'None'})")
                return False
        except Exception as e:
            print(f"❌ Ошибка при скачивании файла {cloud_path}: {e}")
            return False


    def upload(self, local_path: str|Path, cloud_path: str|Path, overwrite: bool = True, create_parent=True):
        """
        Универсальная загрузка файла с автоматическим обходом антивируса
        
        Автоматически:
        1. Загружает файл как .tmp (обходит антивирусную проверку)
        2. Переименовывает на сервере в нужное расширение
        3. Показывает прогресс-бар для файлов > 1MB
        
        Args:
            local_path: Путь к локальному файлу
            cloud_path: Путь в облаке
            overwrite: Перезаписать существующий файл
            create_parent: Создать родительские папки
            
        Returns:
            True если загрузка успешна, False иначе
        """
        local_path = self._normalize_path(local_path)
        cloud_path = self._normalize_path(cloud_path)

        # Получаем размер файла
        file_size = local_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)

        print(f"⬆️  Загружаем файл: {local_path.name} ({file_size_mb:.2f} MB)")

        # Создаем папку если нужно
        if create_parent:
            cloud_path_parent = cloud_path.parent
            if not self.exists(cloud_path_parent):
                if not self.create_dir(cloud_path_parent):
                    print(f"❌ Не удалось создать папку: {cloud_path_parent}")
                    return False

        # Шаг 1: Загружаем как .tmp файл (обходит антивирус)
        tmp_cloud_path = cloud_path.as_posix() + ".tmp"
        
        start_time = time.time()
        
        try:
            # Загружаем .tmp файл с прогресс-баром
            success = self._upload_file_with_progress(local_path, tmp_cloud_path, overwrite, create_parent=False)
            
            if not success:
                print(f"❌ Ошибка загрузки файла {tmp_cloud_path}")
                return False
            
            upload_time = time.time() - start_time
            upload_speed = file_size_mb / upload_time
            
            if not self.move(tmp_cloud_path, cloud_path, overwrite):
                print(f"❌ Ошибка переименования файла")
                # Удаляем .tmp файл если переименование не удалось
                try:
                    self.remove(tmp_cloud_path)
                except:
                    pass
                return False
            print(f"   {cloud_path} загружен за {upload_time:.1f}с ({upload_speed:.2f} MB/s)")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка при загрузке файла {cloud_path}: {e}")
            # Очистка при ошибке
            try:
                self.remove(tmp_cloud_path)
            except:
                pass
            return False

    def _upload_file_with_progress(self, local_path: Path, cloud_path: str, overwrite: bool, create_parent: bool):
        """
        Внутренний метод загрузки файла с прогресс-баром
        
        Args:
            local_path: Путь к локальному файлу
            cloud_path: Путь в облаке
            overwrite: Перезаписать существующий файл
            create_parent: Создать родительские папки
            
        Returns:
            True если загрузка успешна, False иначе
        """
        # Получаем размер файла для прогресс-бара
        file_size = local_path.stat().st_size
        show_progress = file_size > PROGRESS_BAR_FILESIZE  # Показываем прогресс для файлов > 1MB

        # Получаем URL для загрузки
        params = {"path": cloud_path, "overwrite": str(overwrite).lower()}
        response = self._make_request("GET", "/upload", params=params)
        
        if response is None or response.status_code >= 400:
            status_info = response.status_code if response is not None else "None"
            print(f"❌ Не удалось получить ссылку для загрузки: {cloud_path} (статус: {status_info})")
            return False
            
        try:
            upload_url = response.json()["href"]
            
            # Загружаем файл с прогресс-баром
            with open(local_path, "rb") as f:
                if show_progress:
                    # Создаем прогресс-бар для больших файлов
                    with tqdm(
                        total=file_size,
                        unit='B',
                        unit_scale=True,
                        desc=f"⬆️ Загрузка {local_path.name}",
                        leave=False
                    ) as pbar:
                        
                        # Создаем потоковый ридер с прогресс-баром
                        class ProgressFileReader:
                            def __init__(self, file_obj, progress_bar):
                                self.file_obj = file_obj
                                self.progress_bar = progress_bar
                            
                            def read(self, size=None):
                                chunk = self.file_obj.read(size or CHUNK_SIZE)  # 8KB чанки для чтения
                                if chunk:
                                    self.progress_bar.update(len(chunk))
                                return chunk
                        
                        # Создаем прогресс-ридер
                        progress_reader = ProgressFileReader(f, pbar)
                        
                        # Отправляем файл с прогресс-баром
                        response = requests.put(
                            upload_url, 
                            data=progress_reader, 
                            timeout=300,  # Увеличиваем timeout для больших файлов
                            headers={'Content-Type': 'application/octet-stream'}
                        )
                else:
                    # Для маленьких файлов отправляем без прогресс-бара
                    response = requests.put(upload_url, files={"file": f}, timeout=120)
                
                if response.status_code in (201, 202):
                    return True
                else:
                    print(f"❌ Ошибка загрузки: {response.status_code}")
                    return False
                    
        except Exception as e:
            print(f"❌ Ошибка при загрузке: {e}")
            return False

    def move(self, from_cloud_path: str|Path, to_cloud_path: str|Path, overwrite: bool = True):
        """Переместить файл или папку"""
        from_cloud_path = self._normalize_path(from_cloud_path)
        to_cloud_path = self._normalize_path(to_cloud_path)

        params = {
            "from": from_cloud_path,
            "path": to_cloud_path,
            "overwrite": str(overwrite).lower()
        }
        response = self._make_request("POST", "/move", params=params)
        if response and response.status_code in (201, 202):
            # print(f"🚚 Перемещено: {from_cloud_path} → {to_cloud_path}")
            return True
        print(f"❌ Не удалось переместить: {from_cloud_path} → {to_cloud_path}")
        return False


    def copy(self, from_cloud_path: str|Path, to_cloud_path: str|Path, overwrite: bool = True):
        """Скопировать файл или папку"""
        from_cloud_path = self._normalize_path(from_cloud_path)
        to_cloud_path = self._normalize_path(to_cloud_path)

        params = {
            "from": from_cloud_path,
            "path": to_cloud_path,
            "overwrite": str(overwrite).lower()
        }
        response = self._make_request("POST", "/copy", params=params)
        if response and response.status_code in (201, 202):
            print(f"📄 Скопировано: {from_cloud_path} → {to_cloud_path}")
            return True
        print(f"❌ Не удалось скопировать: {from_cloud_path} → {to_cloud_path}")
        return False


if __name__ == "__main__":
    # main() 
    # Это тестовый токен, не используйте его в продакшене
    client = YandexDiskClient("<YANDEX_DISK_TOKEN>")
