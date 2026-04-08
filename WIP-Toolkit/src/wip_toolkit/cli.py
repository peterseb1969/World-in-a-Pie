"""Click CLI entry point for WIP Toolkit."""

from __future__ import annotations

import json as _json
import sys
from importlib.metadata import version

import click
from rich.console import Console
from rich.table import Table

from .archive import ENTITY_FILES, ArchiveReader
from .backfill import backfill_synonyms
from .client import WIPClient
from .config import WIPConfig
from .export.exporter import run_export
from .import_.importer import run_import
from .seed import run_seed
from .status import StatusThresholds, collect_status

console = Console(stderr=True)


def _get_version() -> str:
    try:
        return version("wip-toolkit")
    except Exception:
        return "unknown"


@click.group()
@click.version_option(_get_version(), prog_name="wip-toolkit")
@click.option("--host", default="localhost", help="WIP host (default: localhost)")
@click.option("--proxy", is_flag=True, help="Route through Caddy/Ingress reverse proxy")
@click.option("--port", default=None, type=int, help="Proxy port (default: 8443 for Caddy, 443 for K8s Ingress)")
@click.option("--api-key", default="", help="API key (default: from .env or dev key)")
@click.option("--no-verify-ssl", is_flag=True, help="Disable SSL verification")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.pass_context
def main(ctx: click.Context, host: str, proxy: bool, port: int | None, api_key: str, no_verify_ssl: bool, verbose: bool) -> None:
    """WIP Toolkit — Backup, migration, and data management for World In a Pie."""
    ctx.ensure_object(dict)
    config_kwargs: dict = dict(
        host=host,
        proxy=proxy,
        api_key=api_key,
        verify_ssl=not no_verify_ssl,
        verbose=verbose,
    )
    if port is not None:
        config_kwargs["proxy_port"] = port
    ctx.obj["config"] = WIPConfig(**config_kwargs)


@main.command()
@click.argument("namespace")
@click.argument("output_path")
@click.option("--include-files", is_flag=True, help="Include binary file content")
@click.option("--include-inactive", is_flag=True, help="Include inactive/deprecated entities")
@click.option("--skip-documents", is_flag=True, help="Export only terminologies + templates")
@click.option("--skip-closure", is_flag=True, help="Skip referential integrity closure")
@click.option("--skip-synonyms", is_flag=True, help="Skip Registry synonym export")
@click.option("--latest-only", is_flag=True, help="Export only latest document versions")
@click.option("--filter-templates", default=None,
              help="Only export templates matching this prefix (e.g., 'DND_'). "
                   "Documents are filtered to matching templates. Comma-separated for multiple prefixes.")
@click.option("--dry-run", is_flag=True, help="Show what would be exported")
@click.pass_context
def export(
    ctx: click.Context,
    namespace: str,
    output_path: str,
    include_files: bool,
    include_inactive: bool,
    skip_documents: bool,
    skip_closure: bool,
    skip_synonyms: bool,
    latest_only: bool,
    filter_templates: str | None,
    dry_run: bool,
) -> None:
    """Export a namespace to a ZIP archive.

    NAMESPACE is the WIP namespace to export (e.g., "wip").
    OUTPUT_PATH is the destination file path for the archive.
    """
    config = ctx.obj["config"]
    with WIPClient(config) as client:
        # Check service health first
        console.print("[bold cyan]Checking services...[/bold cyan]")
        health = client.check_all_services()
        all_healthy = True
        for service, (healthy, msg) in health.items():
            status = "[green]OK[/green]" if healthy else f"[red]{msg}[/red]"
            console.print(f"  {service}: {status}")
            if not healthy:
                all_healthy = False

        if not all_healthy and not dry_run:
            console.print("\n[red bold]Some services are not healthy. Aborting.[/red bold]")
            sys.exit(1)

        # Parse template filter prefixes
        template_prefixes = None
        if filter_templates:
            template_prefixes = [p.strip() for p in filter_templates.split(",") if p.strip()]

        stats = run_export(
            client, namespace, output_path,
            include_files=include_files,
            include_inactive=include_inactive,
            skip_documents=skip_documents,
            skip_closure=skip_closure,
            skip_synonyms=skip_synonyms,
            latest_only=latest_only,
            template_prefixes=template_prefixes,
            dry_run=dry_run,
        )

        if stats.warnings:
            console.print(f"\n[yellow]{len(stats.warnings)} warning(s)[/yellow]")

        console.print(f"\n[bold green]Export completed[/bold green] in {stats.duration_seconds}s")


@main.command(name="import")
@click.argument("archive_path")
@click.option("--mode", type=click.Choice(["fresh", "restore"]), default="fresh",
              help="Import mode (default: fresh)")
@click.option("--target-namespace", default=None, help="Override target namespace")
@click.option("--register-synonyms", is_flag=True, help="Register old→new ID synonyms (fresh mode)")
@click.option("--skip-documents", is_flag=True, help="Skip document import")
@click.option("--skip-files", is_flag=True, help="Skip file upload")
@click.option("--batch-size", default=50, type=int, help="Document batch size (default: 50)")
@click.option("--dry-run", is_flag=True, help="Preview without making changes")
@click.option("--continue-on-error", is_flag=True, help="Don't stop on individual failures")
@click.pass_context
def import_cmd(
    ctx: click.Context,
    archive_path: str,
    mode: str,
    target_namespace: str | None,
    register_synonyms: bool,
    skip_documents: bool,
    skip_files: bool,
    batch_size: int,
    dry_run: bool,
    continue_on_error: bool,
) -> None:
    """Import an archive into a WIP instance.

    ARCHIVE_PATH is the path to the ZIP archive to import.
    """
    config = ctx.obj["config"]
    with WIPClient(config) as client:
        stats = run_import(
            client, archive_path,
            mode=mode,
            target_namespace=target_namespace,
            register_synonyms=register_synonyms,
            skip_documents=skip_documents,
            skip_files=skip_files,
            batch_size=batch_size,
            continue_on_error=continue_on_error,
            dry_run=dry_run,
        )

        if stats.errors:
            sys.exit(1)


@main.command()
@click.argument("archive_path")
@click.option("--show-ids", is_flag=True, help="List all entity IDs")
@click.option("--show-references", is_flag=True, help="Show dependency graph")
def inspect(archive_path: str, show_ids: bool, show_references: bool) -> None:
    """Show archive contents without importing.

    ARCHIVE_PATH is the path to the ZIP archive to inspect.
    """
    try:
        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()

            # Summary table
            table = Table(title="Archive Summary")
            table.add_column("Property", style="bold")
            table.add_column("Value")

            table.add_row("Format version", manifest.format_version)
            table.add_row("Tool version", manifest.tool_version)
            table.add_row("Exported at", str(manifest.exported_at))
            table.add_row("Source host", manifest.source_host)
            table.add_row("Namespace", manifest.namespace)
            table.add_row("Include inactive", str(manifest.include_inactive))
            table.add_row("Include files", str(manifest.include_files))
            console.print(table)

            # Entity counts
            counts_table = Table(title="Entity Counts")
            counts_table.add_column("Entity Type", style="bold")
            counts_table.add_column("Count", justify="right")
            counts_table.add_column("Verified", justify="right", style="dim")

            for entity_type in ENTITY_FILES:
                manifest_count = getattr(manifest.counts, entity_type, 0)
                actual_count = reader.entity_count(entity_type)
                match = "[green]OK[/green]" if manifest_count == actual_count else f"[red]{actual_count}[/red]"
                counts_table.add_row(entity_type.title(), str(manifest_count), match)

            counts_table.add_row(
                "Total", str(manifest.counts.total), "", style="bold",
            )
            console.print(counts_table)

            # Closure info
            if manifest.closure.external_terminologies or manifest.closure.external_templates:
                closure_table = Table(title="Closure (External Dependencies)")
                closure_table.add_column("Type", style="bold")
                closure_table.add_column("IDs")
                if manifest.closure.external_terminologies:
                    closure_table.add_row(
                        "Terminologies",
                        ", ".join(manifest.closure.external_terminologies),
                    )
                if manifest.closure.external_templates:
                    closure_table.add_row(
                        "Templates",
                        ", ".join(manifest.closure.external_templates),
                    )
                console.print(closure_table)

            # Warnings
            if manifest.closure.warnings:
                console.print(f"\n[yellow]{len(manifest.closure.warnings)} closure warning(s):[/yellow]")
                for w in manifest.closure.warnings:
                    console.print(f"  {w}")

            # Blobs
            blobs = reader.list_blobs()
            if blobs:
                console.print(f"\nBinary files: {len(blobs)}")

            # Archive size
            console.print(f"\nArchive size: {reader.compressed_size():,} bytes compressed, "
                           f"{reader.total_size():,} bytes uncompressed")

            # Show IDs
            if show_ids:
                _show_entity_ids(reader)

            # Show references
            if show_references:
                _show_references(reader)

    except FileNotFoundError:
        console.print(f"[red]Archive not found:[/red] {archive_path}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error reading archive:[/red] {e}")
        sys.exit(1)


def _show_entity_ids(reader: ArchiveReader) -> None:
    """List all entity IDs in the archive."""
    id_fields = {
        "terminologies": "terminology_id",
        "terms": "term_id",
        "templates": "template_id",
        "documents": "document_id",
        "files": "file_id",
    }

    for entity_type, id_field in id_fields.items():
        entities = list(reader.read_entities(entity_type))
        if not entities:
            continue

        table = Table(title=f"{entity_type.title()} IDs")
        table.add_column("ID", style="bold")
        table.add_column("Source")
        if entity_type in ("terminologies", "templates"):
            table.add_column("Value")
            table.add_column("Version")

        for e in entities:
            eid = e.get(id_field, "?")
            source = e.get("_source", "?")
            if entity_type in ("terminologies", "templates"):
                value = e.get("value", "")
                version = str(e.get("version", ""))
                table.add_row(eid, source, value, version)
            else:
                table.add_row(eid, source)

        console.print(table)


@main.command(name="backfill-synonyms")
@click.argument("namespace")
@click.option("--skip-documents", is_flag=True, help="Skip document synonym backfill")
@click.option("--batch-size", default=100, type=int, help="Synonym batch size (default: 100)")
@click.option("--dry-run", is_flag=True, help="Preview without making changes")
@click.pass_context
def backfill_synonyms_cmd(
    ctx: click.Context,
    namespace: str,
    skip_documents: bool,
    batch_size: int,
    dry_run: bool,
) -> None:
    """Backfill auto-synonyms for all entities in a namespace.

    Iterates all terminologies, terms, templates, and (optionally) documents,
    registering auto-synonyms with the Registry. Idempotent — existing
    synonyms are skipped.

    NAMESPACE is the WIP namespace to backfill (e.g., "wip").
    """
    config = ctx.obj["config"]
    with WIPClient(config) as client:
        console.print(f"[bold]Backfilling synonyms for namespace: {namespace}[/bold]")

        summary = backfill_synonyms(
            client, namespace,
            skip_documents=skip_documents,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        # Print summary
        console.print("\n[bold green]Backfill complete[/bold green]")
        total_added = 0
        total_existing = 0
        for entity_type, counts in summary.items():
            added = counts.get("added", 0)
            existing = counts.get("existing", 0)
            failed = counts.get("failed", 0)
            total_added += added
            total_existing += existing
            console.print(f"  {entity_type}: {added} new, {existing} existing, {failed} failed")

        console.print(f"\n  Total: {total_added} new synonyms registered, {total_existing} already existed")


@main.command(name="update-document")
@click.argument("document_id")
@click.option("--patch", "patch_json", required=True,
              help="JSON Merge Patch (RFC 7396) to apply to the document's `data`. "
                   "Use '-' to read JSON from stdin.")
@click.option("--if-match", type=int, default=None,
              help="Optimistic concurrency: only apply if current version matches.")
@click.pass_context
def update_document_cmd(
    ctx: click.Context,
    document_id: str,
    patch_json: str,
    if_match: int | None,
) -> None:
    """Apply an RFC 7396 JSON Merge Patch to a document.

    DOCUMENT_ID is the canonical document_id (e.g., 'DOC-xxx') or a registered
    synonym. The patch is applied to the document's `data` field:

      - Objects are deep-merged
      - Arrays are REPLACED entirely
      - `null` deletes the corresponding key

    Identity fields cannot be changed via PATCH (use create-document with new
    identity values instead). Archived or soft-deleted documents are rejected.

    Examples:

      wip-toolkit update-document DOC-123 --patch '{"score": 92}'

      wip-toolkit update-document DOC-123 --patch '{"middle_name": null}'

      cat patch.json | wip-toolkit update-document DOC-123 --patch -
    """
    if patch_json == "-":
        patch_json = sys.stdin.read()
    try:
        patch = _json.loads(patch_json)
    except _json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON in --patch:[/red] {e}")
        sys.exit(2)
    if not isinstance(patch, dict):
        console.print("[red]--patch must be a JSON object.[/red]")
        sys.exit(2)

    item: dict = {"document_id": document_id, "patch": patch}
    if if_match is not None:
        item["if_match"] = if_match

    config = ctx.obj["config"]
    with WIPClient(config) as client:
        resp = client.patch(
            "document-store",
            "/api/document-store/documents",
            json=[item],
        )

    result = resp["results"][0]
    if result.get("status") == "error":
        code = result.get("error_code") or "error"
        console.print(
            f"[red]PATCH failed[/red] ({code}): {result.get('error', 'unknown')}",
        )
        sys.exit(1)

    console.print(_json.dumps(result, indent=2, default=str))


@main.command()
@click.argument("data_model_dir")
@click.argument("namespace")
@click.option("--skip-templates", is_flag=True, help="Only seed terminologies and terms")
@click.option("--dry-run", is_flag=True, help="Preview without making changes")
@click.option("--continue-on-error", is_flag=True, help="Don't stop on individual failures")
@click.pass_context
def seed(
    ctx: click.Context,
    data_model_dir: str,
    namespace: str,
    skip_templates: bool,
    dry_run: bool,
    continue_on_error: bool,
) -> None:
    """Seed a namespace from /export-model seed files.

    DATA_MODEL_DIR is the path to the data-model/ directory containing
    terminologies/ and templates/ subdirectories with JSON seed files.

    NAMESPACE is the target WIP namespace (e.g., "dnd").
    """
    config = ctx.obj["config"]
    with WIPClient(config) as client:
        stats = run_seed(
            client, data_model_dir, namespace,
            skip_templates=skip_templates,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
        )

        if stats.errors:
            sys.exit(1)


_SEVERITY_STYLE = {
    "ok": "[green]OK[/green]",
    "warning": "[yellow]WARN[/yellow]",
    "critical": "[red bold]CRIT[/red bold]",
    "unknown": "[dim]?[/dim]",
}


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output JSON instead of human-readable text")
@click.option("--quiet", is_flag=True, help="Cron mode: print nothing on overall=ok")
@click.option("--integrity", is_flag=True,
              help="Also run aggregated referential integrity check (heavier)")
@click.option("--integrity-template-limit", default=1000, type=int,
              help="Max templates to scan for integrity (0 = all, default 1000)")
@click.option("--integrity-document-limit", default=1000, type=int,
              help="Max documents to scan for integrity (0 = all, default 1000)")
@click.option("--failed-events-warning", default=1, type=int,
              help="Failed-event count that triggers a warning (default 1)")
@click.option("--consumer-lag-warning", default=100, type=int,
              help="NATS pending messages that trigger a warning (default 100)")
@click.option("--consumer-lag-critical", default=1000, type=int,
              help="NATS pending messages that trigger critical (default 1000)")
@click.pass_context
def status(
    ctx: click.Context,
    as_json: bool,
    quiet: bool,
    integrity: bool,
    integrity_template_limit: int,
    integrity_document_limit: int,
    failed_events_warning: int,
    consumer_lag_warning: int,
    consumer_lag_critical: int,
) -> None:
    """Aggregate WIP service status and exit non-zero on problems.

    Default mode is cheap and cron-friendly: liveness checks for every service,
    plus reporting-sync /metrics and /alerts and ingest-gateway /metrics.

    Pass --integrity to also run the referential integrity scan (heavier; the
    full scan can take minutes on large instances — limits default to 1000).

    Exit codes:
        0 ok       1 warning       2 critical       3 unknown / unreachable
    """
    config = ctx.obj["config"]
    thresholds = StatusThresholds(
        failed_events_warning=failed_events_warning,
        consumer_lag_warning=consumer_lag_warning,
        consumer_lag_critical=consumer_lag_critical,
    )
    with WIPClient(config) as client:
        report = collect_status(
            client,
            thresholds,
            include_integrity=integrity,
            integrity_template_limit=integrity_template_limit,
            integrity_document_limit=integrity_document_limit,
        )

    if as_json:
        if not (quiet and not report.has_problems()):
            click.echo(_json.dumps(report.to_dict(), indent=2, default=str))
    else:
        if quiet and not report.has_problems():
            pass
        else:
            _print_status_report(report)

    sys.exit(report.exit_code())


def _print_status_report(report) -> None:
    """Render a StatusReport in human-readable form (to stderr console)."""
    overall_style = _SEVERITY_STYLE.get(report.overall, report.overall)
    console.print(f"[bold]WIP status:[/bold] {overall_style}  [dim]({report.checked_at})[/dim]")
    if report.services_unreachable:
        console.print(
            f"  [red]Unreachable:[/red] {', '.join(report.services_unreachable)}"
        )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for c in report.checks:
        table.add_row(c.name, _SEVERITY_STYLE.get(c.severity, c.severity), c.message)
    console.print(table)
    # Print details for non-OK checks (helps cron output explain itself)
    for c in report.checks:
        if c.severity != "ok" and c.details:
            console.print(f"  [dim]{c.name} details:[/dim] {c.details}")


def _show_references(reader: ArchiveReader) -> None:
    """Show dependency graph for templates."""
    templates = list(reader.read_entities("templates"))
    if not templates:
        return

    console.print("\n[bold]Template Dependency Graph[/bold]")

    for tpl in templates:
        tid = tpl.get("template_id", "?")
        value = tpl.get("value", "?")
        version = tpl.get("version", "?")
        deps: list[str] = []

        if tpl.get("extends"):
            deps.append(f"extends {tpl['extends']}")

        for field in tpl.get("fields", []):
            if field.get("terminology_ref"):
                deps.append(f"term_ref {field['terminology_ref']}")
            if field.get("template_ref"):
                deps.append(f"tpl_ref {field['template_ref']}")
            for tt in field.get("target_templates") or []:
                deps.append(f"target_tpl {tt}")
            for tterm in field.get("target_terminologies") or []:
                deps.append(f"target_term {tterm}")

        dep_str = ", ".join(deps) if deps else "[dim]none[/dim]"
        console.print(f"  {tid} v{version} ({value}) → {dep_str}")
