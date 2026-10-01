"""Реестр инструментов и строгая проверка аргументов от модели."""
import json
import math
from .workspace import as_workspace
from .file_browser import list_files
from .file_edit import replace_in_file
from .file_create import create_file, create_directory

def add(a, b):
    return a + b

def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("division_by_zero")
    return a / b

def multiply(a, b):
    return a * b

def subtraction(a, b):
    return a - b

def read_file(path, *, workspace):
    return {"content": as_workspace(workspace).read_text(path)}

TOOL_REGISTRY = {
    "add": add, "divide": divide, "multiply": multiply, "subtraction": subtraction,
    "read_file": read_file, "list_files": list_files, "replace_in_file": replace_in_file,
    "create_file": create_file, "create_directory": create_directory,
}
FILE_TOOLS = frozenset(("read_file", "list_files", "replace_in_file", "create_file", "create_directory"))
WRITE_TOOLS = frozenset(("replace_in_file", "create_file", "create_directory"))

def schema(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }}

NUMBERS = {"a": {"type": "number"}, "b": {"type": "number"}}
PATH = {"type": "string", "description": "Путь внутри текущего workspace."}
TOOL_SCHEMAS = [
    schema("add", "Сложить a и b.", NUMBERS, ["a", "b"]),
    schema("divide", "Разделить a на b.", NUMBERS, ["a", "b"]),
    schema("multiply", "Умножить a на b.", NUMBERS, ["a", "b"]),
    schema("subtraction", "Вычесть b из a.", NUMBERS, ["a", "b"]),
    schema("read_file", "Прочитать UTF-8 файл внутри workspace.", {"path": PATH}, ["path"]),
    schema("list_files", "Посмотреть файлы и папки. truncated=true означает неполную выдачу.",
           {"path": {**PATH, "default": "."},
            "recursive": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200}}, []),
    schema("replace_in_file", "После чтения файла заменить ровно одно вхождение точного фрагмента. Вернуть diff.",
           {"path": PATH, "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"}}, ["path", "old_text", "new_text"]),
    schema("create_file", "Создать новый UTF-8 файл без перезаписи. Родительская папка должна существовать.",
           {"path": PATH, "content": {"type": "string"}}, ["path", "content"]),
    schema("create_directory", "Создать каталог и недостающие родительские папки. Для существующего каталога вернуть already_exists.",
           {"path": PATH}, ["path"]),
]

def active_schemas(config):
    return [item for item in TOOL_SCHEMAS if item["function"]["name"] in config.allowed_tools]

def tool_exists(name):
    return name in TOOL_REGISTRY

def parse_arguments(name, raw):
    if not tool_exists(name):
        raise ValueError("unknown_tool")
    if not isinstance(raw, str):
        raise ValueError("invalid_arguments")
    try:
        args = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("invalid_arguments") from None
    if not isinstance(args, dict):
        raise ValueError("invalid_arguments")
    definition = next(s["function"]["parameters"] for s in TOOL_SCHEMAS
                      if s["function"]["name"] == name)
    if set(args) - set(definition["properties"]):
        raise ValueError("unexpected_fields")
    if not set(definition["required"]).issubset(args):
        raise ValueError("invalid_arguments")
    for key, value in args.items():
        if key in ("a", "b"):
            if type(value) not in (int, float):
                raise ValueError("invalid_type")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("non_finite_value")
        elif key == "recursive":
            if type(value) is not bool:
                raise ValueError("invalid_type")
        elif key == "limit":
            if type(value) is not int or not 1 <= value <= 2000:
                raise ValueError("invalid_arguments")
        elif not isinstance(value, str):
            raise ValueError("invalid_type")
        elif key == "path" and not value.strip():
            raise ValueError("invalid_path")
        elif key == "old_text" and not value:
            raise ValueError("invalid_arguments")
    return args
