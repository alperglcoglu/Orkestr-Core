"""Secret redaction, ported from plugins/orkestr/scripts/lib/core.mjs (MASTER_SPEC §11.2).

A safety net, not a guarantee. tests/test_parity.py runs this and the Node version on the
same corpus and requires identical output.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)")),
    ("api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
]

ASSIGNMENT = re.compile(
    r"\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)\b(\s*[:=]\s*)([\"']?)([^\s\"',;]{6,})\3",
    re.IGNORECASE,
)


def redact_string(text: str) -> str:
    out = text
    for _, pattern in SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)

    def repl(m: re.Match) -> str:
        key, sep, quote, value = m.group(1), m.group(2), m.group(3), m.group(4)
        return m.group(0) if value == REDACTED else f"{key}{sep}{quote}{REDACTED}{quote}"

    return ASSIGNMENT.sub(repl, out)


def redact(value):
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def find_secrets(text: str) -> list[str]:
    found: list[str] = []
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text) and name not in found:
            found.append(name)
    for m in ASSIGNMENT.finditer(text):
        if m.group(4) != REDACTED and "secret-assignment" not in found:
            found.append("secret-assignment")
    return found
