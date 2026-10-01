import json
import os
from pathlib import Path
import re
import traceback


def clean(text):
    text = str(text)
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        text = text.replace(key, "[ключ скрыт]")
    text = " ".join(text.split())
    return text if len(text) <= 320 else text[:317] + "..."


def server_reason(error):
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return ""
    detail = body.get("error", body)
    return clean(detail.get("message", "")) if isinstance(detail, dict) else ""


def describe_error(error):
    name = type(error).__name__
    status = getattr(error, "status_code", None)
    if name == "APITimeoutError":
        message = "DeepSeek не ответил за отведённое время."
    elif name == "ModelTimeout":
        message = "DeepSeek не ответил за отведённое время (model_timeout)."
    elif name == "TimeoutError" and str(error) == "tool_timeout":
        message = "Инструмент не успел выполниться за отведённое время (tool_timeout)."
    elif name == "APIConnectionError":
        message = "Не удалось соединиться с DeepSeek."
    elif status in (401, 403):
        message = f"DeepSeek отклонил доступ к API (HTTP {status})."
    elif status == 429:
        message = "DeepSeek ограничил запросы (HTTP 429)."
    elif isinstance(status, int) and status >= 500:
        message = f"Сервис DeepSeek вернул ошибку (HTTP {status})."
    elif isinstance(status, int):
        detail = server_reason(error)
        if status == 400 and "messages" in detail and any(
            word in detail.lower() for word in ("invalid type", "expected a sequence", "must be a list", "must be an array")
        ):
            message = "DeepSeek отклонил запрос: поле messages имеет неверный формат (HTTP 400)."
        else:
            message = f"DeepSeek отклонил запрос (HTTP {status})."
            if detail:
                message += f" Причина сервера: {detail}"
    elif isinstance(error, ModuleNotFoundError):
        package = "python-dotenv" if error.name == "dotenv" else error.name
        message = f"Не установлена зависимость: {package}."
    elif isinstance(error, ZeroDivisionError):
        message = "Вычисление остановлено: деление на ноль."
    elif isinstance(error, json.JSONDecodeError):
        message = f"Не удалось разобрать JSON: {error.msg} (строка {error.lineno}, столбец {error.colno})."
    elif isinstance(error, KeyError):
        message = f"Не найдено имя или поле: {error}."
    elif isinstance(error, TypeError):
        missing = re.fullmatch(r"(\w+)\(\) missing \d+ required positional arguments?: (.+)", str(error))
        if missing:
            message = f"Функции {missing[1]} не переданы обязательные аргументы: {missing[2]}."
        else:
            message = f"Ошибка типов или аргументов: {error}"
    else:
        message = str(error) if isinstance(error, RuntimeError) else f"{name}: {error}"

    # Одно место в учебном коде даёт точку для исследования, не подсказывая исправление.
    root = Path(__file__).resolve().parent
    frames = [frame for frame in traceback.extract_tb(error.__traceback__)
              if Path(frame.filename).resolve().parent == root
              and Path(frame.filename).name not in {"run.py", "diagnostics.py"}]
    result = clean(message)
    if frames:
        frame = frames[-1]
        result += f"\nМесто: {Path(frame.filename).name}:{frame.lineno} ({frame.name})."
    return result
