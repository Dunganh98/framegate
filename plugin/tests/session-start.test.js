// Tests for scripts/session-start.js. Run from the repo root: node --test "plugin/tests/*.test.js"
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const SCRIPT = path.join(__dirname, '..', 'scripts', 'session-start.js');
const { notice } = require(SCRIPT);
const PLUGIN = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', '.claude-plugin', 'plugin.json'), 'utf8'),
);
const NOTES = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'whats-new.json'), 'utf8'));

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'framegate-start-'));
}

function run(cwd, state) {
  const r = spawnSync(process.execPath, [SCRIPT], {
    cwd,
    input: JSON.stringify({ session_id: 's', cwd, hook_event_name: 'SessionStart', source: 'startup' }),
    env: { ...process.env, CLAUDE_PROJECT_DIR: '', CLAUDE_PLUGIN_DATA: '', FRAMEGATE_STATE_DIR: state },
    encoding: 'utf8',
  });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

function repo() {
  const root = tmpdir();
  fs.writeFileSync(path.join(root, 'framegate.toml'), '');
  return root;
}

function seen(state) {
  return JSON.parse(fs.readFileSync(path.join(state, 'seen-version.json'), 'utf8')).version;
}

test('the notice: one line, only when the version changed', () => {
  assert.strictEqual(notice('0.8.0', null, { '0.8.0': 'x' }), null); // first install
  assert.strictEqual(notice('0.8.0', '0.8.0', { '0.8.0': 'x' }), null);
  assert.strictEqual(notice('0.8.0', '0.7.0', { '0.8.0': ' what changed. ' }),
    'framegate was updated to 0.8.0: what changed.');
  assert.strictEqual(notice('0.8.1', '0.7.0', { '0.8.0': 'x' }), 'framegate was updated to 0.8.1.');
});

test('every released version has its line, and it is one line', () => {
  const line = NOTES[PLUGIN.version];
  assert.ok(typeof line === 'string' && line.trim().length > 20, `whats-new.json lacks ${PLUGIN.version}`);
  assert.ok(!line.includes('\n') && line.length < 240);
});

test('first session after an install: silent, the version is remembered', () => {
  const state = tmpdir();
  const r = run(repo(), state);
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
  assert.strictEqual(seen(state), PLUGIN.version);
});

test('after an update: one user-visible line, once', () => {
  const state = tmpdir();
  const root = repo();
  fs.writeFileSync(path.join(state, 'seen-version.json'), JSON.stringify({ version: '0.1.0' }));
  const r = run(path.join(root), state);
  assert.strictEqual(r.code, 0);
  assert.deepStrictEqual(JSON.parse(r.stdout), {
    systemMessage: `framegate was updated to ${PLUGIN.version}: ${NOTES[PLUGIN.version]}`,
  });
  assert.strictEqual(seen(state), PLUGIN.version);
  assert.deepStrictEqual(run(root, state), { code: 0, stdout: '', stderr: '' }); // not again
});

test('no framegate.toml: silent, and the notice is kept for a framegate session', () => {
  const state = tmpdir();
  fs.writeFileSync(path.join(state, 'seen-version.json'), JSON.stringify({ version: '0.1.0' }));
  const r = run(tmpdir(), state);
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
  assert.strictEqual(seen(state), '0.1.0');
});

test('a broken state file or an unwritable folder never fails the session', () => {
  const state = tmpdir();
  fs.writeFileSync(path.join(state, 'seen-version.json'), 'not json');
  const r = run(repo(), state);
  assert.deepStrictEqual(r, { code: 0, stdout: '', stderr: '' });
  const file = path.join(tmpdir(), 'a-file');
  fs.writeFileSync(file, '');
  assert.strictEqual(run(repo(), path.join(file, 'below')).code, 0);
});
