"""Export orchestrator — coordinates collection, closure, and archive writing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Console

from ..archive import ArchiveWriter
from ..client import WIPClient
from ..models import ClosureInfo, EntityCounts, ExportStats, Manifest, NamespaceConfig
from .closure import compute_closure
from .collector import EntityCollector

console = Console(stderr=True)


def run_export(
    client: WIPClient,
    namespace: str,
    output_path: str | Path,
    *,
    include_files: bool = False,
    include_inactive: bool = False,
    skip_documents: bool = False,
    skip_closure: bool = False,
    dry_run: bool = False,
) -> ExportStats:
    """Run a full namespace export.

    Returns ExportStats with counts and timing.
    """
    start = time.monotonic()
    collector = EntityCollector(client, namespace, include_inactive=include_inactive)

    # Fetch namespace config
    console.print(f"\n[bold]Exporting namespace: {namespace}[/bold]")
    ns_config_data = collector.fetch_namespace_config(namespace)
    ns_config = None
    if ns_config_data:
        ns_config = NamespaceConfig(
            prefix=ns_config_data.get("prefix", namespace),
            description=ns_config_data.get("description", ""),
            isolation_mode=ns_config_data.get("isolation_mode", "open"),
            id_config=ns_config_data.get("id_config"),
        )

    # Phase 1: Collect primary entities
    console.print("\n[bold cyan]Phase 1:[/bold cyan] Collecting primary entities")

    terminologies = collector.fetch_terminologies()
    terms = collector.fetch_all_terms(terminologies)

    templates = collector.fetch_templates()
    # Fetch raw (unresolved) versions of each template
    templates = _fetch_raw_templates(collector, templates)

    documents: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    if not skip_documents:
        documents = collector.fetch_documents()
        files = collector.fetch_files()

    # Tag primary entities
    for entity in terminologies:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in terms:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in templates:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in documents:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in files:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)

    # Phase 2: Referential integrity closure
    closure_info = ClosureInfo()
    if not skip_closure:
        console.print("\n[bold cyan]Phase 2:[/bold cyan] Computing referential integrity closure")
        extra_terms_list, extra_terms_items, extra_templates, warnings = compute_closure(
            client, namespace, terminologies, terms, templates, documents,
        )

        if extra_terms_list or extra_templates:
            terminologies.extend(extra_terms_list)
            terms.extend(extra_terms_items)
            templates.extend(extra_templates)

        closure_info = ClosureInfo(
            external_terminologies=[t["terminology_id"] for t in extra_terms_list],
            external_templates=[t["template_id"] for t in extra_templates],
            iterations=0,  # Set by closure
            warnings=warnings,
        )

        for w in warnings:
            console.print(f"  [yellow]Warning:[/yellow] {w}")
    else:
        console.print("\n[dim]Skipping closure (--skip-closure)[/dim]")

    # Phase 3: Write archive
    counts = EntityCounts(
        terminologies=len(terminologies),
        terms=len(terms),
        templates=len(templates),
        documents=len(documents),
        files=len(files),
    )

    if dry_run:
        console.print("\n[bold yellow]Dry run[/bold yellow] — would export:")
        _print_counts(counts)
        return _build_stats(namespace, counts, closure_info, start)

    console.print("\n[bold cyan]Phase 3:[/bold cyan] Writing archive")
    writer = ArchiveWriter(output_path)

    for entity in terminologies:
        writer.add_entity("terminologies", entity)
    for entity in terms:
        writer.add_entity("terms", entity)
    for entity in templates:
        writer.add_entity("templates", entity)
    for entity in documents:
        writer.add_entity("documents", entity)
    for entity in files:
        writer.add_entity("files", entity)

    # Optionally download and include file blobs
    if include_files and files:
        console.print(f"  Downloading {len(files)} file(s)...")
        for file_meta in files:
            fid = file_meta["file_id"]
            try:
                content = collector.fetch_file_content(fid)
                writer.add_blob(fid, content)
            except Exception as e:
                console.print(f"  [yellow]Warning: Could not download {fid}: {e}[/yellow]")

    manifest = Manifest(
        source_host=client.config.host,
        namespace=namespace,
        namespace_config=ns_config,
        include_inactive=include_inactive,
        include_files=include_files,
        closure=closure_info,
        counts=counts,
    )

    archive_path = writer.write(manifest)
    console.print(f"\n  Archive written to: [green]{archive_path}[/green]")
    _print_counts(counts)

    return _build_stats(namespace, counts, closure_info, start)


def _fetch_raw_templates(
    collector: EntityCollector,
    templates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace resolved templates with raw (unresolved) versions."""
    raw_templates = []
    for tpl in templates:
        tpl_id = tpl["template_id"]
        version = tpl.get("version", 1)
        try:
            raw = collector.fetch_template_raw(tpl_id, version)
            raw_templates.append(raw)
        except Exception:
            # Fall back to the resolved version if raw not available
            raw_templates.append(tpl)
    return raw_templates


def _print_counts(counts: EntityCounts) -> None:
    console.print(f"  Terminologies: {counts.terminologies}")
    console.print(f"  Terms:         {counts.terms}")
    console.print(f"  Templates:     {counts.templates}")
    console.print(f"  Documents:     {counts.documents}")
    console.print(f"  Files:         {counts.files}")
    console.print(f"  [bold]Total:       {counts.total}[/bold]")


def _build_stats(
    namespace: str,
    counts: EntityCounts,
    closure_info: ClosureInfo,
    start: float,
) -> ExportStats:
    return ExportStats(
        namespace=namespace,
        counts=counts,
        closure_iterations=closure_info.iterations,
        external_terminologies=len(closure_info.external_terminologies),
        external_templates=len(closure_info.external_templates),
        warnings=closure_info.warnings,
        duration_seconds=round(time.monotonic() - start, 2),
    )
