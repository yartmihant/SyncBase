# PROJECT_STRUCTURE.md — SyncBase

```
/home/antonov/Base/Orgs/SyncBase/
├── VERSION                          # Версия пакета (читается pyproject.toml)
├── CHANGELOG.md                     # История изменений
├── AGENTS.md                        # Инструкции для AI-агентов
├── PROJECT_STRUCTURE.md             # Этот файл
├── README.md                        # Документация для пользователей
├── pyproject.toml                   # Конфигурация пакета (setuptools, mypy)
├── mypy.ini                         # Настройки mypy (legacy, будет перенесено в pyproject.toml)
│
├── src/
│   └── syncbase/                    # Основной пакет
│       ├── __init__.py              # Публичный API пакета
│       ├── __main__.py              # Точка входа: python -m syncbase
│       ├── cli.py                   # Точка входа CLI (команда syncbase)
│       ├── base.py                  # Класс SyncBase (массовые операции)
│       ├── project.py               # Классы SyncProject, SyncIgnore
│       ├── item.py                  # Классы SyncItem, ItemState
│       ├── client.py                # Класс YandexDiskClient (HTTP API)
│       ├── resolver.py              # find_storage() — поиск .syncbase
│       └── .syncignore.example      # Шаблон .syncignore для новых проектов
│
├── tests/
│   ├── README_integration_test.md   # Инструкция по запуску тестов
│   ├── requirements_test.txt        # Зависимости для тестов
│   ├── check_test_ready.py          # Проверка готовности тестовой среды
│   ├── test_path_structure.py       # Тест структуры путей
│   ├── test_sync_project_integration.py  # Интеграционный тест синхронизации
│   ├── test_yandex_disk_client.py   # Тест HTTP-клиента
│   ├── test_resolver.py             # Unit-тесты resolver.py
│   ├── test_overwrite_guard.py      # Unit-тесты защиты от перезаписи
│   └── test_base/                   # Тестовое хранилище
│       ├── .syncbase                # Токен тестового хранилища (НЕ коммитить в git!)
│       └── test_project/            # Тестовый проект
│           ├── package.json
│           ├── README.md
│           ├── assets/
│           ├── docs/
│           ├── src/
│           └── tests/
│
└── work/                            # Рабочие файлы и планы
    └── 0_0_2026-06-01_REFACTORING_PLAN.md
```

## Правила расположения файлов

- Основной код пакета — только в `src/syncbase/`.
- Временные скрипты, прототипы — в `work/` или `sandbox/` (не в `src/`).
- Тесты — только в `tests/`.
- Файл `.syncbase` в тестовой среде — в `tests/test_base/.syncbase`.
  Этот файл **не должен попасть в git** (добавьте `.syncbase` в `.gitignore`).
