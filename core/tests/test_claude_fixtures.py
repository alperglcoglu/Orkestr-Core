"""Results captured from real Claude Code 2.1.295 print-mode calls (work-order step 4).

The three calls ran through the core's own command line (headless, dontAsk, --restricted,
read-only or review tool profile) on 2026-10-12 with the founder's consent:
- success:   read a file, return structured output;
- max_turns: the same with --max-turns 1, so the turn limit is honored;
- denied:    the agent tried `echo pwned > pwned.txt`; dontAsk denied it and no file appeared.
"""

from __future__ import annotations

from pathlib import Path

from orkestr_core.executor import claude_cli as cc

FIXTURES = Path(__file__).parent / "fixtures" / "claude-2.1.295"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def test_structured_output_and_usage_are_read_from_a_real_result():
    r = cc.parse_result(load("success"), 0)
    assert r.status == "ok" and r.output == {"word": "banana"}
    assert r.usage.num_turns == 3 and r.usage.output_tokens == 139
    assert r.usage.input_tokens == 4 + 4969 + 4840, "cache tokens are counted as input"
    assert r.usage.reported_cost_usd and r.model == "claude-sonnet-5-5"
    assert r.denials == []


def test_the_turn_limit_is_honored_and_classified():
    r = cc.parse_result(load("max_turns"), 1)
    assert r.status == "error" and "maximum number of turns" in r.error


def test_denied_tool_calls_are_reported():
    r = cc.parse_result(load("denied"), 0)
    assert r.status == "ok" and r.output == {"word": "denied"}
    assert r.denials == ["Bash: echo pwned > pwned.txt"]
