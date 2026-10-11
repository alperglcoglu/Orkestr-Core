"""Reading and writing the Phase 1 file layout."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from ..constants import DATA_DIR_NAME
from ..domain.errors import OrkestrError
from ..domain.render import RENDERERS
from ..domain.state import State

RECORD_FOLDERS = ("meetings", "decisions", "reviews", "retros")


def data_dir(root: Path) -> Path:
    return Path(root) / DATA_DIR_NAME


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def json_text(data) -> str:
    """Same layout as JSON.stringify(data, null, 2) followed by a newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def jsonl_text(events: list[dict]) -> str:
    return "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events)


def write_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    # Sync folders (e.g. OneDrive) and antivirus tools can briefly lock files on Windows.
    for delay in (0.02, 0.05, 0.1, 0.2, 0.4, None):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def read_json(path: Path, root: Path):
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise OrkestrError(f"Missing file: {rel}", "E_MISSING") from None
    try:
        return json.loads(text.lstrip("﻿"))
    except json.JSONDecodeError as err:
        raise OrkestrError(f"Corrupted JSON in {rel}: {err}", "E_CORRUPT") from None


def read_phase1(root: Path) -> tuple[State, list[dict], list[str]]:
    """Loads a Phase 1 project. Returns the state, the history, and file-level problems."""
    d = data_dir(root)
    problems: list[str] = []
    project = read_json(d / "project.json", root)
    tasks_doc = read_json(d / "tasks.json", root)
    approvals_doc = read_json(d / "approvals.json", root)
    if not isinstance(project, dict) or "counters" not in project:
        raise OrkestrError("Corrupted project record: .orkestr/project.json", "E_CORRUPT")
    if not isinstance(tasks_doc, dict) or not isinstance(tasks_doc.get("tasks"), list):
        raise OrkestrError("Corrupted task list: .orkestr/tasks.json", "E_CORRUPT")
    if not isinstance(approvals_doc, dict) or not isinstance(approvals_doc.get("approvals"), list):
        raise OrkestrError("Corrupted approval list: .orkestr/approvals.json", "E_CORRUPT")
    state = State(project=project, tasks=tasks_doc["tasks"], approvals=approvals_doc["approvals"])
    for folder in RECORD_FOLDERS:
        folder_dir = d / folder
        if not folder_dir.is_dir():
            continue
        for f in sorted(folder_dir.glob("*.json")):
            rec = read_json(f, root)
            getattr(state, folder)[f.stem] = rec
            if not (folder_dir / f"{f.stem}.md").exists():
                problems.append(f"{folder}/{f.stem}.md: generated view is missing")
    history: list[dict] = []
    hist_file = d / "history.jsonl"
    if not hist_file.exists():
        problems.append("history.jsonl: missing")
    else:
        for i, line in enumerate(hist_file.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                problems.append(f"history.jsonl line {i + 1}: not valid JSON")
    for f in d.rglob("*"):
        if f.is_file() and ".tmp-" in f.name:
            problems.append(f"{f.relative_to(d).as_posix()}: leftover temporary file from an interrupted write")
    return state, history, problems


def phase1_files(state: State, history: list[dict]) -> dict[str, str]:
    """The Phase 1 files for a state, as {relative path under .orkestr/: text}."""
    files = {
        "project.json": json_text(state.project),
        "tasks.json": json_text({"schema_version": 1, "tasks": state.tasks}),
        "approvals.json": json_text({"schema_version": 1, "approvals": state.approvals}),
        "history.jsonl": jsonl_text(history),
    }
    for folder in RECORD_FOLDERS:
        for rid, rec in getattr(state, folder).items():
            files[f"{folder}/{rid}.json"] = json_text(rec)
            files[f"{folder}/{rid}.md"] = RENDERERS[folder](rec)
    return files
