# Moving from agentflow to framegate

The tool was renamed on 2026-11-12 (version 0.6.0). Everything that carried the old name has the
new one: the command (`framegate`), the chat commands (`/framegate:setup`, `/framegate:feature`,
...), the config file (`framegate.toml`), the folder (`.framegate/`), the environment variables
(`FRAMEGATE_*`) and the Python package. Nothing reads the old names any more, so each machine
and each repository that used agentflow is moved once.

Two parts: **A** once per machine, **B** once per repository that was set up with agentflow.

## A. Once per machine

In a terminal (PowerShell or bash):

```
claude plugin uninstall agentflow@agentflow
claude plugin marketplace remove agentflow
uv tool uninstall agentflow

claude plugin marketplace add Dunganh98/framegate
claude plugin install framegate@framegate
uv tool install "git+https://github.com/Dunganh98/framegate@v0.8.0"
```

Then restart Claude Code. Check: `framegate --version` prints `framegate 0.8.0`, and
`/framegate:status` exists in the chat.

Notes:
- A step that says "not installed" / "not found" is fine: go on with the next one.
- If the plugin was installed with `--scope local` in a repository, run the first line in that
  repository (add `--scope local`).
- A clone of the tool itself (to work on it): `git remote set-url origin
  https://github.com/Dunganh98/framegate.git`, `git pull`, and in place of the last line above
  `uv tool install --editable <clone>`.
- Environment variables you set yourself (`AGENTFLOW_TOAST`, `AGENTFLOW_CLAUDE`, ...) are now
  `FRAMEGATE_TOAST`, `FRAMEGATE_CLAUDE`, ...
- Optional: the old mutation environments in WSL can go (`rm -rf ~/.cache/agentflow`); new ones
  are built under `~/.cache/framegate` on the next mutation run.

## B. Once per repository

Finish or park running features first if you can: a feature in the middle of a run keeps
working after the move (its state is in `out/<feature>/` and in git), but it is simpler to move
between features.

Open Claude Code in the repository (after part A) and paste this prompt:

```
This repository was set up with "agentflow", which is now called "framegate" (same tool,
renamed; nothing reads the old names any more). Move the repository to the new name. Do not
change source code, tests or specs, and do not commit or push: show me the result and I commit.

1. Check first: `git status` (tell me if tracked files are modified, and wait for my OK) and
   that no run is active (no `out/.run.lock` of a live process).
2. Rename with git, keeping history: `agentflow.toml` -> `framegate.toml`, and the folder
   `.agentflow/` -> `.framegate/` (project.md, roles/ and whatever else is in it).
3. In `framegate.toml`: replace every `.agentflow/` path with `.framegate/` (e.g. `[project]
   knowledge`, `[roles] extra_dir`) and the word agentflow in comments. A run-files folder
   named after the tool (`[paths] out = "out/agentflow"`, `metrics = ...`) may keep its name:
   ask me; if I want it renamed, move the folder too and update `.gitignore`.
4. Search the whole repository (tracked files, plus `.claude/settings.local.json` and
   `.claude/settings.json`) for "agentflow" in any letter case and update what refers to the
   tool:
   - `CLAUDE.md`: the workflow section (`agentflow` -> `framegate`, `/agentflow:` ->
     `/framegate:`);
   - `.claude/settings.local.json` and `.claude/settings.json`: the allow rules
     `Bash(agentflow *)` / `PowerShell(agentflow *)` -> `Bash(framegate *)` /
     `PowerShell(framegate *)`; an enabled plugin `agentflow@agentflow` ->
     `framegate@framegate`; a marketplace entry named agentflow -> framegate;
   - CI files that call `agentflow gates` -> `framegate gates`, and their install step;
   - a dependency entry `agentflow` in pyproject.toml / requirements files (Python projects)
     -> `framegate @ git+https://github.com/Dunganh98/framegate@v0.8.0`;
   - `.gitignore` lines and docs that name `.agentflow/` or `agentflow.toml`.
   Leave alone: text that is history (retro documents, findings, changelogs, commit messages)
   and names of branches or folders that merely contain the word. List what you left.
5. Python projects only: remove the old package from the project's interpreter (the one
   `framegate install-tools --dry-run` names): `<that python> -m pip uninstall -y agentflow`.
6. Run `framegate install-tools`, then `framegate doctor --brief`. Fix config problems it
   reports that come from the rename; show me the verdict line and any warning left.
7. Feature branches that agentflow created and that are still open: for each, run
   `git config branch.<branch>.framegateFeature <feature>` (the old mark was
   `branch.<branch>.agentflowFeature`; list them with `git config --get-regexp
   agentflowFeature`).
8. Finish with: the files renamed, the files edited (one line each), what you left unchanged
   and why, and the doctor verdict. Then remind me to commit (suggested message: "Rename
   agentflow to framegate") on the base branch the feature branches start from
   (`[git] base` in framegate.toml).
```

What moves by itself, with no step:
- Markers: `spec-approved/<f>`, `tests-approved/<f>` and the others kept their names. Tags left
  by an older version are still read and become private refs on the next write. If the
  repository runs the CI gate job, add `[git] markers = "tags"` to `framegate.toml` (CI cannot
  see private refs).
- Run files, metrics, specs, the retro backlog: unchanged, none of them carries the name.

Two behaviors that changed shortly before the rename and apply after the move too:
- Workflow commits hold only the workflow's paths (source, tests, specs, metrics, knowledge
  file, code map). Edits to `framegate.toml` and other files are yours to commit.
- `/framegate:setup` and `framegate install-tools` install what the project's environment
  needs; a new repository needs nothing beyond part A.
