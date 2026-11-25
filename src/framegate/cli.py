"""framegate command line.

Exit codes: 0 ok / done, 1 error, 2 usage (or hook block), 10 waiting at a human gate (tests or
final; `status` says which), 130 interrupted (Ctrl+C / SIGTERM; agents stopped, state cleared).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

COMMANDS_HELP = """\
commands:
  init                    scaffold framegate.toml, spec templates, .framegate/project.md,
                          .gitignore lines
  spec <feature> [--mode feature|bugfix|refactor]   create/switch to the branch
                          feat/<feature> from [git] base, copy the mode's spec templates,
                          set the SPEC phase lock. bugfix: the tests must fail on today's
                          code first; refactor: they must pass on it, before and after
  spec-check <feature>    mechanical checks of the spec tables (rules without a spec ID,
                          copied code that is not in the named file) and of the stubs
                          (forbidden markers); lists the setup files approve-spec will commit
                          and the uncommitted protected files it refuses; exit 1 on a problem
  port-stubs <feature> [--force]  a port (the spec's "Ported from"): write the stub of
                          every `code` target from the reference: its text at the spec's
                          commit, function bodies replaced by "not implemented" (python;
                          other languages: says so, stubs by hand); lists imports that will
                          not resolve here; --force: overwrite an existing target file
  approve-spec <feature> [--force]  spec-check, [env] refresh, then commit (specs, stubs,
                          setup files) + tag spec-approved/<feature>, reset progress;
                          --force: approve with spec-check problems
  run <feature>           advance to the next human gate (tests, final) or DONE; no other stops
  status [<feature>] [--brief]   progress and which gate is waiting; no feature: all
                          features; --brief: short state + last progress lines (for the chat)
  status --all            every open task on this machine (any repository): mode, phase,
                          last activity; stale = none for 14 days
  watch <feature>         live view: print new progress lines until the run ends
  wake <feature>          a run that waits for the usage limit tries again now (the limit
                          was refilled, or lifted before the time claude named)
  gate-summary <feature>  chat-friendly digest of the waiting gate (questions numbered)
  approve <feature>       approve the waiting gate (tests -> implement; final -> retro)
  reject <feature> --reason TEXT   reject the tests gate; writer + reviewer re-run with it
  answer <feature>        after writing out/<f>/answers.md: tests gate -> writer + reviewer
                          re-run; final gate -> the next run redoes the parked items
  finish <feature> --no-retro      final gate -> DONE without the retro
  retro <feature>         the retro of a run that stopped before DONE (abandoned, a cap, an
                          error): any phase after "spec OK"; the task's state stays as it is
  note <feature> TEXT     one line of what happened in the chat (a question to the user and
                          the reply, something done by hand, a refusal), for the retro
  learn <feature> K1 [K3 ...|all]  add the retro's project-knowledge proposals you accept to
                          the knowledge file ([project] knowledge), committed
  keep <feature> 1 3 [...|take|all|none]  record which retro proposals you accept in the
                          retro backlog (metrics/retro_backlog.md), committed
  backlog [list] [--all] [--scope framework|project]   the open proposals (--all: every one)
  backlog show B3         one proposal with the retro's full text
  backlog done|drop|reopen B3 [B5 ...] [--note TEXT]   mark it applied / dropped / open again
  gates <feature> --scope step|final [--skip-mutation]
  spec-brief <feature>    print the conventions brief for the SPEC session
  stats [<feature>] [--json] [--risk]  what the features cost, from the events: cost,
                          agent / check time, final-gate runs, escaped defects; with a
                          feature: per phase and per final run; --risk: the risk rating
                          next to what you decided at each final gate
  defect <feature> --what TEXT [--found-by TEXT] [--severity low|medium|high]
         [--fix <feature or commit>] [--file PATH ...] [--id SPEC-ID ...] [--force]
                          record a bug found after the task was accepted, against the
                          task that caused it (metrics/escaped_defects.md, committed);
                          `stats` counts them, a later task's retro sees the open ones on
                          its files or spec IDs
  defect --close X3 [--fix ...]   the fix is in; defect [--all]: list the open (all) ones
  install-tools [--dry-run] [--update]  install what the workflow needs into the
                          project's environment (python: the framegate pytest plugin, pytest,
                          pytest-cov, hypothesis, diff-cover, the gate commands' tools);
                          works before `init`; --dry-run: only list what is missing;
                          --update: also bring the framegate pytest plugin there to this
                          version
  survey                  an agent reads the code and build files and maps the source and
                          test folders and what is not the project's code; every code file
                          must be accounted for (out/survey.json, read by probe); minutes
  setup --plan [--survey]  the setup's first command (for /framegate:setup): installs the
                          workflow tools, maps the folders (survey, once), probes, and
                          prints one confirmation table with the decisions it needs
  setup --apply [--set ROW=VALUE ...] [--red "TEST=REASON" ...]   its second: writes the
                          confirmed plan (framegate.toml, templates), installs the gate
                          commands' tools, runs doctor --tree --brief, creates the state
                          branch once the tree is settled, prints what to commit and what
                          is left; --set: a corrected row; --red: a red test the user
                          agreed to list; can run again
  uses <symbol> [--file <path>]  every place in the source (not the tests) that reads
                          the function or variable, as file:line: line (for the spec:
                          check each use site when a requirement changes an input)
  probe [--json]          the project facts every run depends on, read in seconds: the
                          interpreter, the test / lint / format / type commands and whether
                          each tool answers --version, folders, base branch, remote, deploy
                          and CI files to lock, editable install; runs no test, no linter,
                          no agent; works before `init` (for /framegate:setup)
  update [--plugin-version X]   bring this machine in line with the plugin: names the
                          command that installs the command-line tool at the plugin's
                          version (it cannot replace itself while it runs), then updates
                          the project environment's workflow tools; /framegate:update
                          does both in the chat
  preflight [<feature>] [--plugin-version X] [--check]   the start step of the chat
                          skills: the command-line tool at the plugin's version, the
                          project environment's workflow tools at the tool's version
                          (updated unless a task is in progress or --check: an update
                          then waits), a short setup check; prints what it did and what
                          is left to do; off: [update] check, FRAMEGATE_UPDATE_CHECK=0
  doctor [--full] [--tree] [--brief]  check the setup without agents: config, git,
                          claude CLI, tools, gate commands, test selection by spec ID;
                          --full: the whole suite; --tree (the setup, once per project):
                          the whole suite, and what is red on the code as it is (failing
                          tests, lint / format debt with its size, a mutation tool that
                          cannot run here); --brief: only the verdict and what needs action
  commit-setup [--dry-run] [PATH ...]   the files the setup wrote (framegate.toml, the
                          knowledge file, the code map, spec templates, .gitignore, changed
                          packaging / test config; PATH: one more, e.g. a tag helper) in
                          one commit of their own; --dry-run: only list them
  cleanup <feature> [--tags] [--force]   delete out/<feature>/ and its run logs (DONE only);
                          --tags: also the feature's tags (spec-approved, tests-approved,
                          spec-update, tests-update, knowledge-update)
  publish <feature> [--dry-run] [--pr] [--title TEXT] [--force-with-lease] [--unfinished]
                          with [git] state_branch, after DONE: framegate's files of the
                          feature -> the state branch (one commit, pushed); the product
                          (source, tests, the project's setup files) -> one commit on the
                          base, pushed as the branch feat/<feature> (the local work
                          branch is framegate-work/<feature>); --pr: also the pull
                          request (GitHub: gh, Azure DevOps: az), else where to open it;
                          --dry-run: only say what would go where; works after the
                          cleanup too (it needs the spec folder, not the run files)
  pr-description <feature> [--pipeline]   write the pull request's description
                          (specs/<feature>/pr_description.md: what, changes, how it is
                          proven, what is left to do by hand) again and commit it;
                          --pipeline: the text of a deploy / CI change's own pull request
  state [status|init|save|guard] [--push]   the state branch (framegate/<base>): where
                          it is; init: create it from this checkout's framegate files (once
                          per repository); save: this work branch's framegate files -> the
                          state branch; guard: (re)install the pre-push hook that refuses a
                          push of framegate's files to any branch but the state branch
  pipeline <name>         the pipeline task: the separate step that changes deploy / CI
                          files, from the task's pipeline follow-up list or a list of its
                          own; sets the phase in which only those files may be written
  pipeline-check <name>   its mechanical checks (only listed deploy / CI files changed,
                          yml / json parse, every item done, left open with a reason, or
                          closed as `(not needed: <reason>)`)
  pipeline-approve <name> after the user's OK: check, tick the items, commit, end the phase
  unlock [--force]        clear a stale phase lock (.phase/.reqs) left by a killed run
  replay <candidates.md>  measure framegate: real commits as tasks, each delivered in a
                          sandbox cut before the commit and scored with the commit's own
                          tests (docs/replay.md; --template prints an empty file)
  protocol               the chat run protocol the plugin skills follow (printed)
  hook pre-tool-use|stop  Claude Code hooks (stdin = hook JSON; exit 2 = block)

exit codes: 0 ok, 1 error, 10 human gate (tests or final), 130 interrupted (Ctrl+C /
  SIGTERM; run again to resume)
"""


def build_parser() -> argparse.ArgumentParser:
    from framegate import __version__

    ap = argparse.ArgumentParser(
        prog="framegate",
        description="Spec-driven, test-first workflow orchestrator for Claude Code.",
        epilog=COMMANDS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"framegate {__version__}")
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("init", help="scaffold the workflow files into the current repository")
    p.add_argument(
        "--language",
        choices=["python", "dotnet", "generic"],
        help="default: detected (generic: any other language, configured in framegate.toml)",
    )
    for name in (
        "spec-check",
        "run",
        "approve",
        "spec-brief",
        "watch",
        "gate-summary",
        "wake",
        "retro",
    ):
        sub.add_parser(name).add_argument("feature")
    p = sub.add_parser("note", help="one line of what happened in the chat, for the retro")
    p.add_argument("feature")
    p.add_argument("text", nargs=argparse.REMAINDER)  # the rest as it is, also `-> ...`
    p = sub.add_parser("spec")
    p.add_argument("feature")
    p.add_argument(
        "--mode",
        choices=["feature", "bugfix", "refactor"],
        help="what the task is (default: feature, or what the existing spec says)",
    )
    p = sub.add_parser("approve-spec")
    p.add_argument("feature")
    p.add_argument("--force", action="store_true", help="approve with spec-check problems")
    p = sub.add_parser("port-stubs", help="a port: write the code stubs from the reference")
    p.add_argument("feature")
    p.add_argument("--force", action="store_true", help="overwrite an existing target file")
    p = sub.add_parser("status")
    p.add_argument("feature", nargs="?")
    p.add_argument("--brief", action="store_true", help="short state for the chat")
    p.add_argument("--all", action="store_true", help="every open task on this machine")
    p = sub.add_parser("cleanup", help="delete out/<feature>/ and its run logs (DONE only)")
    p.add_argument("feature")
    p.add_argument("--tags", action="store_true", help="also delete the feature's tags")
    p.add_argument("--force", action="store_true", help="even if the feature is not DONE")
    p = sub.add_parser("reject")
    p.add_argument("feature")
    p.add_argument("--reason", required=True)
    sub.add_parser("answer").add_argument("feature")
    p = sub.add_parser("finish", help="final gate -> DONE without the retro")
    p.add_argument("feature")
    p.add_argument("--no-retro", action="store_true", required=True, help="skip the retro")
    p = sub.add_parser("learn", help="add accepted retro knowledge proposals (K1 ...)")
    p.add_argument("feature")
    p.add_argument("items", nargs="+", help="K numbers (K1 K3 or 1,3) or all")
    p = sub.add_parser("keep", help="record the retro proposals you accept in the backlog")
    p.add_argument("feature")
    p.add_argument("items", nargs="+", help="proposal numbers (1 3 or 1,3), take, all or none")
    p = sub.add_parser("backlog", help="the retro backlog: list, show, done, drop, reopen")
    p.add_argument(
        "action", nargs="?", default="list", choices=["list", "show", "done", "drop", "reopen"]
    )
    p.add_argument("ids", nargs="*", help="backlog IDs (B3 B5)")
    p.add_argument("--note", default="", help="e.g. where it was applied (PR, commit)")
    p.add_argument("--all", action="store_true", help="also applied, dropped and skipped ones")
    p.add_argument("--scope", choices=["framework", "project"], default="")
    p = sub.add_parser("deferred", help="the deferred-work list (.framegate/deferred.md)")
    p.add_argument("--file", default="", help="only the entries on this file")
    p.add_argument("--later", default="", help="add: split this over-limit file later")
    p.add_argument("--feature", default="", help="with --later: the task that deferred it")
    p = sub.add_parser("uses", help="where the source reads a function or a variable")
    p.add_argument("symbol")
    p.add_argument("--file", default="", help="only under this source file or folder")
    p = sub.add_parser("gates")
    p.add_argument("feature")
    p.add_argument("--scope", choices=["step", "final"], default="final")
    p.add_argument("--skip-mutation", action="store_true")
    p = sub.add_parser("stats", help="cost and time per feature, from the events")
    p.add_argument("feature", nargs="?")
    p.add_argument("--json", action="store_true", help="the numbers as JSON")
    p.add_argument(
        "--risk", action="store_true", help="the risk rating next to your final-gate decisions"
    )
    p = sub.add_parser("defect", help="log a bug found after a task was accepted")
    p.add_argument("feature", nargs="?", help="the task that caused it")
    p.add_argument("--what", default="", help="one line: what is wrong")
    p.add_argument("--found-by", default="", help="who or what found it (user, CI, a later task)")
    p.add_argument("--severity", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--fix", default="", help="the task or commit that fixed it")
    p.add_argument("--file", action="append", default=[], help="a file the bug is in")
    p.add_argument("--id", action="append", default=[], help="a spec ID it breaks")
    p.add_argument("--force", action="store_true", help="also for a task with no record here")
    p.add_argument("--close", default="", metavar="X<n>", help="mark a logged defect fixed")
    p.add_argument("--all", action="store_true", help="list the fixed ones too")
    p = sub.add_parser(
        "install-tools", help="install the tools the workflow needs into the project env"
    )
    p.add_argument("--dry-run", action="store_true", help="only list what is missing")
    p.add_argument(
        "--update",
        action="store_true",
        help="also bring framegate's own part there (the pytest plugin) to this version",
    )
    p = sub.add_parser("probe", help="the project facts every run depends on (instant checks)")
    p.add_argument("--json", action="store_true", help="the facts as JSON")
    sub.add_parser("survey", help="an agent maps source / test folders from the code (setup)")
    p = sub.add_parser("setup", help="the setup as two commands: --plan, then --apply")
    step = p.add_mutually_exclusive_group(required=True)
    step.add_argument("--plan", action="store_true", help="probe and print the table")
    step.add_argument("--apply", action="store_true", help="write the confirmed plan, check it")
    p.add_argument("--survey", action="store_true", help="--plan: map the folders again")
    p.add_argument("--set", action="append", default=[], metavar="ROW=VALUE")
    p.add_argument("--red", action="append", default=[], metavar="TEST=REASON")
    p.add_argument("--install", action="append", default=[], metavar="REQUIREMENT")
    p = sub.add_parser("preflight", help="the start step of the chat skills: versions, tools")
    p.add_argument("feature", nargs="?")
    p.add_argument("--plugin-version", default="", help="the version of the skill that asks")
    p.add_argument("--check", action="store_true", help="report only, update nothing")
    p = sub.add_parser("update", help="bring the tool and the project's tools to the plugin")
    p.add_argument("--plugin-version", default="", help="default: the plugin on this machine")
    p = sub.add_parser("doctor", help="check the project setup (no agents)")
    p.add_argument("--full", action="store_true", help="also run the whole test suite")
    p.add_argument("--brief", action="store_true", help="only the verdict and what needs action")
    p.add_argument(
        "--tree",
        action="store_true",
        help="also run the whole suite and say what is red on the code as it is (the setup)",
    )
    p = sub.add_parser("commit-setup", help="the setup's files in one commit of their own")
    p.add_argument("paths", nargs="*", help="one more file the setup wrote (a tag helper)")
    p.add_argument("--dry-run", action="store_true", help="only list the files")
    p = sub.add_parser("publish", help="state branch + product branch of a finished feature")
    p.add_argument("feature")
    p.add_argument("--dry-run", action="store_true", help="only say what would go where")
    p.add_argument("--pr", action="store_true", help="also create the pull request")
    p.add_argument("--title", default="", help="commit and pull request title (default: name)")
    p.add_argument(
        "--force-with-lease",
        action="store_true",
        help="replace a remote product branch that has other content",
    )
    p.add_argument("--unfinished", action="store_true", help="even if the feature is not DONE")
    p = sub.add_parser("pr-description", help="write the pull request's description again")
    p.add_argument("feature")
    p.add_argument("--pipeline", action="store_true", help="of the deploy / CI change's own PR")
    p = sub.add_parser("state", help="the state branch that holds framegate's files")
    p.add_argument(
        "action", nargs="?", default="status", choices=["status", "init", "save", "guard"]
    )
    p.add_argument("--push", action="store_true", help="init / save: also push the state branch")
    for name in ("pipeline", "pipeline-check", "pipeline-approve"):
        sub.add_parser(name, help="the pipeline task (deploy / CI files)").add_argument("feature")
    p = sub.add_parser("unlock", help="clear a stale phase lock left by a killed run")
    p.add_argument("--force", action="store_true", help="even if a run looks active")
    sub.add_parser("protocol", help="print the chat run protocol (used by the plugin skills)")
    from framegate.replay import add_parser as add_replay

    add_replay(sub)
    p = sub.add_parser("hook")
    p.add_argument("event", choices=["pre-tool-use", "stop"])
    return ap


def run_hook(event: str) -> int:
    data = sys.stdin.read()
    if event == "pre-tool-use":
        from framegate.hooks.protect_paths import evaluate
    else:
        from framegate.hooks.require_green import evaluate
    code, message = evaluate(data)
    if message:
        print(message, file=sys.stderr)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")  # never crash on a console that can't print a char
    args = build_parser().parse_args(argv)
    if args.command == "hook":  # fast path: no config/adapter imports outside a project
        return run_hook(args.event)
    if args.command == "protocol":
        from framegate.orchestrator import package_text

        print(package_text("templates", "chat_protocol.md").rstrip())
        return 0
    if args.command == "replay":  # works outside a project
        from framegate.replay import run as replay

        return replay.main(args)

    from framegate import publish
    from framegate.config import ConfigError, find_root, load_config
    from framegate.orchestrator import Orchestrator, WorkflowError
    from framegate.state import GitError

    try:
        if args.command == "init":
            from framegate.scaffold import init

            return init(language=args.language)
        if args.command == "install-tools":
            from framegate.scaffold import install_tools

            return install_tools(dry_run=args.dry_run, update=args.update)
        if args.command == "probe":
            from framegate import probe

            return probe.run(as_json=args.json)
        if args.command == "survey":
            from framegate import survey

            return survey.run()
        if args.command == "setup":
            from framegate import setup_cmd

            if args.plan:
                return setup_cmd.plan(resurvey=args.survey)
            return setup_cmd.apply(args.set, args.red, install=args.install)
        if args.command == "preflight":
            from framegate import preflight

            return preflight.run(args.feature, args.plugin_version, check_only=args.check)
        if args.command == "update":
            from framegate import preflight

            target = args.plugin_version or preflight.installed_plugin_version() or ""
            return preflight.run(None, target, update=True)
        if args.command == "status" and args.all:
            from framegate import preflight

            return preflight.status_all()
        root = find_root()
        if root is not None:
            cfg = load_config(root)
        else:
            # [git] state_branch: the base branch holds no framegate.toml; a feature starts
            # from there with the config of the state branch
            wanted = args.command in ("spec", "state", "pipeline")
            from_state = publish.config_from_state() if wanted else None
            if from_state is None:
                hint = publish.missing_config_hint()
                if hint and args.command == "status":
                    # the skills inject this output (a non-zero exit aborts a skill)
                    print(f"No framegate.toml in this checkout. {hint}")
                    return 0
                print(
                    "error: no framegate.toml found here or in a parent directory. "
                    + (hint or "Run `framegate init` in the repository root."),
                    file=sys.stderr,
                )
                return 1
            cfg = from_state
        if args.command == "state":
            from framegate.adapters import get_adapter

            if args.action == "init":
                ignores = get_adapter(cfg).gitignore_entries()
                return publish.state_init(cfg, push=args.push, extra_ignores=ignores)
            if args.action == "save":
                return publish.state_save(cfg, push=args.push)
            if args.action == "guard":
                return publish.state_guard(cfg)
            return publish.state_status(cfg)
        if args.command == "publish":
            return publish.publish(
                cfg,
                args.feature,
                dry_run=args.dry_run,
                pr=args.pr,
                title=args.title,
                force_with_lease=args.force_with_lease,
                unfinished=args.unfinished,
            )
        if args.command == "pr-description":
            return publish.describe(cfg, args.feature, pipeline=args.pipeline)
        if args.command == "doctor":
            from framegate import doctor

            return doctor.run(cfg, full=args.full, only_brief=args.brief, tree=args.tree)
        if args.command == "commit-setup":
            from framegate.scaffold import commit_setup

            return commit_setup(cfg, args.paths, dry_run=args.dry_run)
        if args.command == "spec-brief":
            from framegate.scaffold import spec_brief

            print(spec_brief(cfg, args.feature))
            return 0
        if args.command == "stats":
            from framegate import stats

            return stats.run(cfg, args.feature, as_json=args.json, risk=args.risk)
        if args.command == "defect":
            from framegate import defects

            return defects.run(cfg, args)
        if args.command == "deferred":
            from framegate import deferred

            if args.later:
                from framegate import task_base
                from framegate.adapters import get_adapter

                rel = args.later.replace("\\", "/")
                n = task_base.formatted_lines(get_adapter(cfg), [rel]).get(rel)
                entry = deferred.split_later(cfg, args.feature, rel, n)
                print(entry.line() if entry else f"error: no file {args.later}")
                return 0 if entry else 1
            lines = deferred.listing(cfg, args.file)
            end = ":" if lines else "."
            print(f"{len(lines)} open deferred item(s) in {deferred.PATH}{end}")
            for line in lines:
                print(line)
            return 0
        if args.command == "uses":
            from framegate import uses
            from framegate.adapters import get_adapter

            return uses.run(cfg, get_adapter(cfg), args.symbol, args.file)
        if args.command == "spec-check":
            from framegate.adapters import get_adapter
            from framegate.checks import frame_diff, spec_lint
            from framegate.orchestrator import stray_files_text

            adapter = get_adapter(cfg)
            found = spec_lint.problems(cfg, args.feature, adapter)
            setup, stray = frame_diff.setup_state(adapter)
            for line in found:
                print(f"- {line}")
            if stray:
                print(f"- {stray_files_text(stray)}")
            for line in spec_lint.warnings(cfg, args.feature):
                print(f"- WARNING (not blocking): {line}")
            from framegate import deferred

            for line in deferred.plan_note(cfg, args.feature):  # a note for the spec brief
                print(line)
            from framegate import task_base
            from framegate.checks import structure

            over = structure.plan_over_limit(adapter, args.feature)
            for line in deferred.over_limit_note(
                cfg, args.feature, over, lambda files: task_base.formatted_lines(adapter, files)
            ):
                print(line)
            # for the spec brief: the user approves these with the spec
            print("Setup files changed (committed with the spec): " + (", ".join(setup) or "none"))
            n = len(found) + (1 if stray else 0)
            print(f"Spec check: {n} problem(s)" if n else "Spec check: OK")
            return 1 if n else 0
        if args.command == "port-stubs":
            from framegate import ported
            from framegate.adapters import get_adapter

            return ported.port_stubs(cfg, get_adapter(cfg), args.feature, force=args.force)
        if args.command == "gates":
            from framegate.adapters import get_adapter
            from framegate.gates import run_gates

            ok, _ = run_gates(
                get_adapter(cfg), args.feature, args.scope, args.skip_mutation, echo=print
            )
            return 0 if ok else 1
        if args.command in ("pipeline-check", "pipeline-approve"):
            from framegate import pipeline

            step = pipeline.run_check if args.command == "pipeline-check" else pipeline.approve
            return step(cfg, args.feature)
        orch = Orchestrator(cfg)
        if args.command == "pipeline":
            from framegate import pipeline

            return pipeline.start(orch, args.feature)
        if args.command == "unlock":
            return orch.unlock(force=args.force)
        if args.command == "backlog":
            return orch.backlog_cmd(args.action, args.ids, args.note, args.all, args.scope)
        f = args.feature
        if args.command == "spec":
            return orch.spec(f, args.mode)
        if args.command == "approve-spec":
            from framegate import preflight

            code = orch.approve_spec(f, force=args.force)
            if code == 0:  # the task starts: it keeps this version until it is DONE
                preflight.record_start(orch.cfg, f, restart=True)
            return code
        if args.command == "run":
            from framegate import detached, preflight

            if detached.wanted():
                started = detached.run(orch, f)
                if started is not None:
                    return started
            preflight.record_start(orch.cfg, f)  # a task approved by an older version
            code = orch.run(f)
            note = preflight.version_note(cfg) if code == 0 else ""
            if note:  # DONE: the update that waited for this task
                print(note)
            return code
        if args.command == "retro":
            return orch.retro(f)
        if args.command == "note":
            from framegate.retro import chat_note

            return chat_note(cfg, f, " ".join(args.text))
        if args.command == "wake":
            from framegate.orchestrator import wake

            return wake(cfg, f)
        if args.command in ("status", "watch", "gate-summary", "cleanup"):
            from framegate import chat

            if args.command == "status":
                from framegate import preflight

                note = preflight.version_note(cfg) if f is None or args.brief else ""
                if note:
                    print(note)
                if f is None:
                    return chat.overview(orch)
                return chat.brief(orch, f) if args.brief else orch.status(f)
            if args.command == "watch":
                return chat.watch(orch, f)
            if args.command == "gate-summary":
                return chat.gate_summary(orch, f)
            return chat.cleanup(orch, f, tags=args.tags, force=args.force)
        if args.command == "approve":
            return orch.approve(f)
        if args.command == "reject":
            return orch.reject(f, args.reason)
        if args.command == "answer":
            return orch.answer(f)
        if args.command == "finish":
            return orch.finish(f)
        if args.command == "learn":
            return orch.learn(f, args.items)
        if args.command == "keep":
            return orch.keep(f, args.items)
    except (ConfigError, WorkflowError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"error: unhandled command {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
