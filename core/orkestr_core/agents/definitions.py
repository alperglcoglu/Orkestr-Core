"""Loads the agent roles from plugins/orkestr/agents/*.md, the single source of role prompts
shared by the plugin and the core (MASTER_SPEC §2.3)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..constants import AGENT_ROLES, PLUGIN_DIR
from ..domain.errors import OrkestrError


@dataclass(frozen=True)
class AgentDefinition:
    role: str
    description: str
    tools: tuple[str, ...]
    max_turns: int | None
    prompt: str
    source_path: str
    sha256: str


def agents_dir() -> Path:
    return Path(os.environ.get("ORKESTR_AGENTS_DIR") or PLUGIN_DIR / "agents")


def parse_agent(path: Path) -> AgentDefinition:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise OrkestrError(f"{path.name}: missing frontmatter", "E_AGENT")
    try:
        _, front, body = text.split("---", 2)
    except ValueError:
        raise OrkestrError(f"{path.name}: unterminated frontmatter", "E_AGENT") from None
    meta: dict[str, str] = {}
    for line in front.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    role = meta.get("name")
    if role not in AGENT_ROLES:
        raise OrkestrError(f"{path.name}: unknown role {role!r}", "E_AGENT")
    max_turns = int(meta["maxTurns"]) if meta.get("maxTurns", "").isdigit() else None
    tools = tuple(t.strip() for t in meta.get("tools", "").split(",") if t.strip())
    return AgentDefinition(
        role=role,
        description=meta.get("description", ""),
        tools=tools,
        max_turns=max_turns,
        prompt=body.strip(),
        source_path=path.as_posix(),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def load_agents(directory: Path | None = None) -> dict[str, AgentDefinition]:
    directory = directory or agents_dir()
    if not directory.is_dir():
        raise OrkestrError(f"Agent definitions not found in {directory}. Set ORKESTR_AGENTS_DIR to plugins/orkestr/agents.", "E_AGENT")
    agents = {}
    for f in sorted(directory.glob("*.md")):
        a = parse_agent(f)
        agents[a.role] = a
    missing = [r for r in AGENT_ROLES if r not in agents]
    if missing:
        raise OrkestrError(f"Missing agent definitions: {', '.join(missing)}", "E_AGENT")
    return agents
