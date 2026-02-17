"""Restore mode import — preserves 100% of original IDs.

Uses pre-registration for entities with deterministic composite keys
(terminologies, terms, identity-based documents) and direct ID pass-through
for entities with empty composite keys (templates, identity-less documents, files).
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from ..archive import ArchiveReader
from ..client import WIPClient, WIPClientError
from ..models import EntityCounts, ImportStats

console = Console(stderr=True)


def restore_import(
    client: WIPClient,
    reader: ArchiveReader,
    target_namespace: str,
    *,
    skip_documents: bool = False,
    skip_files: bool = False,
    batch_size: int = 50,
    continue_on_error: bool = False,
    dry_run: bool = False,
) -> ImportStats:
    """Import archive in restore mode, preserving all original IDs."""
    stats = ImportStats(mode="restore", target_namespace=target_namespace)
    manifest = reader.read_manifest()
    stats.source_namespace = manifest.namespace

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — no changes will be made")
        _preview(reader, manifest, skip_documents, skip_files)
        return stats

    # Step 1: Ensure namespace exists
    _ensure_namespace(client, target_namespace, stats)

    # Step 2: Pre-register terminology IDs in Registry
    console.print("\n[bold cyan]Step 1:[/bold cyan] Pre-registering terminology IDs")
    terminologies = list(reader.read_entities("terminologies"))
    _preregister_terminologies(client, target_namespace, terminologies, stats, continue_on_error)

    # Step 3: Pre-register term IDs in Registry
    console.print("\n[bold cyan]Step 2:[/bold cyan] Pre-registering term IDs")
    terms = list(reader.read_entities("terms"))
    _preregister_terms(client, target_namespace, terms, stats, continue_on_error)

    # Step 4: Create terminologies via Def-Store
    console.print("\n[bold cyan]Step 3:[/bold cyan] Creating terminologies")
    _create_terminologies(client, target_namespace, terminologies, stats, continue_on_error)

    # Step 5: Create terms via Def-Store
    console.print("\n[bold cyan]Step 4:[/bold cyan] Creating terms")
    _create_terms(client, target_namespace, terms, batch_size, stats, continue_on_error)

    # Step 6: Create templates as drafts with ID pass-through
    console.print("\n[bold cyan]Step 5:[/bold cyan] Creating templates (as drafts)")
    templates = list(reader.read_entities("templates"))
    _create_templates(client, target_namespace, templates, stats, continue_on_error)

    # Step 7: Activate all draft templates
    console.print("\n[bold cyan]Step 6:[/bold cyan] Activating templates")
    _activate_templates(client, target_namespace, templates, stats, continue_on_error)

    if not skip_documents:
        # Step 8: Create documents (with document_id + version pass-through)
        # Both document_id and version are passed, so the document-store
        # skips Registry registration entirely (restore-mode bypass).
        console.print("\n[bold cyan]Step 7:[/bold cyan] Creating documents")
        documents = list(reader.read_entities("documents"))
        _create_documents(client, target_namespace, documents, batch_size, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping documents (--skip-documents)[/dim]")

    if not skip_files and not skip_documents:
        # Step 10: Upload files with ID pass-through
        console.print("\n[bold cyan]Step 9:[/bold cyan] Uploading files")
        files = list(reader.read_entities("files"))
        _upload_files(client, target_namespace, files, reader, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping files[/dim]")

    return stats


def _ensure_namespace(client: WIPClient, namespace: str, stats: ImportStats) -> None:
    """Create the target namespace if it doesn't exist."""
    try:
        client.get("registry", f"/namespaces/{namespace}")
        console.print(f"  Namespace '{namespace}' already exists")
    except WIPClientError as e:
        if e.status_code == 404:
            console.print(f"  Creating namespace '{namespace}'")
            try:
                client.post("registry", "/namespaces", json={
                    "prefix": namespace,
                    "description": f"Restored from backup",
                    "isolation_mode": "open",
                    "created_by": "wip-toolkit",
                })
            except WIPClientError as create_err:
                stats.errors.append(f"Failed to create namespace: {create_err}")
                raise
        else:
            raise


def _preregister_terminologies(
    client: WIPClient,
    namespace: str,
    terminologies: list[dict],
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Pre-register terminology IDs with their composite keys."""
    if not terminologies:
        return

    batch = [
        {
            "namespace": namespace,
            "entity_type": "terminologies",
            "entry_id": t["terminology_id"],
            "composite_key": {"value": t["value"], "label": t.get("label", t["value"])},
            "created_by": "wip-toolkit-restore",
        }
        for t in terminologies
    ]

    result = _registry_register_batch(client, batch, "terminology", stats, continue_on_error)
    console.print(f"  Pre-registered {result} terminology ID(s)")


def _preregister_terms(
    client: WIPClient,
    namespace: str,
    terms: list[dict],
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Pre-register term IDs with their composite keys."""
    if not terms:
        return

    batch = [
        {
            "namespace": namespace,
            "entity_type": "terms",
            "entry_id": t["term_id"],
            "composite_key": {
                "terminology_id": t["terminology_id"],
                "value": t["value"],
            },
            "created_by": "wip-toolkit-restore",
        }
        for t in terms
    ]

    result = _registry_register_batch(client, batch, "term", stats, continue_on_error)
    console.print(f"  Pre-registered {result} term ID(s)")


def _registry_register_batch(
    client: WIPClient,
    items: list[dict],
    entity_label: str,
    stats: ImportStats,
    continue_on_error: bool,
    batch_size: int = 100,
) -> int:
    """Register items in Registry in batches. Returns count of successful registrations."""
    registered = 0
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        try:
            result = client.post("registry", "/entries/register", json=batch)
            registered += result.get("created", 0) + result.get("already_exists", 0)
            errors = result.get("errors", 0)
            if errors > 0:
                for r in result.get("results", []):
                    if r.get("status") == "error":
                        msg = f"Registry error for {entity_label}: {r.get('error', 'unknown')}"
                        stats.errors.append(msg)
                        if not continue_on_error:
                            raise WIPClientError(msg)
        except WIPClientError:
            if not continue_on_error:
                raise
            stats.errors.append(f"Batch registration failed for {entity_label}s at index {i}")
    return registered


def _create_terminologies(
    client: WIPClient,
    namespace: str,
    terminologies: list[dict],
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create terminologies via Def-Store API."""
    for t in terminologies:
        try:
            payload = {
                "value": t["value"],
                "label": t.get("label", t["value"]),
                "description": t.get("description", ""),
                "namespace": namespace,
                "case_sensitive": t.get("case_sensitive", False),
                "allow_multiple": t.get("allow_multiple", False),
                "extensible": t.get("extensible", False),
                "metadata": t.get("metadata"),
                "created_by": "wip-toolkit-restore",
            }
            client.post("def-store", "/terminologies", json=payload)
            stats.created.terminologies += 1
        except WIPClientError as e:
            if e.status_code == 409:
                stats.skipped.terminologies += 1
            else:
                stats.failed.terminologies += 1
                stats.errors.append(f"Failed to create terminology {t['terminology_id']}: {e}")
                if not continue_on_error:
                    raise

    console.print(
        f"  Created {stats.created.terminologies}, "
        f"skipped {stats.skipped.terminologies}, "
        f"failed {stats.failed.terminologies}"
    )


def _create_terms(
    client: WIPClient,
    namespace: str,
    terms: list[dict],
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create terms via Def-Store bulk API, grouped by terminology."""
    # Group terms by terminology_id
    by_terminology: dict[str, list[dict]] = {}
    for t in terms:
        tid = t["terminology_id"]
        by_terminology.setdefault(tid, []).append(t)

    for tid, term_group in by_terminology.items():
        for i in range(0, len(term_group), batch_size):
            batch = term_group[i:i + batch_size]
            term_payloads = []
            for t in batch:
                term_payloads.append({
                    "value": t["value"],
                    "aliases": t.get("aliases", []),
                    "label": t.get("label", t["value"]),
                    "description": t.get("description", ""),
                    "sort_order": t.get("sort_order", 0),
                    "parent_term_id": t.get("parent_term_id"),
                    "translations": t.get("translations", []),
                    "metadata": t.get("metadata", {}),
                })

            try:
                result = client.post(
                    "def-store",
                    f"/terminologies/{tid}/terms/bulk",
                    json={"terms": term_payloads, "created_by": "wip-toolkit-restore"},
                )
                stats.created.terms += result.get("succeeded", 0)
                stats.failed.terms += result.get("failed", 0)
            except WIPClientError as e:
                stats.failed.terms += len(batch)
                stats.errors.append(f"Failed to create terms batch for {tid}: {e}")
                if not continue_on_error:
                    raise

    console.print(
        f"  Created {stats.created.terms}, failed {stats.failed.terms}"
    )


def _create_templates(
    client: WIPClient,
    namespace: str,
    templates: list[dict],
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create templates as drafts with template_id pass-through.

    Groups by template_id and creates in version order.
    First version uses POST (create), subsequent use PUT (update/new version).
    """
    # Group by template_id, sort by version
    by_id: dict[str, list[dict]] = {}
    for t in templates:
        tid = t["template_id"]
        by_id.setdefault(tid, []).append(t)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    for tid, versions in by_id.items():
        for idx, tpl in enumerate(versions):
            try:
                if idx == 0:
                    # First version: POST with template_id pass-through
                    payload = _template_create_payload(tpl, namespace)
                    client.post("template-store", "/templates", json=payload)
                else:
                    # Subsequent versions: PUT to create new version
                    payload = _template_update_payload(tpl)
                    client.put("template-store", f"/templates/{tid}", json=payload)
                stats.created.templates += 1
            except WIPClientError as e:
                if e.status_code == 409:
                    stats.skipped.templates += 1
                else:
                    stats.failed.templates += 1
                    stats.errors.append(
                        f"Failed to create template {tid} v{tpl.get('version', '?')}: {e}"
                    )
                    if not continue_on_error:
                        raise

    console.print(
        f"  Created {stats.created.templates}, "
        f"skipped {stats.skipped.templates}, "
        f"failed {stats.failed.templates}"
    )


def _template_create_payload(tpl: dict, namespace: str) -> dict[str, Any]:
    """Build a CreateTemplateRequest payload.

    Includes both template_id and version so the template-store
    skips Registry registration (restore-mode bypass).
    """
    return {
        "value": tpl["value"],
        "label": tpl.get("label", tpl["value"]),
        "description": tpl.get("description", ""),
        "template_id": tpl["template_id"],
        "version": tpl.get("version", 1),
        "namespace": namespace,
        "extends": tpl.get("extends"),
        "extends_version": tpl.get("extends_version"),
        "identity_fields": tpl.get("identity_fields", []),
        "fields": tpl.get("fields", []),
        "rules": tpl.get("rules", []),
        "metadata": tpl.get("metadata"),
        "reporting": tpl.get("reporting"),
        "created_by": "wip-toolkit-restore",
        "status": "draft",
    }


def _template_update_payload(tpl: dict) -> dict[str, Any]:
    """Build an UpdateTemplateRequest payload for subsequent versions."""
    return {
        "value": tpl["value"],
        "label": tpl.get("label", tpl["value"]),
        "description": tpl.get("description", ""),
        "extends": tpl.get("extends"),
        "extends_version": tpl.get("extends_version"),
        "identity_fields": tpl.get("identity_fields", []),
        "fields": tpl.get("fields", []),
        "rules": tpl.get("rules", []),
        "metadata": tpl.get("metadata"),
        "reporting": tpl.get("reporting"),
        "updated_by": "wip-toolkit-restore",
    }


def _activate_templates(
    client: WIPClient,
    namespace: str,
    templates: list[dict],
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Activate all draft templates."""
    # Get unique template_ids
    seen: set[str] = set()
    unique_ids = []
    for t in templates:
        tid = t["template_id"]
        if tid not in seen:
            seen.add(tid)
            unique_ids.append(tid)

    activated = 0
    already_active = 0
    for tid in unique_ids:
        try:
            client.post(
                "template-store",
                f"/templates/{tid}/activate",
                params={"namespace": namespace},
            )
            activated += 1
        except WIPClientError as e:
            if e.status_code == 400 and "not 'draft'" in str(e):
                # Already activated by cascading activation — benign
                already_active += 1
            else:
                msg = f"Failed to activate template {tid}: {e}"
                stats.warnings.append(msg)
                if not continue_on_error:
                    raise

    msg = f"  Activated {activated} template(s)"
    if already_active:
        msg += f" ({already_active} already active from cascade)"
    console.print(msg)


def _preregister_documents(
    client: WIPClient,
    namespace: str,
    documents: list[dict],
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Pre-register document IDs for documents with identity fields."""
    identity_docs = [
        d for d in documents
        if d.get("identity_hash")
    ]

    if not identity_docs:
        console.print("  No identity-based documents to pre-register")
        return

    batch = [
        {
            "namespace": namespace,
            "entity_type": "documents",
            "entry_id": d["document_id"],
            "composite_key": {
                "namespace": namespace,
                "identity_hash": d["identity_hash"],
                "template_id": d["template_id"],
            },
            "created_by": "wip-toolkit-restore",
        }
        for d in identity_docs
    ]

    result = _registry_register_batch(client, batch, "document", stats, continue_on_error)
    console.print(f"  Pre-registered {result} document ID(s)")


def _create_documents(
    client: WIPClient,
    namespace: str,
    documents: list[dict],
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create documents via Document-Store API.

    Groups by document_id and creates in version order.
    """
    # Group by document_id, sort by version
    by_id: dict[str, list[dict]] = {}
    for d in documents:
        did = d["document_id"]
        by_id.setdefault(did, []).append(d)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    # Flatten back in version order
    ordered_docs = []
    for versions in by_id.values():
        ordered_docs.extend(versions)

    # Create in batches
    for i in range(0, len(ordered_docs), batch_size):
        batch = ordered_docs[i:i + batch_size]
        items = []
        for d in batch:
            items.append({
                "template_id": d["template_id"],
                "template_version": d.get("template_version"),
                "document_id": d["document_id"],
                "version": d.get("version"),
                "namespace": namespace,
                "data": d["data"],
                "created_by": "wip-toolkit-restore",
                "metadata": d.get("metadata"),
            })

        try:
            result = client.post(
                "document-store", "/documents/bulk",
                json={"items": items, "continue_on_error": continue_on_error},
            )
            stats.created.documents += result.get("created", 0) + result.get("updated", 0)
            stats.failed.documents += result.get("failed", 0)
            for r in result.get("results", []):
                if r.get("error"):
                    stats.errors.append(f"Document error: {r['error']}")
        except WIPClientError as e:
            stats.failed.documents += len(batch)
            stats.errors.append(f"Failed to create document batch at index {i}: {e}")
            if not continue_on_error:
                raise

    console.print(
        f"  Created {stats.created.documents}, failed {stats.failed.documents}"
    )


def _upload_files(
    client: WIPClient,
    namespace: str,
    files: list[dict],
    reader: ArchiveReader,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Upload files with ID pass-through."""
    blobs = set(reader.list_blobs())

    for f in files:
        fid = f["file_id"]
        if fid not in blobs:
            stats.skipped.files += 1
            continue

        blob_data = reader.read_blob(fid)
        if not blob_data:
            stats.skipped.files += 1
            continue

        try:
            data: dict[str, str] = {
                "namespace": namespace,
                "file_id": fid,
            }
            if f.get("metadata", {}).get("description"):
                data["description"] = f["metadata"]["description"]
            if f.get("metadata", {}).get("tags"):
                data["tags"] = ",".join(f["metadata"]["tags"])
            if f.get("metadata", {}).get("category"):
                data["category"] = f["metadata"]["category"]

            client.post_form(
                "document-store", "/files",
                data=data,
                files={"file": (f["filename"], blob_data, f.get("content_type", "application/octet-stream"))},
            )
            stats.created.files += 1
        except WIPClientError as e:
            stats.failed.files += 1
            stats.errors.append(f"Failed to upload file {fid}: {e}")
            if not continue_on_error:
                raise

    console.print(
        f"  Uploaded {stats.created.files}, "
        f"skipped {stats.skipped.files}, "
        f"failed {stats.failed.files}"
    )


def _preview(
    reader: ArchiveReader,
    manifest: Any,
    skip_documents: bool,
    skip_files: bool,
) -> None:
    """Preview what would be imported."""
    counts = manifest.counts
    console.print(f"  Terminologies: {counts.terminologies}")
    console.print(f"  Terms:         {counts.terms}")
    console.print(f"  Templates:     {counts.templates}")
    if not skip_documents:
        console.print(f"  Documents:     {counts.documents}")
    else:
        console.print(f"  Documents:     [dim]skipped[/dim]")
    if not skip_files:
        console.print(f"  Files:         {counts.files}")
    else:
        console.print(f"  Files:         [dim]skipped[/dim]")
