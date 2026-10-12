"""Permission policies (PHASE4_IMPLEMENTATION.md §6).

A policy only tightens the Phase 3 rules, with one exception: it can add test command forms.
It can never remove a MASTER_SPEC §10.2 approval, let anyone carry out a restricted action,
give agents more tools or network access, or raise a limit above the hard caps below.
"""

from __future__ import annotations

import json
import re
import subprocess

from ..constants import DEFAULT_LIMITS, PLUGIN_DIR
from .scope import file_in_scope, scope_problems, scopes_overlap

FIELDS = ("test_commands", "protected_paths", "approval_paths", "security_review_paths", "limits")

HARD_CAPS = {
    "max_agent_calls_per_run": 200,
    "max_agent_calls_per_task": 20,
    "max_fix_attempts": 5,
    "max_recovery_meetings": 2,
    "max_parallel_tasks": 4,
    "max_meeting_participants": 6,
    "max_discussion_rounds": 3,
    "max_reported_cost_usd_per_run": None,  # any positive value
}

# A test command prefix must name a test runner, not open a shell or an interpreter.
BARE_COMMANDS = {"sh", "bash", "zsh", "fish", "pwsh", "powershell", "cmd", "python", "python3", "py", "node", "npx", "deno", "bun", "ruby", "perl", "php"}
SHELL_META = re.compile(r"[;&|<>`$*?\\]|\r|\n")
MAX_ITEMS = 50


def empty_policy() -> dict:
    return {"test_commands": [], "protected_paths": [], "approval_paths": [], "security_review_paths": [], "limits": {}}


def normalize(policy: dict) -> dict:
    return {**empty_policy(), **{k: v for k, v in policy.items() if k in FIELDS}}


def guard_reason(command: str) -> str | None:
    """Asks the plugin's guard (the same rules the agents run under) whether a command is risky."""
    hook_input = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    try:
        r = subprocess.run(
            ["node", str(PLUGIN_DIR / "scripts" / "guard.mjs")], input=hook_input, capture_output=True, text=True, encoding="utf-8", timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"the guard could not check it ({err})"
    if not r.stdout.strip():
        return None
    try:
        out = json.loads(r.stdout)
        return out.get("hookSpecificOutput", {}).get("permissionDecisionReason") or "flagged by the guard"
    except json.JSONDecodeError:
        return "flagged by the guard"


def test_command_problem(prefix: str) -> str | None:
    if not isinstance(prefix, str) or not prefix.strip():
        return "must be a non-empty command"
    if prefix != prefix.strip() or "  " in prefix:
        return "must not have extra spaces"
    if SHELL_META.search(prefix):
        return "shell operators, wildcards, and variables are not allowed"
    words = prefix.split(" ")
    if len(words) < 2:
        return "needs at least two words, for example `go test` or `cargo test`"
    if words[0].lower() in BARE_COMMANDS:
        return f"`{words[0]}` can run any code; name a test runner instead (`python -m pytest`, `node --test`, `npm test`, and `pytest` are always allowed)"
    if words[0].lower() == "git":
        return "git commands are not test commands"
    reason = guard_reason(prefix)
    return f"flagged as risky: {reason}" if reason else None


def _string_list(policy: dict, key: str, e: list[str]) -> list[str]:
    value = policy.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        e.append(f"{key} must be a list of strings")
        return []
    if len(value) > MAX_ITEMS:
        e.append(f"{key} has more than {MAX_ITEMS} entries")
    return value


def policy_problems(policy: dict) -> list[str]:
    """Problems with a whole policy; an empty list means it may be saved."""
    if not isinstance(policy, dict):
        return ["the policy must be a JSON object"]
    e: list[str] = []
    unknown = sorted(set(policy) - set(FIELDS))
    if unknown:
        e.append(f"unknown field(s): {', '.join(unknown)} (fields: {', '.join(FIELDS)})")
    for c in _string_list(policy, "test_commands", e):
        p = test_command_problem(c)
        if p:
            e.append(f"test_commands {c!r}: {p}")
    for key in ("protected_paths", "approval_paths", "security_review_paths"):
        patterns = _string_list(policy, key, e)
        e += [f"{key}: {p}" for p in scope_problems(patterns)]
    limits = policy.get("limits", {})
    if not isinstance(limits, dict):
        e.append("limits must be an object")
        limits = {}
    for key, value in limits.items():
        if key not in HARD_CAPS:
            e.append(f"limits.{key} is not a known limit ({', '.join(HARD_CAPS)})")
            continue
        cap = HARD_CAPS[key]
        if cap is None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                e.append(f"limits.{key} must be a positive number")
        elif isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
            e.append(f"limits.{key} must be an integer from 1 to {cap}")
    return e


def effective_limits(project_limits: dict, policy: dict) -> dict:
    """Project limits with the policy's limits on top."""
    return {**DEFAULT_LIMITS, **project_limits, **(policy.get("limits") or {})}


def protected_overlap(scope: list[str] | None, policy: dict) -> list[str]:
    """Protected patterns a task scope could touch. A task without a scope may touch anything."""
    protected = policy.get("protected_paths") or []
    if not protected:
        return []
    if not scope:
        return list(protected)
    return [p for p in protected if scopes_overlap(scope, [p])]


def protected_files(files: list[str], policy: dict) -> list[str]:
    protected = policy.get("protected_paths") or []
    return [f for f in files if protected and file_in_scope(f, protected)]


def needs_approval(scope: list[str] | None, policy: dict) -> bool:
    paths = policy.get("approval_paths") or []
    return bool(paths) and (not scope or scopes_overlap(scope, paths))


def security_review(scope: list[str] | None, policy: dict) -> bool:
    paths = policy.get("security_review_paths") or []
    return bool(paths) and bool(scope) and scopes_overlap(scope, paths)


def limit_conflicts(tasks: list[dict], limits: dict) -> list[str]:
    """Tasks that already went past a lowered limit; the Phase 1 validator would reject them."""
    out = []
    for t in tasks:
        if t["fix_attempts"] > limits.get("max_fix_attempts", t["fix_attempts"]):
            out.append(f"{t['id']} has {t['fix_attempts']} fix attempts (max_fix_attempts {limits['max_fix_attempts']})")
        if t.get("recovery_meetings", 0) > limits.get("max_recovery_meetings", t.get("recovery_meetings", 0)):
            out.append(f"{t['id']} had {t['recovery_meetings']} recovery meetings (max_recovery_meetings {limits['max_recovery_meetings']})")
    return out
