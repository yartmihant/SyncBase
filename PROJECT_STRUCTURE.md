# PROJECT_STRUCTURE.md — SyncBase

```text
SyncBase/
├── .gitignore                       # Исключения Git, включая ключи вольтов
├── .sync_cache                      # Локальный служебный кэш
├── .syncignore                      # Правила локального рабочего вольта
├── VERSION                          # Версия пакета
├── CHANGELOG.md                     # История изменений
├── AGENTS.md                        # Инструкции для AI-агентов
├── AGENTS.template.md               # Расширенный шаблон инструкций
├── PROJECT_STRUCTURE.md             # Этот файл
├── README.md                        # Пользовательская документация
├── pyproject.toml                   # Сборка, зависимости, pytest и mypy
├── mypy.ini                         # Legacy-настройки mypy
├── sync_base.py                     # Совместимый launcher пакетной CLI
│
├── src/
│   ├── syncbase.egg-info/           # Сгенерированные metadata setuptools
│   └── syncbase/
│       ├── __init__.py              # Публичный API
│       ├── __main__.py              # python -m syncbase
│       ├── cli.py                   # CLI одноуровневого вольта
│       ├── vault.py                 # SyncVault: проекты и массовые операции
│       ├── project.py               # SyncProject и SyncIgnore
│       ├── item.py                  # SyncItem и ItemState
│       ├── client.py                # YandexDiskClient
│       ├── resolver.py              # find_vault(): поиск .syncbase
│       └── .syncignore.example      # Встроенные правила для новых проектов
│
├── tests/
│   ├── conftest.py                  # Импорт исходников текущего checkout
│   ├── test_cli.py                  # Глобальные и контекстные команды
│   ├── test_incremental_index.py    # Инкрементальные хеши, кэш и снимок status
│   ├── test_cloud_scan.py           # Планирование облачного обхода и сбои
│   ├── test_path_structure.py       # <vault>/<project>/... ↔ app:/<project>/...
│   ├── test_overwrite_guard.py      # Защита от потери данных
│   ├── test_rename_detection.py     # Переименования по MD5 и размеру
│   ├── test_resolver.py             # Поиск и чтение ключа вольта
│   ├── test_yandex_disk_client.py   # HTTP-клиент без сети
│   ├── test_base1/                  # Исторические локальные данные/credential
│   └── test_base2/                  # Исторические локальные данные
│
└── work/
    ├── 0_0_2026-06-01_REFACTORING_PLAN.md
    └── benchmark_indexing.py       # Замер индексации на временном проекте без сети
```

## Правила расположения

- Основной код пакета находится только в `src/syncbase/`.
- Один найденный `.syncbase` задаёт один `SyncVault`.
- Непосредственные подпапки вольта — проекты, соответствующие `app:/<project>/`.
- Категории и двухуровневые селекторы `<category> <project>` отсутствуют.
- `.syncbase` и `.sync_cache` являются локальными служебными файлами и никогда
  не включаются в синхронизацию. Временные `.sync_cache.*.tmp` также исключены.
- `.sync_cache` версии 3.0 содержит снимок последнего save/load и отдельный
  индекс MD5 со stat-подписями для инкрементального сканирования.
- Корневой `sync_base.py` — только адаптер CLI, без дублирования бизнес-логики.
- HTTP-клиент импортируется из `syncbase.client`; отдельного модуля
  `yandex_disk_client` нет.
- Каталоги `tests/test_base1` и `tests/test_base2` содержат исторические данные;
  их вложенность не является моделью хранилища и не интерпретируется библиотекой.
