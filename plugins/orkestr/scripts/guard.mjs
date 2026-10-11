#!/usr/bin/env node
// PreToolUse hook entry point. Reads the hook input JSON on stdin; prints an "ask" decision
// for risky tool calls (see lib/guard-rules.mjs) and nothing otherwise. Always exits 0 so a
// guard failure never blocks normal work; Claude Code's permission system still applies.

import { readFileSync } from 'node:fs';
import { decide, hookOutput } from './lib/guard-rules.mjs';

let input = null;
try {
  input = JSON.parse(readFileSync(0, 'utf8'));
} catch {
  input = null;
}
const reason = input ? decide(input) : null;
if (reason) process.stdout.write(JSON.stringify(hookOutput(reason)));
