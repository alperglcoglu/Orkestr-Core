"""Verification the core does itself: changed-file detection with Git and test commands.

Agent claims are never evidence. The core snapshots the working tree before an
implementation call, compares afterwards, and runs the test commands itself.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..constants import DATA_DIR_NAME
from ..domain.errors import OrkestrError
from ..domain.redact import redact_string
from ..executor.base import CancelToken
from ..executor.claude_cli import run_process

# Default test command forms (PHASE3_IMPLEMENTATION.md §7.2 step 5). The second element is the
# Claude Code permission rule that lets an agent run the same commands.
TEST_COMMANDS = [
    (re.compile(r"^node --test( |$)"), "node --test *"),
    (re.compile(r"^npm (run )?test( |$)"), "npm test *"),
    (re.compile(r"^(python|python3|py) -m pytest( |$)"), "python -m pytest *"),
    (re.compile(r"^pytest( |$)"), "pytest *"),
]
TEST_COMMAND_FORMS = ["node --test …", "npm test", "python -m pytest …", "pytest …"]
SHELL_META = re.compile(r"[;&|<>`$]|\r|\n")
OUTPUT_TAIL = 8 * 1024


def command_forms(extra: list[str] | tuple = ()) -> list[str]:
    """The allowed forms for prompts: the defaults plus the policy's test commands."""
    return [*TEST_COMMAND_FORMS, *(f"{e} …" for e in extra)]


def permission_rules(extra: list[str] | tuple = ()) -> list[str]:
    rules = [rule for _, rule in TEST_COMMANDS] + ["npm test"]
    for e in extra:
        rules += [e, f"{e} *"]
    return rules


def command_problem(command: str, extra: list[str] | tuple = ()) -> str | None:
    """`extra` are the policy's test command prefixes (PHASE4_IMPLEMENTATION.md §6.2), already
    checked by domain/policy.py; a command matches one when it is the prefix or starts with it."""
    c = command.strip()
    if not c:
        return "empty command"
    if SHELL_META.search(c):
        return "shell operators are not allowed; use one exact command"
    if not any(p.match(c) for p, _ in TEST_COMMANDS) and not any(c == e or c.startswith(e + " ") for e in extra):
        return f"not an allowed test command (allowed: {', '.join(command_forms(extra))})"
    return None


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise OrkestrError(f"git failed: {err}", "E_GIT") from None
    if r.returncode != 0:
        raise OrkestrError(f"git {' '.join(args)} failed: {r.stderr.strip()}", "E_GIT")
    return r.stdout


def require_git(root: Path):
    """Scope verification needs Git; without it the core refuses implementation steps."""
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree").strip()
    except OrkestrError:
        inside = ""
    if inside != "true":
        raise OrkestrError(
            "The project is not a Git repository. orkestr-core needs Git to check which files an agent changed; "
            "run `git init` in the project (no remote is needed) and try again.",
            "E_NO_GIT",
        )


def _dirty_paths(root: Path) -> list[str]:
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":
            i += 1  # the next entry is the rename source
        paths.append(path)
    return [p for p in paths if not (p == DATA_DIR_NAME or p.startswith(DATA_DIR_NAME + "/"))]


def _hash(root: Path, rel: str) -> str:
    f = root / rel
    try:
        return hashlib.sha256(f.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return "missing"


def snapshot(root: Path) -> dict[str, str]:
    """Hashes of the files that differ from HEAD (or are untracked) right now."""
    return {p: _hash(root, p) for p in _dirty_paths(root)}


def changed_since(root: Path, before: dict[str, str]) -> tuple[list[str], list[str]]:
    """Returns (changed, reverted): files whose content changed since the snapshot, and files
    that were dirty then and are back at their committed state (or deleted when untracked).
    A revert undoes earlier work, so it is reported but not counted against the scope."""
    now = snapshot(root)
    changed = sorted(p for p, h in now.items() if before.get(p) != h)
    reverted = sorted(p for p in before if p not in now)
    return changed, reverted


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    output_tail: str
    passed: bool
    error: str | None = None


def _resolve(argv: list[str]) -> list[str]:
    exe = shutil.which(argv[0])
    if exe:
        return [exe, *argv[1:]]
    if argv[0] == "python":
        return [sys.executable, *argv[1:]]
    return argv


def run_test_command(root: Path, command: str, timeout_s: int, cancel: CancelToken | None = None, extra: list[str] | tuple = ()) -> CommandResult:
    problem = command_problem(command, extra)
    if problem:
        return CommandResult(command, None, 0, False, "", False, error=problem)
    argv = _resolve(shlex.split(command, posix=True))
    start = time.monotonic()
    try:
        code, out, err, stop = run_process(argv, str(root), "", timeout_s, cancel or CancelToken())
    except OSError as err_:
        return CommandResult(command, None, 0, False, "", False, error=f"could not start: {err_}")
    duration = int((time.monotonic() - start) * 1000)
    tail = redact_string((out + ("\n" + err if err else ""))[-OUTPUT_TAIL:])
    if stop:
        return CommandResult(command, code, duration, stop == "timeout", tail, False, error=f"stopped: {stop}")
    return CommandResult(command, code, duration, False, tail, code == 0)
