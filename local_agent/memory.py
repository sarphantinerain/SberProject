"""По одной SQLite-базе с парами вопрос–ответ на канонический workspace."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
from .workspace import MEMORY_ROOT

MEMORY_DIR = MEMORY_ROOT


class HistoryError(RuntimeError):
    pass


class MemoryStore:
    def __init__(self, workspace, turns=10, *, directory=None):
        if type(turns) is not int or turns < 0:
            raise ValueError("HISTORY_TURNS должен быть целым числом не меньше нуля")
        identity = os.path.normcase(str(Path(workspace).resolve(strict=True)))
        name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".sqlite3"
        self.path = Path(directory if directory is not None else MEMORY_DIR) / name
        self.turns = turns

    def load(self):
        if self.turns == 0 or not self.path.exists():
            return []
        try:
            # mode=ro не создаёт базу и не изменяет её при чтении истории.
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                rows = db.execute("SELECT question, answer FROM turns ORDER BY id DESC LIMIT ?",
                                  (self.turns,)).fetchall()
            messages = []
            for question, answer in reversed(rows):
                if not isinstance(question, str) or not isinstance(answer, str):
                    raise HistoryError("Некорректный формат сохранённой истории.")
                messages.extend(({"role": "user", "content": question},
                                 {"role": "assistant", "content": answer}))
            return messages
        except sqlite3.Error as error:
            raise HistoryError("Не удалось прочитать память агента; проверьте файл истории в .agent-memory.") from error

    def append(self, question, answer):
        if self.turns == 0:
            return
        if not isinstance(question, str) or not isinstance(answer, str):
            raise ValueError("История сохраняет только текстовые пары вопрос–ответ")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL)")
                db.execute("INSERT INTO turns(question, answer) VALUES (?, ?)", (question, answer))
                db.execute("DELETE FROM turns WHERE id NOT IN (SELECT id FROM turns ORDER BY id DESC LIMIT ?)",
                           (self.turns,))
        except sqlite3.Error as error:
            raise HistoryError("Не удалось сохранить память агента в .agent-memory.") from error
