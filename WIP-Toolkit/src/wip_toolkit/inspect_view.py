"""Rendering for the ``inspect`` verb.

The verb is thin by design: every number here is computed by
``wip_archive.ArchiveModel``, which the later transform verbs share. This
module only decides how the model's output reads on a terminal. The machine
contract is the model's ``to_dict`` — tables are for humans, JSON is the API.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table
from wip_archive.model import ArchiveModel

SEVERITY_STYLE = {
    "error": "red",
    "warning": "yellow",
    "info": "dim",
}


def render_summary(model: ArchiveModel, console: Console) -> None:
    """The whole-archive view, in the order an operator reads it."""
    _render_header(model, console)
    _render_islands(model, console)
    _render_templates(model, console)
    _render_terminologies(model, console)
    _render_edge_types(model, console)
    _render_files(model, console)
    render_findings(model, console)


def _render_header(model: ArchiveModel, console: Console) -> None:
    summary = model.summary()
    table = Table(title="Archive Summary")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Format version", str(summary["format_version"]))
    table.add_row("Tool version", str(summary["tool_version"]))
    table.add_row("Exported at", str(summary["exported_at"]))
    table.add_row("Source host", str(summary["source_host"]))
    table.add_row("Namespaces", ", ".join(summary["namespaces"]) or "(none)")
    table.add_row("Includes files", str(summary["include_files"]))
    table.add_row(
        "Templates",
        f"{summary['templates']} ({summary['template_versions']} versions)",
    )
    table.add_row(
        "Terminologies",
        f"{summary['terminologies']} ({summary['terms']} terms, "
        f"{summary['term_relations']} relations)",
    )
    table.add_row(
        "Documents",
        f"{summary['documents']} ({summary['document_versions']} versions)",
    )
    table.add_row("Files", str(summary["files"]))
    console.print(table)

    # Manifest counts vs rows present. Reported as a one-line verdict rather
    # than a per-type column: the mismatch detail is a finding, and a clean
    # archive should not spend a table on saying so.
    verification = model.count_verification()
    checked = [ns for ns, v in verification.items() if v["declared"]]
    mismatched = [ns for ns in checked if verification[ns]["mismatches"]]
    if mismatched:
        console.print(
            f"[red]Manifest counts disagree with the archive contents in: "
            f"{', '.join(sorted(mismatched))}[/red]"
        )
    elif checked:
        console.print(
            f"[green]OK[/green] — manifest counts match the rows present "
            f"({', '.join(sorted(checked))})"
        )

    # Provenance: what this archive was derived from, if anything stamped it.
    # The restore door already reads this key; showing it here means a
    # transformed archive declares its history before it is uploaded.
    chain = summary.get("derived_from")
    if chain:
        console.print("\n[bold]Derived from[/bold]")
        hops = chain if isinstance(chain, list) else [chain]
        for position, hop in enumerate(hops, start=1):
            if isinstance(hop, dict):
                name = hop.get("transform", "?")
                detail = ", ".join(
                    f"{k}={v}" for k, v in hop.items() if k != "transform"
                )
                console.print(f"  {position}. {name}" + (f" ({detail})" if detail else ""))
            else:
                console.print(f"  {position}. {hop}")


def _render_islands(model: ArchiveModel, console: Console) -> None:
    islands = model.islands()
    if not islands:
        return
    table = Table(
        title="Islands (self-contained extraction units)",
    )
    table.add_column("#", justify="right")
    table.add_column("Templates")
    table.add_column("Terminologies")
    for island in islands:
        table.add_row(
            str(island.index),
            ", ".join(sorted(model.template_label(t) for t in island.template_ids)),
            ", ".join(
                sorted(model.terminology_label(t) for t in island.terminology_ids)
            )
            or "[dim]none[/dim]",
        )
    console.print(table)

    shared = model.shared_terminologies()
    if shared:
        console.print(
            "\n[yellow]Shared vocabularies[/yellow] — extracting these islands "
            "separately duplicates them into each archive:"
        )
        for terminology_id, indexes in sorted(
            shared.items(), key=lambda kv: model.terminology_label(kv[0])
        ):
            console.print(
                f"  {model.terminology_label(terminology_id)} "
                f"→ islands {', '.join(str(i) for i in indexes)}"
            )


def _render_templates(model: ArchiveModel, console: Console) -> None:
    if not model.templates:
        return
    table = Table(title="Templates")
    table.add_column("Template", style="bold")
    table.add_column("Usage")
    table.add_column("Docs", justify="right")
    table.add_column("Doc versions", justify="right")
    table.add_column("Pinned versions")
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("Island", justify="right")

    for template_id in sorted(model.templates, key=model.template_label):
        report = model.template_report(template_id)
        if report is None:
            continue
        pinned = (
            ", ".join(
                f"v{v}:{count}"
                for v, count in report.docs_per_template_version.items()
            )
            or "[dim]—[/dim]"
        )
        usage = report.usage
        if not report.versioned:
            usage += " [dim](unversioned)[/dim]"
        table.add_row(
            report.label,
            usage,
            str(report.document_count),
            str(report.document_version_count),
            pinned,
            f"{report.in_degree_declared}/{report.in_degree_actual}",
            f"{report.out_degree_declared}/{report.out_degree_actual}",
            "—" if report.island is None else str(report.island),
        )
    console.print(table)
    console.print(
        "[dim]In/Out are declared/actual degree — declared answers "
        "'what does the schema say', actual answers 'what do the documents do'."
        "[/dim]"
    )


def _render_terminologies(model: ArchiveModel, console: Console) -> None:
    if not model.terminologies:
        return
    table = Table(title="Terminologies")
    table.add_column("Terminology", style="bold")
    table.add_column("Terms", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Unused", justify="right")
    table.add_column("Deprecated", justify="right")
    table.add_column("Islands")
    for terminology_id in sorted(model.terminologies, key=model.terminology_label):
        usage = model.terminology_usage(terminology_id)
        table.add_row(
            usage["terminology"],
            str(usage["terms"]),
            str(usage["used"]),
            str(usage["unused"]),
            str(usage["deprecated"]),
            ", ".join(str(i) for i in usage["islands"]) or "[dim]—[/dim]",
        )
    console.print(table)


def _render_edge_types(model: ArchiveModel, console: Console) -> None:
    reports = model.edge_type_reports()
    if not reports:
        return
    table = Table(title="Edge types")
    table.add_column("Edge type", style="bold")
    table.add_column("Declared endpoints")
    table.add_column("Rel. docs", justify="right")
    table.add_column("Latest only", justify="right")
    table.add_column("Versioned")
    table.add_column("Connectivity")
    for report in reports:
        endpoints = (
            f"{', '.join(report.declared_source_templates) or '?'} → "
            f"{', '.join(report.declared_target_templates) or '?'}"
        )
        connectivity = (
            "; ".join(
                f"{d['template']} {d['connected_documents']}/{d['total_documents']}"
                f" ({d['percent']}%)"
                for d in report.disconnection
            )
            or "[dim]none[/dim]"
        )
        table.add_row(
            report.label,
            endpoints,
            str(report.relationship_documents),
            str(report.relationship_documents_latest_only),
            "yes" if report.versioned else "[yellow]no[/yellow]",
            connectivity,
        )
    console.print(table)
    console.print(
        "[dim]Connectivity is the share of each endpoint template's documents "
        "reached by this edge type — dropping it disconnects exactly those.[/dim]"
    )


def _render_files(model: ArchiveModel, console: Console) -> None:
    blobs = model.blob_report()
    if not blobs["file_entities"] and not blobs["blobs"]:
        return
    console.print(
        f"\n[bold]Files:[/bold] {blobs['file_entities']} entities, "
        f"{blobs['blobs']} blobs, {blobs['bytes']:,} bytes declared, "
        f"{blobs['referenced']} referenced by documents"
    )


def render_findings(model: ArchiveModel, console: Console) -> None:
    findings = model.findings()
    if not findings:
        console.print("\n[green]No findings.[/green]")
        return
    table = Table(title="Findings")
    table.add_column("Severity")
    table.add_column("Class", style="bold")
    table.add_column("Subject")
    table.add_column("Count", justify="right")
    table.add_column("Detail")
    for finding in findings:
        style = SEVERITY_STYLE.get(finding.severity, "")
        detail = finding.detail
        if finding.samples:
            detail += f"\n[dim]e.g. {', '.join(finding.samples)}[/dim]"
        table.add_row(
            f"[{style}]{finding.severity}[/{style}]" if style else finding.severity,
            finding.finding_class,
            finding.subject,
            str(finding.count),
            detail,
        )
    console.print(table)


def render_template_dive(
    model: ArchiveModel, template_id: str, console: Console
) -> None:
    """The deep dive — which is literally the plan a filter would execute."""
    report = model.template_report(template_id)
    tpl = model.templates.get(template_id)
    if report is None or tpl is None:
        return

    console.print(f"\n[bold]{report.label}[/bold]  [dim]{template_id}[/dim]")
    console.print(
        f"  namespace {report.namespace} · usage {report.usage} · "
        f"versioned {'yes' if report.versioned else 'no'} · "
        f"island {report.island if report.island is not None else '—'}"
    )

    versions = Table(title="Versions")
    versions.add_column("Version", justify="right")
    versions.add_column("Status")
    versions.add_column("Fields", justify="right")
    versions.add_column("Identity fields")
    versions.add_column("Docs pinned", justify="right")
    versions.add_column("Declared references")
    for version in sorted(tpl.versions):
        node = tpl.versions[version]
        declared = [
            f"template {model.template_label(t)}"
            for t in sorted(node.all_declared_templates())
        ] + [
            f"terminology {model.terminology_label(t)}"
            for t in sorted(node.declared_terminologies)
        ]
        versions.add_row(
            str(version),
            node.status or "[dim]—[/dim]",
            str(node.field_count),
            ", ".join(node.identity_fields) or "[yellow]none (append-only)[/yellow]",
            str(report.docs_per_template_version.get(version, 0)),
            ", ".join(declared) or "[dim]none[/dim]",
        )
    console.print(versions)

    console.print(
        f"  [bold]Deletion question[/bold] — in-degree "
        f"{report.in_degree_declared} declared, {report.in_degree_actual} actual"
    )
    if report.incoming_declared:
        console.print(f"    declared by: {', '.join(report.incoming_declared)}")
    if report.incoming_actual:
        console.print(f"    referenced by: {', '.join(report.incoming_actual)}")
    if not report.incoming_declared and not report.incoming_actual:
        console.print("    [green]nothing points here — safe to drop[/green]")

    console.print(
        "  [bold]Extraction question[/bold] — taking this template also takes:"
    )
    _print_closure(model, "schema closure", report.schema_closure, console)
    _print_closure(model, "data closure (with history)", report.data_closure, console)
    _print_closure(
        model,
        "data closure (latest only)",
        report.data_closure_latest_only,
        console,
    )


def _print_closure(model: ArchiveModel, label: str, closure, console: Console) -> None:
    templates = sorted(model.template_label(t) for t in closure.template_ids)
    terminologies = sorted(
        model.terminology_label(t) for t in closure.terminology_ids
    )
    parts = []
    if templates:
        parts.append(f"templates: {', '.join(templates)}")
    if terminologies:
        parts.append(f"terminologies: {', '.join(terminologies)}")
    console.print(f"    {label}: {' · '.join(parts) or '[green]nothing[/green]'}")
    if closure.external:
        outside = sorted({f"{e.kind} {e.target_id}" for e in closure.external})
        console.print(
            f"      [yellow]outside this archive:[/yellow] {', '.join(outside[:5])}"
            + (f" (+{len(outside) - 5} more)" if len(outside) > 5 else "")
        )


def render_terminology_dive(
    model: ArchiveModel, terminology_id: str, console: Console
) -> None:
    usage = model.terminology_usage(terminology_id)
    if not usage:
        return
    console.print(
        f"\n[bold]{usage['terminology']}[/bold]  [dim]{terminology_id}[/dim]"
    )
    console.print(
        f"  namespace {usage['namespace']} · mutable "
        f"{'yes' if usage['mutable'] else 'no'} · {usage['terms']} terms "
        f"({usage['used']} used, {usage['unused']} unused, "
        f"{usage['deprecated']} deprecated)"
    )
    table = Table(title=f"Terms — {usage['terminology']}")
    table.add_column("Term", style="bold")
    table.add_column("Status")
    table.add_column("References", justify="right")
    table.add_column("Relations", justify="right")
    table.add_column("Aliases")
    for row in model.terminology_terms(terminology_id):
        status = row["status"]
        if status == "deprecated" and row["references"]:
            status = f"[yellow]{status}[/yellow]"
        table.add_row(
            row["term"],
            status,
            str(row["references"]),
            str(row["relations"]),
            ", ".join(row["aliases"]) or "[dim]—[/dim]",
        )
    console.print(table)


def json_payload(
    model: ArchiveModel,
    *,
    templates: list[str] | None = None,
    terminologies: list[str] | None = None,
) -> dict[str, Any]:
    return model.to_dict(templates=templates, terminologies=terminologies)
