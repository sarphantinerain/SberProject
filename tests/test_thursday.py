"""Создание объектов и память: реальные файлы, без сети и API-ключа."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from local_agent import cli, harness, memory, tools
from local_agent.config import Config
from local_agent.memory import MemoryStore, HistoryError
from local_agent.workspace import Workspace, MAX_FILE_BYTES


def call(name, **args):
    return {"id": "thursday", "function": {"name": name, "arguments": json.dumps(args)}}


class ThursdayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.config = Config(workspace=self.root, history_turns=0)
        self.storage = self.root / ".agent-memory"

    def execute(self, name, **args):
        return harness.execute_tool(call(name, **args), self.config)

    def store(self, root=None, turns=10):
        return MemoryStore(root or self.root, turns, directory=self.storage)

    def junction(self, link, target):
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            self.assertEqual(result.returncode, 0)
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_create_utf8_and_empty_file(self):
        result = self.execute("create_file", path="новый.txt", content="Привет\n")
        self.assertTrue(result["created"])
        self.assertEqual((self.root / "новый.txt").read_bytes(), "Привет\n".encode())
        self.assertEqual(self.execute("create_file", path="empty.txt", content="")["bytes"], 0)

    def test_never_overwrites_existing_file_or_directory(self):
        target = self.root / "keep.txt"
        target.write_text("ORIGINAL")
        self.assertEqual(self.execute("create_file", path="keep.txt", content="BAD")["error"], "file_already_exists")
        self.assertEqual(target.read_text(), "ORIGINAL")
        self.assertEqual(self.execute("create_file", path=".", content="BAD")["error"], "file_already_exists")

    def test_concurrent_create_only_one_wins(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda text: self.execute("create_file", path="race.txt", content=text), ["FIRST", "SECOND"]))
        self.assertEqual(sum(item.get("created", False) for item in results), 1)
        self.assertEqual(sum(item.get("error") == "file_already_exists" for item in results), 1)
        self.assertIn((self.root / "race.txt").read_text(), ("FIRST", "SECOND"))

    def test_nested_directories_and_existing_result(self):
        result = self.execute("create_directory", path="creating/dir/in/dir")
        self.assertTrue(result["created"])
        self.assertTrue((self.root / "creating/dir/in/dir").is_dir())
        repeat = self.execute("create_directory", path="creating/dir/in/dir")
        self.assertFalse(repeat["created"])
        self.assertEqual(repeat["status"], "already_exists")
        self.assertTrue(self.execute("create_file", path="creating/dir/in/dir/file.txt", content="OK")["created"])

    def test_missing_parent_and_file_as_directory(self):
        self.assertEqual(self.execute("create_file", path="missing/file.txt", content="x")["error"], "parent_not_found")
        (self.root / "file").write_text("x")
        self.assertEqual(self.execute("create_directory", path="file")["error"], "not_a_directory")
        self.assertEqual(self.execute("create_directory", path="file/child")["error"], "not_a_directory")
        self.assertEqual((self.root / "file").read_text(), "x")

    def test_creation_rejects_private_and_external_paths(self):
        for name in ("create_file", "create_directory"):
            extra = {"content": "x"} if name == "create_file" else {}
            for path in (".env", "nested/.ENV/x", ".agent-memory/data", "../outside/new", str(self.outside / "new")):
                with self.subTest(name=name, path=path):
                    self.assertEqual(self.execute(name, path=path, **extra)["error"], "file_access_denied")
        self.assertFalse((self.root / "nested").exists())
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_creation_through_external_link_blocked(self):
        self.junction(self.root / "external", self.outside)
        self.assertEqual(self.execute("create_file", path="external/new.txt", content="x")["error"], "file_access_denied")
        self.assertEqual(self.execute("create_directory", path="external/new/deep")["error"], "file_access_denied")
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_creation_validation(self):
        for name, arguments in (
            ("create_file", {"path": "x", "content": 3}),
            ("create_file", {"path": "x"}),
            ("create_directory", {"path": " "}),
            ("create_directory", {"path": "x", "workspace": str(self.outside)}),
        ):
            self.assertIn("error", self.execute(name, **arguments))
        self.assertEqual(self.execute("create_file", path="x", content="\x00")["error"], "binary_file")
        self.assertEqual(self.execute("create_file", path="x", content="a" * (MAX_FILE_BYTES + 1))["error"], "file_too_large")
        self.assertFalse((self.root / "x").exists())

    def test_timeout_blocks_both_creation_tools(self):
        for name in ("create_file", "create_directory"):
            with self.subTest(tool=name):
                release, done = Event(), Event()
                original = tools.TOOL_REGISTRY[name]
                def delayed(**kwargs):
                    try:
                        release.wait(2)
                        return original(**kwargs)
                    finally:
                        done.set()
                self.config.tool_timeout = 0.02
                extra = {"content": "x"} if name == "create_file" else {}
                with patch.dict(tools.TOOL_REGISTRY, {name: delayed}):
                    result = self.execute(name, path=name, **extra)
                    release.set()
                    self.assertTrue(done.wait(2))
                self.assertEqual(result["error"], "tool_timeout")
                self.assertFalse((self.root / name).exists())

    def test_history_reopens_and_retains_pairs_in_order(self):
        first = self.store(turns=2)
        for index in range(4):
            first.append(f"q{index}", f"a{index}")
        loaded = self.store(turns=2).load()
        self.assertEqual([m["content"] for m in loaded], ["q2", "a2", "q3", "a3"])
        self.assertEqual([m["role"] for m in loaded], ["user", "assistant"] * 2)
        with contextlib.closing(sqlite3.connect(first.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 2)

    def test_history_workspace_isolation_and_canonical_path(self):
        one, two = self.store(), self.store(self.outside)
        one.append("my code", "ALPHA")
        self.assertEqual(two.load(), [])
        two.append("my code", "BETA")
        self.assertNotEqual(one.path, two.path)
        self.assertEqual(one.path, self.store(self.root / ".").path)
        self.assertEqual(one.load()[-1]["content"], "ALPHA")
        self.assertEqual(two.load()[-1]["content"], "BETA")

    def test_history_disabled_does_not_read_or_write(self):
        disabled = self.store(turns=0)
        disabled.append("q", "a")
        self.assertFalse(self.storage.exists())
        self.store().append("old", "answer")
        before = disabled.path.read_bytes()
        self.assertEqual(disabled.load(), [])
        disabled.append("new", "ignored")
        self.assertEqual(disabled.path.read_bytes(), before)

    def test_history_limit_changes(self):
        store = self.store(turns=3)
        for index in range(3):
            store.append(str(index), str(index))
        smaller = self.store(turns=1)
        self.assertEqual(len(smaller.load()), 2)
        smaller.append("latest", "answer")
        self.assertEqual(len(store.load()), 2)

    def test_history_corruption_reports_error(self):
        store = self.store()
        self.storage.mkdir()
        store.path.write_bytes(b"not a database")
        with self.assertRaises(HistoryError):
            store.load()
        self.assertEqual(store.path.read_bytes(), b"not a database")

    def test_memory_cannot_be_read_changed_or_listed(self):
        store = self.store()
        store.append("private question", "private answer")
        path = str(store.path)
        for name, extra in (
            ("read_file", {}), ("list_files", {}), ("create_file", {"content": "BAD"}),
            ("create_directory", {}), ("replace_in_file", {"old_text": "private", "new_text": "BAD"}),
        ):
            self.assertEqual(self.execute(name, path=path, **extra)["error"], "file_access_denied")
        self.assertIn({"path": ".agent-memory", "type": "blocked"}, self.execute("list_files", recursive=True)["entries"])
        with self.assertRaises(PermissionError):
            Workspace(self.storage)
        self.junction(self.root / "memory-alias", self.storage)
        self.assertEqual(self.execute("list_files", path="memory-alias")["error"], "file_access_denied")
        os.link(store.path, self.root / "alias.sqlite3")
        self.assertEqual(self.execute("read_file", path="alias.sqlite3")["error"], "file_access_denied")

    def test_cli_passes_previous_pair_and_saves_new_one(self):
        store = self.store()
        store.append("Запомни слово", "АБРИКОС")
        captured = []
        class FakeModel:
            def __init__(self, **kwargs):
                pass
            def __call__(self, **kwargs):
                captured.extend(kwargs["messages"])
                return {"message": {"role": "assistant", "content": "АБРИКОС"}, "finish_reason": "stop"}
            def close(self):
                pass
        self.config.history_turns = 10
        with patch.object(memory, "MEMORY_DIR", self.storage), patch.object(cli.Config, "from_env", return_value=self.config):
            with patch.object(cli, "DeepSeekModel", FakeModel), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["Какое слово?"]), 0)
        self.assertEqual([m["role"] for m in captured], ["system", "user", "assistant", "user"])
        self.assertEqual(captured[2]["content"], "АБРИКОС")
        self.assertEqual(len(store.load()), 4)

    def test_failed_run_not_added_to_history(self):
        self.config.history_turns = 10
        store = self.store()
        store.append("old", "answer")
        with patch.object(memory, "MEMORY_DIR", self.storage), patch.object(cli.Config, "from_env", return_value=self.config):
            with patch.object(cli, "DeepSeekModel", side_effect=RuntimeError("test offline")), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["new question"]), 1)
        self.assertEqual(len(store.load()), 2)

    def test_history_turns_env_and_invalid_value(self):
        from local_agent import config as module
        settings = self.base / "settings.env"
        settings.write_text("HISTORY_TURNS=2\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch.object(module, "ENV_PATH", settings):
            self.assertEqual(Config.from_env(self.root).history_turns, 2)
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                Config(workspace=self.root, history_turns=value)


if __name__ == "__main__":
    unittest.main()
