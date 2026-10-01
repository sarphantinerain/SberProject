"""Один запрос к агенту; workspace — текущая папка терминала."""
import argparse
from pathlib import Path
import sys
from .clients import DeepSeekModel
from .config import Config
from .diagnostics import describe_error
from .logging_setup import Logger
from .memory import MemoryStore
from . import harness

def main(argv=None):
    # UTF-8 также при перенаправлении вывода в файл/журнал на Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="sberagent", description=__doc__)
    parser.add_argument("question", nargs="*", help="Запрос пользователя")
    args = parser.parse_args(argv)
    if args.question:
        question = " ".join(args.question).strip()
    elif sys.stdin.isatty():
        try:
            question = input("Ваш запрос: ").strip()
        except EOFError:
            question = ""
    else:
        question = ""
    if not question:
        parser.error("передайте непустой запрос аргументом или в терминале")
    model = None
    try:
        root = Path.cwd().resolve(strict=True)
        config = Config.from_env(workspace_root=root)
        logger = Logger(enabled=config.log_events)
        memory = MemoryStore(config.workspace.root, config.history_turns)
        history = memory.load()
        logger.event("history", "loaded", turns=len(history) // 2)
        model = DeepSeekModel(config=config)
        result = harness.run(model, question, config, logger=logger, history=history)
        if result.reason == "max_steps":
            raise RuntimeError(f"max_steps: достигнут лимит {config.max_steps} запросов к модели")
        print(f"Ответ: {result.answer}")
        memory.append(question, result.answer)
        logger.event("history", "saved", turns=min(config.history_turns, len(history) // 2 + 1))
        return 0
    except Exception as error:
        print(f"Ошибка: {describe_error(error)}", file=sys.stderr)
        return 1
    finally:
        if model is not None:
            model.close()

if __name__ == "__main__":
    sys.exit(main())
