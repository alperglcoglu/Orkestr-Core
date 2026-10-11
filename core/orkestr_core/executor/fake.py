"""Scripted executor for tests (PHASE3_IMPLEMENTATION.md §6.3). No model is called.

Script entries are matched by (kind, role, task_id); the first unused matching entry wins.
An entry is an AgentResult, a dict (taken as ok output), or a callable that receives the
request and may edit files in the project before returning either of those.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from .base import AgentRequest, AgentResult, CancelToken, ExecutorCapabilities, Usage

Entry = AgentResult | dict | Callable[[AgentRequest], Any]


class FakeExecutor:
    name = "fake"

    def __init__(self):
        self._script: list[tuple[dict, Entry, bool]] = []
        self.requests: list[AgentRequest] = []
        self._lock = threading.Lock()

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(structured_output=True, tool_use=True, edits_files=True, turn_limit=True, usage_reporting=True)

    def add(self, kind: str, entry: Entry, role: str | None = None, task_id: str | None = None, repeat: bool = False) -> "FakeExecutor":
        self._script.append(({"kind": kind, "role": role, "task_id": task_id}, entry, repeat))
        return self

    def calls(self, kind: str | None = None) -> list[AgentRequest]:
        return [r for r in self.requests if kind is None or r.kind == kind]

    def _take(self, req: AgentRequest) -> Entry | None:
        with self._lock:
            self.requests.append(req)
            for i, (match, entry, repeat) in enumerate(self._script):
                if match["kind"] != req.kind:
                    continue
                if match["role"] and match["role"] != req.role:
                    continue
                if match["task_id"] and match["task_id"] != req.task_id:
                    continue
                if not repeat:
                    self._script.pop(i)
                return entry
        return None

    def run(self, request: AgentRequest, cancel: CancelToken) -> AgentResult:
        if cancel.cancelled:
            return AgentResult(status="cancelled", error="cancelled before start")
        entry = self._take(request)
        if entry is None:
            return AgentResult(status="error", error=f"fake executor has no script for {request.kind}/{request.role}/{request.task_id}")
        if callable(entry):
            entry = entry(request)
        if isinstance(entry, AgentResult):
            return entry
        return AgentResult(status="ok", output=entry, text="", usage=Usage(num_turns=1, input_tokens=100, output_tokens=50, reported_cost_usd=None), model="fake")
