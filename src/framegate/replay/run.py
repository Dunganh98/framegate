"""`framegate replay`: per candidate build the sandbox, let a headless driver agent play the
user, stop it at a cap, score. Candidates run N at a time; one that fails does not stop the
others. A replay never publishes, pushes or cleans up."""

from __future__ import annotations

import argparse
import importlib.resources
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from framegate import procs
from framegate.config import ConfigError, load_config
from framegate.detached import ENV as DETACH_ENV
from framegate.replay import MAX_HOURS, MAX_USD
from framegate.replay import limit as limits
from framegate.replay import score as scoring
from framegate.replay.candidates import TEMPLATE, Candidate, CandidatesError, parse
from framegate.replay.chat import claude_exe, read_pid
from framegate.replay.sandbox import (
    Layout,
    ReplayError,
    author_paths,
    build,
    make_env,
    prune,
    read_json,
    source,
    write_json,
)

DRIVER_TOOLS = "Bash,PowerShell,Read,Write,Edit,Glob,Grep,Monitor"
POLL_SECONDS = 30.0
RETRO_TIMEOUT = 3600
#: set on Ctrl+C: every driver is stopped
STOP = threading.Event()


@dataclass(frozen=True)
class Caps:
    usd: float = MAX_USD
    hours: float = MAX_HOURS


def cap_hit(caps: Caps, elapsed_s: float, waited_s: float, usd: float) -> str:
    """Why the run must stop now ('' = go on). Usage-limit waits do not count as time."""
    if usd > caps.usd:
        return f"cost cap: {usd:.2f} USD (cap {caps.usd:g})"
    hours = (elapsed_s - waited_s) / 3600
    if hours > caps.hours:
        return f"time cap: {hours:.1f} h (cap {caps.hours:g})"
    return ""


def watch(
    proc: Any,
    caps: Caps,
    spend: Callable[[], tuple[float, float]],
    poll: float = POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    kill: Callable[[Any], None] = procs.kill_tree,
) -> str:
    """Wait for the driver; at a cap (or Ctrl+C) kill its whole tree. `spend`: USD spent and
    seconds waited for the usage limit so far. Returns why it was stopped ('' = it ended)."""
    start = clock()
    try:
        while proc.poll() is None:
            if STOP.wait(poll):
                return "interrupted"
            usd, waited = spend()
            why = cap_hit(caps, clock() - start, waited, usd)
            if why:
                return why
        return ""
    finally:
        kill(proc)


def run_env(lay: Layout) -> dict[str, str]:
    """The sandbox's python and framegate first on PATH, no update check, no chat memory, and
    `framegate run` detached: the chat's next turn kills the background tasks of the last."""
    env = dict(os.environ, FRAMEGATE_UPDATE_CHECK="0", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
    env[DETACH_ENV] = "1"
    env.pop("FRAMEGATE_AGENT", None)
    bin_dir = pathlib.Path(read_json(lay.run / "run.json")["python"]).parent
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def instructions(lay: Layout, c: Candidate, caps: Caps, src: pathlib.Path) -> str:
    text = importlib.resources.files("framegate.replay").joinpath("driver.md").read_text("utf-8")
    values = {
        "slug": c.name,
        "kind": c.kind,
        "sandbox": str(lay.sandbox),
        "driver": str(lay.driver),
        "forbidden": "\n".join(f"  - `{p}`" for p in (lay.answer.parent, lay.sources, src)),
        "max_usd": f"{caps.usd:g}",
        "max_hours": f"{caps.hours:g}",
    }
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _kill(pid: int, group: bool = False) -> None:
    if not pid or not procs.pid_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
    elif group:
        os.killpg(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGTERM)


def stop_engine(sandbox: pathlib.Path) -> None:
    """A `framegate run` the killed driver left behind in the sandbox."""
    try:
        lock = procs.read_lock(load_config(sandbox).out_dir)
    except ConfigError:
        return
    _kill(int((lock or {}).get("pid") or 0))


def stop_chat(driver: pathlib.Path) -> None:
    """A chat call that is still running: it is detached (chat.py), so killing the driver's
    tree does not reach it."""
    for mark in driver.glob("turn-*.pid"):
        _kill(read_pid(mark), group=True)
        mark.unlink(missing_ok=True)


def outcome(driver: pathlib.Path) -> str:
    """The driver's own line on how the run ended (outcome.txt), '' if it left none."""
    try:
        text = (driver / "outcome.txt").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    return next((line.strip()[:200] for line in text.splitlines() if line.strip()), "")


def claude_driver(lay: Layout, c: Candidate, caps: Caps, src: pathlib.Path) -> dict[str, Any]:
    """One headless `claude` that follows driver.md in the driver folder."""
    prompt = lay.driver / "DRIVER.md"
    prompt.write_text(instructions(lay, c, caps, src), encoding="utf-8")
    cmd = [claude_exe(), "-p", "--output-format", "json", "--permission-mode", "acceptEdits"]
    # the driver only follows rules: a small model does (FRAMEGATE_REPLAY_DRIVER_MODEL)
    cmd += ["--model", os.environ.get("FRAMEGATE_REPLAY_DRIVER_MODEL") or "sonnet"]
    cmd += ["--strict-mcp-config", "--allowedTools", DRIVER_TOOLS]  # no MCP servers

    def spend() -> tuple[float, float]:
        numbers = scoring.engine(lay.sandbox, lay.name)
        return numbers["engine_usd"] + scoring.chat_cost(lay.driver), numbers["waited_s"]

    start = time.time()
    with (
        open(prompt, encoding="utf-8") as stdin,
        open(lay.driver / "driver.json", "w", encoding="utf-8") as out,
        open(lay.driver / "driver.err", "w", encoding="utf-8") as err,
    ):
        proc = procs.popen(
            cmd, job=True, cwd=lay.driver, env=run_env(lay), stdin=stdin, stdout=out, stderr=err
        )
        why = watch(proc, caps, spend)
    stop_chat(lay.driver)
    if why:
        stop_engine(lay.sandbox)
    try:
        answer = read_json(lay.driver / "driver.json")
    except ValueError:
        answer = {}
    return {
        "stopped": why,
        "wall_s": round(time.time() - start),
        "driver_usd": round(float(answer.get("total_cost_usd") or 0), 2),
        **({} if why else limits.record(limits.detect(lay.driver))),
    }


def retro(lay: Layout) -> str:
    """`framegate retro <name>` for a run that did not reach DONE, if this framegate has it."""
    py = read_json(lay.run / "run.json")["python"]
    try:
        r = subprocess.run(
            [py, "-m", "framegate", "retro", lay.name],
            cwd=lay.sandbox,
            env=run_env(lay),
            capture_output=True,
            timeout=RETRO_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"failed ({type(e).__name__})"
    # 2: the command line was refused (no such command)
    return "ran" if r.returncode == 0 else "not available" if r.returncode == 2 else "failed"


@dataclass
class Options:
    out: pathlib.Path
    #: folder of the candidates file: a relative `repo` starts there
    base: pathlib.Path
    parallel: int = 3
    caps: Caps = Caps()
    prepare_only: bool = False
    score_only: bool = False
    force: bool = False
    prune: bool = False
    driver: Callable[[Layout, Candidate, Caps, pathlib.Path], dict[str, Any]] = claude_driver
    retro: Callable[[Layout], str] = retro
    env: Callable[[Candidate, pathlib.Path], pathlib.Path] = make_env
    echo: Callable[[str], None] = field(default=lambda line: print(line, flush=True))
    #: the wait for a usage-limit reset (seconds); True = interrupted
    wait: Callable[[float], bool] = STOP.wait
    #: the candidates file: no agent of a run may read it
    file: pathlib.Path | None = None


def _prune(lay: Layout, opts: Options) -> None:
    opts.echo(f"[{lay.name}] pruned: {prune(lay) / 2**20:.0f} MB freed")


def run_one(c: Candidate, opts: Options, src: pathlib.Path) -> dict[str, Any]:
    """One candidate, as far as the options ask: its row of the results."""
    lay = Layout(opts.out, c.name)
    row: dict[str, Any] = {"name": c.name, "kind": c.kind}
    run_json, score_json = lay.run / "run.json", lay.run / "score.json"
    state, earlier = read_json(run_json), read_json(score_json)
    probes, extra = opts.base / "probes" / c.name, opts.base / "extra" / c.name
    # a score that failed is no score: scored again, the run is not repeated
    retry = earlier.get("verdict") == scoring.FAILED and not (opts.force or opts.prepare_only)
    if opts.score_only or retry:
        if state.get("state") not in ("finished", "stopped"):
            return {
                **row,
                "status": state.get("state", "not built"),
                "note": state.get("reason", ""),
            }
        if not pathlib.Path(state.get("python") or "-").is_file():
            note = "the sandbox's environment is gone (pruned): --force runs the candidate again"
            opts.echo(f"[{c.name}] cannot be scored again: {note}")
            if not earlier:
                return {**row, "status": "not scorable", "note": note}
            # the verdict stays; what the events, the driver's files and git say is read again
            earlier.update(scoring.scorecard(lay))
            write_json(score_json, earlier)
            return earlier
        if retry:
            opts.echo(f"[{c.name}] the score failed last time: scoring again")
    else:
        if earlier and not opts.force:
            opts.echo(f"[{c.name}] has a score: skipped (--force runs it again)")
            if opts.prune:
                _prune(lay, opts)
            return earlier

        def prepare() -> str:
            """Build the sandbox; the reason when the candidate is dropped."""
            opts.echo(f"[{c.name}] building the sandbox")
            try:
                hide = author_paths(opts.base, opts.file, opts.out)
                build(c, lay, src, opts.env, probes, extra, hide)
            except ReplayError as e:
                write_json(run_json, {"state": "dropped", "reason": str(e)})
                opts.echo(f"[{c.name}] dropped: {e}")
                return str(e).splitlines()[0]
            return ""

        if state.get("state") != "prepared" or opts.force:
            if why := prepare():
                return {**row, "status": "dropped", "note": why}
            state = read_json(run_json)
        if opts.prepare_only:
            return {**row, "status": "prepared"}
        caps = Caps(c.max_usd or opts.caps.usd, c.max_hours or opts.caps.hours)

        def start() -> dict[str, Any]:
            opts.echo(f"[{c.name}] running (caps: {caps.usd:g} USD, {caps.hours:g} h)")
            write_json(run_json, {**read_json(run_json), "state": "running"})
            return opts.driver(lay, c, caps, src)

        def rebuild() -> None:
            if why := prepare():
                raise ReplayError(why)

        # the caps count per run of the driver: a wait for the reset is outside them
        driven, limited = limits.drive(start, rebuild, opts.wait, opts.echo, c.name)
        if driven.get("stopped") == "interrupted":
            raise ReplayError("interrupted")
        state = read_json(run_json)
        if limited:
            write_json(run_json, {**state, **driven, "state": limits.LIMITED, "reason": limited})
            opts.echo(f"[{c.name}] {limits.LIMITED}: {limited}")
            return {**row, "status": limits.LIMITED, "verdict": limits.LIMITED, "note": limited}
        state = {**state, **driven, "state": "stopped" if driven.get("stopped") else "finished"}
        write_json(run_json, state)

    stage = scoring.engine(lay.sandbox, c.name)["stage"]
    # before the engine ran there is nothing for a retro to read
    if stage not in ("DONE", *scoring.NOT_RUN) and "retro" not in state:
        opts.echo(f"[{c.name}] ended at {stage}: retro")
        state["retro"] = opts.retro(lay)
        write_json(run_json, state)
    row.update(scoring.score(lay, c.probes, probes, c.extra, extra), status="scored")
    row.update({k: state[k] for k in ("stopped", "wall_s", "driver_usd", "retro") if k in state})
    failed = row["verdict"] == scoring.FAILED
    why = f"score failed ({str(row.get('result_tail', ''))[:200]}): run --score-only again"
    notes = [why] if failed else []
    broken = row.get("probes_failed") or []
    notes += [f"probes failed: {', '.join(broken)}"[:200]] if broken else []
    late = row.get("extra_failed") or []
    notes += [f"extra tests failed: {', '.join(late)}"[:200]] if late else []
    notes += [state.get("stopped") or ("" if stage == "DONE" else f"ended at {stage}")]
    notes += [outcome(lay.driver)] if stage != "DONE" else []
    notes += [f"retro {state['retro']}"] if "retro" in state else []
    row["note"] = "; ".join(n for n in notes if n)
    write_json(score_json, row)
    if failed:
        opts.echo(f"[{c.name}] {row['note']}")
    elif row["verdict"] == "not run":
        opts.echo(f"[{c.name}] not run: {row['note']}")
    else:
        opts.echo(f"[{c.name}] {row['verdict']}: {row['passed']}/{row['hidden']} hidden tests")
    if opts.prune and not failed:  # scoring again needs the sandbox's environment
        _prune(lay, opts)
    return row


def run_all(cands: list[Candidate], opts: Options) -> list[dict[str, Any]]:
    """Every candidate, `opts.parallel` at a time; an error ends that candidate only."""

    def safe(c: Candidate) -> dict[str, Any]:
        row = {"name": c.name, "kind": c.kind}
        if isinstance(sources[c.repo], ReplayError):
            return {**row, "status": "dropped", "note": str(sources[c.repo]).splitlines()[0]}
        try:
            return run_one(c, opts, sources[c.repo])
        except Exception as e:  # noqa: BLE001 - the other candidates go on
            opts.echo(f"[{c.name}] error: {e}")
            return {**row, "status": "error", "note": f"{type(e).__name__}: {e}".splitlines()[0]}

    sources: dict[str, Any] = {}
    for c in cands:  # one after the other: two candidates may share a clone
        if c.repo not in sources:
            try:
                sources[c.repo] = source(c.repo, opts.base, opts.out / "sources")
            except ReplayError as e:
                sources[c.repo] = e
    STOP.clear()
    with ThreadPoolExecutor(max_workers=max(1, opts.parallel)) as pool:
        futures = [pool.submit(safe, c) for c in cands]
        try:
            return [f.result() for f in futures]
        except KeyboardInterrupt:
            STOP.set()
            raise


def main(args: argparse.Namespace) -> int:
    if args.template:
        print(TEMPLATE, end="")
        return 0
    if not args.candidates:
        print("error: name the candidates file (or --template)", file=sys.stderr)
        return 2
    path = pathlib.Path(args.candidates).resolve()
    try:
        cands = parse(path.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except CandidatesError as e:
        for line in str(e).splitlines():
            print(f"{path.name}: {line}", file=sys.stderr)
        return 2
    unknown = set(args.only) - {c.name for c in cands}
    if unknown:
        print(f"error: --only: no candidate {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out).resolve() if args.out else path.with_name(path.stem + "-replay")
    opts = Options(
        out,
        path.parent,
        args.parallel,
        Caps(args.max_usd, args.max_hours),
        args.prepare_only,
        args.score_only,
        args.force,
        args.prune,
        file=path,
    )
    chosen = [c for c in cands if not args.only or c.name in args.only]
    rows = {row["name"]: row for row in run_all(chosen, opts)}
    for c in cands:  # the table holds every candidate of the file that has a score
        earlier = read_json(out / "runs" / c.name / "score.json")
        if c.name not in rows and earlier:
            rows[c.name] = earlier
    ordered = [rows[c.name] for c in cands if c.name in rows]
    scoring.write_results(out, ordered)
    print("\n" + scoring.table(ordered) + "\n" + scoring.details(ordered))
    print(f"Results: {out / 'results.md'}")
    bad = any(r["status"] == "error" or r.get("verdict") == scoring.FAILED for r in ordered)
    return 1 if bad else 0
