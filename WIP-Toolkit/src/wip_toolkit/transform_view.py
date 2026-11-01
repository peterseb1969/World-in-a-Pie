"""What every transform verb prints: where the output went, what travelled,
and the verdict of verifying it. One rendering for `filter` and the
transforms after it, so they report alike."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table
from wip_archive.transform import TransformResult

from .inspect_view import render_findings

# `--json` of every transform verb: versioned like inspect's and verify's
# (major.minor; additions are new optional fields and bump the minor).
TRANSFORM_JSON_SCHEMA = "1.1"  # 1.1: dry_run; output null in a dry run

_COLOURS = {"pass": "green", "warn": "yellow", "fail": "red"}
_TYPES = ("terminologies", "terms", "term_relations", "templates", "documents", "files",
          "registry_entries")


def json_payload(result: TransformResult) -> dict[str, Any]:
    return {
        "schema": TRANSFORM_JSON_SCHEMA,
        "output": str(result.output) if result.output is not None else None,
        "dry_run": result.output is None,
        "verdict": result.verdict,
        "hop": result.hop,
        "findings": [f.to_dict() for f in result.findings],
    }


def render_result(result: TransformResult, console: Console) -> None:
    target = result.output if result.output is not None else "dry run, nothing written"
    table = Table(title=f"{result.hop['transform']} → {target}")
    table.add_column("Entity")
    table.add_column("Kept", justify="right")
    table.add_column("Dropped", justify="right")
    for entity_type in _TYPES:
        kept = result.kept.get(entity_type, 0)
        dropped = result.dropped.get(entity_type, 0)
        if kept or dropped:
            table.add_row(entity_type, f"{kept:,}", f"{dropped:,}")
    if result.blobs_kept or result.blobs_dropped:
        table.add_row("blobs", f"{result.blobs_kept:,}", f"{result.blobs_dropped:,}")
    console.print(table)
    console.print(
        f"{result.referents:,} referent(s): entities the output references but does "
        "not carry; a restore resolves them on the target by their keys"
    )
    if result.findings:
        render_findings(result.model, console, result.findings)
    verdict = result.verdict
    console.print(f"[{_COLOURS[verdict]}]verdict: {verdict}[/{_COLOURS[verdict]}]")
    if result.output is None:
        console.print("Dry run: this is what would be written. Nothing was written.")


# `diff --json`: versioned like the transform verbs' JSON.
DIFF_JSON_SCHEMA = "1.0"


def render_diff(diff: Any, console: Console) -> None:
    """The differing (namespace, entity type) rows, then referents, blobs
    and the two provenance chains."""
    if diff.namespaces_only_a or diff.namespaces_only_b:
        console.print(f"namespaces only in A: {', '.join(diff.namespaces_only_a) or '—'}; "
                      f"only in B: {', '.join(diff.namespaces_only_b) or '—'}")
    table = Table(title="Rows (A → B)")
    for column in ("Namespace", "Entity", "Added", "Removed", "Changed", "Unchanged"):
        table.add_column(column, justify="left" if column in ("Namespace", "Entity") else "right")
    rows = [((ns, t), d) for (ns, t), d in sorted(diff.types.items()) if d.differs]
    rows += [(("(archive)", "referents"), diff.referents), (("(archive)", "blobs"), diff.blobs)]
    for (ns, entity_type), d in rows:
        if d.differs:
            table.add_row(ns, entity_type, f"{d.added:,}", f"{d.removed:,}", f"{d.changed:,}",
                          f"{d.unchanged:,}")
    if table.row_count:
        console.print(table)
    for (ns, entity_type), d in rows:
        for key, fields in d.changed_samples.items():
            changed = ", ".join(fields) if fields else "content"
            console.print(f"  changed {ns} {entity_type} {key}: {changed}")
    for label, chain in (("A", diff.chain_a), ("B", diff.chain_b)):
        hops = " → ".join(str(h.get("transform", "?")) for h in chain) or "a full backup"
        console.print(f"{label}: {hops}")
    if diff.identical:
        console.print("[green]identical[/green]")
    else:
        console.print("[yellow]differ[/yellow]")
