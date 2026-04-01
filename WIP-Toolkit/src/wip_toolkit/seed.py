"""Seed command — bootstrap a WIP namespace from /export-model seed files.

Reads the directory structure produced by the /export-model slash command:

    data-model/
    ├── terminologies/    # {VALUE}.json — terminology + inline terms
    └── templates/        # {NN}_{VALUE}.json — templates (numbered for deps)

Terminology seed files use human-readable values for terminology_ref (e.g.,
"DND_SPELL_LEVEL"), not UUIDs. This command resolves them after creating
terminologies.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from .client import WIPClient, WIPClientError

console = Console(stderr=True)


def run_seed(
    client: WIPClient,
    data_model_dir: str | Path,
    namespace: str,
    *,
    skip_templates: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
) -> SeedStats:
    """Seed a namespace from /export-model seed files."""
    start = time.monotonic()
    data_model = Path(data_model_dir)
    stats = SeedStats(namespace=namespace)

    term_dir = data_model / "terminologies"
    tpl_dir = data_model / "templates"

    if not data_model.is_dir():
        stats.errors.append(f"Directory not found: {data_model}")
        return stats

    # Collect files
    term_files = sorted(term_dir.glob("*.json")) if term_dir.is_dir() else []
    tpl_files = sorted(tpl_dir.glob("*.json")) if tpl_dir.is_dir() else []

    console.print(f"[bold]Seeding namespace '{namespace}' from {data_model}[/bold]")
    console.print(f"  Terminologies: {len(term_files)} files")
    console.print(f"  Templates:     {len(tpl_files)} files")

    if dry_run:
        console.print("\n[bold yellow]Dry run[/bold yellow] — no changes will be made")
        stats.duration_seconds = round(time.monotonic() - start, 2)
        return stats

    if not term_files and not tpl_files:
        console.print("\n[yellow]No seed files found[/yellow]")
        stats.duration_seconds = round(time.monotonic() - start, 2)
        return stats

    # Step 1: Ensure namespace exists
    _ensure_namespace(client, namespace, stats)
    if stats.errors:
        return stats

    # Step 2: Create terminologies + inline terms
    # Build value→ID map for resolving terminology_ref in templates
    terminology_map: dict[str, str] = {}  # value → terminology_id

    if term_files:
        console.print(f"\n[bold cyan]Step 1:[/bold cyan] Creating {len(term_files)} terminologies")
        for f in term_files:
            _create_terminology_from_seed(
                client, namespace, f, terminology_map, stats, continue_on_error,
            )

    # Step 3: Create templates (in file order — numbered for deps)
    if tpl_files and not skip_templates:
        console.print(f"\n[bold cyan]Step 2:[/bold cyan] Creating {len(tpl_files)} templates")
        for f in tpl_files:
            _create_template_from_seed(
                client, namespace, f, terminology_map, stats, continue_on_error,
            )
    elif skip_templates:
        console.print("\n[dim]Skipping templates (--skip-templates)[/dim]")

    stats.duration_seconds = round(time.monotonic() - start, 2)

    # Summary
    console.print(f"\n[bold green]Seed completed[/bold green] in {stats.duration_seconds}s")
    console.print(
        f"  Terminologies: {stats.created_terminologies} created, "
        f"{stats.skipped_terminologies} skipped, "
        f"{stats.failed_terminologies} failed"
    )
    console.print(
        f"  Terms:         {stats.created_terms} created, "
        f"{stats.skipped_terms} skipped, "
        f"{stats.failed_terms} failed"
    )
    if not skip_templates:
        console.print(
            f"  Templates:     {stats.created_templates} created, "
            f"{stats.skipped_templates} skipped, "
            f"{stats.failed_templates} failed"
        )

    if stats.warnings:
        console.print(f"\n[yellow]{len(stats.warnings)} warning(s):[/yellow]")
        for w in stats.warnings:
            console.print(f"  {w}")
    if stats.errors:
        console.print(f"\n[red]{len(stats.errors)} error(s):[/red]")
        for e in stats.errors:
            console.print(f"  {e}")

    return stats


class SeedStats:
    """Track seed operation results."""

    def __init__(self, namespace: str = "") -> None:
        self.namespace = namespace
        self.created_terminologies = 0
        self.skipped_terminologies = 0
        self.failed_terminologies = 0
        self.created_terms = 0
        self.skipped_terms = 0
        self.failed_terms = 0
        self.created_templates = 0
        self.skipped_templates = 0
        self.failed_templates = 0
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.duration_seconds: float = 0.0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def _ensure_namespace(client: WIPClient, namespace: str, stats: SeedStats) -> None:
    """Create the namespace if it doesn't exist."""
    try:
        client.get("registry", f"/namespaces/{namespace}")
        console.print(f"  Namespace '{namespace}' exists")
    except WIPClientError as e:
        if e.status_code == 404:
            console.print(f"  Creating namespace '{namespace}'")
            try:
                client.post("registry", "/namespaces", json={
                    "prefix": namespace,
                    "description": "Seeded by wip-toolkit",
                    "created_by": "wip-toolkit-seed",
                })
            except WIPClientError as create_err:
                stats.errors.append(f"Failed to create namespace: {create_err}")
        else:
            stats.errors.append(f"Failed to check namespace: {e}")


def _create_terminology_from_seed(
    client: WIPClient,
    namespace: str,
    seed_file: Path,
    terminology_map: dict[str, str],
    stats: SeedStats,
    continue_on_error: bool,
) -> None:
    """Create a terminology and its inline terms from a seed file."""
    try:
        data = json.loads(seed_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        stats.errors.append(f"Failed to read {seed_file.name}: {e}")
        return

    value = data.get("value", seed_file.stem)
    terms = data.pop("terms", [])

    # Create terminology
    payload = {
        "value": value,
        "label": data.get("label", value),
        "description": data.get("description", ""),
        "namespace": namespace,
        "case_sensitive": data.get("case_sensitive", False),
        "allow_multiple": data.get("allow_multiple", False),
        "extensible": data.get("extensible", False),
        "mutable": data.get("mutable", False),
        "metadata": data.get("metadata"),
        "created_by": "wip-toolkit-seed",
    }

    try:
        result = client.post("def-store", "/terminologies", json=[payload])
        r = result["results"][0]
        if r["status"] == "created":
            tid = r["id"]
            terminology_map[value] = tid
            stats.created_terminologies += 1
            console.print(f"  [green]Created[/green] {value} ({len(terms)} terms)")
        elif r["status"] == "error" and "already exists" in r.get("error", ""):
            # Fetch existing ID for the map
            tid = _lookup_terminology_id(client, namespace, value)
            if tid:
                terminology_map[value] = tid
            stats.skipped_terminologies += 1
            console.print(f"  [dim]Exists[/dim]  {value} ({len(terms)} terms)")
        else:
            stats.failed_terminologies += 1
            stats.errors.append(f"Failed to create {value}: {r.get('error')}")
            if not continue_on_error:
                return
            return
    except WIPClientError as e:
        stats.failed_terminologies += 1
        stats.errors.append(f"Failed to create {value}: {e}")
        if not continue_on_error:
            return
        return

    # Create terms
    tid = terminology_map.get(value)
    if not tid or not terms:
        return

    term_payloads = []
    for t in terms:
        term_payloads.append({
            "value": t["value"],
            "label": t.get("label", t["value"]),
            "description": t.get("description", ""),
            "aliases": t.get("aliases", []),
            "sort_order": t.get("sort_order", 0),
            "translations": t.get("translations", []),
            "metadata": t.get("metadata", {}),
            "created_by": "wip-toolkit-seed",
        })

    try:
        result = client.post("def-store", f"/terminologies/{tid}/terms", json=term_payloads)
        created = result.get("succeeded", 0)
        failed = result.get("failed", 0)
        stats.created_terms += created
        stats.failed_terms += failed
        # Count skipped (already existing) terms
        for r in result.get("results", []):
            if r.get("status") == "error" and "already exists" in r.get("error", ""):
                stats.skipped_terms += 1
                stats.failed_terms -= 1  # Don't count "already exists" as failure
    except WIPClientError as e:
        stats.failed_terms += len(term_payloads)
        stats.errors.append(f"Failed to create terms for {value}: {e}")


def _lookup_terminology_id(
    client: WIPClient,
    namespace: str,
    value: str,
) -> str | None:
    """Look up a terminology ID by value."""
    try:
        result = client.get(
            "def-store",
            f"/terminologies/by-value/{value}",
            params={"namespace": namespace},
        )
        return result.get("terminology_id")
    except WIPClientError:
        return None


def _resolve_terminology_ref(
    ref: str,
    terminology_map: dict[str, str],
) -> str:
    """Resolve a terminology_ref — could be a human-readable value or a UUID."""
    # If it's already in the map (by value), return the ID
    if ref in terminology_map:
        return terminology_map[ref]
    # If it looks like a UUID, pass through (legacy seed files)
    if len(ref) > 30 and "-" in ref:
        return ref
    # Not resolved — return as-is and let the API try synonym resolution
    return ref


def _resolve_template_fields(
    fields: list[dict[str, Any]],
    terminology_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Resolve terminology_ref values in template fields."""
    resolved = []
    for field in fields:
        field = dict(field)
        if field.get("terminology_ref"):
            field["terminology_ref"] = _resolve_terminology_ref(
                field["terminology_ref"], terminology_map,
            )
        if field.get("array_terminology_ref"):
            field["array_terminology_ref"] = _resolve_terminology_ref(
                field["array_terminology_ref"], terminology_map,
            )
        # target_terminologies is a list
        if field.get("target_terminologies"):
            field["target_terminologies"] = [
                _resolve_terminology_ref(t, terminology_map)
                for t in field["target_terminologies"]
            ]
        resolved.append(field)
    return resolved


def _create_template_from_seed(
    client: WIPClient,
    namespace: str,
    seed_file: Path,
    terminology_map: dict[str, str],
    stats: SeedStats,
    continue_on_error: bool,
) -> None:
    """Create a template from a seed file, resolving terminology_ref values."""
    try:
        data = json.loads(seed_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        stats.errors.append(f"Failed to read {seed_file.name}: {e}")
        return

    value = data.get("value", seed_file.stem)
    fields = data.get("fields", [])

    # Resolve terminology_ref values to IDs
    resolved_fields = _resolve_template_fields(fields, terminology_map)

    payload = {
        "value": value,
        "label": data.get("label", value),
        "description": data.get("description", ""),
        "namespace": namespace,
        "identity_fields": data.get("identity_fields", []),
        "fields": resolved_fields,
        "extends": data.get("extends"),
        "extends_version": data.get("extends_version"),
        "rules": data.get("rules", []),
        "metadata": data.get("metadata"),
        "reporting": data.get("reporting"),
        "created_by": "wip-toolkit-seed",
    }

    try:
        result = client.post("template-store", "/templates", json=[payload])
        r = result["results"][0]
        if r["status"] == "created":
            stats.created_templates += 1
            console.print(f"  [green]Created[/green] {value} ({len(fields)} fields)")
        elif r["status"] == "error" and "already exists" in r.get("error", ""):
            stats.skipped_templates += 1
            console.print(f"  [dim]Exists[/dim]  {value}")
        else:
            stats.failed_templates += 1
            stats.errors.append(f"Failed to create {value}: {r.get('error')}")
            console.print(f"  [red]Failed[/red]  {value}: {r.get('error')}")
    except WIPClientError as e:
        stats.failed_templates += 1
        stats.errors.append(f"Failed to create {value}: {e}")
        console.print(f"  [red]Failed[/red]  {value}: {e}")
