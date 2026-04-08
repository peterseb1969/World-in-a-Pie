"""Import orchestrator — dispatches to restore or fresh mode."""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console

from .._progress import ProgressCallback
from .._progress import emit as _emit
from ..archive import ArchiveReader
from ..client import WIPClient
from ..models import ImportStats, ProgressEvent
from .fresh import fresh_import
from .restore import restore_import

console = Console(stderr=True)

__all__ = ["ProgressCallback", "run_import"]


def run_import(
    client: WIPClient,
    archive_path: str | Path,
    *,
    mode: str = "restore",
    target_namespace: str | None = None,
    register_synonyms: bool = False,
    skip_documents: bool = False,
    skip_files: bool = False,
    batch_size: int = 50,
    continue_on_error: bool = False,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    non_interactive: bool = False,
    tmp_dir: str | Path | None = None,
) -> ImportStats:
    """Run an import from an archive file.

    Args:
        mode: "restore" (preserve IDs) or "fresh" (new IDs)
        target_namespace: Override target namespace (defaults to source namespace)
        progress_callback: Optional observer invoked at phase boundaries with
            a :class:`ProgressEvent`. Designed for callers that need to surface
            progress without parsing console output (e.g. a REST endpoint
            streaming SSE). Exceptions raised inside the callback are swallowed.
        non_interactive: Reserved for parity with :func:`run_export`. The
            current import path does not prompt, but server callers should set
            this to ``True`` to opt out of any future interactive branches.
        tmp_dir: Reserved for parity with :func:`run_export`. The current
            :class:`ArchiveReader` reads entities directly from the ZIP and
            needs no scratch dir, but accepting the kwarg keeps the
            export/import API symmetric and lets server callers thread
            ``WIP_BACKUP_DIR`` through both runners (CASE-29).
    """
    del non_interactive  # currently no interactive branches in import path
    del tmp_dir  # reserved — ArchiveReader has no scratch dir today
    start = time.monotonic()

    with ArchiveReader(archive_path) as reader:
        manifest = reader.read_manifest()
        namespace = target_namespace or manifest.namespace

        _emit(progress_callback, ProgressEvent(
            phase="start",
            message=f"Importing archive into namespace: {namespace}",
            percent=0.0,
            details={
                "archive_path": str(archive_path),
                "source_namespace": manifest.namespace,
                "target_namespace": namespace,
                "mode": mode,
                "entities": manifest.counts.total,
                "dry_run": dry_run,
            },
        ))

        console.print(f"\n[bold]Importing archive:[/bold] {archive_path}")
        console.print(f"  Source namespace: {manifest.namespace}")
        console.print(f"  Target namespace: {namespace}")
        console.print(f"  Mode: [bold]{mode}[/bold]")
        console.print(f"  Entities: {manifest.counts.total}")

        # Check service health
        console.print("\n[bold cyan]Checking services...[/bold cyan]")
        _emit(progress_callback, ProgressEvent(
            phase="phase_health_check",
            message="Checking service health",
            percent=2.0,
        ))
        health = client.check_all_services()
        all_healthy = True
        for service, (healthy, msg) in health.items():
            status = "[green]OK[/green]" if healthy else f"[red]{msg}[/red]"
            console.print(f"  {service}: {status}")
            if not healthy:
                all_healthy = False

        if not all_healthy:
            console.print("\n[red bold]Some services are not healthy. Aborting.[/red bold]")
            failed = ImportStats(mode=mode, target_namespace=namespace,
                                 errors=["Service health check failed"])
            _emit(progress_callback, ProgressEvent(
                phase="error",
                message="Service health check failed",
                details={"health": {k: v[1] for k, v in health.items()}},
            ))
            return failed

        if mode == "restore":
            stats = restore_import(
                client, reader, namespace,
                skip_documents=skip_documents,
                skip_files=skip_files,
                batch_size=batch_size,
                continue_on_error=continue_on_error,
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
        elif mode == "fresh":
            stats = fresh_import(
                client, reader, namespace,
                register_synonyms=register_synonyms,
                skip_documents=skip_documents,
                skip_files=skip_files,
                batch_size=batch_size,
                continue_on_error=continue_on_error,
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
        else:
            raise ValueError(f"Unknown import mode: {mode}")

    stats.duration_seconds = round(time.monotonic() - start, 2)

    # Summary
    console.print(f"\n[bold]Import complete[/bold] ({stats.duration_seconds}s)")
    console.print(f"  Created: T={stats.created.terminologies} Tm={stats.created.terms} "
                   f"Tpl={stats.created.templates} D={stats.created.documents} F={stats.created.files}")
    if stats.skipped.total:
        console.print(f"  Skipped: T={stats.skipped.terminologies} Tm={stats.skipped.terms} "
                       f"Tpl={stats.skipped.templates} D={stats.skipped.documents} F={stats.skipped.files}")
    if stats.failed.total:
        console.print(f"  [red]Failed:  T={stats.failed.terminologies} Tm={stats.failed.terms} "
                       f"Tpl={stats.failed.templates} D={stats.failed.documents} F={stats.failed.files}[/red]")
    if stats.id_mappings:
        console.print(f"  ID mappings: {stats.id_mappings}")
    if stats.synonyms_registered:
        console.print(f"  Synonyms registered: {stats.synonyms_registered}")
    if stats.errors:
        console.print(f"\n  [red]{len(stats.errors)} error(s):[/red]")
        for err in stats.errors[:10]:
            console.print(f"    {err}")
        if len(stats.errors) > 10:
            console.print(f"    ... and {len(stats.errors) - 10} more")
    if stats.warnings:
        console.print(f"\n  [yellow]{len(stats.warnings)} warning(s):[/yellow]")
        for w in stats.warnings[:10]:
            console.print(f"    {w}")

    _emit(progress_callback, ProgressEvent(
        phase="complete",
        message=(
            f"Import complete: {stats.created.total} created, "
            f"{stats.failed.total} failed"
        ),
        percent=100.0,
        details={
            "mode": stats.mode,
            "source_namespace": stats.source_namespace,
            "target_namespace": stats.target_namespace,
            "created": stats.created.model_dump(),
            "skipped": stats.skipped.model_dump(),
            "failed": stats.failed.model_dump(),
            "duration_seconds": stats.duration_seconds,
        },
    ))

    return stats
