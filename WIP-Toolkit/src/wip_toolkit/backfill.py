"""Backfill auto-synonyms for existing entities.

Phase 5 of universal synonym resolution: iterates all entities in a
namespace and registers auto-synonyms for any that don't have one.
Idempotent — Registry returns ``already_exists`` for duplicates.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from .client import WIPClient, WIPClientError

console = Console(stderr=True)


def backfill_synonyms(
    client: WIPClient,
    namespace: str,
    *,
    skip_documents: bool = False,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register auto-synonyms for all entities in a namespace.

    Returns a summary dict with counts per entity type.
    """
    summary: dict[str, dict[str, int]] = {}

    # Terminologies
    console.print("\n[bold cyan]Step 1:[/bold cyan] Backfilling terminology synonyms")
    terminologies = _fetch_all(client, "def-store", "/terminologies", namespace)
    items = []
    for t in terminologies:
        items.append({
            "target_id": t["terminology_id"],
            "synonym_namespace": namespace,
            "synonym_entity_type": "terminologies",
            "synonym_composite_key": {
                "ns": namespace,
                "type": "terminology",
                "value": t["value"],
            },
            "created_by": "wip-toolkit-backfill",
        })
    summary["terminologies"] = _register_batch(
        client, items, batch_size, dry_run=dry_run,
    )

    # Terms — need terminology value for composite key
    console.print("\n[bold cyan]Step 2:[/bold cyan] Backfilling term synonyms")
    term_items: list[dict] = []
    for t in terminologies:
        terms = _fetch_all(
            client, "def-store",
            f"/terminologies/{t['terminology_id']}/terms",
            namespace=None,  # terms endpoint doesn't filter by namespace
        )
        for term in terms:
            term_items.append({
                "target_id": term["term_id"],
                "synonym_namespace": namespace,
                "synonym_entity_type": "terms",
                "synonym_composite_key": {
                    "ns": namespace,
                    "type": "term",
                    "terminology": t["value"],
                    "value": term["value"],
                },
                "created_by": "wip-toolkit-backfill",
            })
    summary["terms"] = _register_batch(
        client, term_items, batch_size, dry_run=dry_run,
    )

    # Templates
    console.print("\n[bold cyan]Step 3:[/bold cyan] Backfilling template synonyms")
    templates = _fetch_all(
        client, "template-store", "/templates",
        namespace, extra_params={"latest_only": "true"},
    )
    tpl_items = []
    for t in templates:
        tpl_items.append({
            "target_id": t["template_id"],
            "synonym_namespace": namespace,
            "synonym_entity_type": "templates",
            "synonym_composite_key": {
                "ns": namespace,
                "type": "template",
                "value": t["value"],
            },
            "created_by": "wip-toolkit-backfill",
        })
    summary["templates"] = _register_batch(
        client, tpl_items, batch_size, dry_run=dry_run,
    )

    # Documents (optional — only identity-based documents can be backfilled)
    if not skip_documents:
        console.print("\n[bold cyan]Step 4:[/bold cyan] Backfilling document synonyms (identity-based only)")
        # Build template_id → value lookup
        tpl_value_map = {t["template_id"]: t["value"] for t in templates}
        documents = _fetch_all(
            client, "document-store", "/documents",
            namespace, extra_params={"latest_only": "true"},
        )
        doc_items = []
        for d in documents:
            identity_hash = d.get("identity_hash")
            template_value = tpl_value_map.get(d.get("template_id", ""))
            if not identity_hash or not template_value:
                continue
            doc_items.append({
                "target_id": d["document_id"],
                "synonym_namespace": namespace,
                "synonym_entity_type": "documents",
                "synonym_composite_key": {
                    "ns": namespace,
                    "type": "document",
                    "template": template_value,
                    "identity_hash": identity_hash,
                },
                "created_by": "wip-toolkit-backfill",
            })
        summary["documents"] = _register_batch(
            client, doc_items, batch_size, dry_run=dry_run,
        )
    else:
        console.print("\n[dim]Skipping documents (--skip-documents)[/dim]")
        summary["documents"] = {"total": 0, "added": 0, "existing": 0, "failed": 0}

    return summary


def _fetch_all(
    client: WIPClient,
    service: str,
    path: str,
    namespace: str | None,
    extra_params: dict[str, str] | None = None,
) -> list[dict]:
    """Fetch all entities from a paginated endpoint."""
    params: dict[str, Any] = {}
    if namespace:
        params["namespace"] = namespace
    if extra_params:
        params.update(extra_params)
    items = client.fetch_all_paginated(service, path, params=params, page_size=100)
    console.print(f"  Fetched {len(items)} entities from {path}")
    return items


def _register_batch(
    client: WIPClient,
    items: list[dict],
    batch_size: int,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Register synonym items in batches. Returns counts."""
    counts = {"total": len(items), "added": 0, "existing": 0, "failed": 0}

    if dry_run:
        console.print(f"  [yellow]Dry run:[/yellow] would register {len(items)} synonym(s)")
        return counts

    if not items:
        console.print("  No synonyms to register")
        return counts

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        try:
            response = client.post("registry", "/synonyms/add", json=batch)
            for r in response.get("results", []):
                status = r.get("status", "")
                if status == "added":
                    counts["added"] += 1
                elif status == "already_exists":
                    counts["existing"] += 1
                else:
                    counts["failed"] += 1
        except WIPClientError as e:
            counts["failed"] += len(batch)
            console.print(f"  [red]Batch failed at index {i}: {e}[/red]")

    console.print(
        f"  Registered {counts['added']} new, "
        f"{counts['existing']} already existed, "
        f"{counts['failed']} failed"
    )
    return counts
