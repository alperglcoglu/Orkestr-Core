"""Errors and small helpers shared by the domain layer."""

from __future__ import annotations

import re
from datetime import datetime, timezone


class OrkestrError(Exception):
    """A rule violation or invalid input. `code` matches the Phase 1 state tool's codes."""

    def __init__(self, message: str, code: str = "E_INVALID"):
        super().__init__(message)
        self.message = message
        self.code = code


def now() -> str:
    """ISO 8601 UTC with milliseconds, the same shape as JavaScript's toISOString()."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_id(prefix: str, n: int) -> str:
    return f"{prefix}-{n:03d}"


def is_id(prefix: str, value) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"{prefix}-\d{{3,}}", value) is not None


def non_empty(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)
