"""Настройки агента; рабочая папка определяется только местом запуска."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from .workspace import Workspace, as_workspace

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_TOOLS = ("add", "divide", "multiply", "subtraction", "read_file",
                 "list_files", "replace_in_file", "create_file", "create_directory")

@dataclass
class Config:
    max_steps: int = 8
    model_timeout: float = 20.0
    tool_timeout: float = 5.0
    allowed_tools: tuple = DEFAULT_TOOLS
    workspace: Workspace = field(default_factory=Workspace.from_cwd)
    log_events: bool = True
    history_turns: int = 10

    def __post_init__(self):
        import math
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise ValueError("max_steps должен быть положительным целым")
        for value in (self.model_timeout, self.tool_timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Таймауты должны быть положительными конечными числами")
        self.workspace = as_workspace(self.workspace)
        if type(self.history_turns) is not int or self.history_turns < 0:
            raise ValueError("HISTORY_TURNS должен быть целым числом не меньше нуля")

    @classmethod
    def from_env(cls, workspace_root=None):
        from dotenv import load_dotenv
        # Настройки установленного агента, а не .env из обрабатываемой папки.
        load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
        tools = os.getenv("AGENT_ALLOWED_TOOLS")
        return cls(
            max_steps=int(os.getenv("AGENT_MAX_STEPS", "8")),
            model_timeout=float(os.getenv("AGENT_MODEL_TIMEOUT", "20")),
            tool_timeout=float(os.getenv("AGENT_TOOL_TIMEOUT", "5")),
            allowed_tools=tuple(t.strip() for t in tools.split(",") if t.strip())
            if tools is not None else DEFAULT_TOOLS,
            workspace=Workspace(workspace_root if workspace_root is not None else Path.cwd()),
            log_events=os.getenv("AGENT_LOG_EVENTS", "1").lower() not in ("0", "false"),
            history_turns=int(os.getenv("HISTORY_TURNS", "10")),
        )
