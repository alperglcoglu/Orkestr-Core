"""Claude Code CLI executor without a model: command line, result parsing, timeout, cancel."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

from orkestr_core.domain.errors import OrkestrError
from orkestr_core.executor import claude_cli as cc
from orkestr_core.executor.base import AgentRequest, CancelToken
from orkestr_core.workflow import verify as vf


def req(tools="implement", **kw) -> AgentRequest:
    base = dict(
        role="backend-engineer",
        kind="implement",
        prompt="do it",
        system_prompt="role",
        cwd=".",
        tools=tools,
        output_schema={"type": "object"},
        max_turns=30,
        test_command_rules=vf.permission_rules(),
    )
    base.update(kw)
    return AgentRequest(**base)


def test_the_command_line_uses_headless_json_dont_ask_and_no_forbidden_flags():
    cmd = cc.build_command("claude", req(), "sys.md", "settings.json")
    assert cmd[:4] == ["claude", "-p", "--output-format", "json"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands", "--restricted"):
        assert flag in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "30"
    joined = " ".join(cmd)
    for forbidden in cc.FORBIDDEN_FLAGS:
        assert forbidden not in joined
    assert "do it" not in cmd, "the prompt goes in on stdin"


def test_tool_profiles_never_include_agent_web_or_unrestricted_bash():
    for profile in ("read_only", "review", "implement"):
        cmd = cc.build_command("claude", req(tools=profile), "s", "t")
        tools = cmd[cmd.index("--tools") + 1].split(",")
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        assert not {"Agent", "Task", "WebFetch", "WebSearch"} & set(tools)
        assert "Bash" not in allowed, "Bash only through test-command rules"
        assert all(a.startswith("Bash(") or a in tools for a in allowed)
    read_only = cc.build_command("claude", req(tools="read_only"), "s", "t")
    assert read_only[read_only.index("--tools") + 1] == "Read,Grep,Glob"
    implement = cc.build_command("claude", req(tools="implement"), "s", "t")
    assert "Bash(node --test *)" in implement[implement.index("--allowedTools") + 1]


def test_forbidden_flags_are_refused_even_via_the_model_name():
    with pytest.raises(OrkestrError, match="forbidden"):
        cc.build_command("claude", req(model="x --dangerously-skip-permissions"), "s", "t")


def test_the_guard_hook_is_wired_into_the_settings():
    hooks = cc.guard_settings()["hooks"]["PreToolUse"][0]
    assert "Bash" in hooks["matcher"] and "Write" in hooks["matcher"]
    assert hooks["hooks"][0]["command"].endswith('guard.mjs"')


SUCCESS = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 4,
    "result": "done",
    "session_id": "abc",
    "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100, "output_tokens": 42},
    "structured_output": {"summary": "ok"},
    "modelUsage": {"claude-sonnet-5-5": {}},
}


def test_a_success_result_is_parsed_with_usage():
    r = cc.parse_result(json.dumps(SUCCESS), 0)
    assert r.status == "ok" and r.output == {"summary": "ok"}
    assert (r.usage.num_turns, r.usage.input_tokens, r.usage.output_tokens, r.usage.reported_cost_usd) == (4, 115, 42, 0.0123)
    assert r.model == "claude-sonnet-5-5" and r.session_id == "abc"


def test_json_inside_the_text_result_is_used_when_there_is_no_structured_output():
    data = {**SUCCESS, "structured_output": None, "result": 'Here:\n```json\n{"summary": "x"}\n```'}
    assert cc.parse_result(json.dumps(data), 0).output == {"summary": "x"}


def test_errors_usage_limits_and_turn_limits_are_classified():
    limit = {**SUCCESS, "is_error": True, "subtype": "success", "result": "Claude usage limit reached. Your limit will reset at 5pm"}
    assert cc.parse_result(json.dumps(limit), 1).status == "usage_limit"
    turns = {**SUCCESS, "is_error": True, "subtype": "error_max_turns"}
    r = cc.parse_result(json.dumps(turns), 1)
    assert r.status == "error" and "maximum number of turns" in r.error
    garbage = cc.parse_result("not json at all", 2, "Error: something broke")
    assert garbage.status == "error" and "something broke" in garbage.error
    assert cc.parse_result("", 1, "API Error: 429 rate limit").status == "usage_limit"


def test_secrets_in_agent_output_are_redacted():
    data = {**SUCCESS, "result": "token=ghp_" + "a1B2" * 9}
    assert "ghp_" not in cc.parse_result(json.dumps(data), 0).text


def test_versions_are_parsed_and_old_claude_is_refused(tmp_path, monkeypatch):
    assert cc.parse_version("2.1.295 (Claude Code)") == (2, 1, 295)
    assert cc.parse_version("1.0.51 (Claude Code)") < (2, 1, 295)
    fake = tmp_path / ("claude.cmd" if sys.platform == "win32" else "claude")
    fake.write_text("@echo 1.0.51 (Claude Code)\n" if sys.platform == "win32" else "#!/bin/sh\necho '1.0.51 (Claude Code)'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("ORKESTR_CLAUDE_BIN", str(fake))
    with pytest.raises(OrkestrError, match="too old"):
        cc.ClaudeCliExecutor()


def test_a_timeout_kills_the_process():
    t0 = time.monotonic()
    code, out, err, stop = cc.run_process([sys.executable, "-c", "import time; time.sleep(30)"], ".", "", 1, CancelToken())
    assert stop == "timeout"
    assert time.monotonic() - t0 < 15


def test_cancel_stops_a_running_process():
    token = CancelToken()
    threading.Timer(0.5, token.cancel).start()
    t0 = time.monotonic()
    code, out, err, stop = cc.run_process([sys.executable, "-c", "import time; time.sleep(30)"], ".", "", 60, token)
    assert stop == "cancelled"
    assert time.monotonic() - t0 < 15


def test_the_prompt_is_passed_on_stdin():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # the child decodes stdin with the locale otherwise
    code, out, err, stop = cc.run_process([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], ".", "çalış", 30, CancelToken(), env=env)
    assert stop is None and code == 0 and out.strip() == "ÇALIŞ"


def test_only_allowed_test_commands_are_run():
    assert vf.command_problem("node --test tests/") is None
    assert vf.command_problem("npm test") is None
    assert vf.command_problem("python -m pytest -q") is None
    for bad in ("node --test; rm -rf /", "npm test && git push", "curl http://x", "node script.js", "pytest $(whoami)", ""):
        assert vf.command_problem(bad), bad
