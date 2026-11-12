// Tests for scripts/framegate-hook.js. Run from the repo root: node --test "plugin/tests/*.test.js"
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const WRAPPER = path.join(__dirname, '..', 'scripts', 'framegate-hook.js');

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'framegate-hook-'));
}

function run(event, payload, { cwd, env = {} } = {}) {
  const r = spawnSync(process.execPath, [WRAPPER, event], {
    cwd,
    input: typeof payload === 'string' ? payload : JSON.stringify(payload),
    env: { ...process.env, CLAUDE_PROJECT_DIR: '', FRAMEGATE_AGENT: '', ...env },
    encoding: 'utf8',
  });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

function writePayload(cwd, filePath) {
  return {
    session_id: 's', cwd, hook_event_name: 'PreToolUse', tool_name: 'Write',
    tool_input: { file_path: filePath, content: 'x' },
  };
}

function framegateRepo() {
  const root = tmpdir();
  fs.writeFileSync(path.join(root, 'framegate.toml'), '');
  fs.mkdirSync(path.join(root, 'src'));
  return root;
}

const MISSING = { FRAMEGATE_BIN: 'framegate-not-installed-xyz' };
// an agent process of `framegate run` (the orchestrator sets FRAMEGATE_AGENT=<role>)
const AGENT = { FRAMEGATE_AGENT: 'implement' };

test('no framegate.toml: pre-tool-use exits 0 silently, CLI not started', () => {
  const dir = tmpdir();
  const r = run('pre-tool-use', writePayload(dir, 'a.txt'), { cwd: dir, env: MISSING });
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
});

test('no framegate.toml: stop exits 0 silently', () => {
  const dir = tmpdir();
  const r = run('stop', { cwd: dir, hook_event_name: 'Stop', stop_hook_active: false }, { cwd: dir, env: MISSING });
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
});

test('no framegate.toml: unparsable stdin still exits 0', () => {
  const dir = tmpdir();
  const r = run('pre-tool-use', 'not json', { cwd: dir, env: MISSING });
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
});

test('framegate.toml + missing CLI: pre-tool-use blocks with exit 2 and a clear message', () => {
  const root = framegateRepo();
  const sub = path.join(root, 'src');
  const r = run('pre-tool-use', writePayload(sub, 'a.py'), { cwd: sub, env: MISSING });
  assert.strictEqual(r.code, 2);
  assert.strictEqual(r.stdout, '');
  assert.match(r.stderr, /not found on PATH/);
  assert.match(r.stderr, /framegate\.toml/);
  assert.match(r.stderr, /uv tool install/);
});

test('framegate.toml + missing CLI: stop is a non-blocking error (exit 1)', () => {
  const root = framegateRepo();
  const r = run('stop', { cwd: root, hook_event_name: 'Stop', stop_hook_active: false }, { cwd: root, env: { ...MISSING, ...AGENT } });
  assert.strictEqual(r.code, 1);
  assert.match(r.stderr, /not found on PATH/);
});

test('stop outside an agent run (a chat session): exit 0 at once, CLI not started', () => {
  const root = framegateRepo();
  fs.writeFileSync(path.join(root, '.phase'), 'IMPLEMENT');
  const r = run('stop', { cwd: root, hook_event_name: 'Stop', stop_hook_active: false }, { cwd: root, env: MISSING });
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
});

test('target file inside a framegate repo is detected even when cwd is outside', () => {
  const root = framegateRepo();
  const outside = tmpdir();
  const target = path.join(root, 'src', 'new_file.py');
  const r = run('pre-tool-use', writePayload(outside, target), { cwd: outside, env: MISSING });
  assert.strictEqual(r.code, 2);
});

test('CLAUDE_PROJECT_DIR pointing at a framegate repo is detected', () => {
  const root = framegateRepo();
  const outside = tmpdir();
  const r = run('stop', { cwd: outside, hook_event_name: 'Stop' },
    { cwd: outside, env: { ...MISSING, ...AGENT, CLAUDE_PROJECT_DIR: root } });
  assert.strictEqual(r.code, 1);
});

test('framegate.toml + CLI present: stdin is passed through, stdout/stderr and exit code returned', () => {
  // Fake CLI: FRAMEGATE_BIN=node makes the wrapper run `node hook <event>`, i.e. the file `hook`
  // in the working directory. Works the same on Windows and Linux.
  const root = framegateRepo();
  fs.writeFileSync(path.join(root, 'hook'), [
    "const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
    "process.stdout.write('event=' + process.argv[2] + ' tool=' + d.tool_name);",
    "process.stderr.write('blocked');",
    'process.exit(2);',
  ].join('\n'));
  const r = run('pre-tool-use', writePayload(root, 'x.py'), { cwd: root, env: { FRAMEGATE_BIN: process.execPath } });
  assert.deepStrictEqual(r, { code: 2, stdout: 'event=pre-tool-use tool=Write', stderr: 'blocked' });
});

function shellPayload(cwd, tool, command) {
  return { session_id: 's', cwd, hook_event_name: 'PreToolUse', tool_name: tool, tool_input: { command } };
}

function fakeCli(root) {
  fs.writeFileSync(path.join(root, 'hook'), [
    "const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
    "process.stderr.write('checked ' + d.tool_name);",
    'process.exit(2);',
  ].join('\n'));
}

test('Bash/PowerShell without a phase file: exit 0, CLI not started', () => {
  const root = framegateRepo();
  fakeCli(root);
  for (const tool of ['Bash', 'PowerShell']) {
    const r = run('pre-tool-use', shellPayload(root, tool, 'ls | head'), { cwd: root, env: { FRAMEGATE_BIN: process.execPath } });
    assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
  }
});

test('Bash with the phase file (default or [paths] phase_file): the CLI checks it', () => {
  const root = framegateRepo();
  fakeCli(root);
  fs.writeFileSync(path.join(root, '.phase'), 'IMPLEMENT');
  const cli = { FRAMEGATE_BIN: process.execPath, ...AGENT };
  let r = run('pre-tool-use', shellPayload(root, 'Bash', 'ls | head'), { cwd: root, env: cli });
  assert.deepStrictEqual(r, { code: 2, stdout: '', stderr: 'checked Bash' });
  fs.writeFileSync(path.join(root, 'framegate.toml'), '[paths]\nphase_file = "out/.phase"\n');
  r = run('pre-tool-use', shellPayload(root, 'Bash', 'ls | head'), { cwd: root, env: cli });
  assert.strictEqual(r.code, 0); // .phase is not the configured file
  fs.mkdirSync(path.join(root, 'out'));
  fs.writeFileSync(path.join(root, 'out', '.phase'), 'TESTS');
  r = run('pre-tool-use', shellPayload(root, 'PowerShell', 'ls | head'), { cwd: root, env: cli });
  assert.deepStrictEqual(r, { code: 2, stdout: '', stderr: 'checked PowerShell' });
});

test('Bash during a run but outside an agent (a chat session): exit 0, CLI not started', () => {
  const root = framegateRepo();
  fakeCli(root);
  fs.writeFileSync(path.join(root, '.phase'), 'IMPLEMENT');
  const r = run('pre-tool-use', shellPayload(root, 'Bash', 'ls | head'), { cwd: root, env: { FRAMEGATE_BIN: process.execPath } });
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
});

test('Bash with a phase file but no CLI: not blocked (the permission check still applies)', () => {
  const root = framegateRepo();
  fs.writeFileSync(path.join(root, '.phase'), 'IMPLEMENT');
  const r = run('pre-tool-use', shellPayload(root, 'Bash', 'ls | head'), { cwd: root, env: { ...MISSING, ...AGENT } });
  assert.strictEqual(r.code, 1);
  assert.match(r.stderr, /not found on PATH/);
});
