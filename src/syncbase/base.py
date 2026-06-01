from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .project import SyncProject, SyncIgnore
from .client import YandexDiskClient


class SyncBase:
    """Управление всей базой знаний — точка входа для массовых операций."""

    def __init__(self, base_path: str | Path, token: str):
        self.base_path = Path(base_path)
        self.token = token
        self.cloud_client = YandexDiskClient(token)

    # ------------------------------------------------------------------ #
    #  Обнаружение структуры                                              #
    # ------------------------------------------------------------------ #

    def _get_local_categories(self) -> List[str]:
        if not self.base_path.exists():
            return []
        base_syncignore = self._read_base_syncignore()
        return [
            entry.name
            for entry in sorted(self.base_path.iterdir())
            if entry.is_dir()
            and not base_syncignore.should_ignore(entry.name, is_directory=True)
        ]

    def _get_local_projects(self, category: str) -> List[str]:
        category_path = self.base_path / category
        if not category_path.exists() or not category_path.is_dir():
            return []
        category_syncignore = self._read_category_syncignore(category)
        return [
            entry.name
            for entry in sorted(category_path.iterdir())
            if entry.is_dir()
            and not category_syncignore.should_ignore(entry.name, is_directory=True)
        ]

    def _get_cloud_categories(self) -> List[str]:
        items = self.cloud_client.list("app:/") or []
        base_syncignore = self._read_base_syncignore()
        return sorted(
            item["name"]
            for item in items
            if item.get("type") == "dir"
            and not base_syncignore.should_ignore(item["name"], is_directory=True)
        )

    def _get_cloud_projects(self, category: str) -> List[str]:
        items = self.cloud_client.list(f"app:/{category}") or []
        category_syncignore = self._read_category_syncignore(category)
        return sorted(
            item["name"]
            for item in items
            if item.get("type") == "dir"
            and not category_syncignore.should_ignore(item["name"], is_directory=True)
        )

    # ------------------------------------------------------------------ #
    #  .syncignore                                                         #
    # ------------------------------------------------------------------ #

    def _read_base_syncignore(self) -> SyncIgnore:
        syncignore_path = self.base_path / ".syncignore"
        syncignore = SyncIgnore()
        if syncignore_path.exists() and syncignore_path.is_file():
            try:
                syncignore.parse_rules(syncignore_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"⚠️ Не удалось прочитать базовый .syncignore: {e}")
        return syncignore

    def _read_category_syncignore(self, category: str) -> SyncIgnore:
        syncignore_path = self.base_path / category / ".syncignore"
        syncignore = SyncIgnore()
        if syncignore_path.exists() and syncignore_path.is_file():
            try:
                syncignore.parse_rules(syncignore_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"⚠️ Не удалось прочитать .syncignore для '{category}': {e}")
        return syncignore

    # ------------------------------------------------------------------ #
    #  Контекст запуска                                                    #
    # ------------------------------------------------------------------ #

    def _resolve_context(self, cwd_path: str) -> Dict[str, Optional[str]]:
        """Определяет уровень, на котором был вызван скрипт (project/category/base/outside)."""
        current_path = Path(cwd_path).resolve()
        base_path_abs = self.base_path.resolve()

        try:
            rel = current_path.relative_to(base_path_abs)
        except ValueError:
            return {"level": "outside", "category": None, "project": None}

        parts = rel.parts
        if not parts:
            return {"level": "base", "category": None, "project": None}
        if len(parts) == 1:
            return {"level": "category", "category": parts[0], "project": None}
        return {"level": "project", "category": parts[0], "project": parts[1]}

    # ------------------------------------------------------------------ #
    #  Вывод списка                                                        #
    # ------------------------------------------------------------------ #

    def cmd_list(self):
        print("📋 Список проектов (локально/облако):")
        local_categories = set(self._get_local_categories())
        cloud_categories = set(self._get_cloud_categories())
        all_categories = sorted(local_categories | cloud_categories)

        print(
            f"🔍 Найдено категорий: {len(all_categories)}"
            f" (локальных: {len(local_categories)}, облачных: {len(cloud_categories)})"
        )

        for category in all_categories:
            marks = "/".join(
                [s for s, cond in [("local", category in local_categories), ("cloud", category in cloud_categories)] if cond]
            ) or "—"
            local_projects = set(self._get_local_projects(category))
            cloud_projects = set(self._get_cloud_projects(category))
            all_projects = sorted(local_projects | cloud_projects)

            print(f"\n📂 Категория: {category} [{marks}]")
            if not all_projects:
                print("  (пусто)")
                continue
            for project in all_projects:
                pmarks = "/".join(
                    [s for s, c in [("local", project in local_projects), ("cloud", project in cloud_projects)] if c]
                ) or "—"
                print(f"  - {project} [{pmarks}]")

    # ------------------------------------------------------------------ #
    #  Итерация по проектам                                               #
    # ------------------------------------------------------------------ #

    def _iter_selected_projects(
        self, selector: Tuple[str, Optional[str], Optional[str]]
    ) -> List[Tuple[str, str]]:
        scope, category, project = selector
        selected: List[Tuple[str, str]] = []

        if scope in ("single", "category_one") and category and project:
            selected.append((category, project))
            return selected

        if scope == "category_all" and category:
            local = set(self._get_local_projects(category))
            cloud = set(self._get_cloud_projects(category))
            for proj in sorted(local | cloud):
                selected.append((category, proj))
            return selected

        if scope == "all_all":
            local_c = set(self._get_local_categories())
            cloud_c = set(self._get_cloud_categories())
            for cat in sorted(local_c | cloud_c):
                local = set(self._get_local_projects(cat))
                cloud = set(self._get_cloud_projects(cat))
                for proj in sorted(local | cloud):
                    selected.append((cat, proj))
            return selected

        return selected

    def _run_for_project(self, command: str, category: str, project: str, force: bool = False):
        sp = SyncProject(self.base_path, category, project, self.token)
        if command == "status":
            sp.show_status()
        elif command == "save":
            sp.sync_save(force=force)
        elif command == "load":
            sp.sync_load(force=force)
        else:
            print(f"❌ Неизвестная операция для проекта: {command}")

    # ------------------------------------------------------------------ #
    #  Разбор аргументов                                                  #
    # ------------------------------------------------------------------ #

    def _select_targets(
        self,
        command: str,
        ctx: Dict[str, Optional[str]],
        args: List[str],
    ) -> List[Tuple[str, str]]:
        """Преобразует контекст + аргументы команды в список целей (category, project)."""
        level = ctx.get("level")
        category = ctx.get("category")
        project = ctx.get("project")

        # Запуск из-под проекта без аргументов
        if level == "project" and not args:
            return self._iter_selected_projects(("single", category, project))

        # Запуск из-под категории с одним аргументом
        if level == "category" and len(args) == 1:
            if args[0] == "all":
                return self._iter_selected_projects(("category_all", category, None))
            return self._iter_selected_projects(("category_one", category, args[0]))

        # Глобальная форма: <category> <project> | <category> all | all all
        if len(args) == 2:
            a1, a2 = args[0], args[1]
            if a1 == "all" and a2 == "all":
                return self._iter_selected_projects(("all_all", None, None))
            if a1 != "all" and a2 == "all":
                return self._iter_selected_projects(("category_all", a1, None))
            if a1 != "all" and a2 != "all":
                return self._iter_selected_projects(("single", a1, a2))

        self._print_usage(command)
        import sys
        sys.exit(1)

    def _print_usage(self, cmd: Optional[str] = None):
        base = (
            "Использование:\n"
            "  syncbase list\n"
            "  syncbase status [all all | <category> all | <category> <project>]\n"
            "  syncbase save   [-f] [all all | <category> all | <category> <project>]\n"
            "  syncbase load   [-f] [all all | <category> all | <category> <project>]\n\n"
            "Флаг -f / --force разрешает перезапись более новых файлов более старыми.\n\n"
            "При вызове из-под папки проекта: 'status' | 'save' | 'load' без аргументов.\n"
            "При вызове из-под папки категории: 'status|save|load all' или 'status|save|load <project>'."
        )
        if cmd:
            print(f"❗ Уточните аргументы для команды '{cmd}'.")
        print(base)
