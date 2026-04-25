"""Export orchestrator — coordinates collection, closure, and archive writing.

Streaming architecture:
- Phase 1a: Fetch small entities (terminologies, terms, templates) — fits in memory
- Phase 1b: Stream documents page by page (cursor-based, 1000/page)
- Phase 1c: Stream files metadata
- Phase 2: (unless --skip-synonyms) Batch-fetch Registry synonyms
- Phase 3: Write manifest + finalize ZIP

Memory usage: O(page_size) regardless of dataset size.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.console import Console

from .._progress import ProgressCallback
from .._progress import emit as _emit
from ..archive import ENTITY_FILES, ArchiveWriter
from ..client import WIPClient
from ..models import (
    ClosureInfo,
    EntityCounts,
    ExportStats,
    Manifest,
    NamespaceConfig,
    ProgressEvent,
)
from .closure import compute_closure
from .collector import EntityCollector

console = Console(stderr=True)

__all__ = ["ProgressCallback", "run_export"]


def run_export(
    client: WIPClient,
    namespace: str,
    output_path: str | Path,
    *,
    include_files: bool = False,
    include_inactive: bool = False,
    skip_documents: bool = False,
    skip_closure: bool = False,
    skip_synonyms: bool = False,
    latest_only: bool = False,
    template_prefixes: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    non_interactive: bool = False,
    tmp_dir: str | Path | None = None,
) -> ExportStats:
    """Run a full namespace export.

    Args:
        progress_callback: Optional observer invoked at phase boundaries with
            a :class:`ProgressEvent`. Designed for callers that need to surface
            progress without parsing console output (e.g. a REST endpoint
            streaming SSE). Exceptions raised inside the callback are swallowed.
        non_interactive: If True, never prompt the user. When file blobs are
            present in the namespace but ``include_files=False``, the export
            proceeds without blobs and emits a warning instead of asking. Use
            this when calling from a server context with no controlling TTY.
        tmp_dir: Override the directory used for scratch files (JSONL temp
            files and per-blob tempfiles). Defaults to the system temp dir.
            Server callers should pass the same value as their final-archive
            destination so all backup-related disk usage lives under one
            operator-controlled volume (CASE-29).

    Returns:
        ExportStats with counts and timing.
    """
    start = time.monotonic()
    collector = EntityCollector(client, namespace, include_inactive=include_inactive)
    _emit(progress_callback, ProgressEvent(
        phase="start",
        message=f"Exporting namespace: {namespace}",
        percent=0.0,
        details={"namespace": namespace, "include_files": include_files,
                 "latest_only": latest_only, "dry_run": dry_run},
    ))

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

    # Phase 1a: Collect small entities (fit in memory)
    console.print("\n[bold cyan]Phase 1a:[/bold cyan] Collecting terminologies, terms, templates")
    _emit(progress_callback, ProgressEvent(
        phase="phase_1a_entities",
        message="Collecting terminologies, terms, and templates",
        percent=5.0,
    ))

    terminologies = collector.fetch_terminologies()
    terms = collector.fetch_all_terms(terminologies)

    templates = collector.fetch_templates()
    # Fetch raw (unresolved) versions of each template
    templates = _fetch_raw_templates(collector, templates)

    # Apply template prefix filter
    if template_prefixes:
        before = len(templates)
        templates = [
            t for t in templates
            if any(t.get("value", "").startswith(prefix) for prefix in template_prefixes)
        ]
        console.print(
            f"  Filtered templates by prefix {template_prefixes}: "
            f"{before} → {len(templates)}"
        )
        # Also filter terminologies to only those referenced by remaining templates
        referenced_term_ids = _collect_terminology_refs(templates)
        before_terms = len(terminologies)
        terminologies = [t for t in terminologies if t["terminology_id"] in referenced_term_ids]
        terms = [t for t in terms if t.get("terminology_id") in referenced_term_ids]
        console.print(
            f"  Filtered terminologies to referenced: "
            f"{before_terms} → {len(terminologies)}"
        )

    # Fetch term-relations for all terminologies
    term_relations = collector.fetch_all_term_relations(terminologies)

    # Tag primary entities
    for entity in terminologies:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in terms:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in term_relations:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)
    for entity in templates:
        entity.setdefault("_source", "primary")
        entity.setdefault("_namespace", namespace)

    # Build template_id set for document filtering
    filtered_template_ids = {t["template_id"] for t in templates} if template_prefixes else None

    # Referential integrity closure (before documents)
    closure_info = ClosureInfo()
    if not skip_closure:
        console.print("\n[bold cyan]Closure:[/bold cyan] Computing referential integrity")
        _emit(progress_callback, ProgressEvent(
            phase="phase_closure",
            message="Computing referential integrity closure",
            percent=15.0,
        ))
        # For closure computation, we need documents in memory.
        # If skip_documents, pass empty list.
        closure_docs: list[dict[str, Any]] = []
        if not skip_documents:
            # Fetch a lightweight set for closure analysis
            closure_docs = collector.fetch_documents(
                latest_only=latest_only, template_ids=filtered_template_ids,
            )
        known_document_ids = {d["document_id"] for d in closure_docs} if closure_docs else None
        extra_terms_list, extra_terms_items, extra_templates, warnings = compute_closure(
            client, namespace, terminologies, terms, templates, closure_docs,
            known_document_ids=known_document_ids,
        )

        if extra_terms_list or extra_templates:
            terminologies.extend(extra_terms_list)
            terms.extend(extra_terms_items)
            templates.extend(extra_templates)

        closure_info = ClosureInfo(
            external_terminologies=[t["terminology_id"] for t in extra_terms_list],
            external_templates=[t["template_id"] for t in extra_templates],
            iterations=0,
            warnings=warnings,
        )

        for w in warnings:
            console.print(f"  [yellow]Warning:[/yellow] {w}")
    else:
        console.print("\n[dim]Skipping closure (--skip-closure)[/dim]")

    # Phase 1b: Count documents for dry run or prepare streaming
    doc_count = 0
    file_count = 0

    if dry_run:
        if not skip_documents:
            if filtered_template_ids is not None:
                # With template filter, we already fetched closure_docs — use that count
                doc_count = len(closure_docs)
                file_count = 0  # Can't cheaply count filtered files
            else:
                # Quick count via one page with page_size=1 to get total
                try:
                    data = client.get(
                        "document-store", "/documents",
                        params={"namespace": namespace, "page_size": 1},
                    )
                    doc_count = data.get("total", 0)
                except Exception:
                    doc_count = 0
            try:
                data = client.get(
                    "document-store", "/files",
                    params={"namespace": namespace, "page_size": 1},
                )
                file_count = data.get("total", 0)
            except Exception:
                file_count = 0

        counts = EntityCounts(
            terminologies=len(terminologies),
            terms=len(terms),
            term_relations=len(term_relations),
            templates=len(templates),
            documents=doc_count,
            files=file_count,
        )
        console.print("\n[bold yellow]Dry run[/bold yellow] — would export:")
        _print_counts(counts)
        return _build_stats(namespace, counts, closure_info, start)

    # Phase 1a: Write small entities to archive
    console.print("\n[bold cyan]Phase 1:[/bold cyan] Writing entities to archive")
    writer = ArchiveWriter(output_path, tmp_dir=tmp_dir)

    for entity in terminologies:
        writer.add_entity("terminologies", entity)
    for entity in terms:
        writer.add_entity("terms", entity)
    for entity in term_relations:
        writer.add_entity("term_relations", entity)
    for entity in templates:
        writer.add_entity("templates", entity)

    # Phase 1b: Stream documents
    if not skip_documents:
        console.print("\n[bold cyan]Phase 1b:[/bold cyan] Streaming documents")
        _emit(progress_callback, ProgressEvent(
            phase="phase_1b_documents",
            message="Streaming documents to archive",
            percent=30.0,
        ))
        for page in collector.stream_documents(latest_only=latest_only, page_size=1000):
            for doc in page:
                if filtered_template_ids is not None and doc.get("template_id") not in filtered_template_ids:
                    continue
                doc.setdefault("_source", "primary")
                doc.setdefault("_namespace", namespace)
                writer.add_entity("documents", doc)
        doc_count = writer.entity_count("documents")

        # Phase 1c: Files metadata
        files = collector.fetch_files()
        for entity in files:
            entity.setdefault("_source", "primary")
            entity.setdefault("_namespace", namespace)
            writer.add_entity("files", entity)
        file_count = len(files)

        # Warn if files exist but --include-files not set
        if files and not include_files:
            warning_msg = (
                f"{len(files)} file(s) found in namespace but include_files=False. "
                "Documents referencing these files may fail during import."
            )
            console.print(f"\n  [yellow]Warning:[/yellow] {warning_msg}")
            _emit(progress_callback, ProgressEvent(
                phase="warning_files_skipped",
                message=warning_msg,
                details={"file_count": len(files)},
            ))
            # Only prompt when running interactively from a TTY. In server /
            # scripted contexts (non_interactive=True or no TTY), proceed.
            if not non_interactive and sys.stdin.isatty() and not click.confirm(
                "  Continue without file blobs?", default=True,
            ):
                raise SystemExit("Export cancelled by user")

        # Optionally download and include file blobs
        if include_files and files:
            console.print(f"  Downloading {len(files)} file(s)...")
            _emit(progress_callback, ProgressEvent(
                phase="phase_1c_files",
                message=f"Downloading {len(files)} file blob(s)",
                percent=55.0,
                total=len(files),
            ))
            for idx, file_meta in enumerate(files, start=1):
                fid = file_meta["file_id"]
                try:
                    with writer.open_blob(fid) as dest:
                        collector.download_file_content(fid, dest)
                except Exception as e:
                    console.print(f"  [yellow]Warning: Could not download {fid}: {e}[/yellow]")
                # Emit per-file progress only at every 10th file (or last) to
                # avoid flooding the callback for large file-stores.
                if idx == len(files) or idx % 10 == 0:
                    _emit(progress_callback, ProgressEvent(
                        phase="phase_1c_files",
                        message=f"Downloaded {idx}/{len(files)} file(s)",
                        current=idx,
                        total=len(files),
                    ))
    else:
        console.print("\n[dim]Skipping documents (--skip-documents)[/dim]")
        files = []

    # Phase 2: Registry synonyms (unless --skip-synonyms)
    if not skip_synonyms:
        console.print("\n[bold cyan]Phase 2:[/bold cyan] Fetching Registry synonyms")
        _emit(progress_callback, ProgressEvent(
            phase="phase_2_synonyms",
            message="Fetching Registry synonyms",
            percent=80.0,
        ))
        synonyms = _fetch_synonyms(collector, terminologies, terms, templates,
                                   writer, namespace)
        if synonyms:
            writer.write_synonyms_file(synonyms)
            console.print(f"  Wrote {len(synonyms)} synonym(s) to archive")
        else:
            console.print("  No custom synonyms found")
    else:
        console.print("\n[dim]Skipping synonyms (--skip-synonyms)[/dim]")

    # Phase 3: Write manifest + finalize ZIP
    counts = EntityCounts(
        terminologies=len(terminologies),
        terms=len(terms),
        term_relations=len(term_relations),
        templates=len(templates),
        documents=doc_count,
        files=file_count,
    )

    console.print("\n[bold cyan]Phase 3:[/bold cyan] Finalizing archive")
    _emit(progress_callback, ProgressEvent(
        phase="phase_3_finalize",
        message="Writing manifest and finalizing archive",
        percent=95.0,
    ))
    manifest = Manifest(
        source_host=client.config.host,
        namespace=namespace,
        namespace_config=ns_config,
        include_inactive=include_inactive,
        include_files=include_files,
        include_all_versions=not latest_only,
        closure=closure_info,
        counts=counts,
    )

    archive_path = writer.write(manifest)
    console.print(f"\n  Archive written to: [green]{archive_path}[/green]")
    _print_counts(counts)

    _emit(progress_callback, ProgressEvent(
        phase="complete",
        message=f"Export complete: {counts.total} entities",
        percent=100.0,
        details={
            "archive_path": str(archive_path),
            "counts": counts.model_dump(),
        },
    ))

    return _build_stats(namespace, counts, closure_info, start)


def _collect_terminology_refs(templates: list[dict[str, Any]]) -> set[str]:
    """Collect all terminology IDs referenced by a set of templates."""
    refs: set[str] = set()
    for tpl in templates:
        for field in tpl.get("fields", []):
            for key in ("terminology_ref", "array_terminology_ref"):
                val = field.get(key)
                if val:
                    refs.add(val)
            for tterm in field.get("target_terminologies") or []:
                refs.add(tterm)
    return refs


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


def _fetch_synonyms(
    collector: EntityCollector,
    terminologies: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    writer: ArchiveWriter,
    namespace: str,
) -> list[dict[str, Any]]:
    """Fetch custom Registry synonyms for all entities in the archive.

    Reads document/file entity IDs from the writer's temp files to avoid
    holding them all in memory. Returns a list of synonym dicts.
    """
    # Collect all entity IDs from small entities (in memory)
    id_fields = {
        "terminologies": ("terminology_id", terminologies),
        "terms": ("term_id", terms),
        "templates": ("template_id", templates),
    }

    unique_ids: list[str] = []
    seen: set[str] = set()
    for id_field, entities in id_fields.values():
        for entity in entities:
            eid = entity.get(id_field)
            if eid and eid not in seen:
                seen.add(eid)
                unique_ids.append(eid)

    # Read document/file IDs from temp files (O(scan) not O(memory))
    for entity_type, id_field in [("documents", "document_id"), ("files", "file_id")]:
        tmp_path = Path(writer._tmp_dir) / ENTITY_FILES[entity_type]
        if tmp_path.exists():
            with open(tmp_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entity = json.loads(line)
                    eid = entity.get(id_field)
                    if eid and eid not in seen:
                        seen.add(eid)
                        unique_ids.append(eid)

    if not unique_ids:
        return []

    # Bulk fetch registry entries
    registry_map = collector.fetch_registry_entries(unique_ids)

    # Extract synonyms (only non-primary composite keys)
    synonyms: list[dict[str, Any]] = []
    for eid, reg_data in registry_map.items():
        primary_key = reg_data.get("primary_composite_key", {})
        for syn in reg_data.get("synonyms", []):
            composite_key = syn.get("composite_key", {})
            # Skip the primary key — it's not a "custom" synonym
            if composite_key == primary_key:
                continue
            synonyms.append({
                "entry_id": eid,
                "namespace": syn.get("namespace", namespace),
                "entity_type": syn.get("entity_type", ""),
                "composite_key": composite_key,
            })

    return synonyms


def _print_counts(counts: EntityCounts) -> None:
    console.print(f"  Terminologies:  {counts.terminologies}")
    console.print(f"  Terms:          {counts.terms}")
    if counts.term_relations:
        console.print(f"  Term Relations: {counts.term_relations}")
    console.print(f"  Templates:      {counts.templates}")
    console.print(f"  Documents:      {counts.documents}")
    console.print(f"  Files:          {counts.files}")
    console.print(f"  [bold]Total:        {counts.total}[/bold]")


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
