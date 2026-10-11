"""The Python ports must behave exactly like the Node state tool (PHASE3 §5.3, §11)."""

from __future__ import annotations

import json
import subprocess

from orkestr_core import constants
from orkestr_core.domain.redact import find_secrets, redact_string
from orkestr_core.domain.scope import file_in_scope, patterns_overlap

from conftest import REPO, needs_node

pytestmark = needs_node

LIB = (REPO / "plugins" / "orkestr" / "scripts" / "lib").as_uri()

CORPUS = [
    "plain text, no secrets",
    "sk-ant-" + "x" * 30 + " trailing",
    "key sk-proj-" + "Ab1_-" * 5,
    "ghp_" + "a1B2" * 9,
    "github_pat_" + "x1_" * 10,
    "AKIA" + "B" * 16,
    "xoxb-1234567890-abc",
    "AIza" + "z" * 35,
    "Authorization: Bearer abc.def-ghi_jkl~mno",
    'password = "hunter2hunter2"',
    "TOKEN: abcdef123456; next",
    "client_secret='s3cr3tvalue'",
    "api-key=[REDACTED]",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----\nunterminated",
    "token budget is 4 meetings",
    "Müşteri şifresi: password=çokgizli123",
]

SCOPE_CASES = [
    ("src/api/**", "src/api/users.js"),
    ("src/*.js", "src/main.js"),
    ("src/*.js", "src/lib/main.js"),
    ("src/**/x.js", "src/x.js"),
    ("src/**/x.js", "src/a/b/x.js"),
    ("SRC/Api", "src/api/v1/a.js"),
    ("docs/?.md", "docs/a.md"),
    ("src/a.js", "src/a.json"),
]


def node(script: str):
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_redaction_matches_the_node_redactor():
    script = f"""
import {{ redactString, findSecrets }} from '{LIB}/core.mjs';
const corpus = {json.dumps(CORPUS)};
console.log(JSON.stringify(corpus.map((s) => [redactString(s), findSecrets(s)])));
"""
    expected = node(script)
    for text, (redacted, found) in zip(CORPUS, expected):
        assert redact_string(text) == redacted, text
        assert sorted(find_secrets(text)) == sorted(found), text


def test_scope_matching_matches_the_node_rules():
    script = f"""
import {{ fileInScope, patternsOverlap }} from '{LIB}/scope.mjs';
const cases = {json.dumps(SCOPE_CASES)};
console.log(JSON.stringify(cases.map(([p, f]) => [fileInScope(f, [p]), patternsOverlap(p, f)])));
"""
    for (pattern, file), (in_scope, overlap) in zip(SCOPE_CASES, node(script)):
        assert file_in_scope(file, [pattern]) == in_scope, (pattern, file)
        assert patterns_overlap(pattern, file) == overlap, (pattern, file)


def test_constants_match_the_node_state_tool():
    script = f"""
import {{ DATA_DIR_NAME, DEFAULT_LIMITS, AGENT_ROLES, PREFIX }} from '{LIB}/core.mjs';
import {{ TRANSITIONS }} from '{LIB}/state.mjs';
console.log(JSON.stringify({{ DATA_DIR_NAME, DEFAULT_LIMITS, AGENT_ROLES, PREFIX, TRANSITIONS }}));
"""
    from orkestr_core.domain.state import TRANSITIONS

    js = node(script)
    assert js["DATA_DIR_NAME"] == constants.DATA_DIR_NAME
    assert js["DEFAULT_LIMITS"] == constants.DEFAULT_LIMITS
    assert tuple(js["AGENT_ROLES"]) == constants.AGENT_ROLES
    assert js["PREFIX"] == constants.PREFIX
    assert {k: list(v) for k, v in TRANSITIONS.items()} == js["TRANSITIONS"]
