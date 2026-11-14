"""The files of a port (references.py: a spec with a filled `## Ported from`).

**Stubs from the reference** (`framegate port-stubs <feature>`, run by the spec dialogue): for
each `code` row the target file is written as the reference file's text at the commit the spec
names, with the function and method bodies replaced by "not implemented" (the language
adapter's `reference_stub`; python only). Everything else is the reference's, byte for byte, so
the implementer starts from the reference's wording and a file it leaves alone is identical.
Seen live (da-argos, a runner ported from da-otto): hand-written stubs in this repository's
style were kept, three structure findings at the review, one more implementer run and gate.
A code row that is no source file of the language (a data or config file) is copied as it is.
The stub's imports are the reference's: the ones that will not resolve here are listed (the
red check needs tests that can be collected), and fixing those lines is the one hand edit.

**Carried-tests report** (test_checks.md, the final brief): for each `tests` row the test
functions of the reference file and which of them are in the target file, by name. Seen live:
14 of the reference's 60 tests were carried over next to 50 new ones, and nothing said which
were left out and why.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from framegate import references
from framegate.adapters.base import LanguageAdapter
from framegate.config import Config
from framegate.state import load_progress, tag_exists

CARRIED_NAME = "Reference tests carried over"


def _supports_stubs(adapter: LanguageAdapter) -> bool:
    return type(adapter).reference_stub is not LanguageAdapter.reference_stub


def port_stubs(
    cfg: Config,
    adapter: LanguageAdapter,
    feature: str,
    force: bool = False,
    echo: Callable[[str], None] = print,
) -> int:
    """`framegate port-stubs <feature>`: write the stub of every `code` target of the port
    from the reference. 0 = done (also: nothing to do, not supported); 1 = a problem line."""
    port = references.feature_port(cfg, feature)
    if port is None:
        echo(
            f"port-stubs: {cfg.paths.specs}/{feature}/requirements.md has no filled "
            f"'{references.PORT_SECTION}' section: nothing to do."
        )
        return 0
    rows = port.of_kind("code")
    if not _supports_stubs(adapter):
        echo(
            f"NOT SUPPORTED: no stub generator for language '{cfg.language}'. Write the stubs "
            "of the ported files by hand: the reference's public names, signatures and doc "
            "comments as they are, bodies that only signal 'not implemented', no spec IDs "
            "added."
        )
        return 0
    where = references.pinned_commit(cfg, feature, load_progress(cfg, feature).get("references"))
    if where is None:
        echo(
            f"PROBLEM: reference '{port.reference}' is not declared in framegate.toml "
            "[references] or its folder is not on this machine: no stub written."
        )
        return 1
    if tag_exists(cfg, f"spec-approved/{feature}") and not force:
        echo(
            "PROBLEM: the spec is approved, the stubs are frozen with it. After a spec change "
            "that needs new stubs: `framegate port-stubs <feature> --force`, then approve "
            "the spec again."
        )
        return 1
    folder, commit = where
    counts = {"written": 0, "unchanged": 0, "kept": 0, "problems": 0}
    made: dict[str, bytes] = {}
    for row in rows:
        data, origin = references.reference_bytes(folder, row.source, commit)
        label = f"{row.target} <- {port.reference}/{row.source}"
        if data is None:
            echo(f"PROBLEM: {label}: not found in the reference. Check the row's Source path.")
            counts["problems"] += 1
            continue
        notes = ["copied as it is (no source file of this language)"]
        text = data
        if row.source.endswith(tuple(adapter.source_suffixes)):
            try:
                made_stub = adapter.reference_stub(data)
            except (SyntaxError, ValueError, RecursionError) as e:
                echo(
                    f"PROBLEM: {label}: the reference file cannot be parsed "
                    f"({type(e).__name__}: {e}). Write this stub by hand."
                )
                counts["problems"] += 1
                continue
            assert made_stub is not None
            text, notes = made_stub
        target = cfg.root / row.target
        if target.is_file() and target.read_bytes() == text:
            counts["unchanged"] += 1
            made[row.target] = text
            echo(f"UNCHANGED: {label}")
            continue
        if target.is_file() and not force:
            counts["kept"] += 1
            echo(f"KEPT: {row.target} exists and is not the generated stub (--force overwrites it)")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text)
        counts["written"] += 1
        made[row.target] = text
        echo(f"WROTE: {label} (reference {origin}): " + "; ".join(notes))
    ported = {r.source: r.target for r in rows}
    imports = [
        line
        for target, text in made.items()
        if target.endswith(tuple(adapter.source_suffixes))
        for line in _import_problems(adapter, target, text, ported)
    ]
    for line in imports:
        echo(f"IMPORT: {line}")
    echo(
        f"port-stubs: {counts['written']} written, {counts['unchanged']} unchanged, "
        f"{counts['kept']} kept, {counts['problems']} problem(s); {len(imports)} import(s) "
        "that will not resolve here"
        + (
            ". Fix each IMPORT line in its stub (the only hand edit of a generated stub; the "
            "tests must be collectable) and list the kind of change under 'What may differ'."
            if imports
            else "."
        )
    )
    return 1 if counts["problems"] else 0


def _import_problems(
    adapter: LanguageAdapter, target: str, text: bytes, ported: dict[str, str]
) -> list[str]:
    try:
        return adapter.stub_import_problems(target, text, ported)
    except Exception as e:  # a report: never a reason to lose the stubs that were written
        return [f"{target}: imports not checked ({type(e).__name__}: {e})"]


# ---------- which reference tests were carried over ----------
def carried(
    cfg: Config, adapter: LanguageAdapter, feature: str, pins: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """For a port with `tests` rows: per row the reference file's test functions and the ones
    whose name is not in the target file. {} for a normal feature or a port without test
    rows; "skipped" names the reason when it cannot be told here."""
    port = references.feature_port(cfg, feature)
    rows = port.of_kind("tests") if port else []
    if port is None or not rows:
        return {}
    rep: dict[str, Any] = {"reference": port.reference, "skipped": "", "files": []}
    where = references.pinned_commit(cfg, feature, pins)
    if where is None:
        rep["skipped"] = f"reference '{port.reference}' cannot be read on this machine"
        return rep
    folder, commit = where
    for row in rows:
        entry: dict[str, Any] = {"source": row.source, "target": row.target}
        data, _ = references.reference_bytes(folder, row.source, commit)
        if data is None:
            entry["note"] = "not found in the reference"
        else:
            try:
                names = adapter.test_names(data)
                target = cfg.root / row.target
                have = adapter.test_names(target.read_bytes()) if target.is_file() else []
            except (SyntaxError, ValueError) as e:  # a file that cannot be parsed
                names, have = [], []
                entry["note"] = f"not parsed ({type(e).__name__}: {e})"
            if names is None or have is None:
                rep["skipped"] = f"not supported for language '{cfg.language}'"
                rep["files"] = []
                return rep
            short = {n.rsplit("::", 1)[-1] for n in have}
            missing = [n for n in names if n not in have and n.rsplit("::", 1)[-1] not in short]
            entry.update(total=len(names), missing=missing)
        rep["files"].append(entry)
    return rep


def carried_counts(rep: dict[str, Any]) -> tuple[int, int]:
    """(carried, all) reference tests of the report."""
    rows = [r for r in rep.get("files") or [] if "total" in r]
    total = sum(int(r["total"]) for r in rows)
    return total - sum(len(r["missing"]) for r in rows), total


def carried_lines(rep: dict[str, Any]) -> list[str]:
    """The report for test_checks.md ([] for a normal feature)."""
    if not rep:
        return []
    if rep.get("skipped"):
        return [f"SKIPPED: {rep['skipped']}"]
    done, total = carried_counts(rep)
    out = [
        f"{done} of {total} test function(s) of reference '{rep['reference']}' are in the "
        "target files (by name)."
    ]
    for row in rep["files"]:
        label = f"- {row['source']} -> {row['target']}: "
        if row.get("note"):
            out.append(label + row["note"])
            continue
        missing = row["missing"]
        out.append(label + f"{row['total'] - len(missing)} of {row['total']} carried over")
        out += [f"    NOT CARRIED OVER: {name}" for name in missing]
    if done < total:
        out.append(
            "Every test listed as not carried over needs a reason in test_summary.md, '## "
            "Reference tests not carried over', tied to the spec's 'What may differ' or to "
            "code that is not ported yet; without one it is an objection."
        )
    return out


def brief_line(
    cfg: Config, adapter: LanguageAdapter, feature: str, pins: list[dict[str, Any]] | None
) -> str:
    """The final brief's generated line about the carried tests ("" for a normal feature)."""
    rep = carried_report(cfg, adapter, feature, pins)
    return carried_summary(rep, f"{cfg.paths.out}/{feature}/test_checks.md")


def carried_report(
    cfg: Config, adapter: LanguageAdapter, feature: str, pins: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """`carried`, as a report: a failure says so and never stops the tests phase or the
    brief."""
    try:
        return carried(cfg, adapter, feature, pins)
    except Exception as e:
        return {"skipped": f"report failed ({type(e).__name__}: {e})"}


def checks_section(rep: dict[str, Any]) -> str:
    """The report as a section of test_checks.md ("" for a normal feature)."""
    if not rep:
        return ""
    done, total = carried_counts(rep)
    count = "SKIPPED" if rep.get("skipped") else f"{done} of {total}"
    body = "\n".join(carried_lines(rep))
    return f"### {CARRIED_NAME} (note, not blocking): {count}\n~~~\n{body}\n~~~"


def carried_summary(rep: dict[str, Any], where: str) -> str:
    """One line for the final brief ("" for a normal feature); `where`: the file with the
    names."""
    if not rep:
        return ""
    if rep.get("skipped"):
        return f"reference tests carried: SKIPPED, {rep['skipped']}"
    done, total = carried_counts(rep)
    return f"reference tests carried: {done} of {total}" + (
        f"; not carried: see {where}, '{CARRIED_NAME}'" if done < total else ""
    )
