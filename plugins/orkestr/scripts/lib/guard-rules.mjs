// Rules for the PreToolUse guard (MASTER_SPEC §10.2 "Enforcement in Phase 1").
// A guardrail, not the security boundary: it matches command text and can miss unusual forms.

const RULES = [
  {
    why: 'founder-only orkestr action (plan/decision approval, approval decision, task authorization, retry, autonomy change, or project cancellation)',
    test: (c) =>
      /orkestr\.mjs/.test(c) &&
      (/\b(plan\s+approve|decision\s+(approve|reject)|task\s+authorize|approval\s+(grant|reject|expire|cancel)|project\s+(cancel|autonomy))\b/.test(c) ||
        /--retry\b/.test(c)),
  },
  { why: 'git push to a remote repository', test: (c) => /\bgit\b[^\n;&|]*\bpush\b/.test(c) },
  {
    why: 'publishing a package or release',
    test: (c) =>
      /\b(npm|pnpm|yarn)\s+(npm\s+)?publish\b|\bcargo\s+publish\b|\btwine\s+upload\b|\bgh\s+release\s+create\b|\bdocker\s+push\b|\bgem\s+push\b/.test(c),
  },
  {
    why: 'deployment or infrastructure change',
    test: (c) =>
      /\b(vercel|netlify)\b[^\n]*(\bdeploy\b|--prod\b)|\b(fly|flyctl)\s+deploy\b|\bfirebase\s+deploy\b|\bkubectl\s+(apply|delete|replace)\b|\bterraform\s+(apply|destroy)\b|\bhelm\s+(install|upgrade|uninstall)\b|\bgh\s+workflow\s+run\b/i.test(c),
  },
  {
    why: 'destructive file or repository operation',
    test: (c) =>
      /\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*\b|--recursive\b)/.test(c) ||
      /\bRemove-Item\b[^\n]*-Recurse\b/i.test(c) ||
      /\b(rd|rmdir)\s+\/s\b/i.test(c) ||
      /\bdel\s+[^\n]*\/[sq]\b/i.test(c) ||
      /\bgit\s+reset\s+--hard\b/.test(c) ||
      /\bgit\s+clean\s+-[a-zA-Z]*f/.test(c) ||
      /\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)/.test(c) ||
      /\bgit\s+branch\s+-D\b/.test(c) ||
      /\bdrop\s+(table|database|schema)\b/i.test(c) ||
      /\btruncate\s+table\b/i.test(c) ||
      /\b(mkfs|diskpart)\b/i.test(c) ||
      /\bformat(\.com)?\s+[a-z]:/i.test(c),
  },
  {
    why: 'permission or access change',
    test: (c) =>
      /\bchmod\b[^\n]*(\b777\b|a\+rwx)/.test(c) ||
      /\bchown\s+-R\b/.test(c) ||
      /\b(icacls|takeown|cacls)\b/i.test(c) ||
      /\bSet-Acl\b/i.test(c) ||
      /\bgh\s+repo\s+edit\b[^\n]*--visibility\b/.test(c),
  },
];

const STATE_FILE =
  /(^|[\\/])\.orkestr[\\/]((project|tasks|approvals)\.json|history\.jsonl|(meetings|decisions|reviews|retros)[\\/][A-Z]{1,2}-\d+\.(json|md))$/;

/** Returns a reason string when the tool call needs founder confirmation, otherwise null. */
export function decide(input) {
  const tool = input?.tool_name;
  const ti = input?.tool_input ?? {};
  if (tool === 'Bash' || tool === 'PowerShell') {
    const command = typeof ti.command === 'string' ? ti.command : '';
    // A single state-tool invocation only records text (e.g. --action "git push"); it never runs it.
    // Chained commands are still checked against every rule.
    // Quoted arguments (e.g. --risks "a; b") are text, so they are blanked before looking for chaining.
    const unquoted = command.replace(/"(?:[^"\\]|\\.)*"|'[^']*'/g, '""');
    const stateToolOnly =
      /^\s*node(\.exe)?\s+("[^"]*orkestr\.mjs"|'[^']*orkestr\.mjs'|\S*orkestr\.mjs)(\s|$)/.test(command) && !/[;&|`\n]|\$\(/.test(unquoted);
    const rules = stateToolOnly ? RULES.slice(0, 1) : RULES;
    const hit = rules.find((r) => r.test(command));
    if (hit) return `Orkestr guard: ${hit.why}. Founder confirmation required (MASTER_SPEC §10.2).`;
  }
  if (tool === 'Write' || tool === 'Edit' || tool === 'MultiEdit') {
    const file = typeof ti.file_path === 'string' ? ti.file_path : '';
    if (STATE_FILE.test(file)) {
      return 'Orkestr guard: .orkestr state files must be changed through the orkestr state tool, not edited directly.';
    }
  }
  return null;
}

export function hookOutput(reason) {
  return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'ask', permissionDecisionReason: reason } };
}
