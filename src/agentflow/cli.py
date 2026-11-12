"""agentflow command line.

Exit codes: 0 ok / done, 1 error, 2 usage (or hook block), 10 waiting at a human gate (tests or
final; `status` says which), 130 interrupted (Ctrl+C / SIGTERM; agents stopped, state cleared).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

COMMANDS_HELP = """\
commands:
  init                    scaffold agentflow.toml, spec templates, .agentflow/project.md,
                          .gitignore lines
  spec <feature> [--mode feature|bugfix|refactor]   create/switch to the branch
                          feat/<feature> from [git] base, copy the mode's spec templates,
                          set the SPEC phase lock. bugfix: the tests must fail on today's
                          code first; refactor: they must pass on it, before and after
  spec-check <feature>    mechanical checks of the spec tables (rules without a spec ID,
                          copied code that is not in the named file); exit 1 on a problem
  approve-spec <feature> [--force]  spec-check, then commit + tag spec-approved/<feature>,
                          reset progress; --force: approve with spec-check problems
  run <feature>           advance to the next human gate (tests, final) or DONE; no other stops
  status [<feature>] [--brief]   progress and which gate is waiting; no feature: all
                          features; --brief: short state + last progress lines (for the chat)
  watch <feature>         live view: print new progress lines until the run ends
  gate-summary <feature>  chat-friendly digest of the waiting gate (questions numbered)
  approve <feature>       approve the waiting gate (tests -> implement; final -> retro)
  reject <feature> --reason TEXT   reject the tests gate; writer + reviewer re-run with it
  answer <feature>        after writing out/<f>/answers.md: tests gate -> writer + reviewer
                          re-run; final gate -> the next run redoes the parked items
  finish <feature> --no-retro      final gate -> DONE without the retro
  learn <feature> K1 [K3 ...|all]  add the retro's project-knowledge proposals you accept to
                          the knowledge file ([project] knowledge), committed
  keep <feature> 1 3 [...|take|all|none]  record which retro proposals you accept in the
                          retro backlog (metrics/retro_backlog.md), committed
  backlog [list] [--all] [--scope framework|project]   the open proposals (--all: every one)
  backlog show B3         one proposal with the retro's full text
  backlog done|drop|reopen B3 [B5 ...] [--note TEXT]   mark it applied / dropped / open again
  gates <feature> --scope step|final [--skip-mutation]
  spec-brief <feature>    print the conventions brief for the SPEC session
  stats [<feature>] [--json]  what the features cost, from the events: cost, agent / check
                          time, final-gate runs; with a feature: per phase and per final run
  install-tools [--dry-run]  install what the workflow needs into the project's
                          environment (python: the agentflow pytest plugin, pytest,
                          pytest-cov, hypothesis, diff-cover, the gate commands' tools);
                          works before `init`; --dry-run: only list what is missing
  doctor [--full] [--brief]  check the setup without agents: config, git, claude CLI,
                          tools, gate commands, test selection by spec ID; --full: the whole
                          suite; --brief: only the verdict and what needs action
  cleanup <feature> [--tags] [--force]   delete out/<feature>/ and its run logs (DONE only);
                          --tags: also the feature's tags (spec-approved, tests-approved,
                          spec-update, tests-update, knowledge-update)
  unlock [--force]        clear a stale phase lock (.phase/.reqs) left by a killed run
  protocol                the chat run protocol the plugin skills follow (printed)
  hook pre-tool-use|stop  Claude Code hooks (stdin = hook JSON; exit 2 = block)

exit codes: 0 ok, 1 error, 10 human gate (tests or final), 130 interrupted (Ctrl+C /
  SIGTERM; run again to resume)
"""


def build_parser() -> argparse.ArgumentParser:
    from agentflow import __version__

    ap = argparse.ArgumentParser(
        prog="agentflow",
        description="Spec-driven, test-first workflow orchestrator for Claude Code.",
        epilog=COMMANDS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"agentflow {__version__}")
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("init", help="scaffold the workflow files into the current repository")
    p.add_argument(
        "--language",
        choices=["python", "dotnet", "generic"],
        help="default: detected (generic: any other language, configured in agentflow.toml)",
    )
    for name in ("spec-check", "run", "approve", "spec-brief", "watch", "gate-summary"):
        sub.add_parser(name).add_argument("feature")
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
    p = sub.add_parser("status")
    p.add_argument("feature", nargs="?")
    p.add_argument("--brief", action="store_true", help="short state for the chat")
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
    p = sub.add_parser("gates")
    p.add_argument("feature")
    p.add_argument("--scope", choices=["step", "final"], default="final")
    p.add_argument("--skip-mutation", action="store_true")
    p = sub.add_parser("stats", help="cost and time per feature, from the events")
    p.add_argument("feature", nargs="?")
    p.add_argument("--json", action="store_true", help="the numbers as JSON")
    p = sub.add_parser(
        "install-tools", help="install the tools the workflow needs into the project env"
    )
    p.add_argument("--dry-run", action="store_true", help="only list what is missing")
    p = sub.add_parser("doctor", help="check the project setup (no agents)")
    p.add_argument("--full", action="store_true", help="also run the whole test suite")
    p.add_argument("--brief", action="store_true", help="only the verdict and what needs action")
    p = sub.add_parser("unlock", help="clear a stale phase lock left by a killed run")
    p.add_argument("--force", action="store_true", help="even if a run looks active")
    sub.add_parser("protocol", help="print the chat run protocol (used by the plugin skills)")
    p = sub.add_parser("hook")
    p.add_argument("event", choices=["pre-tool-use", "stop"])
    return ap


def run_hook(event: str) -> int:
    data = sys.stdin.read()
    if event == "pre-tool-use":
        from agentflow.hooks.protect_paths import evaluate
    else:
        from agentflow.hooks.require_green import evaluate
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
        from agentflow.orchestrator import package_text

        print(package_text("templates", "chat_protocol.md").rstrip())
        return 0

    from agentflow.config import ConfigError, find_root, load_config
    from agentflow.orchestrator import Orchestrator, WorkflowError
    from agentflow.state import GitError

    try:
        if args.command == "init":
            from agentflow.scaffold import init

            return init(language=args.language)
        if args.command == "install-tools":
            from agentflow.scaffold import install_tools

            return install_tools(dry_run=args.dry_run)
        root = find_root()
        if root is None:
            print(
                "error: no agentflow.toml found here or in a parent directory. "
                "Run `agentflow init` in the repository root.",
                file=sys.stderr,
            )
            return 1
        cfg = load_config(root)
        if args.command == "doctor":
            from agentflow import doctor

            return doctor.run(cfg, full=args.full, only_brief=args.brief)
        if args.command == "spec-brief":
            from agentflow.scaffold import spec_brief

            print(spec_brief(cfg, args.feature))
            return 0
        if args.command == "stats":
            from agentflow import stats

            return stats.run(cfg, args.feature, as_json=args.json)
        if args.command == "spec-check":
            from agentflow.checks import spec_lint

            found = spec_lint.problems(cfg, args.feature)
            for line in found:
                print(f"- {line}")
            print(f"Spec check: {len(found)} problem(s)" if found else "Spec check: OK")
            return 1 if found else 0
        if args.command == "gates":
            from agentflow.adapters import get_adapter
            from agentflow.gates import run_gates

            ok, _ = run_gates(
                get_adapter(cfg), args.feature, args.scope, args.skip_mutation, echo=print
            )
            return 0 if ok else 1
        orch = Orchestrator(cfg)
        if args.command == "unlock":
            return orch.unlock(force=args.force)
        if args.command == "backlog":
            return orch.backlog_cmd(args.action, args.ids, args.note, args.all, args.scope)
        f = args.feature
        if args.command == "spec":
            return orch.spec(f, args.mode)
        if args.command == "approve-spec":
            return orch.approve_spec(f, force=args.force)
        if args.command == "run":
            return orch.run(f)
        if args.command in ("status", "watch", "gate-summary", "cleanup"):
            from agentflow import chat

            if args.command == "status":
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
