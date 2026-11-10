#!/usr/bin/env node
// agentflow plugin hook wrapper: `node agentflow-hook.js <event>` -> `agentflow hook <event>`.
//
// The plugin is installed user-wide, so this runs on every Write/Edit and every Stop in every repo.
// - No agentflow.toml up the tree (from the hook's cwd, CLAUDE_PROJECT_DIR or the target file):
//   exit 0 silently without starting the CLI.
// - agentflow.toml found: pass the hook JSON to `agentflow hook <event>` on stdin and return its
//   exit code and output unchanged (exit 2 + stderr = block).
// - agentflow.toml found but the CLI is missing: PreToolUse blocks (exit 2) with an install hint;
//   other events report a non-blocking error (exit 1), since Claude can't fix a missing CLI.
// - Stop and Bash/PowerShell checks are for the agents of `agentflow run` only: the orchestrator
//   starts them with AGENTFLOW_AGENT set, which reaches this hook through Claude Code's
//   environment. Without it (a chat session, also while a run is active) exit 0 at once, so the
//   chat never starts a second test run next to the agent's.
// Node is used because hook exec form needs a real executable on Windows (node.exe) and the same
// file then runs unchanged on Linux/macOS. See docs/plugin.md.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const CONFIG = 'agentflow.toml';
const INSTALL_HINT = 'Install it with `uv tool install <agentflow source>` (see the agentflow README), then retry.';

function findConfig(start) {
  if (!start) return null;
  let dir = path.resolve(start);
  try {
    if (fs.existsSync(dir) && !fs.statSync(dir).isDirectory()) dir = path.dirname(dir);
  } catch (_) { /* fall through with dir as given */ }
  for (;;) {
    const candidate = path.join(dir, CONFIG);
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function targetDir(data) {
  const input = (data && data.tool_input) || {};
  const raw = input.file_path || input.notebook_path;
  if (!raw || typeof raw !== 'string') return null;
  const base = data.cwd || process.cwd();
  // The file may not exist yet (Write creates it): search from its parent directory.
  return path.dirname(path.resolve(base, raw));
}

const SHELL_TOOLS = new Set(['Bash', 'PowerShell']);

// Shell calls are checked only during an agentflow run: without the phase file there is nothing
// to check, so skip starting the CLI ([paths] phase_file, default .phase).
function phaseFileExists(config) {
  let file = '.phase';
  try {
    const m = /^\s*phase_file\s*=\s*["']([^"']+)["']/m.exec(fs.readFileSync(config, 'utf8'));
    if (m) file = m[1];
  } catch (_) { /* default */ }
  return fs.existsSync(path.join(path.dirname(config), file));
}

function main() {
  const event = process.argv[2];
  if (!event) {
    process.stderr.write('agentflow-hook.js: missing event argument\n');
    return 1;
  }

  let raw = '';
  try { raw = fs.readFileSync(0, 'utf8'); } catch (_) { /* no stdin */ }
  let data = {};
  try { data = JSON.parse(raw); } catch (_) { /* leave empty; the CLI reports bad input */ }

  const starts = [data.cwd, process.env.CLAUDE_PROJECT_DIR, targetDir(data), process.cwd()];
  let config = null;
  for (const s of starts) {
    config = findConfig(s);
    if (config) break;
  }
  if (!config) return 0;
  const shell = SHELL_TOOLS.has(data && data.tool_name);
  const agent = Boolean((process.env.AGENTFLOW_AGENT || '').trim());
  if ((event === 'stop' || shell) && !agent) return 0;
  if (shell && !phaseFileExists(config)) return 0;

  const bin = process.env.AGENTFLOW_BIN || 'agentflow';
  const r = spawnSync(bin, ['hook', event], {
    input: raw,
    stdio: ['pipe', 'inherit', 'inherit'],
    windowsHide: true,
  });

  if (r.error) {
    const missing = r.error.code === 'ENOENT';
    const what = missing
      ? `the \`${bin}\` CLI was not found on PATH`
      : `the \`${bin}\` CLI could not be started (${r.error.message})`;
    const msg = `agentflow: ${what}, but this repository is configured for agentflow (${config}).`;
    if (event === 'pre-tool-use' && !shell) {
      process.stderr.write(`${msg} File writes are blocked until it works. ${INSTALL_HINT} ` +
        'Do not work around this block; tell the user.\n');
      return 2;
    }
    process.stderr.write(`${msg} The ${event} hook was skipped. ${INSTALL_HINT}\n`);
    return 1;
  }
  if (r.status === null) return 1; // killed by a signal
  return r.status;
}

process.exitCode = main();
