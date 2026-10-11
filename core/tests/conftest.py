"""Shared fixtures. No test calls a model or the network (PHASE3_IMPLEMENTATION.md §11)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from orkestr_core.domain import state as st
from orkestr_core.executor.fake import FakeExecutor
from orkestr_core.service import Project
from orkestr_core.workflow.engine import Engine

REPO = Path(__file__).resolve().parents[2]
PLUGIN_TOOL = REPO / "plugins" / "orkestr" / "scripts" / "orkestr.mjs"
HAS_NODE = shutil.which("node") is not None

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js is not installed")


def git(root: Path, *args: str):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def git_repo(root: Path):
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# Sample\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")


def node_tool(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(PLUGIN_TOOL), *args], cwd=root, capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    git_repo(tmp_path)
    p = Project(tmp_path)
    p.init("Sample", "A tiny sample project")
    return p


@pytest.fixture
def fake() -> FakeExecutor:
    return FakeExecutor()


def engine(project: Project, fake: FakeExecutor, **kw) -> Engine:
    kw.setdefault("test_timeout_s", 120)
    return Engine(project, fake, **kw)


def plan_task(title="Add module", owner="backend-engineer", scope=("src/**",), verify=("node --test tests/",), depends_on=(), **extra) -> dict:
    return {
        "title": title,
        "owner": owner,
        "description": "",
        "acceptance_criteria": ["works"],
        "depends_on": list(depends_on),
        "scope": list(scope),
        "verify": list(verify),
        "requires_approval": False,
        **extra,
    }


def plan_output(*tasks: dict) -> dict:
    return {"summary": "plan", "prd_markdown": "# PRD\n\nA tiny product.\n", "tasks": list(tasks)}


def write_files(root: Path, files: dict[str, str]):
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")


PASSING_TEST = "import test from 'node:test';\nimport assert from 'node:assert';\ntest('ok', () => assert.ok(true));\n"
FAILING_TEST = "import test from 'node:test';\nimport assert from 'node:assert';\ntest('bad', () => assert.ok(false));\n"


def implement(files: dict[str, str], tests: tuple[str, ...] = ("node --test tests/",), **extra):
    """A fake implementation step that writes files and returns an ImplementOutput."""

    def run(req):
        write_files(Path(req.cwd), files)
        return {"summary": "done", "changed_files": list(files), "test_commands": list(tests), "notes": [], **extra}

    return run


def review(verdict="PASS", findings=()):
    return {"verdict": verdict, "findings": list(findings), "assumptions": []}


def mutate(project: Project, fn):
    return project.mutate(fn)


def tasks_by_id(project: Project) -> dict[str, dict]:
    return {t["id"]: t for t in project.store.load().tasks}


__all__ = ["st"]
