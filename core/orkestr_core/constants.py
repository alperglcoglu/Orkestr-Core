"""Single source of names, IDs, and defaults for the core (MASTER_SPEC §2.4)."""

from __future__ import annotations

from pathlib import Path

# Same values as plugins/orkestr/scripts/lib/core.mjs; tests/test_parity.py checks they match.
DATA_DIR_NAME = ".orkestr"
SCHEMA_VERSION = 1

DB_FILE = "core.db"
TOKEN_FILE = "core-token"
LOCK_FILE = "core.lock"
REPORTS_DIR = "reports"

DEFAULT_LIMITS = {
    "max_meeting_participants": 4,
    "max_discussion_rounds": 2,
    "max_fix_attempts": 3,
    "max_recovery_meetings": 1,
    "max_parallel_tasks": 2,
}

# Limits that only the core enforces (PHASE3_IMPLEMENTATION.md §10). They live in the same
# project.limits object; the Phase 1 validator ignores keys it does not know.
CORE_LIMITS = {
    "max_agent_calls_per_run": 30,
    "max_agent_calls_per_task": 8,
}

AGENT_TIMEOUT_S = 900
TEST_TIMEOUT_S = 600

AUTONOMY_MODES = ("bounded", "supervised")
DEFAULT_AUTONOMY = "bounded"
MANAGED_BY = ("plugin", "core")

AGENT_ROLES = (
    "product-manager",
    "system-architect",
    "tech-lead",
    "backend-engineer",
    "frontend-engineer",
    "ml-engineer",
    "data-engineer",
    "qa-engineer",
    "security-reviewer",
    "researcher",
)
OWNERS = (*AGENT_ROLES, "orchestrator", "founder")
FOUNDER = "founder"

PREFIX = {
    "task": "T",
    "meeting": "M",
    "decision": "D",
    "approval": "A",
    "review": "R",
    "retro": "RT",
}

# Oldest Claude Code version this phase is validated with (PHASE3_IMPLEMENTATION.md §6.2).
MIN_CLAUDE_VERSION = (2, 1, 295)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "orkestr"
