from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from unittest import mock

from framegate import doc_proposals, gates
from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.checks import added_docs, bug_tests, plan_rewords, spec_rows, step_notes
from framegate.config import load_config
from framegate.specs import feature_mode, plan_steps
from framegate.state import tag_exists
from framegate.task_base import REF as TASK_BASE

USAGE = (
    "Runs the read-only checks on recorded runs (<round>/runs/<candidate>/sandbox), each in "
    "a scratch copy of the run's repository, and prints what every check flags. For a new "
    "check before it is merged: the false alarms show here. Nothing is written into a run."
)
SKIP = "answers"
SHOWN = 8
NOT_REACHED = "-"

Check = Callable[[LanguageAdapter, str], list[str]]


def rewords(adapter: LanguageAdapter, feature: str) -> list[str]:
    steps = [s.num for s in plan_steps(adapter.cfg, feature)]
    out = plan_rewords.check(adapter, feature, steps).output.splitlines()
    return [x for x in out if x.startswith("REWORD NOT DONE")]


def added(adapter: LanguageAdapter, feature: str) -> list[str]:
    return added_docs.problems(adapter, feature)


def notes(adapter: LanguageAdapter, feature: str) -> list[str]:
    return step_notes.stale_candidates(adapter, feature, step_notes.step_files(adapter))


def rows(adapter: LanguageAdapter, feature: str) -> list[str]:
    return spec_rows.problems(adapter.cfg, feature)


def bug_spec(adapter: LanguageAdapter, feature: str) -> list[str]:
    return bug_tests.spec_problems(adapter, feature)


def doc_merge(adapter: LanguageAdapter, feature: str) -> list[str]:
    after = doc_proposals.proposals(adapter.cfg, feature)
    with mock.patch.object(doc_proposals, "merged", lambda found: found):
        before = doc_proposals.proposals(adapter.cfg, feature)
    return [f"merged into another: {p.shown()}" for p in before if p not in after]


def existing(adapter: LanguageAdapter, feature: str) -> list[str]:
    files, left, _ = gates.existing_test_files(adapter, feature)
    return [f"would run: {f}" for f in files] + [f"left out (over the cap): {f}" for f in left]


#: name -> (the tree it is computed on, the check)
CHECKS: dict[str, tuple[str, Check]] = {
    "spec-rows": ("spec", rows),
    "bug-tests": ("spec", bug_spec),
    "rewords": ("end", rewords),
    "doc-merge": ("end", doc_merge),
    "existing-tests": ("end", existing),
    "added-docs": ("work", added),
    "step-notes": ("work", notes),
}


@dataclass
class Result:
    run: str
    feature: str
    mode: str = ""
    found: dict[str, list[str] | str] = field(default_factory=dict)

    def cell(self, name: str) -> str:
        got = self.found.get(name, NOT_REACHED)
        return got if isinstance(got, str) else str(len(got))


def sandboxes(root: pathlib.Path) -> list[pathlib.Path]:
    rounds = [root, *(p for p in sorted(root.iterdir()) if p.is_dir() and p.name != SKIP)]
    found = [s for r in rounds for s in sorted((r / "runs").glob("*/sandbox"))]
    return [s for s in found if s.is_dir() and s.parent.name != SKIP]


def features(sandbox: pathlib.Path) -> list[str]:
    out, specs = sandbox / "out", sandbox / "specs"
    if not out.is_dir():
        return []
    return sorted(p.name for p in out.iterdir() if (specs / p.name / "requirements.md").is_file())


def git(root: pathlib.Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def scratch_copy(sandbox: pathlib.Path, target: pathlib.Path, names: list[str]) -> str:
    end = git(sandbox, "rev-parse", "HEAD")
    git(target, "init", "-q")
    git(target, "fetch", "-q", "--update-head-ok", str(sandbox), "+refs/*:refs/*")
    git(target, "checkout", "-q", "--detach", end)
    for name in names:
        folder = target / "out" / name
        folder.mkdir(parents=True, exist_ok=True)
        for p in (sandbox / "out" / name).glob("*.md"):
            shutil.copyfile(p, folder / p.name)
    return end


def tree_for(kind: str, clone: pathlib.Path, feature: str, end: str) -> bool:
    cfg = load_config(clone, require=False)
    git(clone, "reset", "-q", "--hard")
    git(clone, "checkout", "-q", "--detach", end)
    if kind == "end":
        return True
    if kind == "spec":
        if not tag_exists(cfg, f"spec-approved/{feature}"):
            return False
        git(clone, "checkout", "-q", "--detach", f"refs/spec-approved/{feature}")
        return True
    marker = f"tests-approved/{feature}"
    if not tag_exists(cfg, marker):
        return False
    formatted = f"{TASK_BASE}{marker}"
    has = subprocess.run(["git", "show-ref", "--verify", "-q", formatted], cwd=clone)
    git(clone, "reset", "-q", "--soft", formatted if has.returncode == 0 else f"refs/{marker}")
    return True


def check_run(sandbox: pathlib.Path, label: str, only: str = "") -> list[Result]:
    names = features(sandbox)
    if not names:
        return [Result(label, "", found={n: "no feature" for n in CHECKS})]
    results = []
    with tempfile.TemporaryDirectory(prefix="framegate-runs-", ignore_cleanup_errors=True) as tmp:
        clone = pathlib.Path(tmp)
        end = scratch_copy(sandbox, clone, names)
        for feature in names:
            res = Result(label, feature)
            for name, (kind, check) in CHECKS.items():
                if only and name != only:
                    continue
                try:
                    if not tree_for(kind, clone, feature, end):
                        continue
                    adapter = get_adapter(load_config(clone, require=False))
                    res.mode = res.mode or feature_mode(adapter.cfg, feature)
                    res.found[name] = check(adapter, feature)
                except Exception as e:  # one broken run must not hide the others
                    res.found[name] = f"error: {type(e).__name__}: {str(e)[:120]}"
            results.append(res)
    return results


def table(results: list[Result], names: list[str]) -> list[str]:
    head = ["run", "feature", "mode", *names]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in results:
        cells = [r.run, r.feature, r.mode, *(r.cell(n) for n in names)]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def details(res: Result) -> list[str]:
    out = [f"## {res.run} ({res.feature or 'no feature'}, {res.mode or '?'})"]
    for name, got in res.found.items():
        if isinstance(got, str):
            out.append(f"{name}: {got}")
            continue
        out.append(f"{name}: {len(got)}")
        out += [f"  {x[:300]}" for x in got[:SHOWN]]
        out += [f"  ... {len(got) - SHOWN} more"] if len(got) > SHOWN else []
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="check_on_runs.py", description=USAGE)
    p.add_argument("root", type=pathlib.Path, help="a round folder, or the folder of rounds")
    p.add_argument("--check", default="", choices=["", *CHECKS], help="one check only")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    found = sandboxes(args.root)
    if not found:
        print(f"no runs/<candidate>/sandbox under {args.root}", file=sys.stderr)
        return 2
    results: list[Result] = []
    for sandbox in found:
        label = "/".join(sandbox.relative_to(args.root.parent).parts[:-1]).replace("/runs/", "/")
        for res in check_run(sandbox, label, args.check):
            results.append(res)
            print("\n".join(details(res)), flush=True)
    names = [args.check] if args.check else list(CHECKS)
    print("\n" + "\n".join(table(results, names)))
    print(f"\n{NOT_REACHED} = the run did not reach the marker the check needs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
