"""Проверки week3 без сети и без настоящих ключей."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from local_agent import cli, harness, tools
from local_agent.config import Config
from local_agent.file_edit import WriteGuard
from local_agent.logging_setup import Logger
from local_agent.workspace import Workspace, MAX_FILE_BYTES

def call(name, **args):
    return {"id": "test-call", "function": {"name": name, "arguments": json.dumps(args)}}

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "one"
        self.root.mkdir()
        self.other = self.base / "two"
        self.other.mkdir()
        (self.root / "README.md").write_text("ONE", encoding="utf-8")
        (self.other / "README.md").write_text("TWO", encoding="utf-8")
        (self.root / ".env").write_text("DUMMY=not-a-secret", encoding="utf-8")
        self.config = Config(workspace=self.root, history_turns=0)
        self.log = io.StringIO()
        self.logger = Logger(stream=self.log)

    def execute(self, name, **args):
        return harness.execute_tool(call(name, **args), self.config, logger=self.logger, step=1)

    def test_arithmetic_and_errors(self):
        for name, a, b, expected in (
            ("add", 18, 24, 42), ("divide", 42, 6, 7),
            ("multiply", 6, 7, 42), ("subtraction", 45, 3, 42),
        ):
            self.assertEqual(self.execute(name, a=a, b=b), expected)
        self.assertEqual(self.execute("divide", a=12, b=0), {"error": "division_by_zero"})

    def test_two_workspaces_and_subfolder(self):
        self.assertEqual(self.execute("read_file", path="README.md")["content"], "ONE")
        self.config = Config(workspace=self.other)
        self.assertEqual(self.execute("read_file", path="README.md")["content"], "TWO")
        child = self.root / "nested"
        child.mkdir()
        self.config = Config(workspace=child)
        self.assertEqual(self.execute("read_file", path="../README.md")["error"], "file_access_denied")

    def test_workspace_stays_fixed_after_chdir(self):
        with contextlib.chdir(self.other):
            self.assertEqual(self.execute("read_file", path="README.md")["content"], "ONE")

    def test_outside_relative_and_absolute(self):
        for path in ("../two/README.md", str(self.other / "README.md")):
            self.assertEqual(self.execute("read_file", path=path)["error"], "file_access_denied")
        self.assertEqual(self.execute("list_files", path=str(self.other))["error"], "file_access_denied")

    def test_env_blocked_for_read_list_and_edit(self):
        self.assertEqual(self.execute("read_file", path=".env")["error"], "file_access_denied")
        self.assertEqual(self.execute("replace_in_file", path=".env",
                                     old_text="DUMMY", new_text="BAD")["error"], "file_access_denied")
        entries = self.execute("list_files")["entries"]
        self.assertIn({"path": ".env", "type": "blocked"}, entries)
        self.assertEqual((self.root / ".env").read_text(), "DUMMY=not-a-secret")

    def test_external_directory_link(self):
        link = self.root / "external"
        if os.name == "nt":
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.other)],
                                       capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        else:
            link.symlink_to(self.other, target_is_directory=True)
        self.assertEqual(self.execute("read_file", path="external/README.md")["error"], "file_access_denied")
        self.assertEqual(self.execute("list_files", path="external")["error"], "file_access_denied")
        self.assertEqual(self.execute("replace_in_file", path="external/README.md",
                                     old_text="TWO", new_text="BAD")["error"], "file_access_denied")

    def test_hardlink_is_blocked(self):
        os.link(self.other / "README.md", self.root / "alias.txt")
        self.assertEqual(self.execute("read_file", path="alias.txt")["error"], "file_access_denied")

    def test_recursive_listing_and_limit(self):
        nested = self.root / "docs" / "start"
        nested.mkdir(parents=True)
        (nested / "launch.txt").write_text("RUN", encoding="utf-8")
        listing = self.execute("list_files", recursive=True)
        self.assertFalse(listing["truncated"])
        self.assertIn({"path": "docs/start/launch.txt", "type": "file"}, listing["entries"])
        limited = self.execute("list_files", limit=1)
        self.assertEqual(len(limited["entries"]), 1)
        self.assertTrue(limited["truncated"])

    def test_exact_listing_limit_not_truncated(self):
        self.assertFalse(self.execute("list_files", limit=2)["truncated"])

    def test_replace_preserves_crlf_and_returns_diff(self):
        target = self.root / "stage.txt"
        target.write_bytes(b"STAGE=old\r\nKEEP=yes\r\n")
        result = self.execute("replace_in_file", path="stage.txt",
                              old_text="STAGE=old", new_text="STAGE=new")
        self.assertTrue(result["changed"])
        self.assertEqual(result["replacements"], 1)
        self.assertIn("-STAGE=old", result["diff"])
        self.assertIn("+STAGE=new", result["diff"])
        self.assertEqual(target.read_bytes(), b"STAGE=new\r\nKEEP=yes\r\n")

    def test_replace_errors_do_not_change_file(self):
        target = self.root / "stage.txt"
        for original, fragment, code in (
            ("STAGE=old", "absent", "replacement_not_found"),
            ("old old", "old", "replacement_ambiguous"),
            ("aaa", "aa", "replacement_ambiguous"),
        ):
            target.write_text(original, encoding="utf-8")
            self.assertEqual(self.execute("replace_in_file", path="stage.txt",
                                         old_text=fragment, new_text="new")["error"], code)
            self.assertEqual(target.read_text(), original)
        self.assertEqual(self.execute("replace_in_file", path=str(self.other / "README.md"),
                                     old_text="TWO", new_text="BAD")["error"], "file_access_denied")
        self.assertEqual((self.other / "README.md").read_text(), "TWO")

    def test_noop_and_empty_replacement(self):
        self.assertFalse(self.execute("replace_in_file", path="README.md",
                                      old_text="ONE", new_text="ONE")["changed"])
        self.assertTrue(self.execute("replace_in_file", path="README.md",
                                     old_text="ONE", new_text="")["changed"])
        self.assertEqual((self.root / "README.md").read_text(), "")

    def test_read_size_binary_missing_directory(self):
        for filename, content, code in (
            ("big.txt", b"x" * (MAX_FILE_BYTES + 1), "file_too_large"),
            ("binary.txt", b"a\x00b", "binary_file"),
            ("invalid.txt", b"\xff", "invalid_utf8"),
        ):
            (self.root / filename).write_bytes(content)
            self.assertEqual(self.execute("read_file", path=filename)["error"], code)
        self.assertEqual(self.execute("read_file", path="missing.txt")["error"], "path_not_found")
        self.assertEqual(self.execute("read_file", path=".")["error"], "not_a_file")

    def test_validation_and_allowlist(self):
        for name, args in (
            ("list_files", {"limit": True}), ("list_files", {"limit": 0}),
            ("list_files", {"recursive": "yes"}), ("add", {"a": True, "b": 2}),
            ("replace_in_file", {"path": "README.md", "old_text": "", "new_text": "x"}),
            ("read_file", {"path": "README.md", "workspace": str(self.other)}),
        ):
            self.assertIn("error", self.execute(name, **args))
        self.config.allowed_tools = ("add",)
        self.assertEqual(self.execute("read_file", path="README.md")["error"], "tool_not_allowed")
        self.assertEqual([s["function"]["name"] for s in tools.active_schemas(self.config)], ["add"])
        malformed = call("add", a=1, b=2)
        malformed["function"]["arguments"] = "{"
        self.assertEqual(harness.execute_tool(malformed, self.config)["error"], "invalid_arguments")

    def test_timeout_returns_without_waiting_and_prevents_late_write(self):
        release, finished = Event(), Event()
        original = tools.TOOL_REGISTRY["replace_in_file"]
        def delayed(**kwargs):
            try:
                release.wait(2)
                return original(**kwargs)
            finally:
                finished.set()
        self.config.tool_timeout = 0.03
        with patch.dict(tools.TOOL_REGISTRY, {"replace_in_file": delayed}):
            start = time.monotonic()
            result = self.execute("replace_in_file", path="README.md", old_text="ONE", new_text="BAD")
            elapsed = time.monotonic() - start
            release.set()
            self.assertTrue(finished.wait(2))
        self.assertEqual(result, {"error": "tool_timeout"})
        self.assertLess(elapsed, 0.5)
        self.assertEqual((self.root / "README.md").read_text(), "ONE")

    def test_timeout_reports_already_committed_result(self):
        guard = WriteGuard()
        release, finished = Event(), Event()
        result = {"changed": True}
        guard.commit(lambda: None, result)
        def delayed():
            try:
                release.wait(2)
                return result
            finally:
                finished.set()
        try:
            self.assertEqual(harness._run_with_timeout(delayed, {}, 0.01, guard=guard), result)
        finally:
            release.set()
            self.assertTrue(finished.wait(2))

    def test_file_changed_between_read_and_commit(self):
        target = self.root / "README.md"
        class ChangingGuard(WriteGuard):
            def commit(self, action, result):
                target.write_text("EDITED ELSEWHERE", encoding="utf-8")
                return super().commit(action, result)
        from local_agent.file_edit import replace_in_file
        from local_agent.workspace import FileToolError
        with self.assertRaises(FileToolError) as caught:
            replace_in_file("README.md", "ONE", "BAD", workspace=self.root, guard=ChangingGuard())
        self.assertEqual(caught.exception.code, "file_changed")
        self.assertEqual(target.read_text(), "EDITED ELSEWHERE")
        self.assertEqual(list(self.root.glob(".kerman-*")), [])

    def test_logging_includes_denial(self):
        self.execute("read_file", path="../two/README.md")
        events = [json.loads(line) for line in self.log.getvalue().splitlines()]
        self.assertTrue(any(e["stage"] == "tool_error" and e["status"] == "file_access_denied" for e in events))

    def test_real_cli_flow_with_fake_model_and_log(self):
        class FakeModel:
            def __init__(self, **kwargs):
                self.calls = 0
            def __call__(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"message": {"role": "assistant", "content": None,
                            "tool_calls": [call("add", a=18, b=24)]}, "finish_reason": "tool_calls"}
                self_result = json.loads(kwargs["messages"][-1]["content"])
                assert self_result == 42
                return {"message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}
            def close(self):
                pass
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "DeepSeekModel", FakeModel), patch.object(cli.Config, "from_env", return_value=self.config):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(cli.main(["Сложи 18 и 24"]), 0)
        self.assertIn("42", output.getvalue())
        self.assertIn('"stage": "tool_end"', errors.getvalue())

    def test_step_limit_and_model_timeout(self):
        def endless(**kwargs):
            return {"message": {"role": "assistant", "content": None,
                    "tool_calls": [call("add", a=1, b=2)]}, "finish_reason": "tool_calls"}
        self.config.max_steps = 2
        self.assertEqual(harness.run(endless, "test", self.config).reason, "max_steps")
        def slow(**kwargs):
            raise TimeoutError()
        with self.assertRaises(harness.ModelTimeout):
            harness.run(slow, "test", self.config)

    def test_env_loaded_before_config_and_cannot_override_workspace(self):
        from local_agent import config as config_module
        settings = self.base / "settings.env"
        settings.write_text("AGENT_MAX_STEPS=3\nAGENT_TOOL_TIMEOUT=0.5\nAGENT_WORKSPACE=" + str(self.other),
                            encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch.object(config_module, "ENV_PATH", settings):
            config = Config.from_env(self.root)
        self.assertEqual(config.max_steps, 3)
        self.assertEqual(config.tool_timeout, 0.5)
        self.assertEqual(config.workspace.root, self.root.resolve())

if __name__ == "__main__":
    unittest.main()
