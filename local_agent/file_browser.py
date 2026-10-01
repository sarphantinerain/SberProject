"""Подключите list_files к реестру инструментов и передавайте workspace из harness."""

from collections import deque
import os
from pathlib import Path

from .workspace import FileToolError, as_workspace


def list_files(path=".", recursive=False, limit=200, *, workspace):
    if not isinstance(path, str) or not path.strip():
        raise FileToolError("invalid_arguments")
    if type(recursive) is not bool or type(limit) is not int or not 1 <= limit <= 2000:
        raise FileToolError("invalid_arguments")
    scope = as_workspace(workspace)
    start = scope.resolve(path, kind="directory")
    pending = deque([start.relative_to(scope.root)])
    seen = set()
    entries = []
    truncated = False
    while pending:
        relative_dir = pending.popleft()
        directory = scope.resolve(relative_dir.as_posix(), kind="directory")
        if directory in seen:
            continue
        seen.add(directory)
        # Читаем имена постепенно и останавливаемся по limit.
        # Подумайте, зачем модели знать, что выдача обрезана.
        with os.scandir(directory) as children:
            for child in children:
                if len(entries) >= limit:
                    truncated = True
                    break
                relative = relative_dir / child.name
                try:
                    target = scope.resolve(relative.as_posix())
                    kind = "directory" if target.is_dir() else "file"
                except (FileToolError, OSError):
                    # Модель увидит имя заблокированного элемента внутри workspace.
                    # Внешний путь, на который ведёт ссылка, в ответ не передаём.
                    entries.append({"path": relative.as_posix(), "type": "blocked"})
                    continue
                entries.append({"path": relative.as_posix(), "type": kind})
                if recursive and kind == "directory" and target not in seen:
                    pending.append(relative)
        if truncated:
            break
    entries.sort(key=lambda item: item["path"])
    return {"entries": entries, "truncated": truncated}
