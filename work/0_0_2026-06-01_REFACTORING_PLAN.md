# Plan: SyncBase Refactoring — v0.0.23 → v0.1.0

Дата: 2026-06-01  
Версия: `0.0.24` (текущая) → `0.1.0` (после рефакторинга)

---

## Задачи

### Задача 1: Новая парадигма хранилища — поиск ключевого файла вверх по дереву

**Суть:**  
Сейчас скрипт читает `.env` рядом с собой (`load_dotenv()`), что жёстко привязывает его к одному хранилищу на устройство и мешает упаковке в pip-пакет.

**Новая схема:**
- Убрать зависимость от `python-dotenv` (из `sync_base.py`, `sync_project.py`, `sync_item.py`).
- Ввести специальный файл хранилища — назовём его `.syncbase` (или `syncbase.key` — уточнить). Файл содержит одну строку: OAuth-токен Яндекса.
- При запуске команды `sync_base` / `syncbase` скрипт ищет этот файл начиная с `os.getcwd()`, идёт вверх по дереву папок (через `Path.parent`), пока не найдёт файл или не упрётся в корень `/`.
- Найденная папка с этим файлом становится **корнем хранилища** (`BASE_PATH`). Токен читается из содержимого файла.
- Если файл не найден — выводить понятное сообщение с инструкцией по инициализации хранилища.

**Формат файла-ключа:**
```
# SyncBase storage key
# Создан: 2026-06-01
YANDEX_DISK_TOKEN=y0__xCPz...
```
Имя файла: `.syncbase`

**Команда инициализации хранилища:**  
`syncbase init <oauth_token>` — создаёт `.syncbase` в текущей папке.

**Затронутые файлы:**
- `sync_base.py` (функция `main()`)
- `sync_project.py` (удалить `load_dotenv`)
- `sync_item.py` (удалить `load_dotenv`)

**Статус:** `[ ]` не начато

---

### Задача 2: Инструкция по созданию приложения Яндекс.Диска в README.md

**Суть:**  
Добавить в `README.md` раздел «Настройка приложения в Яндекс.OAuth» с пошаговой инструкцией:

1. Перейти на https://oauth.yandex.ru/ → «Зарегистрировать приложение»
2. Название: любое (например, `MySyncBase`)
3. Платформа: «Веб-сервисы»
4. Callback URL: `https://oauth.yandex.ru/verification_code`
5. Права доступа: `cloud_api:disk.app_folder` (доступ к папке приложения)
6. Получить Client ID
7. Получить OAuth-токен через браузер:
   `https://oauth.yandex.ru/authorize?response_type=token&client_id=<CLIENT_ID>`
8. Скопировать токен из URL redirect'а
9. Инициализировать хранилище: `syncbase init <TOKEN>`

**Статус:** `[ ]` не начато

---

### Задача 3: Реорганизация в pip-пакет `syncbase`

**Суть:**  
Переместить код в `src/syncbase/`, создать корневую структуру проекта по стандарту и подготовить к публикации на PyPI.

**Целевая структура:**
```
/home/antonov/Base/Orgs/SyncBase/
├── VERSION
├── CHANGELOG.md           (создать, пустой)
├── AGENTS.md              (создать из шаблона)
├── PROJECT_STRUCTURE.md   (создать)
├── README.md              (обновить)
├── pyproject.toml         (создать)
├── mypy.ini               (оставить)
├── src/
│   └── syncbase/
│       ├── __init__.py
│       ├── __main__.py    (точка входа: python -m syncbase)
│       ├── cli.py         (main(), _print_usage() — из sync_base.py)
│       ├── base.py        (класс SyncBase — из sync_base.py)
│       ├── project.py     (класс SyncProject, SyncIgnore — из sync_project.py)
│       ├── item.py        (класс SyncItem, ItemState — из sync_item.py)
│       ├── client.py      (класс YandexDiskClient — из yandex_disk_client.py)
│       ├── resolver.py    (поиск .syncbase вверх по дереву — новый)
│       └── .syncignore.example
├── tests/
│   ├── check_test_ready.py
│   ├── README_integration_test.md
│   ├── requirements_test.txt
│   ├── test_path_structure.py
│   ├── test_sync_project_integration.py
│   ├── test_yandex_disk_client.py
│   ├── test_resolver.py   (новые тесты — задача 5)
│   ├── test_overwrite_guard.py (новые тесты — задача 5)
│   └── test_base/
│       └── test_project/
│           └── ...
├── work/
│   └── 0_0_2026-06-01_REFACTORING_PLAN.md
└── .syncignore.example
```

**pyproject.toml:**
- `[project]` name=`syncbase`, version читается из VERSION
- `[project.scripts]` `syncbase = "syncbase.cli:main"`
- dependencies: `requests`, `tqdm` (убрать `python-dotenv`)
- `[tool.setuptools.packages.find]` where=`["src"]`

**Старые файлы в корне** (`sync_base.py`, `sync_project.py`, `sync_item.py`, `yandex_disk_client.py`) — **оставить** до завершения переноса и тестирования, затем удалить.

**Статус:** `[ ]` не начато

---

### Задача 4: Защита от перезаписи более новых файлов более старыми

**Суть:**  
При выполнении `load` пользователь может случайно перезаписать свежую локальную работу старой облачной копией (и наоборот при `save`).

**Логика защиты:**

1. **Сбор изменений** — уже происходит в ходе `local_scan()` + `cloud_scan()`.
2. **Детектор перезаписи** — перед применением операции синхронизации находить файлы, которые:
   - имеют разный MD5 (т.е. будет перезапись содержимого), **И**
   - целевая сторона (та, которая будет перезаписана) новее источника по `modified`.
   - При `load`: локальный файл новее облачного → опасная перезапись.
   - При `save`: облачный файл новее локального → опасная перезапись.
3. **Блокировка** — если обнаружены такие файлы и флаг `--force` не задан:
   - Выводить список проблемных файлов с деталями (имя, локальная дата, облачная дата, что новее).
   - Завершать скрипт с `sys.exit(1)`.
4. **Флаг `--force` / `-f`** — разрешает перезапись. Применяется к командам `save` и `load`:
   ```
   syncbase save -f
   syncbase load -f category project
   ```
   В логе для таких файлов выводить иконку `⚠️ [FORCE]`.

**Место реализации:**  
- Новый метод `SyncProject.check_overwrite_safety(direction: Literal['save', 'load'], force: bool) -> bool` в `project.py`.
- Вызывать в `sync_save()` и `sync_load()` после `cloud_scan()`, до применения операций.
- Аргумент `force` пробрасывать от CLI через `SyncBase._run_for_project()`.

**Статус:** `[ ]` не начато

---

### Задача 5: Unit-тесты для нового функционала

**Принцип:** реальное тестовое хранилище (токен из `.syncbase` в `tests/test_base/`), без mock'ов.

**Файлы токена:**  
После рефакторинга создать `tests/test_base/.syncbase` с токеном `<TEST_YANDEX_DISK_TOKEN>`.

**Новые тестовые файлы:**

#### `tests/test_resolver.py`
Тесты поиска `.syncbase` вверх по дереву:
- `test_finds_key_in_current_dir` — файл есть в текущей папке
- `test_finds_key_in_parent_dir` — файл есть в родительской папке
- `test_finds_key_walking_up` — файл найден через несколько уровней
- `test_returns_none_at_root` — файл не найден, доходим до `/`
- `test_base_path_is_dir_of_key_file` — корень хранилища = папка с `.syncbase`

#### `tests/test_overwrite_guard.py`
Тесты защиты от перезаписи:
- `test_no_block_when_no_newer_files` — нет более новых файлов → синхронизация идёт
- `test_blocks_load_when_local_newer` — локальный файл новее облачного при `load` → блок
- `test_blocks_save_when_cloud_newer` — облачный файл новее локального при `save` → блок
- `test_force_allows_overwrite` — с флагом `--force` перезапись разрешена
- `test_force_logs_warning_icon` — проверить, что в логе есть `[FORCE]` или `⚠️`

**Статус:** `[ ]` не начато

---

## Порядок выполнения

```
1 → 3 → 2 → 4 → 5
```

- **1 сначала**, потому что от него зависит `resolver.py` и вся инициализация.
- **3 сразу после**, потому что реорганизация определяет структуру для задач 2, 4, 5.
- **2** — простая документация, добавляется параллельно с 3.
- **4** — новый функционал, встраивается в уже перенесённый код.
- **5** — тесты, пишутся на финальной структуре.

---

## Что НЕ меняется

- Система команд CLI (`list`, `status`, `save`, `load`) — без изменений.
- Двухуровневая структура хранилища (категории / проекты) — без изменений.
- Логика `.syncignore` — без изменений.
- API Яндекс.Диска — без изменений.
- Многопоточное сканирование — без изменений.

---

## Открытые вопросы

- [ ] **Имя файла-ключа:** `.syncbase` — ок? (Альтернативы: `.syncbase.key`, `syncbase.env`) # ok
- [ ] **Формат файла-ключа:** одна строка токена или ini-формат `KEY=VALUE`? # ini-формат `KEY=VALUE`
- [ ] **Команда `init`:** добавлять её сразу или оставить ручное создание файла? # пока ручное
- [ ] **Версия:** `0.0.23` → `0.1.0` (значительный рефакторинг) — согласовать. # да, 0.1.0
