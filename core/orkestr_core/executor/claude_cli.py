"""Claude Code CLI executor (PHASE3_IMPLEMENTATION.md §6.2).

One headless `claude -p` subprocess per agent call, list arguments, no shell. The prompt
goes in on stdin. Permissions: `dontAsk` plus an explicit tool allowlist per profile, so
nothing outside the allowlist can run and nobody is ever prompted. `--restricted` ignores
the user, project, and local settings files, so a repository's own `.claude/settings.json`
cannot widen what an agent may do; only the core's `--settings` apply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..constants import MIN_CLAUDE_VERSION, PLUGIN_DIR
from ..domain.errors import OrkestrError
from ..domain.redact import redact_string
from .base import AgentRequest, AgentResult, CancelToken, ExecutorCapabilities, Usage

# These would switch billing, add a paid fallback, or bypass permissions. Never passed.
FORBIDDEN_FLAGS = (
    "--bare",
    "--fallback-model",
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "bypassPermissions",
)

PROFILE_TOOLS = {
    "read_only": ["Read", "Grep", "Glob"],
    "review": ["Read", "Grep", "Glob", "Bash"],
    "implement": ["Read", "Grep", "Glob", "Bash", "Edit", "Write"],
}

USAGE_LIMIT = re.compile(r"usage limit|rate limit|limit reached|limit will reset|quota|too many requests|overloaded", re.IGNORECASE)

# Environment markers of a parent Claude Code session; a nested headless run must not inherit them.
PARENT_SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def find_binary(configured: str | None = None) -> str | None:
    return os.environ.get("ORKESTR_CLAUDE_BIN") or configured or shutil.which("claude")


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def allowed_tools(profile: str, test_rules: list[str]) -> list[str]:
    tools = [t for t in PROFILE_TOOLS[profile] if t != "Bash"]
    if "Bash" in PROFILE_TOOLS[profile]:
        tools += [f"Bash({rule})" for rule in test_rules]
    return tools


def guard_settings(denied_paths: list[str] | None = None) -> dict:
    """PreToolUse hook running the plugin's guard. Under dontAsk an "ask" becomes a denial.
    Protected paths from the policy become deny rules for the file tools (a guardrail only;
    the core's own Git check after the call is the enforcement)."""
    guard = (PLUGIN_DIR / "scripts" / "guard.mjs").as_posix()
    settings: dict = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash|PowerShell|Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": f'node "{guard}"'}]}
            ]
        }
    }
    if denied_paths:
        settings["permissions"] = {"deny": [f"{tool}({p})" for p in denied_paths for tool in ("Edit", "Write")]}
    return settings


def build_command(binary: str, req: AgentRequest, system_prompt_file: str, settings_file: str) -> list[str]:
    cmd = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--append-system-prompt-file",
        system_prompt_file,
        "--tools",
        ",".join(PROFILE_TOOLS[req.tools]),
        "--allowedTools",
        ",".join(allowed_tools(req.tools, req.test_command_rules)),
        "--permission-mode",
        "dontAsk",
        "--restricted",
        "--settings",
        settings_file,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if req.max_turns:
        cmd += ["--max-turns", str(req.max_turns)]
    if req.output_schema:
        cmd += ["--json-schema", json.dumps(req.output_schema, separators=(",", ":"))]
    if req.model:
        cmd += ["--model", req.model]
    bad = [a for a in cmd if any(f in a for f in FORBIDDEN_FLAGS)]
    if bad:
        raise OrkestrError(f"Refusing forbidden Claude Code flags: {bad}", "E_FORBIDDEN")
    return cmd


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            value = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _denial(d: dict) -> str:
    tool = d.get("tool_name") or "tool"
    tool_input = d.get("tool_input") or {}
    detail = tool_input.get("command") or tool_input.get("file_path") or ""
    return redact_string(f"{tool}: {detail}" if detail else tool)[:300]


def parse_result(stdout: str, returncode: int | None, stderr: str = "") -> AgentResult:
    data = None
    for candidate in [stdout, *reversed((stdout or "").splitlines())]:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and value.get("type") == "result":
            data = value
            break
    if data is None:
        message = redact_string((stderr or stdout or "").strip()[-2000:]) or f"claude exited with code {returncode}"
        status = "usage_limit" if USAGE_LIMIT.search(message) else "error"
        return AgentResult(status=status, error=message)

    usage_raw = data.get("usage") or {}
    input_tokens = sum(
        usage_raw.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    ) if usage_raw else None
    usage = Usage(
        num_turns=data.get("num_turns"),
        input_tokens=input_tokens,
        output_tokens=usage_raw.get("output_tokens") if usage_raw else None,
        reported_cost_usd=data.get("total_cost_usd"),
    )
    model = next(iter(data.get("modelUsage") or {}), None)
    denials = [_denial(d) for d in data.get("permission_denials") or []]
    text = redact_string(str(data.get("result") or ""))
    if data.get("is_error") or data.get("subtype") != "success":
        detail = text or str(data.get("subtype") or "error")
        if data.get("subtype") == "error_max_turns":
            detail = f"maximum number of turns reached ({usage.num_turns})"
        status = "usage_limit" if USAGE_LIMIT.search(text) else "error"
        return AgentResult(status=status, text=text, usage=usage, session_id=data.get("session_id"), error=detail, model=model, denials=denials)
    output = data.get("structured_output")
    if not isinstance(output, dict):
        output = _extract_json(text)
    return AgentResult(status="ok", output=output, text=text, usage=usage, session_id=data.get("session_id"), model=model, denials=denials)


def kill_tree(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_process(cmd: list[str], cwd: str, stdin_text: str, timeout_s: float, cancel: CancelToken, env: dict | None = None) -> tuple[int | None, str, str, str | None]:
    """Runs a process, enforcing the timeout and cancellation. Returns (code, out, err, stop_reason)."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    out: list[str] = []
    err: list[str] = []

    def pump():
        o, e = proc.communicate(stdin_text)
        out.append(o or "")
        err.append(e or "")

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout_s
    stop = None
    while t.is_alive():
        if cancel.cancelled:
            stop = "cancelled"
        elif time.monotonic() > deadline:
            stop = "timeout"
        if stop:
            kill_tree(proc)
            break
        t.join(0.2)
    t.join(10)
    return proc.returncode, "".join(out), "".join(err), stop


class ClaudeCliExecutor:
    name = "claude-cli"

    def __init__(self, binary: str | None = None, check_version: bool = True):
        self.binary = find_binary(binary)
        if not self.binary:
            raise OrkestrError("Claude Code CLI not found. Install it, or set ORKESTR_CLAUDE_BIN to the claude executable.", "E_EXECUTOR")
        self.version = None
        if check_version:
            self.version = self._version()
            if self.version is None or self.version < MIN_CLAUDE_VERSION:
                found = ".".join(map(str, self.version)) if self.version else "unknown"
                need = ".".join(map(str, MIN_CLAUDE_VERSION))
                raise OrkestrError(
                    f"Claude Code {found} at {self.binary} is too old; orkestr-core needs {need} or newer. "
                    "Set ORKESTR_CLAUDE_BIN to a newer claude executable.",
                    "E_EXECUTOR",
                )

    def _version(self) -> tuple[int, ...] | None:
        try:
            r = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return parse_version(r.stdout)

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(structured_output=True, tool_use=True, edits_files=True, turn_limit=True, usage_reporting=True)

    def run(self, request: AgentRequest, cancel: CancelToken) -> AgentResult:
        if cancel.cancelled:
            return AgentResult(status="cancelled", error="cancelled before start")
        with tempfile.TemporaryDirectory(prefix="orkestr-") as tmp:
            sys_file = Path(tmp) / "system-prompt.md"
            sys_file.write_text(request.system_prompt, encoding="utf-8")
            settings_file = Path(tmp) / "settings.json"
            settings_file.write_text(json.dumps(guard_settings(request.denied_paths)), encoding="utf-8")
            cmd = build_command(self.binary, request, str(sys_file), str(settings_file))
            env = {k: v for k, v in os.environ.items() if k not in PARENT_SESSION_ENV}
            code, out, err, stop = run_process(cmd, request.cwd, request.prompt, request.timeout_s, cancel, env=env)
        if stop == "cancelled":
            return AgentResult(status="cancelled", error="cancelled by the founder")
        if stop == "timeout":
            return AgentResult(status="timeout", error=f"no result within {request.timeout_s} s; the process was stopped")
        return parse_result(out, code, err)
