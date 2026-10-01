"""Создание объектов: общая проверка путей, эксклюзивное создание и WriteGuard."""
import os
from pathlib import Path

from .file_edit import WriteGuard
from .workspace import FileToolError, MAX_FILE_BYTES, as_workspace


def create_file(path, content, *, workspace, guard=None):
    if not isinstance(content, str):
        raise FileToolError("invalid_arguments")
    if "\x00" in content:
        raise FileToolError("binary_file")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise FileToolError("invalid_utf8") from error
    if len(encoded) > MAX_FILE_BYTES:
        raise FileToolError("file_too_large")
    scope = as_workspace(workspace)
    guard = guard or WriteGuard()
    guard.check()
    target = scope.resolve_for_creation(path)
    result = {"path": target.relative_to(scope.root).as_posix(),
              "created": True, "status": "created", "bytes": len(encoded)}

    def commit():
        if scope.resolve_for_creation(path) != target:
            raise FileToolError("file_changed")
        raw = Path(path)
        named = raw if raw.is_absolute() else scope.root / raw
        # В том числе не следуем через уже существующую висячую ссылку.
        if os.path.lexists(named):
            raise FileToolError("file_already_exists")
        try:
            scope.resolve(str(target.parent), kind="directory")
        except FileToolError as error:
            if error.code == "path_not_found":
                raise FileToolError("parent_not_found") from error
            raise
        guard.check()
        try:
            stream = target.open("xb")  # атомарно запрещает перезапись даже при гонке
        except FileExistsError as error:
            raise FileToolError("file_already_exists") from error
        with stream:
            owned = os.fstat(stream.fileno())
            try:
                stream.write(encoded)
                stream.flush()
            except BaseException:
                stream.close()
                # Удаляем только созданный нами незавершённый файл.
                if target.exists() and os.path.samestat(owned, target.stat()):
                    target.unlink()
                raise

    return guard.commit(commit, result)


def create_directory(path, *, workspace, guard=None):
    scope = as_workspace(workspace)
    guard = guard or WriteGuard()
    guard.check()
    target = scope.resolve_for_creation(path)
    result = {"path": target.relative_to(scope.root).as_posix(),
              "created": False, "status": "already_exists"}

    def commit():
        if scope.resolve_for_creation(path) != target:
            raise FileToolError("file_changed")
        if target.is_dir():
            return
        guard.check()
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            if not target.is_dir():
                raise FileToolError("not_a_directory") from None
        except NotADirectoryError:
            raise FileToolError("not_a_directory") from None
        else:
            result.update(created=True, status="created")

    return guard.commit(commit, result)
