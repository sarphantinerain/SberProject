"""Структурированное логирование событий harness.

Пишем JSON-строки в stderr, чтобы не мешать финальному ответу в stdout.
Секреты вырезаются, длинные тексты усечены.
"""

import json
import os
import re
import sys
import time
import uuid


SECRET_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)
MAX_TEXT = 160


def new_run_id():
    return uuid.uuid4().hex[:12]


def _sanitize(value):
    """Рекурсивно вырезаем секреты и усекаем длинные строки."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SECRET_RE.search(str(key)):
                result[key] = "[скрыто]"
            else:
                result[key] = _sanitize(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        key = os.environ.get("DEEPSEEK_API_KEY")
        if key:
            value = value.replace(key, "[ключ скрыт]")
        return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 3] + "..."
    return value


class Logger:
    """Пишет события в stderr. Если enabled=False — молчит."""

    def __init__(self, run_id=None, *, enabled=True, stream=None):
        self.run_id = run_id or new_run_id()
        self.enabled = enabled
        self.stream = stream or sys.stderr

    def event(self, stage, status, *, step=None, call_id=None, duration_ms=None, **extra):
        if not self.enabled:
            return
        record = {
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
        }
        if step is not None:
            record["step"] = step
        if call_id is not None:
            record["call_id"] = call_id
        if duration_ms is not None:
            record["duration_ms"] = round(duration_ms, 1)
        if extra:
            record.update(_sanitize(extra))
        try:
            self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.stream.flush()
        except Exception:
            # Логирование не должно ломать основной сценарий.
            pass