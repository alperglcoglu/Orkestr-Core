"""Budget and safety rules for the core's own code (PHASE3_IMPLEMENTATION.md §3, §11)."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
PACKAGE = CORE / "orkestr_core"
SOURCES = sorted(PACKAGE.rglob("*.py"))

NETWORK_MODULES = {"socket", "urllib", "urllib3", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "websocket", "websockets"}
# uvicorn/fastapi serve the local API; they never make outbound calls.
ALLOWED = {"fastapi", "uvicorn", "pydantic"}


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_core_makes_no_network_calls():
    for f in SOURCES:
        bad = imports(f) & NETWORK_MODULES
        assert not bad, f"{f.relative_to(CORE)} imports {bad}"


def test_no_urls_in_the_core_except_the_local_api():
    for f in SOURCES:
        for url in re.findall(r"https?://[^\s\"'`)]+", f.read_text(encoding="utf-8")):
            assert url.startswith(("http://127.0.0.1", "http://{HOST}")), f"{f.relative_to(CORE)}: {url}"


def test_dependencies_are_exactly_the_approved_pinned_list():
    meta = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime = {d.split("==")[0]: d for d in meta["dependencies"]}
    assert set(runtime) == ALLOWED
    dev = {d.split("==")[0] for d in meta["optional-dependencies"]["dev"]}
    assert dev == {"pytest", "httpx"}
    assert all("==" in d for d in [*meta["dependencies"], *meta["optional-dependencies"]["dev"]]), "versions are pinned"


def test_third_party_imports_are_limited_to_the_approved_packages():
    stdlib = set(__import__("sys").stdlib_module_names)
    for f in SOURCES:
        third = {m for m in imports(f) if m not in stdlib and m != "orkestr_core"}
        assert third <= ALLOWED, f"{f.relative_to(CORE)} imports {third - ALLOWED}"


def test_forbidden_claude_flags_appear_only_in_the_executor_refusal_list():
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        for flag in ("--bare", "--fallback-model", "--dangerously-skip-permissions", "bypassPermissions"):
            if flag in text:
                assert f.name == "claude_cli.py", f"{f.name} mentions {flag}"


def test_no_paid_provider_sdk_or_api_key_handling():
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" not in text, f.name
        assert not ({"anthropic", "openai"} & imports(f)), f.name


def test_audit_events_are_never_updated_or_deleted_in_code():
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"(UPDATE|DELETE FROM)\s+audit_event", text), f.name
