"""Подключите точечную замену в UTF-8-файле и возвращайте модели результат с diff."""

import difflib
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock
from time import monotonic

from .workspace import FileToolError, MAX_FILE_BYTES, as_workspace


class WriteGuard:
    """Свяжите guard с таймаутом в harness: отмена должна запрещать позднюю запись."""

    def __init__(self, seconds=None):
        self.deadline = None if seconds is None else monotonic() + seconds
        self._lock = Lock()
        self._cancelled = False
        self._result = None

    def check(self):
        if self._cancelled or (self.deadline is not None and monotonic() >= self.deadline):
            raise FileToolError("tool_timeout")

    def commit(self, action, result):
        with self._lock:
            self.check()
            action()
            self._result = result
        return result

    def cancel(self):
        with self._lock:
            self._cancelled = True
            # Проверьте возвращённый результат в обработчике таймаута:
            # запись могла успеть завершиться до отмены.
            return self._result


def replace_in_file(path, old_text, new_text, *, workspace, guard=None):
    if (not isinstance(path, str) or not path.strip()
            or not isinstance(old_text, str) or not old_text
            or not isinstance(new_text, str)):
        raise FileToolError("invalid_arguments")
    scope = as_workspace(workspace)
    guard = guard or WriteGuard()
    guard.check()
    target, original = scope.read_bytes(path)
    try:
        text = original.decode("utf-8")
    except UnicodeError as error:
        raise FileToolError("invalid_utf8") from error
    if "\x00" in text or "\x00" in old_text or "\x00" in new_text:
        raise FileToolError("binary_file")
    first = text.find(old_text)
    if first < 0:
        raise FileToolError("replacement_not_found")
    # Совпадения могут перекрываться. Проверьте замену 'aa' в строке 'aaa'.
    if text.find(old_text, first + 1) >= 0:
        raise FileToolError("replacement_ambiguous")
    updated = text.replace(old_text, new_text, 1)
    encoded = updated.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise FileToolError("file_too_large")
    relative = target.relative_to(scope.root).as_posix()
    diff_lines = difflib.unified_diff(
        text.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile=relative, tofile=relative,
    )
    diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in diff_lines)
    result = {
        "path": relative, "changed": updated != text,
        "replacements": 1 if updated != text else 0,
        "diff": diff[:8000], "diff_truncated": len(diff) > 8000,
    }
    if updated == text:
        return result
    guard.check()
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".kerman-", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))

        def commit():
            current, content = scope.read_bytes(path)
            if current != target or content != original:
                raise FileToolError("file_changed")
            guard.check()
            # Только после повторной проверки заменяем файл целиком.
            # До этого момента новый текст находится во временном файле.
            os.replace(temporary, target)

        return guard.commit(commit, result)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
