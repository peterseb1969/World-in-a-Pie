"""Import orchestrator — dispatches to restore or fresh mode."""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console

from ..archive import ArchiveReader
from ..client import WIPClient
from ..models import ImportStats
from .fresh import fresh_import
from .restore import restore_import

console = Console(stderr=True)


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
) -> ImportStats:
    """Run an import from an archive file.

    Args:
        mode: "restore" (preserve IDs) or "fresh" (new IDs)
        target_namespace: Override target namespace (defaults to source namespace)
    """
    start = time.monotonic()

    with ArchiveReader(archive_path) as reader:
        manifest = reader.read_manifest()
        namespace = target_namespace or manifest.namespace

        console.print(f"\n[bold]Importing archive:[/bold] {archive_path}")
        console.print(f"  Source namespace: {manifest.namespace}")
        console.print(f"  Target namespace: {namespace}")
        console.print(f"  Mode: [bold]{mode}[/bold]")
        console.print(f"  Entities: {manifest.counts.total}")

        # Check service health
        console.print("\n[bold cyan]Checking services...[/bold cyan]")
        health = client.check_all_services()
        all_healthy = True
        for service, (healthy, msg) in health.items():
            status = "[green]OK[/green]" if healthy else f"[red]{msg}[/red]"
            console.print(f"  {service}: {status}")
            if not healthy:
                all_healthy = False

        if not all_healthy:
            console.print("\n[red bold]Some services are not healthy. Aborting.[/red bold]")
            return ImportStats(mode=mode, target_namespace=namespace,
                               errors=["Service health check failed"])

        if mode == "restore":
            stats = restore_import(
                client, reader, namespace,
                skip_documents=skip_documents,
                skip_files=skip_files,
                batch_size=batch_size,
                continue_on_error=continue_on_error,
                dry_run=dry_run,
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

    return stats
