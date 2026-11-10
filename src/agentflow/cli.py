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
  spec <feature>          create/switch to the branch feat/<feature> from [git] base, copy
                          the spec templates, set the SPEC phase lock
  approve-spec <feature>  commit + tag spec-approved/<feature>, reset progress
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
  gates <feature> --scope step|final [--skip-mutation]
  spec-brief <feature>    print the conventions brief for the SPEC session
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
    p.add_argument("--language", choices=["python", "dotnet"], help="default: detected")
    for name in ("spec", "approve-spec", "run", "approve", "spec-brief", "watch", "gate-summary"):
        sub.add_parser(name).add_argument("feature")
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
    p = sub.add_parser("gates")
    p.add_argument("feature")
    p.add_argument("--scope", choices=["step", "final"], default="final")
    p.add_argument("--skip-mutation", action="store_true")
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
        if args.command == "unlock":
            return orch.unlock(force=args.force)
        f = args.feature
        if args.command == "spec":
            return orch.spec(f)
        if args.command == "approve-spec":
            return orch.approve_spec(f)
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
    except (ConfigError, WorkflowError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"error: unhandled command {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
