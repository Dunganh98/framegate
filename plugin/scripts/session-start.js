#!/usr/bin/env node
// framegate plugin SessionStart hook: one line when the plugin is a newer version than the one
// this user last saw.
//
// Claude Code updates the plugin in the background (marketplace auto-update) and loads the new
// version in the next session; nothing else tells the user what changed. This hook does, once:
//   framegate was updated to 0.8.0: <the line for that version in whats-new.json>
// - Only in a repository with a framegate.toml (as the other hooks), so the line is not spent
//   in a session that has nothing to do with framegate.
// - The first session after an install shows nothing (there was no older version).
// - The version last seen is kept outside the plugin folder (an update replaces that folder):
//   CLAUDE_PLUGIN_DATA when Claude Code sets it, else ~/.claude/framegate.
// - Never blocks and never fails a session: any error exits 0 silently.
// The CLI is brought to the plugin's version by the skills (their "Version" step), not here.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const CONFIG = 'framegate.toml';
const ROOT = path.join(__dirname, '..');

function findConfig(start) {
  if (!start) return null;
  let dir = path.resolve(start);
  for (;;) {
    if (fs.existsSync(path.join(dir, CONFIG))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { return null; }
}

function stateFile() {
  const dir = (process.env.FRAMEGATE_STATE_DIR || process.env.CLAUDE_PLUGIN_DATA || '').trim()
    || path.join(os.homedir(), '.claude', 'framegate');
  return path.join(dir, 'seen-version.json');
}

// The notice for a session that runs `version` after the user last saw `seen` (null: none).
function notice(version, seen, notes) {
  if (!version || !seen || seen === version) return null;
  const line = String((notes && notes[version]) || '').trim();
  return `framegate was updated to ${version}` + (line ? `: ${line}` : '.');
}

function main() {
  let data = {};
  try { data = JSON.parse(fs.readFileSync(0, 'utf8')); } catch (_) { /* no or bad stdin */ }
  const starts = [data.cwd, process.env.CLAUDE_PROJECT_DIR, process.cwd()];
  if (!starts.some((s) => findConfig(s))) return;

  const plugin = readJson(path.join(ROOT, '.claude-plugin', 'plugin.json'));
  const version = plugin && typeof plugin.version === 'string' ? plugin.version : '';
  if (!version) return;
  const file = stateFile();
  const state = readJson(file);
  const seen = state && typeof state.version === 'string' ? state.version : null;
  if (seen === version) return;

  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ version }) + '\n');
  const text = notice(version, seen, readJson(path.join(ROOT, 'whats-new.json')));
  if (text) process.stdout.write(JSON.stringify({ systemMessage: text }) + '\n');
}

if (require.main === module) {
  try { main(); } catch (_) { /* never fail a session over a notice */ }
  process.exitCode = 0;
}

module.exports = { notice };
