"""Click CLI entry point for WIP Toolkit."""

from __future__ import annotations

import json as _json
import sys
from importlib.metadata import version
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from wip_archive.archive import ArchiveReader
from wip_archive.model import AmbiguousNameError, ArchiveModel

from .backfill import backfill_synonyms
from .client import WIPClient, WIPClientError
from .config import WIPConfig
from .export.exporter import run_export
from .inspect_view import (
    json_payload,
    render_findings,
    render_migratability,
    render_summary,
    render_template_dive,
    render_terminology_dive,
)
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
@click.option("--include-inactive", is_flag=True,
              help="Include inactive/deprecated entities. Required for full document "
                   "version history: prior versions are status=inactive in document-store, "
                   "so without this flag the export carries latest active versions only.")
@click.option("--skip-documents", is_flag=True, help="Export only terminologies + templates")
@click.option("--skip-closure", is_flag=True, help="Skip referential integrity closure")
@click.option("--skip-synonyms", is_flag=True, help="Skip Registry synonym export")
@click.option("--latest-only", is_flag=True,
              help="Export only latest document versions. Note: without "
                   "--include-inactive the export is latest-only already; this flag "
                   "mainly re-narrows an --include-inactive export.")
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

    Deprecated: use `backup` (the server engine's complete archive), then
    `filter`, `prune`, `latest-only` or `strip-blobs` to shape it. export
    is removed in the next release.

    Default contract: the archive carries the latest ACTIVE version of each
    document. Full version history requires --include-inactive (superseded
    versions are status=inactive in document-store).
    """
    console.print(
        "[yellow]Deprecated:[/yellow] `export` is removed in the next release. Use "
        "`wip-toolkit backup NAMESPACE OUT.zip` (the server's complete archive), then "
        "`filter`, `prune`, `latest-only` or `strip-blobs` to shape it."
    )
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


@main.command()
@click.argument("archive_path")
@click.argument("subjects", nargs=-1)
@click.option(
    "--terminology",
    "terminologies",
    multiple=True,
    help="Deep-dive a terminology by value or id (repeatable)",
)
@click.option(
    "--namespace",
    "namespaces",
    multiple=True,
    help="Restrict the analysis to these namespaces (repeatable)",
)
@click.option(
    "--target-version",
    type=int,
    default=None,
    help="With one TEMPLATE: would its active documents migrate to this version?",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.option("--show-ids", is_flag=True, help="List all entity IDs")
@click.option("--show-references", is_flag=True, help="Show raw dependency graph")
def inspect(
    archive_path: str,
    subjects: tuple[str, ...],
    terminologies: tuple[str, ...],
    namespaces: tuple[str, ...],
    target_version: int | None,
    as_json: bool,
    show_ids: bool,
    show_references: bool,
) -> None:
    """Analyse an archive: structure, dependencies, health.

    ARCHIVE_PATH is the ZIP to read; SUBJECTS are templates (by value or id)
    to deep-dive. Reads archives only — analysing a running instance means
    taking a backup and analysing that.

    A deep dive is also the plan a future `filter` would execute: what it
    prints as the closure of a template is what filtering to that template
    would carry along.

    --target-version N with one TEMPLATE assesses migrating its active
    documents to version N, as a migrate dry run would: each is validated as
    a write against N (terms re-resolved, declared renames applied), with no
    instance needed. Reference and file fields are not checked.
    """
    if target_version is not None and len(subjects) != 1:
        raise click.UsageError("--target-version needs exactly one TEMPLATE.")
    try:
        with ArchiveReader(archive_path) as reader:
            model = ArchiveModel.load(
                reader, namespaces=list(namespaces) or None
            )

            selected_templates = []
            for name in subjects:
                try:
                    template = model.resolve_template(name)
                except AmbiguousNameError as e:
                    console.print(f"[red]{e}[/red]")
                    sys.exit(2)
                if template is None:
                    console.print(f"[red]No such template in archive:[/red] {name}")
                    sys.exit(2)
                selected_templates.append(template.template_id)

            selected_terminologies = []
            for name in terminologies:
                try:
                    node = model.resolve_terminology(name)
                except AmbiguousNameError as e:
                    console.print(f"[red]{e}[/red]")
                    sys.exit(2)
                if node is None:
                    console.print(
                        f"[red]No such terminology in archive:[/red] {name}"
                    )
                    sys.exit(2)
                selected_terminologies.append(node.terminology_id)

            migration = None
            if target_version is not None:
                from wip_archive.migratability import assess_migration

                migration = assess_migration(
                    reader, model.definitions, selected_templates[0], target_version,
                    namespaces=model.namespaces,
                )

            if as_json:
                click.echo(
                    _json.dumps(
                        json_payload(
                            model,
                            templates=selected_templates,
                            terminologies=selected_terminologies,
                            migratability=migration,
                        ),
                        indent=2,
                        default=str,
                    )
                )
                return

            if selected_templates or selected_terminologies:
                for template_id in selected_templates:
                    render_template_dive(model, template_id, console)
                if migration is not None:
                    render_migratability(migration, console)
                for terminology_id in selected_terminologies:
                    render_terminology_dive(model, terminology_id, console)
            else:
                render_summary(model, console)
                console.print(
                    f"\nArchive size: {reader.compressed_size():,} bytes "
                    f"compressed, {reader.total_size():,} bytes uncompressed"
                )

            # The original flags stay as shallow projections over the raw
            # entity rows — they answer "what is literally in the file",
            # which the analysis view deliberately abstracts away.
            if show_ids:
                _show_entity_ids(reader)
            if show_references:
                _show_references(reader)

    except FileNotFoundError:
        console.print(f"[red]Archive not found:[/red] {archive_path}")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error reading archive:[/red] {e}")
        sys.exit(1)


@main.command()
@click.argument("archive_path")
@click.option(
    "--namespace",
    "namespaces",
    multiple=True,
    help="Restrict the check to these namespaces (repeatable)",
)
@click.option("--strict", is_flag=True, help="Also exit 1 on warnings")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def verify(
    archive_path: str,
    namespaces: tuple[str, ...],
    strict: bool,
    as_json: bool,
) -> None:
    """Check an archive offline: is it consistent and restorable?

    Reads the archive alone. Beyond inspect's findings it checks every
    document against the template version it is pinned to, its stored term
    references and its identity hash. What the archive does not carry (a
    shared template or vocabulary) is listed as unverifiable, not judged.

    Verdict: fail (errors), warn (warnings only) or pass. Exits 1 on fail;
    with --strict, also on warn.
    """
    from .verify import json_payload, verdict, verify_archive

    try:
        model = verify_archive(archive_path, list(namespaces) or None)
    except FileNotFoundError:
        console.print(f"[red]Archive not found:[/red] {archive_path}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error reading archive:[/red] {e}")
        sys.exit(1)

    findings = model.findings()
    outcome = verdict(findings)
    if as_json:
        click.echo(_json.dumps(json_payload(model), indent=2, default=str))
    else:
        render_findings(model, console)
        check = model.document_check
        if check is not None:
            console.print(
                f"\n{check.checked:,} document row(s) checked, "
                f"{check.invalid_documents:,} invalid; "
                f"{check.identity_checked:,} identity hash(es) recomputed"
            )
        colour = {"pass": "green", "warn": "yellow", "fail": "red"}[outcome]
        console.print(f"[{colour}]verdict: {outcome}[/{colour}]")
    if outcome == "fail" or (strict and outcome == "warn"):
        sys.exit(1)


@main.command(name="filter")
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.argument("templates", nargs=-1)
@click.option(
    "--boundary",
    type=click.Choice(["strict", "drop", "stubs", "closure"]),
    default="drop",
    show_default=True,
    help="What happens to a reference from a kept document to one the selection leaves out",
)
@click.option("--no-relationships", is_flag=True,
              help="Leave behind the relationship documents of the selected documents")
@click.option("--definitions-only", is_flag=True,
              help="Templates, terminologies and terms only: no document or file")
@click.option("--yes", is_flag=True,
              help="With --boundary closure: proceed when it keeps more than half the documents")
@click.option("--dry-run", is_flag=True,
              help="Plan, copy and verify on scratch space; write nothing to OUTPUT_PATH")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def filter_cmd(
    archive_path: str,
    output_path: str,
    templates: tuple[str, ...],
    boundary: str,
    no_relationships: bool,
    definitions_only: bool,
    yes: bool,
    dry_run: bool,
    force: bool,
    as_json: bool,
) -> None:
    """Write a new archive holding only some templates' documents.

    TEMPLATES are template values, ids or ns:VALUE. Every version of their
    documents travels and, unless --no-relationships, every relationship
    document whose source is one of them. Definitions always close: the
    templates the kept documents pin, everything those declare, and their
    terminologies, whole.

    A reference from a kept document to a document left out is never
    rewritten. Under --boundary drop the target is recorded as a referent,
    which a restore resolves on the target install; strict refuses instead;
    stubs keeps the targets one hop out; closure keeps following them.

    The output is verified before it is written, and the verdict and the
    parameters are recorded in its derived_from chain. Exits 1 when the
    output's verdict is fail (it is still written) or nothing was written.
    """
    from wip_archive.filter import BoundaryViolation, FilterError, plan_filter
    from wip_archive.transform import OutputExistsError, run_transform

    from .transform_view import json_payload, render_result

    if Path(output_path).exists() and not force and not dry_run:
        console.print(f"[red]Output exists:[/red] {output_path} (pass --force to overwrite)")
        sys.exit(1)
    try:
        with ArchiveReader(archive_path) as reader:
            model = ArchiveModel.load(reader)
            plan = plan_filter(
                reader, model, list(templates), boundary=boundary,
                relationships=not no_relationships, definitions_only=definitions_only,
            )
    except BoundaryViolation as refused:
        console.print(f"[red]Refused (--boundary strict):[/red] {refused}")
        console.print("Nothing written. --boundary drop records those documents as referents.")
        sys.exit(1)
    except FilterError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if (boundary == "closure" and not yes and not dry_run
            and len(plan.documents) * 2 > plan.archive_documents):
        console.print(
            f"--boundary closure keeps {len(plan.documents):,} of the archive's "
            f"{plan.archive_documents:,} documents ({plan.added_documents:,} added by "
            "following references). Nothing written; pass --yes to proceed."
        )
        sys.exit(1)

    params = {
        "templates": list(templates),
        "boundary": boundary,
        "relationships": not no_relationships,
        "definitions_only": definitions_only,
    }
    try:
        result = run_transform(
            archive_path, output_path, transform="filter",
            toolkit_version=_get_version(), params=params,
            keep_row=plan.keep_row, summary=plan.summary(), overwrite=force, dry_run=dry_run,
        )
    except OutputExistsError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(json_payload(result), indent=2, default=str))
    else:
        if not definitions_only:
            console.print(
                f"{plan.selected_documents:,} document(s) selected, "
                f"{plan.relationship_documents:,} relationship document(s) with them, "
                f"{plan.added_documents:,} added by --boundary {boundary}; "
                f"{plan.cut_references:,} reference(s) cross to "
                f"{len(plan.cut_targets):,} document(s) left out"
            )
        render_result(result, console)
    if result.verdict == "fail":
        sys.exit(1)


@main.command()
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--dry-run", is_flag=True,
              help="Plan, copy and verify on scratch space; write nothing to OUTPUT_PATH")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def prune(
    archive_path: str, output_path: str, dry_run: bool, force: bool, as_json: bool
) -> None:
    """Write a new archive without the templates nothing uses.

    A template is used when documents are pinned to it, or when the schema
    of a template with documents needs it (parent, reference target,
    edge-type endpoint). The rest go, with every terminology that only they
    declared and nothing kept uses. A terminology no template declares
    stays: an app may use it through validation alone. Every document and
    file travels.

    The output is verified before it is written, and what was pruned is
    recorded in its derived_from chain. Exits 1 on a fail verdict.
    """
    from wip_archive.prune import plan_prune
    from wip_archive.transform import OutputExistsError, run_transform

    from .transform_view import json_payload, render_result

    if Path(output_path).exists() and not force and not dry_run:
        console.print(f"[red]Output exists:[/red] {output_path} (pass --force to overwrite)")
        sys.exit(1)
    with ArchiveReader(archive_path) as reader:
        plan = plan_prune(reader, ArchiveModel.load(reader))
    try:
        result = run_transform(
            archive_path, output_path, transform="prune",
            toolkit_version=_get_version(), params={},
            keep_row=plan.keep_row, summary=plan.summary(), overwrite=force, dry_run=dry_run,
        )
    except OutputExistsError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(json_payload(result), indent=2, default=str))
    else:
        if plan.template_names or plan.terminology_names:
            for label, names in (("template", plan.template_names),
                                 ("terminology", plan.terminology_names)):
                for name in names:
                    console.print(f"{'would prune' if dry_run else 'pruned'} {label} {name}")
        else:
            console.print("Nothing unused: every template and terminology travels.")
        render_result(result, console)
    if result.verdict == "fail":
        sys.exit(1)


@main.command(name="latest-only")
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--dry-run", is_flag=True,
              help="Plan, copy and verify on scratch space; write nothing to OUTPUT_PATH")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def latest_only_cmd(
    archive_path: str, output_path: str, dry_run: bool, force: bool, as_json: bool
) -> None:
    """Write a new archive with each document's newest version only.

    The newest version travels whatever its status, so a document retired
    in its newest version stays retired. A version a pinned reference names
    travels too: the platform guards such a reference. Every template
    version stays. A file only dropped versions referenced leaves with them.

    The output is verified before it is written, records
    include_all_versions false, and counts what it dropped in its
    derived_from chain. Exits 1 on a fail verdict.
    """
    from wip_archive.latest_only import plan_latest_only
    from wip_archive.transform import OutputExistsError, run_transform

    from .transform_view import json_payload, render_result

    if Path(output_path).exists() and not force and not dry_run:
        console.print(f"[red]Output exists:[/red] {output_path} (pass --force to overwrite)")
        sys.exit(1)
    with ArchiveReader(archive_path) as reader:
        plan = plan_latest_only(reader, ArchiveModel.load(reader))
    try:
        result = run_transform(
            archive_path, output_path, transform="latest-only",
            toolkit_version=_get_version(), params={},
            keep_row=plan.keep_row, summary=plan.summary(),
            manifest_updates={"include_all_versions": False}, overwrite=force, dry_run=dry_run,
        )
    except OutputExistsError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(json_payload(result), indent=2, default=str))
    else:
        console.print(
            f"{plan.versions_dropped:,} older version row(s) "
            f"{'would be dropped' if dry_run else 'dropped'}; "
            f"{plan.pinned_versions_kept:,} kept because a pinned reference names them"
        )
        render_result(result, console)
    if result.verdict == "fail":
        sys.exit(1)


@main.command(name="strip-blobs")
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--dry-run", is_flag=True,
              help="Plan, copy and verify on scratch space; write nothing to OUTPUT_PATH")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def strip_blobs_cmd(
    archive_path: str, output_path: str, dry_run: bool, force: bool, as_json: bool
) -> None:
    """Write a new archive without file content.

    File rows leave with their blobs: a file row without its bytes would
    restore as a file that lists but has no content. Each file a kept
    document references is recorded as a referent instead. Files are keyed
    by the checksum of their bytes, so a restore points the documents at an
    install's own copy of the same file, and reports the reference as
    unresolved where there is none. Everything else travels unchanged.

    The output is verified before it is written. Exits 1 on a fail verdict.
    """
    from wip_archive.strip_blobs import StripBlobsPlan
    from wip_archive.transform import OutputExistsError, run_transform

    from .transform_view import json_payload, render_result

    if Path(output_path).exists() and not force and not dry_run:
        console.print(f"[red]Output exists:[/red] {output_path} (pass --force to overwrite)")
        sys.exit(1)
    try:
        result = run_transform(
            archive_path, output_path, transform="strip-blobs",
            toolkit_version=_get_version(), params={},
            keep_row=StripBlobsPlan().keep_row, overwrite=force, dry_run=dry_run,
        )
    except OutputExistsError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(json_payload(result), indent=2, default=str))
    else:
        render_result(result, console)
    if result.verdict == "fail":
        sys.exit(1)


@main.command()
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_dir", type=click.Path(file_okay=False))
@click.option("--by", type=click.Choice(["namespace", "island"]), default="namespace",
              show_default=True, help="One archive per namespace, or per island")
@click.option("--dry-run", is_flag=True,
              help="Plan, copy and verify every part on scratch space; write nothing")
@click.option("--force", is_flag=True, help="Overwrite part archives that exist")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def split(
    archive_path: str, output_dir: str, by: str, dry_run: bool, force: bool, as_json: bool
) -> None:
    """Write an archive's parts as separate archives into OUTPUT_DIR.

    --by namespace (default) writes <ns>.zip per namespace. --by island
    writes island-<n>.zip per island: templates joined by references, with
    their documents, files and terminologies (a terminology several islands
    use is copied into each). Terminologies no island uses, documents
    pinned to a template the archive does not carry, and files no document
    references go into unattached.zip. A reference from one part into another becomes a
    referent. Every part is verified before it is written and records its
    own derived_from hop. Exits 1 if any part's verdict is fail, or when a
    part archive exists without --force (nothing is written then).
    """
    from wip_archive.split import SplitError, plan_split
    from wip_archive.transform import run_transform

    from .transform_view import TRANSFORM_JSON_SCHEMA, json_payload

    with ArchiveReader(archive_path) as reader:
        try:
            parts = plan_split(reader, ArchiveModel.load(reader), by)
        except SplitError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)
    out_dir = Path(output_dir)
    targets = [(part, out_dir / f"{part.name}.zip") for part in parts]
    existing = [str(t) for _p, t in targets if t.exists()]
    if existing and not force and not dry_run:
        console.print(f"[red]Part archive(s) exist:[/red] {', '.join(existing)} "
                      "(pass --force to overwrite). Nothing written.")
        sys.exit(1)

    results = []
    for part, target in targets:
        results.append((part, run_transform(
            archive_path, target, transform="split", toolkit_version=_get_version(),
            params={"by": by, "part": part.name}, keep_row=part.keep_row,
            overwrite=force, dry_run=dry_run,
        )))

    if as_json:
        click.echo(_json.dumps({
            "schema": TRANSFORM_JSON_SCHEMA,
            "by": by,
            "dry_run": dry_run,
            "parts": [{"part": p.name, "description": p.description, **json_payload(r)}
                      for p, r in results],
        }, indent=2, default=str))
    else:
        table = Table(title=f"split --by {by}" + (" (dry run, nothing written)" if dry_run else ""))
        for column in ("Part", "Holds", "Documents", "Referents", "Verdict"):
            table.add_column(column, justify="right" if column in ("Documents", "Referents")
                             else "left")
        for part, result in results:
            table.add_row(f"{part.name}.zip", part.description,
                          f"{result.kept.get('documents', 0):,}", f"{result.referents:,}",
                          result.verdict)
        console.print(table)
    if any(r.verdict == "fail" for _p, r in results):
        sys.exit(1)


@main.command(name="export-csv")
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_dir", type=click.Path(file_okay=False))
@click.option("--all-versions", is_flag=True,
              help="Every version row, not just each document's latest")
@click.option("--dry-run", is_flag=True, help="List the files and row counts; write nothing")
@click.option("--force", is_flag=True, help="Overwrite CSV files that exist")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def export_csv_cmd(
    archive_path: str, output_dir: str, all_versions: bool, dry_run: bool, force: bool,
    as_json: bool,
) -> None:
    """Write an archive's documents as one CSV per template.

    OUTPUT_DIR/<namespace>/<TEMPLATE>.csv, each document's latest version
    (--all-versions: every version). Columns: document_id, version, status,
    then the data fields flattened by dot path (arrays as JSON text), in
    the template's field order. A term field is its value plus a
    <path>__term_id column (a JSON list for an array). A data field named
    like one of these columns (status, version, document_id, …__term_id) is
    written as data:<name>. No instance needed. An existing CSV refuses the
    run without --force, before anything is written.
    """
    from wip_archive.export_csv import ExportCollisionError, ExportExistsError, export_csv

    with ArchiveReader(archive_path) as reader:
        try:
            files = export_csv(reader, ArchiveModel.load(reader), output_dir,
                               all_versions=all_versions, overwrite=force, dry_run=dry_run)
        except ExportExistsError as e:
            console.print(f"[red]{e}[/red] (pass --force to overwrite). Nothing written.")
            sys.exit(1)
        except ExportCollisionError as e:
            console.print(f"[red]{e}[/red]. Nothing written.")
            sys.exit(1)

    if as_json:
        click.echo(_json.dumps({
            "schema": "1.0",
            "dry_run": dry_run,
            "all_versions": all_versions,
            "files": [{"namespace": f.namespace, "template": f.template, "path": str(f.path),
                       "rows": f.rows, "columns": f.columns} for f in files],
        }, indent=2, default=str))
        return
    table = Table(title="export-csv" + (" (dry run, nothing written)" if dry_run else ""))
    table.add_column("File")
    table.add_column("Rows", justify="right")
    table.add_column("Columns", justify="right")
    for f in files:
        table.add_row(str(f.path), f"{f.rows:,}", f"{len(f.columns):,}")
    console.print(table)


@main.command()
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--mapping", "mapping_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Mapping file (.json, or .yaml/.yml): template, to_version, "
                   "optional from_versions and fill rules")
@click.option("--skip-failing", is_flag=True,
              help="Leave documents the target would refuse on their version, instead of refusing")
@click.option("--dry-run", is_flag=True,
              help="Plan, migrate and verify on scratch space; write nothing")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def migrate(
    archive_path: str, output_path: str, mapping_path: str, skip_failing: bool,
    dry_run: bool, force: bool, as_json: bool,
) -> None:
    """Re-pin a template's documents to another version, filling gaps.

    What live migrate_documents does, offline, plus fills for what the
    target needs: the template's active documents on another version are
    validated as writes against the target (renames applied, then the
    mapping's fills: a constant, {"copy": path}, {"format": "..."} or
    {"value": ...}; a fill never overwrites data). Each old version is
    retired and the next version written, pinned to the target, with the
    same document_id and identity hash; a versioned:false template is
    overwritten in place. Refused, with nothing written, when the target is
    missing or inactive, identity fields differ (a fork), or any document
    would fail (unless --skip-failing). The output is verified, an identity
    mismatch counting as an error, and the mapping recorded in its hop.
    """
    import yaml
    from wip_archive.migrate import MigrationRefused, plan_migration
    from wip_archive.transform import OutputExistsError, run_transform

    from .transform_view import json_payload, render_result

    source = Path(mapping_path)
    try:
        text = source.read_text(encoding="utf-8")
        is_yaml = source.suffix.lower() in (".yaml", ".yml")
        mapping = yaml.safe_load(text) if is_yaml else _json.loads(text)
    except (OSError, ValueError, yaml.YAMLError) as e:
        console.print(f"[red]Cannot read the mapping:[/red] {e}")
        sys.exit(1)
    if not isinstance(mapping, dict):
        console.print("[red]The mapping must be an object (a mapping of keys).[/red]")
        sys.exit(1)
    if Path(output_path).exists() and not force and not dry_run:
        console.print(f"[red]Output exists:[/red] {output_path} (pass --force to overwrite)")
        sys.exit(1)
    with ArchiveReader(archive_path) as reader:
        try:
            plan = plan_migration(reader, ArchiveModel.load(reader), mapping,
                                  skip_failing=skip_failing)
        except MigrationRefused as refused:
            console.print(f"[red]Refused:[/red] {refused}")
            for failure in refused.failures:
                console.print(f"  {failure}")
            sys.exit(1)
    try:
        result = run_transform(
            archive_path, output_path, transform="migrate", toolkit_version=_get_version(),
            params=mapping, keep_row=plan.keep_row, rewrite_row=plan.rewrite_row,
            strict_identity=True, summary=plan.summary(), overwrite=force, dry_run=dry_run,
        )
    except OutputExistsError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(json_payload(result), indent=2, default=str))
    else:
        verb = "would migrate" if dry_run else "migrated"
        console.print(f"{plan.template}: {verb} {len(plan.cohort):,} document(s) to "
                      f"v{plan.to_version}; {plan.skipped:,} left on their version")
        for fname, count in plan.filled.items():
            console.print(f"  filled {fname} in {count:,} document(s)")
        for failure in plan.failed:
            console.print(f"  [yellow]skipped[/yellow] {failure}")
        render_result(result, console)
    if result.verdict == "fail":
        sys.exit(1)


@main.command()
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--field", "fields", multiple=True, required=True,
              help="TEMPLATE=path[,path...]: field paths to clear (repeatable)")
@click.option("--dry-run", is_flag=True,
              help="Plan, redact and verify on scratch space; write nothing")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def redact(
    archive_path: str, output_path: str, fields: tuple[str, ...], dry_run: bool,
    force: bool, as_json: bool,
) -> None:
    """Clear named fields from every version of a template's documents.

    --field PERSON=email,contact.phone sets those values to null in every
    version row, whatever its status. A redacted term, reference or file
    field drops its snapshots; a file no remaining document references
    leaves, blob included. Identity fields are refused (their values are in
    the Registry, and changing identity is a fork), and so is a path the
    template never declared. Rows keep their timestamps and writers; the
    derived_from hop records params_redacted with the paths, never the
    values. The output is verified, an identity mismatch counting as an
    error.
    """
    from wip_archive.redact import RedactionRefused, plan_redaction
    from wip_archive.transform import OutputExistsError, run_transform

    from .transform_view import json_payload, render_result

    spec: dict[str, list[str]] = {}
    for entry in fields:
        template, sep, paths = entry.partition("=")
        names = [p.strip() for p in paths.split(",") if p.strip()]
        if not sep or not template or not names:
            raise click.UsageError(f"--field expects TEMPLATE=path[,path...], got '{entry}'.")
        spec.setdefault(template, []).extend(n for n in names if n not in spec.get(template, []))
    if Path(output_path).exists() and not force and not dry_run:
        console.print(f"[red]Output exists:[/red] {output_path} (pass --force to overwrite)")
        sys.exit(1)
    with ArchiveReader(archive_path) as reader:
        try:
            plan = plan_redaction(reader, ArchiveModel.load(reader), spec)
        except RedactionRefused as refused:
            console.print(f"[red]Refused:[/red] {refused}")
            sys.exit(1)
    try:
        result = run_transform(
            archive_path, output_path, transform="redact", toolkit_version=_get_version(),
            params=plan.params(), params_redacted=True, keep_row=plan.keep_row,
            rewrite_row=plan.rewrite_row, strict_identity=True, summary=plan.summary(),
            overwrite=force, dry_run=dry_run,
        )
    except OutputExistsError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(_json.dumps(json_payload(result), indent=2, default=str))
    else:
        verb = "would clear" if dry_run else "cleared"
        console.print(f"{verb} {plan.values_cleared:,} value(s) in {plan.rows_redacted:,} "
                      f"row(s); {len(plan.files_dropped):,} file(s) no longer referenced")
        render_result(result, console)
    if result.verdict == "fail":
        sys.exit(1)


@main.command()
@click.argument("archive_a", type=click.Path(exists=True, dir_okay=False))
@click.argument("archive_b", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def diff(archive_a: str, archive_b: str, as_json: bool) -> None:
    """Show what differs between two archives, row by row.

    Rows match by their identity per namespace and entity type (a template
    or document version by id and version, the rest by id). A matched row
    is changed when any field differs; for a sample of changes the fields
    are named. Referents, blobs and both derived_from chains are compared
    too. Exits 0 when identical, 1 when they differ, 2 on an unreadable
    archive.
    """
    from wip_archive.diff import diff_archives

    from .transform_view import DIFF_JSON_SCHEMA, render_diff

    try:
        with ArchiveReader(archive_a) as a, ArchiveReader(archive_b) as b:
            result = diff_archives(a, b)
    except Exception as e:
        console.print(f"[red]Error reading archive:[/red] {e}")
        sys.exit(2)
    if as_json:
        click.echo(_json.dumps({"schema": DIFF_JSON_SCHEMA, **result.to_dict()},
                               indent=2, default=str))
    else:
        render_diff(result, console)
    sys.exit(0 if result.identical else 1)


def _show_entity_ids(reader: ArchiveReader) -> None:
    """List all entity IDs in the archive, per namespace."""
    id_fields = {
        "terminologies": "terminology_id",
        "terms": "term_id",
        "templates": "template_id",
        "documents": "document_id",
        "files": "file_id",
    }

    # Always pass an explicit namespace: the reader's omitted-namespace
    # convenience raises on a multi-namespace archive. Explicit passes
    # straight through, so the single-namespace output is unchanged.
    namespaces = reader.list_namespaces()
    multi = len(namespaces) > 1

    for entity_type, id_field in id_fields.items():
        for ns in namespaces:
            entities = list(reader.read_entities(entity_type, namespace=ns))
            if not entities:
                continue

            title = f"{entity_type.title()} IDs"
            if multi:
                title += f" — {ns}"
            table = Table(title=title)
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
                   "Use '-' to read JSON from stdin; use '{}' for a "
                   "metadata-only update.")
@click.option("--metadata-patch", "metadata_patch_json", default=None,
              help="JSON Merge Patch (RFC 7396) applied to the document's "
                   "`metadata.custom`. Metadata versions like data; a "
                   "metadata-only change mints a new version.")
@click.option("--if-match", type=int, default=None,
              help="Optimistic concurrency: only apply if current version matches.")
@click.pass_context
def update_document_cmd(
    ctx: click.Context,
    document_id: str,
    patch_json: str,
    metadata_patch_json: str | None,
    if_match: int | None,
) -> None:
    """Apply an RFC 7396 JSON Merge Patch to a document.

    DOCUMENT_ID is the canonical document_id (e.g., 'DOC-xxx') or a registered
    synonym. The patch is applied to the document's `data` field:

      - Objects are deep-merged
      - Arrays are REPLACED entirely
      - `null` deletes the corresponding key

    --metadata-patch applies the same merge semantics to `metadata.custom`
    (platform-owned metadata like warnings/source_system cannot be addressed).

    Identity fields cannot be changed via PATCH (use create-document with new
    identity values instead). Archived or soft-deleted documents are rejected.

    Examples:

      wip-toolkit update-document DOC-123 --patch '{"score": 92}'

      wip-toolkit update-document DOC-123 --patch '{"middle_name": null}'

      wip-toolkit update-document DOC-123 --patch '{}' --metadata-patch '{"reviewed": true}'

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

    metadata_patch: dict | None = None
    if metadata_patch_json is not None:
        try:
            metadata_patch = _json.loads(metadata_patch_json)
        except _json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON in --metadata-patch:[/red] {e}")
            sys.exit(2)
        if not isinstance(metadata_patch, dict):
            console.print("[red]--metadata-patch must be a JSON object.[/red]")
            sys.exit(2)

    item: dict = {"document_id": document_id, "patch": patch}
    if metadata_patch is not None:
        item["metadata_patch"] = metadata_patch
    if if_match is not None:
        item["if_match"] = if_match

    config = ctx.obj["config"]
    with WIPClient(config) as client:
        resp = client.patch(
            "document-store",
            "/documents",
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


_MERGE_ONLY_OPTIONS = (
    "on_clash", "older_versions", "add_missing", "extend_terminologies", "align_templates",
    "carry_retirements",
)


@main.command()
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--mode", type=click.Choice(["restore", "merge", "fresh"]), default="restore",
              show_default=True,
              help="restore: into an empty namespace, ids kept. merge: into a namespace "
                   "that holds data, ids kept. fresh: into a new namespace, ids re-minted.")
@click.option("--namespace", default=None,
              help="Namespace the request is authorized against (default: the archive's).")
@click.option("--target-namespace", default=None,
              help="Where to write (required for a single-namespace fresh restore).")
@click.option("--map", "namespace_map", multiple=True, metavar="SOURCE=TARGET",
              help="Fresh only: map an archived namespace to a new one (repeatable).")
@click.option("--on-clash", type=click.Choice(["skip", "overwrite", "newer"]), default=None,
              help="Merge only: for a document the target already holds, keep the target's "
                   "(skip, default), take the archive's (overwrite), or take it only where "
                   "it was updated more recently (newer).")
@click.option("--older-versions", type=click.Choice(["retire", "keep"]), default=None,
              help="Merge only: when overwrite/newer puts the archive's version on top, "
                   "soft-delete the target's older versions (retire, default) or leave "
                   "them active (keep).")
@click.option("--add-missing", is_flag=True, default=None,
              help="Merge only: insert terminologies and templates the target lacks.")
@click.option("--extend-terminologies", is_flag=True, default=None,
              help="Merge only: add terms the target's terminologies lack.")
@click.option("--align-templates", is_flag=True, default=None,
              help="Merge only: for an archive from another install. Match templates "
                   "by value and map each archive version onto the target version "
                   "whose schema can take its documents; documents no version can "
                   "take are listed on the job and not merged. Dry-run first.")
@click.option("--carry-retirements", is_flag=True, default=None,
              help="Merge only: retire the target's active terms the archive retired. "
                   "Without it they stay active and the merge reports how many.")
@click.option("--skip-documents", is_flag=True, help="Definitions only.")
@click.option("--skip-files", is_flag=True, help="Do not restore file blobs.")
@click.option("--dry-run", is_flag=True, help="Report what would happen; write nothing.")
@click.option("--force", is_flag=True,
              help="Send an archive that is known to be broken anyway: one that fails "
                   "the offline verify run before uploading, or whose own verification "
                   "failed (verdict 'fail' in its derived_from chain), which the server "
                   "otherwise refuses. One flag passes both gates.")
@click.option("--allow-missing-identity", is_flag=True,
              help="Restore mode only: restore an archive that carries entities but no "
                   "registry identity rows (an export made without synonyms), which the "
                   "server otherwise refuses because nothing restored would resolve by id.")
@click.option("--no-wait", is_flag=True, help="Print the queued job and return.")
@click.option("--timeout", default=3600.0, show_default=True,
              help="Seconds to follow the job. A job not finished by then keeps "
                   "running on the server, and the command exits 1.")
@click.option("--json", "as_json", is_flag=True, help="Print the final job snapshot as JSON.")
@click.pass_context
def restore(
    ctx: click.Context,
    archive_path: str,
    mode: str,
    namespace: str | None,
    target_namespace: str | None,
    namespace_map: tuple[str, ...],
    on_clash: str | None,
    older_versions: str | None,
    add_missing: bool | None,
    extend_terminologies: bool | None,
    align_templates: bool | None,
    carry_retirements: bool | None,
    skip_documents: bool,
    skip_files: bool,
    dry_run: bool,
    force: bool,
    allow_missing_identity: bool,
    no_wait: bool,
    timeout: float,
    as_json: bool,
) -> None:
    """Restore or merge an archive through the server's restore engine.

    ARCHIVE_PATH is a backup ZIP. Before uploading, the archive is verified
    offline (as `wip-toolkit verify` does): an error refuses the upload
    unless --force, warnings are printed. The server then validates everything
    that enters; this command uploads the archive, starts the job and follows
    it. Exits 1 when the archive is refused or the job fails.

    Examples:

      wip-toolkit restore backup.zip

      wip-toolkit restore newer.zip --mode merge --on-clash newer \\
          --extend-terminologies --dry-run

      wip-toolkit restore newer.zip --mode merge --on-clash overwrite \\
          --older-versions keep

      wip-toolkit restore other-install.zip --mode merge --align-templates --dry-run
    """
    from pathlib import Path

    from .restore import build_form, start_restore, wait_for_job

    given = {
        "on_clash": on_clash, "older_versions": older_versions,
        "add_missing": add_missing, "extend_terminologies": extend_terminologies,
        "align_templates": align_templates, "carry_retirements": carry_retirements,
    }
    if mode != "merge":
        misplaced = [name for name in _MERGE_ONLY_OPTIONS if given[name]]
        if misplaced:
            flags = ", ".join("--" + n.replace("_", "-") for n in misplaced)
            raise click.UsageError(f"{flags} apply to --mode merge only.")
    if namespace_map and mode != "fresh":
        raise click.UsageError("--map applies to --mode fresh only.")
    if allow_missing_identity and mode != "restore":
        raise click.UsageError("--allow-missing-identity applies to --mode restore only.")

    parsed_map: dict[str, str] = {}
    for entry in namespace_map:
        source, sep, target = entry.partition("=")
        if not sep or not source or not target:
            raise click.UsageError(f"--map expects SOURCE=TARGET, got '{entry}'.")
        parsed_map[source] = target

    path = Path(archive_path)

    # The verify gate: catch an archive that would not restore intact before
    # it costs an upload and a failed job.
    from .verify import counts, verdict, verify_archive

    model = verify_archive(str(path))
    findings = model.findings()
    outcome = verdict(findings)
    if outcome != "pass" and not as_json:
        tally = counts(findings)
        console.print(f"verify: {tally['error']} error(s), {tally['warning']} warning(s)")
        for finding in findings:
            if finding.severity in ("error", "warning"):
                colour = "red" if finding.severity == "error" else "yellow"
                console.print(
                    f"  [{colour}]{finding.severity}[/{colour}]  "
                    f"{finding.finding_class}  {finding.subject} ({finding.count})"
                )
    if outcome == "fail":
        if not force:
            if as_json:
                click.echo(_json.dumps(
                    {"uploaded": False, "verdict": outcome,
                     "findings": [f.to_dict() for f in findings]},
                    indent=2, default=str,
                ))
            else:
                console.print(
                    "[red]✗ not uploaded — fix the archive, or pass --force[/red]"
                )
            sys.exit(1)
        if not as_json:
            console.print("[yellow]--force: uploading despite the errors above[/yellow]")

    if namespace is None:
        namespace = model.manifest.namespace

    form = build_form(
        mode,
        target_namespace=target_namespace,
        namespace_map=parsed_map or None,
        on_clash=on_clash or "skip",
        older_versions=older_versions or "retire",
        add_missing=bool(add_missing),
        extend_terminologies=bool(extend_terminologies),
        align_templates=bool(align_templates),
        carry_retirements=bool(carry_retirements),
        skip_documents=skip_documents,
        skip_files=skip_files,
        dry_run=dry_run,
        allow_failed_verify=force,
        allow_missing_identity=allow_missing_identity,
    )

    config = ctx.obj["config"]
    with WIPClient(config) as client:
        try:
            snapshot = start_restore(client, path, namespace, form)
        except WIPClientError as e:
            console.print(f"[red]Restore refused:[/red] {e}")
            if e.response_body:
                console.print(_json.dumps(e.response_body, indent=2, default=str))
            sys.exit(1)
        if not no_wait:
            last_message: list[str | None] = [None]

            def _progress(snap: dict) -> None:
                message = snap.get("message")
                if message and message != last_message[0] and not as_json:
                    console.print(f"[dim]{message}[/dim]")
                last_message[0] = message

            snapshot = wait_for_job(
                client, snapshot["job_id"], timeout=timeout, on_progress=_progress
            )

    if as_json:
        click.echo(_json.dumps(snapshot, indent=2, default=str))
    else:
        status = snapshot.get("status")
        colour = {"complete": "green", "failed": "red"}.get(status or "", "yellow")
        console.print(f"[{colour}]{snapshot.get('job_id')}: {status}[/{colour}]")
        if snapshot.get("error"):
            console.print(f"[red]{snapshot['error']}[/red]")
        for warning in snapshot.get("warnings") or []:
            console.print(f"[yellow]warning:[/yellow] {warning}")
        if snapshot.get("result"):
            console.print(_json.dumps(snapshot["result"], indent=2, default=str))
    status = snapshot.get("status")
    if status == "failed":
        sys.exit(1)
    if not no_wait and status != "complete":
        # The follow ended at --timeout with the job still going: success
        # is only a complete job, as for backup.
        message = (f"{snapshot.get('job_id')}: not finished within --timeout; the job "
                   "keeps running on the server.")
        if as_json:
            click.echo(message, err=True)
        else:
            console.print(f"[red]{message}[/red]")
        sys.exit(1)


@main.command()
@click.argument("namespace")
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--also", "also", multiple=True,
              help="Back up this namespace too, into the same archive (repeatable)")
@click.option("--all-namespaces", is_flag=True, help="Back up every namespace")
@click.option("--include-files", is_flag=True, help="Include file blobs in the archive")
@click.option("--skip-documents", is_flag=True, help="Definitions only")
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--timeout", default=3600.0, show_default=True,
              help="Seconds to wait for the job before giving up (it keeps running)")
@click.option("--json", "as_json", is_flag=True, help="Print the final job snapshot as JSON")
@click.pass_context
def backup(
    ctx: click.Context,
    namespace: str,
    output_path: str,
    also: tuple[str, ...],
    all_namespaces: bool,
    include_files: bool,
    skip_documents: bool,
    force: bool,
    timeout: float,
    as_json: bool,
) -> None:
    """Back up a namespace with the server's backup engine and download it.

    The engine reads the databases directly and writes a complete,
    restorable archive: every entity in every status and version, with the
    Registry rows and the referents. This command starts the job, follows
    it and saves the archive to OUTPUT_PATH. Shape it afterwards with
    filter, prune, latest-only or strip-blobs. Needs admin on every
    namespace backed up. Exits 1 when the job fails or does not finish
    within --timeout.

    Examples:

      wip-toolkit backup library library.zip

      wip-toolkit backup library both.zip --also kb --include-files
    """
    from .backup import build_request, download_archive, start_backup
    from .restore import wait_for_job

    output = Path(output_path)
    if output.exists() and not force:
        console.print(f"[red]Output exists:[/red] {output} (pass --force to overwrite)")
        sys.exit(1)
    body = build_request(namespaces=list(also) or None, all_namespaces=all_namespaces,
                         include_files=include_files, skip_documents=skip_documents)

    config = ctx.obj["config"]
    with WIPClient(config) as client:
        try:
            snapshot = start_backup(client, namespace, body)
        except WIPClientError as e:
            console.print(f"[red]Backup refused:[/red] {e}")
            sys.exit(1)
        last_message: list[str | None] = [None]

        def _progress(snap: dict) -> None:
            message = snap.get("message")
            if message and message != last_message[0] and not as_json:
                console.print(f"[dim]{message}[/dim]")
            last_message[0] = message

        snapshot = wait_for_job(client, snapshot["job_id"], timeout=timeout,
                                on_progress=_progress)
        status = snapshot.get("status")
        if status != "complete":
            if as_json:
                click.echo(_json.dumps(snapshot, indent=2, default=str))
            else:
                console.print(f"[red]{snapshot.get('job_id')}: {status}[/red]")
                if snapshot.get("error"):
                    console.print(f"[red]{snapshot['error']}[/red]")
                if status not in ("failed",):
                    console.print("Not finished within --timeout; the job keeps running "
                                  "on the server.")
            sys.exit(1)
        size = download_archive(client, snapshot["job_id"], output)

    if as_json:
        click.echo(_json.dumps({**snapshot, "output": str(output), "bytes": size},
                               indent=2, default=str))
    else:
        console.print(f"[green]{snapshot['job_id']}: complete[/green] → {output} "
                      f"({size:,} bytes)")


@main.command(name="system-backup")
@click.argument("output_path", type=click.Path(dir_okay=False))
@click.option("--force", is_flag=True, help="Overwrite OUTPUT_PATH if it exists")
@click.option("--namespace", "namespaces", multiple=True,
              help="Scope the backup to this namespace (repeatable)")
@click.option("--attachment", "attachments", multiple=True,
              help="Carry this attachment in a scoped backup (repeatable)")
@click.pass_context
def system_backup_cmd(
    ctx: click.Context, output_path: str, force: bool,
    namespaces: tuple[str, ...], attachments: tuple[str, ...],
) -> None:
    """Back up the Registry's API keys, grants, reservations and attachments.

    The system backup is separate from namespace backups: it holds every
    stored API key (its bcrypt hash, never a plaintext), every namespace
    grant and every reservation, and no namespace data. Config keys, the
    master key among them, are not included. Needs a platform admin key.

    The file is written with mode 0600. Keep it like the install's secrets:
    it holds every key hash.

    With --namespace (and --attachment) the backup is scoped and declares its
    scope: the grants and reservations of those namespaces, the keys whose
    namespaces all lie in them, and the named attachments. A mirror restore of
    it removes only within that scope. Keys the scope touches but cannot carry
    are listed.

    Examples:

      wip-toolkit system-backup auth-state.json

      wip-toolkit system-backup aa.json --namespace aa --namespace aa-data --attachment aa
    """
    import os

    output = Path(output_path)
    if output.exists() and not force:
        console.print(f"[red]Output exists:[/red] {output} (pass --force to overwrite)")
        sys.exit(1)
    config = ctx.obj["config"]
    with WIPClient(config) as client:
        try:
            if namespaces or attachments:
                params = {}
                if namespaces:
                    params["namespaces"] = ",".join(namespaces)
                if attachments:
                    params["attachments"] = ",".join(attachments)
                archive = client.post("registry", "/system-backup", params=params)
            else:
                archive = client.post("registry", "/system-backup")
        except WIPClientError as e:
            console.print(f"[red]System backup refused:[/red] {e}")
            sys.exit(1)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        _json.dump(archive, fh, indent=2)
    os.chmod(output, 0o600)
    console.print(
        f"[green]System backup[/green] → {output}: "
        f"{len(archive.get('api_keys', []))} key(s), {len(archive.get('grants', []))} grant(s), "
        f"{len(archive.get('reservations', []))} reservation(s), "
        f"{len(archive.get('attachments', []))} attachment(s)"
    )
    scope = (archive.get("source") or {}).get("scope")
    if scope:
        console.print(f"Scope: namespaces {', '.join(scope.get('namespaces', [])) or '(none)'}; "
                      f"attachments {', '.join(scope.get('attachments', [])) or '(none)'}")
        left_out = scope.get("keys_left_out", [])
        if left_out:
            console.print(f"[yellow]Keys not carried[/yellow] (they hold a namespace outside the "
                          f"scope, or none; a scoped mirror leaves them alone): {', '.join(left_out)}")


@main.command(name="system-restore")
@click.argument("archive_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--apply", "apply_", is_flag=True,
              help="Write the changes (without it, only report what would change)")
@click.option("--overwrite", is_flag=True,
              help="Replace records that exist here with different content (= --mode overwrite)")
@click.option("--mode", type=click.Choice(["upsert", "overwrite", "mirror"]), default=None,
              help="upsert (default), overwrite, or mirror: also remove what the archive "
                   "does not hold")
@click.option("--expect", default=None, metavar="DIGEST",
              help="With --mode mirror --apply: the plan digest the reviewed dry run "
                   "printed; refused if the plan changed since")
@click.option("--include-user-grants", is_flag=True,
              help="Restore user grants although the archive comes from another OIDC issuer")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON")
@click.pass_context
def system_restore_cmd(
    ctx: click.Context,
    archive_path: str,
    apply_: bool,
    overwrite: bool,
    mode: str | None,
    expect: str | None,
    include_user_grants: bool,
    as_json: bool,
) -> None:
    """Restore a system backup: API keys, grants, reservations and attachments.

    Reports what would change unless --apply is given. Records are upserted
    by identity and never deleted; restored records arrive unmanaged. Records
    the install's files manage here are never replaced. A grant on a
    namespace that does not exist (and is not reserved by the archive's
    owner) is written orphaned: it counts as no grant until a platform admin
    creates the namespace (restoring
    the namespace backup as an admin does) or a namespace admin activates it,
    so this and the namespace restores work in either order. An archive made
    under another API key hash salt is adopted with its salt by a stack that
    holds no keys of its own, and refused by one that does. Needs a platform
    admin key.

    --mode mirror also removes every record the archive does not hold (keys
    with their grants, grants, reservations; attachments are deactivated),
    never the install's managed records, the bootstrap grants, or the key
    this command runs with. Review the dry run, then apply with the plan
    digest it prints.

    Examples:

      wip-toolkit system-restore auth-state.json

      wip-toolkit system-restore auth-state.json --apply

      wip-toolkit system-restore auth-state.json --mode mirror

      wip-toolkit system-restore auth-state.json --mode mirror --apply --expect sha256:...
    """
    try:
        archive = _json.loads(Path(archive_path).read_text())
    except (OSError, ValueError) as e:
        console.print(f"[red]Cannot read {archive_path}:[/red] {e}")
        sys.exit(1)
    params = {
        "dry_run": str(not apply_).lower(),
        "overwrite": str(overwrite).lower(),
        "include_user_grants": str(include_user_grants).lower(),
    }
    if mode is not None:
        params["mode"] = mode
    if expect is not None:
        params["expect"] = expect
    config = ctx.obj["config"]
    with WIPClient(config) as client:
        try:
            report = client.post("registry", "/system-restore", json=archive, params=params)
        except WIPClientError as e:
            console.print(f"[red]System restore refused:[/red] {e}")
            sys.exit(1)
    if as_json:
        click.echo(_json.dumps(report, indent=2))
        return
    for warning in report.get("warnings", []):
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    for refused in report.get("refused", []):
        console.print(f"[red]Refused:[/red] {refused['id']}: the restore would {refused['reason']}")
    for part, label in (("reservations", "Reservations"), ("api_keys", "API keys"),
                        ("grants", "Grants"), ("attachments", "Attachments")):
        section = report.get(part, {})
        console.print(
            f"[bold]{label}[/bold]: {len(section.get('created', []))} created, "
            f"{len(section.get('replaced', []))} replaced, "
            f"{len(section.get('unchanged', []))} unchanged, "
            f"{len(section.get('skipped', []))} skipped"
        )
        orphaned = set(section.get("orphaned", []))
        unresolved = set(section.get("unresolved", []))
        for verb in ("created", "replaced"):
            for record_id in section.get(verb, []):
                note = " (orphaned until its namespace exists)" if record_id in orphaned else ""
                if record_id in unresolved:
                    note = " (upstream does not resolve from the Registry yet)"
                console.print(f"  {verb}: {record_id}{note}")
        for skipped in section.get("skipped", []):
            console.print(f"  skipped: {skipped['id']} ({skipped['reason']})")
        for verb in ("deleted", "deactivated"):
            for record_id in section.get(verb, []):
                console.print(f"  [red]{verb}[/red]: {record_id}")
    if report.get("plan_digest") and report.get("dry_run", True):
        console.print(
            f"Mirror plan digest: {report['plan_digest']} "
            "(pass it with --expect when you apply)"
        )
    if report.get("dry_run", True):
        console.print("[dim]Dry run: nothing was written. Pass --apply to restore.[/dim]")


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
    """Show dependency graph for templates, per namespace."""
    # Explicit namespace per iteration — the omitted-namespace convenience
    # raises on a multi-namespace archive (same reasoning as _show_entity_ids).
    namespaces = reader.list_namespaces()
    multi = len(namespaces) > 1
    templates: list[dict] = []
    for ns in namespaces:
        for tpl in reader.read_entities("templates", namespace=ns):
            if multi:
                tpl = {**tpl, "_ns": ns}
            templates.append(tpl)
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
        # Plain-text namespace prefix — square brackets would be swallowed
        # by rich as a markup tag.
        ns_prefix = f"{tpl['_ns']} :: " if "_ns" in tpl else ""
        console.print(f"  {ns_prefix}{tid} v{version} ({value}) → {dep_str}")
