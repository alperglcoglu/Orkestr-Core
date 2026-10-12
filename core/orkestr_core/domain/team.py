"""Configurable agent teams (PHASE4_IMPLEMENTATION.md §5).

Per project, each role can be switched off and given a model, a lower turn limit, and extra
founder instructions. Tool profiles, permissions, and limits are not part of a team: the core
sets them on the command line for each step, so no team value can widen what an agent may do.
"""

from __future__ import annotations

import re

from ..constants import AGENT_ROLES
from .errors import OrkestrError
from .redact import redact_string

# Planning, the fallback owner and meetings, reviews, and security reviews depend on these.
REQUIRED_ROLES = ("product-manager", "tech-lead", "qa-engineer", "security-reviewer")
# Starts with a letter or digit, so a model value can never be read as a command-line flag.
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}$")
INSTRUCTIONS_MAX = 2000
FIELDS = ("enabled", "model", "max_turns", "instructions")


def default_role(role: str) -> dict:
    return {"role": role, "enabled": True, "model": None, "max_turns": None, "instructions": None}


def default_team() -> dict[str, dict]:
    return {r: default_role(r) for r in AGENT_ROLES}


def change_problems(role: str, change: dict, definition_max_turns: int | None) -> list[str]:
    """Problems with a change to one role; an empty list means it may be saved."""
    if role not in AGENT_ROLES:
        return [f"unknown role {role!r} (roles: {', '.join(AGENT_ROLES)})"]
    e: list[str] = []
    unknown = sorted(set(change) - set(FIELDS))
    if unknown:
        e.append(f"unknown field(s): {', '.join(unknown)}")
    if "enabled" in change:
        if not isinstance(change["enabled"], bool):
            e.append("enabled must be true or false")
        elif not change["enabled"] and role in REQUIRED_ROLES:
            e.append(f"{role} is required and cannot be disabled (required: {', '.join(REQUIRED_ROLES)})")
    model = change.get("model")
    if model is not None and (not isinstance(model, str) or not MODEL_PATTERN.match(model)):
        e.append("model must be 1–64 letters, digits, '.', '-', '_', '[' or ']', starting with a letter or digit")
    turns = change.get("max_turns")
    if turns is not None:
        if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
            e.append("max_turns must be a positive integer")
        elif definition_max_turns is not None and turns > definition_max_turns:
            e.append(f"max_turns can only lower the limit; {role} allows at most {definition_max_turns}")
    text = change.get("instructions")
    if text is not None:
        if not isinstance(text, str):
            e.append("instructions must be text")
        elif len(text) > INSTRUCTIONS_MAX:
            e.append(f"instructions are limited to {INSTRUCTIONS_MAX} characters")
    return e


def apply_change(current: dict, change: dict, definition_max_turns: int | None) -> dict:
    """Returns the role row after the change. Empty strings clear model and instructions."""
    change = {k: (None if v == "" and k in ("model", "instructions") else v) for k, v in change.items()}
    problems = change_problems(current["role"], change, definition_max_turns)
    if problems:
        raise OrkestrError(f"Team change for {current['role']} rejected: " + "; ".join(problems), "E_TEAM")
    row = {**current, **change}
    if row.get("instructions"):
        row["instructions"] = redact_string(row["instructions"].strip()) or None
    return row


def enabled_roles(team: dict[str, dict]) -> list[str]:
    return [r for r in AGENT_ROLES if team.get(r, default_role(r))["enabled"]]


def system_prompt(base: str, row: dict) -> str:
    """The role prompt plus the founder's instructions, which come after it and cannot replace it."""
    if not row.get("instructions"):
        return base
    return f"{base}\n\n## Founder's instructions for this project\n\n{row['instructions']}\n"


def effective_model(row: dict, run_model: str | None) -> str | None:
    """Role model, then the run's --model, then Claude Code's default (None)."""
    return row.get("model") or run_model


def effective_max_turns(row: dict, definition_max_turns: int | None) -> int | None:
    turns = row.get("max_turns")
    if turns is None:
        return definition_max_turns
    return min(turns, definition_max_turns) if definition_max_turns is not None else turns


def differences(team: dict[str, dict]) -> dict[str, dict]:
    """Only the values that differ from the defaults, for run snapshots and the dashboard."""
    out = {}
    for role, row in team.items():
        diff = {k: row[k] for k in FIELDS if row.get(k) != default_role(role)[k]}
        if diff:
            out[role] = diff
    return out
