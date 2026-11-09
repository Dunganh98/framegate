"""agentflow command line.

Exit codes: 0 ok / done, 1 error, 2 usage (or hook block), 10 waiting for a human gate,
11 waiting for answers to agent questions, 12 escalation, 13 spec change required.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

COMMANDS_HELP = """\
commands:
  init                    scaffold agentflow.toml, spec templates, .gitignore lines
  spec <feature>          copy the spec templates, set the SPEC phase lock
  approve-spec <feature>  commit + tag spec-approved/<feature>, reset progress
  run <feature>           advance TESTS -> IMPLEMENT -> REVIEW -> PR -> RETRO until a wait
  status <feature>        show progress and what is waiting
  approve <feature>       approve the waiting human gate (tests / review findings)
  reject <feature> --reason TEXT   reject the waiting gate; the reason becomes agent feedback
  answer <feature> [--abort]       continue after questions/escalation (answers.md)
  gates <feature> --scope step|final [--skip-mutation]
  spec-brief <feature>    print the conventions brief for the SPEC session
  hook pre-tool-use|stop  Claude Code hooks (stdin = hook JSON; exit 2 = block)

exit codes: 0 ok, 1 error, 10 human gate, 11 questions, 12 escalation, 13 spec change
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
    p.add_argument("--language", choices=["python", "dotnet"], help="default: detected")
    for name in ("spec", "approve-spec", "run", "status", "approve", "spec-brief"):
        sub.add_parser(name).add_argument("feature")
    p = sub.add_parser("reject")
    p.add_argument("feature")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("answer")
    p.add_argument("feature")
    p.add_argument("--abort", action="store_true", help="the spec itself must change")
    p = sub.add_parser("gates")
    p.add_argument("feature")
    p.add_argument("--scope", choices=["step", "final"], default="final")
    p.add_argument("--skip-mutation", action="store_true")
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

    from agentflow.config import ConfigError, find_root, load_config
    from agentflow.orchestrator import Orchestrator, WorkflowError
    from agentflow.state import GitError

    try:
        if args.command == "init":
            from agentflow.scaffold import init

            return init(language=args.language)
        root = find_root()
        if root is None:
            print(
                "error: no agentflow.toml found here or in a parent directory. "
                "Run `agentflow init` in the repository root.",
                file=sys.stderr,
            )
            return 1
        cfg = load_config(root)
        if args.command == "spec-brief":
            from agentflow.scaffold import spec_brief

            print(spec_brief(cfg, args.feature))
            return 0
        if args.command == "gates":
            from agentflow.adapters import get_adapter
            from agentflow.gates import run_gates

            ok, _ = run_gates(
                get_adapter(cfg), args.feature, args.scope, args.skip_mutation, echo=print
            )
            return 0 if ok else 1
        orch = Orchestrator(cfg)
        f = args.feature
        if args.command == "spec":
            return orch.spec(f)
        if args.command == "approve-spec":
            return orch.approve_spec(f)
        if args.command == "run":
            return orch.run(f)
        if args.command == "status":
            return orch.status(f)
        if args.command == "approve":
            return orch.approve(f)
        if args.command == "reject":
            return orch.reject(f, args.reason)
        if args.command == "answer":
            return orch.answer(f, abort=args.abort)
    except (ConfigError, WorkflowError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"error: unhandled command {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
