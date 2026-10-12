"""The executor interface. An executor runs one agent step and returns a structured result;
it never changes project state."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal, Protocol

ToolProfile = Literal["read_only", "review", "implement"]
ResultStatus = Literal["ok", "invalid_output", "error", "usage_limit", "timeout", "cancelled"]


@dataclass
class ExecutorCapabilities:
    structured_output: bool
    tool_use: bool
    edits_files: bool
    turn_limit: bool
    usage_reporting: bool


@dataclass
class AgentRequest:
    role: str
    kind: str  # plan | implement | review | contribution | synthesis | retro
    prompt: str
    system_prompt: str
    cwd: str
    tools: ToolProfile
    output_schema: dict | None = None
    scope: list[str] = field(default_factory=list)
    max_turns: int | None = None
    timeout_s: int = 900
    model: str | None = None
    test_command_rules: list[str] = field(default_factory=list)  # permission rules for test commands
    task_id: str | None = None
    denied_paths: list[str] = field(default_factory=list)  # protected paths (policy); a guardrail, the core's Git check enforces them


@dataclass
class Usage:
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reported_cost_usd: float | None = None


@dataclass
class AgentResult:
    status: ResultStatus
    output: dict | None = None
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    session_id: str | None = None
    error: str | None = None
    model: str | None = None
    denials: list[str] = field(default_factory=list)  # tool calls the permission rules refused


class CancelToken:
    """Set by pause/cancel; executors poll it and stop their subprocess."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)


class AgentExecutor(Protocol):
    name: str

    def capabilities(self) -> ExecutorCapabilities: ...

    def run(self, request: AgentRequest, cancel: CancelToken) -> AgentResult: ...
