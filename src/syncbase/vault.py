"""Управление одноуровневым вольтом и его проектами."""

from pathlib import Path
from typing import Literal, Optional

from .client import YandexDiskClient
from .project import SyncIgnore, SyncProject

Command = Literal["status", "save", "load"]


class SyncVault:
    """Вольт, содержащий проекты непосредственно в корневой папке."""

    def __init__(self, vault_path: Path | str, token: str):
        self.path = Path(vault_path)
        self.token = token
        self.cloud_path = Path("app:")
        self.cloud_client = YandexDiskClient(token)

    @property
    def name(self) -> str:
        return self.path.name

    def _read_syncignore(self) -> SyncIgnore:
        """Прочитать правила, исключающие проекты на уровне вольта."""
        rules = SyncIgnore()
        path = self.path / ".syncignore"
        if path.is_file():
            try:
                rules.parse_rules(path.read_text(encoding="utf-8"))
            except OSError as exc:
                print(f"⚠️ Не удалось прочитать .syncignore вольта: {exc}")
        return rules

    def get_local_projects(self) -> list[str]:
        if not self.path.is_dir():
            return []
        rules = self._read_syncignore()
        return [
            entry.name
            for entry in sorted(self.path.iterdir())
            if entry.is_dir()
            and not rules.should_ignore(entry.name, is_directory=True)
        ]

    def get_cloud_projects(self) -> list[str]:
        rules = self._read_syncignore()
        return sorted(
            item["name"]
            for item in (self.cloud_client.list(self.cloud_path) or [])
            if item.get("type") == "dir"
            and isinstance(item.get("name"), str)
            and not rules.should_ignore(item["name"], is_directory=True)
        )

    def get_projects(self) -> list[str]:
        """Вернуть объединение локальных и облачных проектов."""
        return sorted(set(self.get_local_projects()) | set(self.get_cloud_projects()))

    def resolve_project(self, cwd: Path | str) -> Optional[str]:
        """Определить проект по первому сегменту пути относительно вольта."""
        try:
            relative = Path(cwd).resolve().relative_to(self.path.resolve())
        except ValueError:
            return None
        if not relative.parts:
            return None
        return relative.parts[0]

    def project(self, name: str) -> SyncProject:
        """Создать синхронизатор проекта, проверив одноуровневое имя."""
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            raise ValueError(f"Некорректное имя проекта: {name!r}")
        return SyncProject(self.path, name, self.token)

    def run_project(
        self,
        command: Command,
        project_name: str,
        force: bool = False,
    ) -> None:
        project = self.project(project_name)
        local_exists = project.local_path.is_dir()

        if command == "save" and not local_exists:
            print(
                f"⚠️ Пропуск save для {project_name}: локального проекта нет. "
                "Используйте 'load'."
            )
            return
        if command == "status" and not local_exists:
            print(
                f"📊 {project_name}: локального проекта нет. "
                "Выполните 'load' для восстановления."
            )
            return

        if command == "status":
            project.show_status()
        elif command == "save":
            project.sync_save(force=force)
        else:
            project.sync_load(force=force)

    def run_all(self, command: Command, force: bool = False) -> None:
        projects = self.get_projects()
        if not projects:
            print("⚠️ Не найдено ни одного проекта для обработки.")
            return
        for project_name in projects:
            self.run_project(command, project_name, force=force)

    def show_projects(self) -> None:
        """Показать одноуровневый список проектов вольта."""
        local = set(self.get_local_projects())
        cloud = set(self.get_cloud_projects())
        projects = sorted(local | cloud)

        print(f"📦 Вольт: {self.name}")
        print(f"   локально: {self.path}")
        print("   облако: app:/")
        if not projects:
            print("   (проектов нет)")
            return

        for name in projects:
            marks = []
            if name in local:
                marks.append("local")
            if name in cloud:
                marks.append("cloud")
            print(f"   - {name} [{'/'.join(marks)}]")
