# PROJECT_STRUCTURE.md — SyncBase

```text
SyncBase/
├── VERSION                          # Версия пакета
├── CHANGELOG.md                     # История изменений
├── AGENTS.md                        # Инструкции для AI-агентов
├── AGENTS.template.md               # Расширенный шаблон инструкций
├── PROJECT_STRUCTURE.md             # Этот файл
├── README.md                        # Пользовательская документация
├── pyproject.toml                   # Сборка, зависимости, pytest и mypy
├── mypy.ini                         # Legacy-настройки mypy
├── sync_base.py                     # Совместимый launcher пакетной CLI
├── yandex_disk_client.py            # Совместимый импорт HTTP-клиента
│
├── src/
│   ├── yandex_disk_client.py        # Устанавливаемый legacy-импорт клиента
│   └── syncbase/
│       ├── __init__.py              # Публичный API
│       ├── __main__.py              # python -m syncbase
│       ├── cli.py                   # CLI одноуровневого вольта
│       ├── vault.py                 # SyncVault и SyncIgnore
│       ├── item.py                  # SyncItem и ItemState
│       ├── client.py                # YandexDiskClient
│       ├── resolver.py              # find_vault(): поиск .syncbase
│       └── .syncignore.example      # Стартовые правила нового вольта
│
├── tests/
│   ├── test_cli.py                  # Язык команд и запрет project-селекторов
│   ├── test_path_structure.py       # Отображение <vault>/... ↔ app:/...
│   ├── test_overwrite_guard.py      # Защита от потери данных
│   ├── test_resolver.py             # Поиск и чтение ключа вольта
│   ├── test_yandex_disk_client.py   # HTTP-клиент без сети
│   ├── test_base1/                  # Исторические локальные данные/credential
│   └── test_base2/                  # Исторические локальные данные
│
└── work/
    └── 0_0_2026-06-01_REFACTORING_PLAN.md
```

## Правила расположения

- Основной код пакета находится только в `src/syncbase/`.
- Один найденный `.syncbase` задаёт один `SyncVault`; весь его локальный корень
  напрямую соответствует `app:/`.
- Категории, проекты и селекторы двухуровневой структуры отсутствуют.
- `.syncbase` и `.sync_cache` являются локальными служебными файлами и никогда
  не включаются в синхронизацию.
- Корневые `sync_base.py` и `yandex_disk_client.py` — только адаптеры, без
  дублирования бизнес-логики.
- Каталоги `tests/test_base1` и `tests/test_base2` содержат исторические данные;
  их вложенность не является моделью хранилища и не интерпретируется библиотекой.
