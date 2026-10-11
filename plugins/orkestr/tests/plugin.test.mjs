// Plugin structure and budget-rule tests (PHASE1_IMPLEMENTATION.md §2–§4, §9; MASTER_SPEC §13).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { PLUGIN_ROOT, project } from './helpers.mjs';
import { AGENT_ROLES } from '../scripts/lib/core.mjs';

const read = (rel) => fs.readFileSync(path.join(PLUGIN_ROOT, rel), 'utf8');
const exists = (rel) => fs.existsSync(path.join(PLUGIN_ROOT, rel));

function frontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/.exec(text);
  assert.ok(m, 'missing YAML frontmatter');
  const fields = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z][\w-]*):\s*(.*)$/.exec(line);
    if (kv) fields[kv[1]] = kv[2].replace(/^"(.*)"$/, '$1').trim();
  }
  return { fields, body: text.slice(m[0].length) };
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : [full];
  });
}

const manifest = JSON.parse(read('.claude-plugin/plugin.json'));
const SKILLS = ['run', 'plan', 'meeting', 'build', 'review', 'status', 'retro'];

test('manifest is valid JSON with a kebab-case name and required metadata', () => {
  assert.equal(manifest.name, 'orkestr');
  assert.match(manifest.name, /^[a-z][a-z0-9-]*$/);
  for (const k of ['version', 'description']) assert.ok(manifest[k], k);
  assert.ok(manifest.author?.name);
  assert.ok(!fs.readdirSync(path.join(PLUGIN_ROOT, '.claude-plugin')).some((f) => f !== 'plugin.json'), 'only plugin.json inside .claude-plugin/');
});

test('exactly the six entry points exist, as founder-invoked skills, with no commands/ duplicates', () => {
  assert.ok(!exists('commands'), 'commands/ must not exist (skills only)');
  const dirs = fs.readdirSync(path.join(PLUGIN_ROOT, 'skills')).sort();
  assert.deepEqual(dirs, [...SKILLS].sort());
  for (const s of SKILLS) {
    const { fields, body } = frontmatter(read(`skills/${s}/SKILL.md`));
    assert.equal(fields.name, s);
    assert.ok(fields.description && fields.description.length > 20, `${s}: description`);
    assert.equal(fields['disable-model-invocation'], 'true', `${s}: must be founder-invoked only`);
    assert.match(body, /\$\{CLAUDE_PLUGIN_ROOT\}\/reference\/policies\.md/, `${s}: loads shared policies`);
    assert.ok(body.split('\n').length < 500, `${s}: keep SKILL.md under 500 lines`);
  }
});

test('all ten agents exist with bounded turns and no ability to spawn agents', () => {
  assert.equal(AGENT_ROLES.length, 10);
  assert.ok(!AGENT_ROLES.includes('devops-engineer'), 'DevOps is deliberately not part of this phase');
  const files = fs.readdirSync(path.join(PLUGIN_ROOT, 'agents')).filter((f) => f.endsWith('.md')).sort();
  assert.deepEqual(files, AGENT_ROLES.map((r) => `${r}.md`).sort());
  for (const role of AGENT_ROLES) {
    const { fields, body } = frontmatter(read(`agents/${role}.md`));
    assert.equal(fields.name, role);
    assert.ok(fields.description, `${role}: description`);
    const turns = Number(fields.maxTurns);
    assert.ok(Number.isInteger(turns) && turns > 0 && turns <= 30, `${role}: maxTurns must be 1..30`);
    const tools = (fields.tools ?? '').split(',').map((t) => t.trim()).filter(Boolean);
    assert.ok(tools.length > 0, `${role}: explicit tools allowlist required`);
    for (const forbidden of ['Agent', 'Task']) assert.ok(!tools.includes(forbidden), `${role}: must not have ${forbidden}`);
    for (const ignored of ['permissionMode', 'hooks', 'mcpServers']) assert.ok(!(ignored in fields), `${role}: ${ignored} is ignored for plugin agents`);
    assert.match(body, /\.orkestr/, `${role}: states that it does not write project state`);
  }
});

test('hook config uses exec form with node and points at an existing script', () => {
  const hooks = JSON.parse(read('hooks/hooks.json'));
  const entries = hooks.hooks.PreToolUse;
  assert.equal(entries.length, 1);
  const h = entries[0].hooks[0];
  assert.equal(h.type, 'command');
  assert.equal(h.command, 'node');
  assert.deepEqual(h.args, ['${CLAUDE_PLUGIN_ROOT}/scripts/guard.mjs']);
  assert.ok(exists('scripts/guard.mjs'));
  assert.match(entries[0].matcher, /Bash/);
  assert.match(entries[0].matcher, /PowerShell/);
});

test('every ${CLAUDE_PLUGIN_ROOT} path referenced by skills and references exists', () => {
  const texts = [...SKILLS.map((s) => `skills/${s}/SKILL.md`), 'reference/policies.md', 'reference/data-model.md'];
  for (const rel of texts) {
    for (const m of read(rel).matchAll(/\$\{CLAUDE_PLUGIN_ROOT\}\/([\w./-]+)/g)) {
      const target = m[1].replace(/[.,)]+$/, '');
      assert.ok(exists(target), `${rel} references missing ${target}`);
    }
  }
});

test('namespace references match the manifest name (rename safety)', () => {
  const texts = [
    ...SKILLS.map((s) => `skills/${s}/SKILL.md`),
    ...AGENT_ROLES.map((r) => `agents/${r}.md`),
    'reference/policies.md',
  ];
  for (const rel of texts) {
    for (const m of read(rel).matchAll(/(?:^|[\s`/(])([a-z][a-z0-9-]*):(run|plan|meeting|build|review|status|retro|product-manager|system-architect|tech-lead|backend-engineer|frontend-engineer|ml-engineer|data-engineer|qa-engineer|security-reviewer|researcher)\b/g)) {
      assert.equal(m[1], manifest.name, `${rel}: "${m[0].trim()}" uses a different namespace`);
    }
  }
});

test('budget rules: no MCP/LSP servers, no bin/, no package dependencies, no URLs in scripts', () => {
  assert.ok(!exists('.mcp.json'), 'no MCP servers');
  assert.ok(!exists('.lsp.json'), 'no LSP servers');
  assert.ok(!('mcpServers' in manifest) && !('lspServers' in manifest));
  assert.ok(!exists('bin'), 'no bin/ directory');
  if (exists('package.json')) {
    const pkg = JSON.parse(read('package.json'));
    assert.ok(!pkg.dependencies && !pkg.devDependencies, 'no package dependencies');
  }
  for (const file of walk(path.join(PLUGIN_ROOT, 'scripts'))) {
    const text = fs.readFileSync(file, 'utf8');
    assert.doesNotMatch(text, /https?:\/\//, `${path.basename(file)} must not contain URLs`);
    for (const m of text.matchAll(/from\s+'([^']+)'/g)) {
      assert.ok(m[1].startsWith('node:') || m[1].startsWith('.'), `${path.basename(file)} imports non-stdlib module ${m[1]}`);
    }
  }
});

test('templates are accepted by the state tool', (t) => {
  const p = project(t);
  const tpl = (name) => JSON.parse(read(`reference/templates/${name}`));
  p.ok('plan', 'import', p.draft('plan.json', tpl('plan-draft.json')));
  p.ok('meeting', 'record', p.draft('meeting.json', tpl('meeting-draft.json')));
  p.ok('review', 'record', p.draft('review.json', tpl('review-draft.json')));
  p.ok('retro', 'record', p.draft('retro.json', tpl('retro-draft.json')));
  p.ok('validate');
});
