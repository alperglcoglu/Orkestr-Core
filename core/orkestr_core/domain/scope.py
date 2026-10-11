"""File-scope rules for parallel implementation, ported from scope.mjs (MASTER_SPEC §5.3).

A task's scope is a list of path patterns relative to the project root:
  "src/api/**"   everything under src/api
  "src/api"      the file or directory src/api (and everything under it)
  "src/*.js"     .js files directly in src
An empty scope means "exclusive": the task may touch anything, so it runs alone.
Overlap detection is conservative, and matching is case-insensitive.
"""

from __future__ import annotations

import re

WILDCARD = re.compile(r"[*?\[\]{}]")


def normalize_pattern(p: str) -> str:
    p = p.strip().replace("\\", "/")
    p = re.sub(r"^\./+", "", p)
    p = re.sub(r"/+", "/", p)
    p = re.sub(r"/$", "", p)
    return p.lower()


def scope_problems(scope) -> list[str]:
    if scope is None:
        return []
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
        return ['"scope" must be a list of path patterns']
    problems = []
    for raw in scope:
        p = raw.strip().replace("\\", "/")
        if not p:
            problems.append("scope patterns must not be empty")
        elif re.match(r"^([a-zA-Z]:|/)", p):
            problems.append(f'scope "{raw}" must be relative to the project root')
        elif ".." in p.split("/"):
            problems.append(f'scope "{raw}" must not contain ".."')
        elif re.match(r"^\.orkestr(/|$)", p, re.IGNORECASE):
            problems.append(f'scope "{raw}" must not include .orkestr/ (state changes go through the state tool)')
    return problems


def _literal_segments(pattern: str) -> list[str]:
    out = []
    for seg in normalize_pattern(pattern).split("/"):
        if WILDCARD.search(seg):
            break
        out.append(seg)
    return out


def _is_prefix(a: list[str], b: list[str]) -> bool:
    return len(a) <= len(b) and all(seg == b[i] for i, seg in enumerate(a))


def patterns_overlap(a: str, b: str) -> bool:
    la, lb = _literal_segments(a), _literal_segments(b)
    if not la or not lb:
        return True
    return _is_prefix(la, lb) or _is_prefix(lb, la)


def scopes_overlap(a: list[str] | None, b: list[str] | None) -> bool:
    a, b = a or [], b or []
    if not a or not b:
        return True
    return any(patterns_overlap(x, y) for x in a for y in b)


def _pattern_regex(pattern: str) -> re.Pattern:
    p = normalize_pattern(pattern)
    if not WILDCARD.search(p):
        return re.compile("^" + re.escape(p) + "(/.*)?$")
    out = ""
    i = 0
    while i < len(p):
        c = p[i]
        if c == "*" and i + 1 < len(p) and p[i + 1] == "*":
            out += ".*"
            i += 1
            if i + 1 < len(p) and p[i + 1] == "/":
                i += 1
        elif c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


def file_in_scope(file: str, scope: list[str] | None) -> bool:
    scope = scope or []
    if not scope:
        return True
    f = normalize_pattern(file)
    return any(_pattern_regex(p).match(f) for p in scope)
