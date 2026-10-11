#!/usr/bin/env node
// Creates a throwaway project folder for Phase 2 validation, next to this repository
// (outside it, so nothing it produces can be committed or pushed by accident).
// Usage: node phase2/setup-sandbox.mjs [target-dir]   (default: ../orkestr-sandbox)
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const target = path.resolve(process.argv[2] ?? path.join(repoRoot, '..', 'orkestr-sandbox'));

if (target === repoRoot || target.startsWith(repoRoot + path.sep)) {
  console.error(`Refusing: ${target} is inside the Orkestr repository. Pick a folder outside it.`);
  process.exit(1);
}
if (fs.existsSync(target) && fs.readdirSync(target).length > 0) {
  console.error(`Refusing: ${target} exists and is not empty. Delete it or pass another folder.`);
  process.exit(1);
}

fs.mkdirSync(target, { recursive: true });
fs.writeFileSync(path.join(target, 'README.md'), '# Orkestr Phase 2 sandbox\n\nThrowaway project. Do not push.\n');
fs.writeFileSync(path.join(target, '.gitignore'), 'node_modules/\n.orkestr/drafts/\n');

// Local Git only: lets `ork scope check` and the scorecard see changed files. No remote is added.
const git = (...args) => spawnSync('git', args, { cwd: target, encoding: 'utf8' });
if (git('--version').status === 0) {
  git('init', '-q');
  git('add', '-A');
  git('-c', 'user.name=orkestr-sandbox', '-c', 'user.email=sandbox@localhost', 'commit', '-q', '-m', 'sandbox start');
  console.log('Local Git repository created (no remote).');
} else {
  console.log('Git not found; continuing without it (scope checks will rely on agent reports).');
}

const pluginDir = path.join(repoRoot, 'plugins', 'orkestr');
console.log(`
Sandbox ready: ${target}

Next:
  cd "${target}"
  claude --plugin-dir "${pluginDir}" --model sonnet

Then follow phase2/README.md. When done:
  node "${path.join(repoRoot, 'phase2', 'scorecard.mjs')}" "${target}" --rerun
`);
