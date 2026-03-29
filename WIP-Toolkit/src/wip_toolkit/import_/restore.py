"""Restore mode import — preserves 100% of original IDs.

Simplified flow (no pre-registration needed):
1. Create terminologies with ID pass-through → service calls Registry.register()
2. Create terms with ID pass-through
3. Create templates as drafts with ID pass-through
4. Activate templates
5. Create documents with ID pass-through (streamed from archive)
6. Upload files with ID pass-through
7. (if synonyms.jsonl in archive) Bulk-register additional synonyms

Services handle Registry registration during their create flows when
document_id/template_id is passed through. No separate pre-registration
step is needed.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from ..archive import ArchiveReader
from ..client import WIPClient, WIPClientError
from ..models import ImportStats

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

    # Step 0: Ensure namespace exists
    _ensure_namespace(client, target_namespace, stats)

    # Step 1: Create terminologies via Def-Store (service handles Registry)
    # Def-Store doesn't support terminology_id pass-through, so we build
    # old→new ID mappings after creation and remap all downstream references.
    console.print("\n[bold cyan]Step 1:[/bold cyan] Creating terminologies")
    terminologies = list(reader.read_entities("terminologies"))
    _create_terminologies(client, target_namespace, terminologies, stats, continue_on_error)
    term_id_map = _build_terminology_id_map(client, target_namespace, terminologies)
    if term_id_map:
        console.print(f"  ID mappings: {len(term_id_map)} terminology ID(s) remapped")

    # Step 2: Create terms via Def-Store (using remapped terminology IDs)
    console.print("\n[bold cyan]Step 2:[/bold cyan] Creating terms")
    terms = list(reader.read_entities("terms"))
    _create_terms(client, target_namespace, terms, batch_size, stats, continue_on_error, term_id_map)

    # Step 2b: Create relationships (after terms exist)
    relationships = list(reader.read_entities("relationships"))
    if relationships:
        console.print("\n[bold cyan]Step 2b:[/bold cyan] Creating relationships")
        _create_relationships(client, target_namespace, relationships, batch_size, stats, continue_on_error)

    # Step 3: Create templates as drafts with ID pass-through
    # Remap terminology_ref fields to new IDs
    console.print("\n[bold cyan]Step 3:[/bold cyan] Creating templates (as drafts)")
    templates = list(reader.read_entities("templates"))
    if term_id_map:
        _remap_template_terminology_refs(templates, term_id_map)
    _create_templates(client, target_namespace, templates, stats, continue_on_error)

    # Step 4: Activate all draft templates
    console.print("\n[bold cyan]Step 4:[/bold cyan] Activating templates")
    _activate_templates(client, target_namespace, templates, stats, continue_on_error)

    if not skip_files and not skip_documents:
        # Step 5: Upload files (before documents — documents may reference files)
        console.print("\n[bold cyan]Step 5:[/bold cyan] Uploading files")
        files = list(reader.read_entities("files"))
        _upload_files(client, target_namespace, files, reader, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping files[/dim]")

    if not skip_documents:
        # Step 6: Create documents (streamed from archive)
        console.print("\n[bold cyan]Step 6:[/bold cyan] Creating documents")
        _create_documents_streamed(client, target_namespace, reader, batch_size, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping documents (--skip-documents)[/dim]")

    # Step 7: Restore synonyms (from synonyms.jsonl if present)
    console.print("\n[bold cyan]Step 7:[/bold cyan] Restoring Registry synonyms")
    _restore_synonyms(
        client, target_namespace, reader, stats, continue_on_error,
        skip_documents=skip_documents, skip_files=skip_files,
        source_namespace=stats.source_namespace,
    )

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
                    "description": "Restored from backup",
                    "isolation_mode": "open",
                    "created_by": "wip-toolkit",
                })
            except WIPClientError as create_err:
                stats.errors.append(f"Failed to create namespace: {create_err}")
                raise
        else:
            raise


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
                "terminology_id": t["terminology_id"],
                "namespace": namespace,
                "case_sensitive": t.get("case_sensitive", False),
                "allow_multiple": t.get("allow_multiple", False),
                "extensible": t.get("extensible", False),
                "mutable": t.get("mutable", False),
                "metadata": t.get("metadata"),
                "created_by": "wip-toolkit-restore",
            }
            result = client.post("def-store", "/terminologies", json=[payload])
            r = result["results"][0]
            if r["status"] == "created":
                stats.created.terminologies += 1
            elif r["status"] == "error" and "already exists" in r.get("error", ""):
                stats.skipped.terminologies += 1
            else:
                stats.failed.terminologies += 1
                stats.errors.append(f"Failed to create terminology {t['terminology_id']}: {r.get('error')}")
                if not continue_on_error:
                    raise WIPClientError(r.get("error", "Unknown error"))
        except WIPClientError:
            if not continue_on_error:
                raise

    console.print(
        f"  Created {stats.created.terminologies}, "
        f"skipped {stats.skipped.terminologies}, "
        f"failed {stats.failed.terminologies}"
    )


def _build_terminology_id_map(
    client: WIPClient,
    namespace: str,
    terminologies: list[dict],
) -> dict[str, str]:
    """Build old→new ID mapping by fetching all terminologies in the target namespace.

    Matches by value (e.g., DND_CLASS_NAME) which is unique within a namespace.
    """
    id_map: dict[str, str] = {}
    try:
        # Fetch all terminologies in the target namespace (paginated, max 100/page)
        all_items: list[dict] = []
        page = 1
        while True:
            data = client.get("def-store", "/terminologies",
                              params={"namespace": namespace, "page_size": 100, "page": page})
            items = data.get("items", [])
            all_items.extend(items)
            if len(items) < 100:
                break
            page += 1
        target_by_value = {t["value"]: t["terminology_id"] for t in all_items}

        for t in terminologies:
            old_id = t["terminology_id"]
            new_id = target_by_value.get(t["value"])
            if new_id and new_id != old_id:
                id_map[old_id] = new_id
    except WIPClientError as e:
        console.print(f"  [yellow]Warning: Could not fetch target terminologies for ID mapping: {e}[/yellow]")

    return id_map


def _create_terms(
    client: WIPClient,
    namespace: str,
    terms: list[dict],
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
    term_id_map: dict[str, str] | None = None,
) -> None:
    """Create terms via Def-Store bulk API, grouped by terminology."""
    # Group terms by terminology_id (remapped if needed)
    by_terminology: dict[str, list[dict]] = {}
    for t in terms:
        tid = t["terminology_id"]
        if term_id_map:
            tid = term_id_map.get(tid, tid)
        by_terminology.setdefault(tid, []).append(t)

    for tid, term_group in by_terminology.items():
        for i in range(0, len(term_group), batch_size):
            batch = term_group[i:i + batch_size]
            term_payloads = []
            for t in batch:
                payload = {
                    "value": t["value"],
                    "term_id": t.get("term_id"),
                    "aliases": t.get("aliases", []),
                    "label": t.get("label", t["value"]),
                    "description": t.get("description", ""),
                    "sort_order": t.get("sort_order", 0),
                    "parent_term_id": t.get("parent_term_id"),
                    "translations": t.get("translations", []),
                    "metadata": t.get("metadata", {}),
                    "created_by": "wip-toolkit-restore",
                }
                term_payloads.append(payload)

            try:
                result = client.post(
                    "def-store",
                    f"/terminologies/{tid}/terms",
                    json=term_payloads,
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


def _create_relationships(
    client: WIPClient,
    namespace: str,
    relationships: list[dict],
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create relationships via Def-Store ontology API."""
    created = 0
    failed = 0
    skipped = 0

    for i in range(0, len(relationships), batch_size):
        batch = relationships[i:i + batch_size]
        payloads = []
        for r in batch:
            payloads.append({
                "source_term_id": r["source_term_id"],
                "target_term_id": r["target_term_id"],
                "relationship_type": r["relationship_type"],
                "metadata": r.get("metadata") or {},
            })

        try:
            result = client.post(
                "def-store", "/ontology/relationships",
                json=payloads,
                params={"namespace": namespace},
            )
            for r in result.get("results", []):
                if r.get("status") == "created":
                    created += 1
                elif r.get("status") == "skipped":
                    skipped += 1
                else:
                    failed += 1
        except WIPClientError as e:
            failed += len(batch)
            stats.errors.append(f"Failed to create relationship batch at index {i}: {e}")
            if not continue_on_error:
                raise

    stats.created.relationships = created
    stats.failed.relationships = failed
    stats.skipped.relationships = skipped
    msg = f"  Created {created}"
    if skipped:
        msg += f", skipped {skipped}"
    if failed:
        msg += f", failed {failed}"
    console.print(msg)


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
                    result = client.post("template-store", "/templates", json=[payload])
                else:
                    # Subsequent versions: PUT to create new version
                    payload = _template_update_payload(tpl)
                    payload["template_id"] = tid
                    result = client.put("template-store", "/templates", json=[payload])
                r = result["results"][0]
                if r["status"] == "error":
                    if "already exists" in r.get("error", ""):
                        stats.skipped.templates += 1
                    else:
                        stats.failed.templates += 1
                        stats.errors.append(
                            f"Failed to create template {tid} v{tpl.get('version', '?')}: {r.get('error')}"
                        )
                        if not continue_on_error:
                            raise WIPClientError(r.get("error", "Unknown error"))
                else:
                    stats.created.templates += 1
            except Exception as e:
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


def _remap_template_terminology_refs(
    templates: list[dict], term_id_map: dict[str, str],
) -> None:
    """Remap terminology_ref IDs in template fields to new IDs."""
    for tpl in templates:
        for field in tpl.get("fields", []):
            for key in ("terminology_ref", "array_terminology_ref"):
                val = field.get(key)
                if val and val in term_id_map:
                    field[key] = term_id_map[val]
            target_terms = field.get("target_terminologies")
            if target_terms:
                field["target_terminologies"] = [
                    term_id_map.get(t, t) for t in target_terms
                ]


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


def _create_documents_streamed(
    client: WIPClient,
    namespace: str,
    reader: ArchiveReader,
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create documents streamed from the archive.

    Reads documents from the JSONL file in the archive, groups by document_id
    to ensure version ordering, then creates in batches.
    """
    # Read all documents, group by document_id for version ordering
    by_id: dict[str, list[dict]] = {}
    for d in reader.read_entities("documents"):
        did = d["document_id"]
        by_id.setdefault(did, []).append(d)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    # Flatten back in version order
    ordered_docs = []
    for versions in by_id.values():
        ordered_docs.extend(versions)

    # Create in batches, collecting failures for retry
    failed_docs: list[dict] = []

    for i in range(0, len(ordered_docs), batch_size):
        batch = ordered_docs[i:i + batch_size]
        items = _build_document_payloads(batch, namespace)

        try:
            result = client.post(
                "document-store", "/documents",
                json=items,
                params={"continue_on_error": "true"},
            )
            stats.created.documents += result.get("succeeded", 0)
            for idx_r, r in enumerate(result.get("results", [])):
                if r.get("status") == "error":
                    failed_docs.append(batch[idx_r])
        except WIPClientError as e:
            failed_docs.extend(batch)
            stats.errors.append(f"Failed to create document batch at index {i}: {e}")
            if not continue_on_error:
                raise

    # Retry failed documents (handles ordering issues like parent_class refs)
    if failed_docs:
        console.print(f"  Retrying {len(failed_docs)} failed document(s)...")
        retry_items = _build_document_payloads(failed_docs, namespace)
        try:
            result = client.post(
                "document-store", "/documents",
                json=retry_items,
                params={"continue_on_error": "true"},
            )
            stats.created.documents += result.get("succeeded", 0)
            retry_failed = result.get("failed", 0)
            stats.failed.documents += retry_failed
            for r in result.get("results", []):
                if r.get("error"):
                    stats.errors.append(f"Document error: {r['error']}")
            if retry_failed == 0:
                console.print(f"  Retry succeeded — all {len(failed_docs)} document(s) created")
            else:
                console.print(f"  Retry: {result.get('succeeded', 0)} created, {retry_failed} still failed")
        except WIPClientError as e:
            stats.failed.documents += len(failed_docs)
            stats.errors.append(f"Failed to retry documents: {e}")
            if not continue_on_error:
                raise
    else:
        stats.failed.documents = 0

    console.print(
        f"  Created {stats.created.documents}, failed {stats.failed.documents}"
    )


def _build_document_payloads(docs: list[dict], namespace: str) -> list[dict]:
    """Build document create payloads from archive data."""
    return [
        {
            "template_id": d["template_id"],
            "template_version": d.get("template_version"),
            "document_id": d["document_id"],
            "version": d.get("version") or 1,
            "namespace": namespace,
            "data": d["data"],
            "created_by": "wip-toolkit-restore",
            "metadata": d.get("metadata"),
        }
        for d in docs
    ]


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


def _restore_synonyms(
    client: WIPClient,
    namespace: str,
    reader: ArchiveReader,
    stats: ImportStats,
    continue_on_error: bool,
    *,
    skip_documents: bool = False,
    skip_files: bool = False,
    source_namespace: str | None = None,
) -> None:
    """Restore Registry synonyms from synonyms.jsonl in the archive.

    Falls back to _registry metadata on entities for backward compatibility
    with archives that don't have synonyms.jsonl.

    When target namespace differs from source, rewrites the ``ns`` field
    in composite keys so synonyms resolve correctly in the new namespace.
    """
    if reader.has_synonyms():
        # New format: synonyms.jsonl
        synonym_items: list[dict] = []
        for syn in reader.read_synonyms():
            composite_key = syn.get("composite_key", {})
            # Phase 4: rewrite namespace in composite key
            composite_key = _rewrite_namespace(composite_key, source_namespace, namespace)
            synonym_items.append({
                "target_id": syn["entry_id"],
                "synonym_namespace": namespace,
                "synonym_entity_type": syn.get("entity_type", ""),
                "synonym_composite_key": composite_key,
                "created_by": "wip-toolkit-restore",
            })

        if not synonym_items:
            console.print("  No synonyms to restore")
            return

        batch_size = 100
        total_registered = 0
        for i in range(0, len(synonym_items), batch_size):
            batch = synonym_items[i:i + batch_size]
            try:
                response = client.post("registry", "/synonyms/add", json=batch)
                for r in response.get("results", []):
                    if r.get("status") in ("added", "already_exists"):
                        total_registered += 1
            except WIPClientError as e:
                stats.errors.append(f"Failed to restore synonym batch at index {i}: {e}")
                if not continue_on_error:
                    raise

        stats.synonyms_registered = total_registered
        console.print(f"  Restored {total_registered} synonym(s)")
    else:
        # Legacy format: extract from _registry metadata on entities
        _restore_synonyms_legacy(
            client, namespace, reader, stats, continue_on_error,
            skip_documents=skip_documents, skip_files=skip_files,
            source_namespace=source_namespace,
        )


def _restore_synonyms_legacy(
    client: WIPClient,
    namespace: str,
    reader: ArchiveReader,
    stats: ImportStats,
    continue_on_error: bool,
    *,
    skip_documents: bool = False,
    skip_files: bool = False,
    source_namespace: str | None = None,
) -> None:
    """Restore synonyms from _registry metadata (legacy archive format)."""
    id_field_for_type = {
        "terminologies": "terminology_id",
        "terms": "term_id",
        "templates": "template_id",
        "documents": "document_id",
        "files": "file_id",
    }

    seen_ids: set[str] = set()
    synonym_items: list[dict] = []

    skip_types = set()
    if skip_documents:
        skip_types.add("documents")
    if skip_files:
        skip_types.add("files")

    for entity_type, id_field in id_field_for_type.items():
        if entity_type in skip_types:
            continue
        for entity in reader.read_entities(entity_type):
            eid = entity.get(id_field)
            if not eid or eid in seen_ids:
                continue

            registry = entity.get("_registry", {})
            synonyms = registry.get("synonyms", [])
            if not synonyms:
                continue

            seen_ids.add(eid)
            for syn in synonyms:
                composite_key = syn.get("composite_key", {})
                # Phase 4: rewrite namespace in composite key
                composite_key = _rewrite_namespace(composite_key, source_namespace, namespace)
                synonym_items.append({
                    "target_id": eid,
                    "synonym_namespace": namespace,
                    "synonym_entity_type": syn.get("entity_type", ""),
                    "synonym_composite_key": composite_key,
                    "created_by": "wip-toolkit-restore",
                })

    if not synonym_items:
        console.print("  No synonyms to restore")
        return

    batch_size = 100
    total_registered = 0
    for i in range(0, len(synonym_items), batch_size):
        batch = synonym_items[i:i + batch_size]
        try:
            response = client.post("registry", "/synonyms/add", json=batch)
            for r in response.get("results", []):
                if r.get("status") in ("added", "already_exists"):
                    total_registered += 1
        except WIPClientError as e:
            stats.errors.append(f"Failed to restore synonym batch at index {i}: {e}")
            if not continue_on_error:
                raise

    stats.synonyms_registered = total_registered
    console.print(f"  Restored {total_registered} synonym(s)")


def _rewrite_namespace(
    composite_key: dict[str, Any],
    source_namespace: str | None,
    target_namespace: str,
) -> dict[str, Any]:
    """Rewrite the ``ns`` field in a composite key when namespaces differ.

    Phase 4 of universal synonym resolution: when an archive is imported
    into a different namespace, the ``ns`` component in auto-synonym
    composite keys must be updated so resolution works in the target namespace.
    """
    if not source_namespace or source_namespace == target_namespace:
        return composite_key
    result = dict(composite_key)
    if result.get("ns") == source_namespace:
        result["ns"] = target_namespace
    return result


def _preview(
    reader: ArchiveReader,
    manifest: Any,
    skip_documents: bool,
    skip_files: bool,
) -> None:
    """Preview what would be imported."""
    counts = manifest.counts
    console.print(f"  Terminologies:  {counts.terminologies}")
    console.print(f"  Terms:          {counts.terms}")
    if counts.relationships:
        console.print(f"  Relationships:  {counts.relationships}")
    console.print(f"  Templates:      {counts.templates}")
    if not skip_documents:
        console.print(f"  Documents:     {counts.documents}")
    else:
        console.print("  Documents:     [dim]skipped[/dim]")
    if not skip_files:
        console.print(f"  Files:         {counts.files}")
    else:
        console.print("  Files:         [dim]skipped[/dim]")
    if reader.has_synonyms():
        syn_count = sum(1 for _ in reader.read_synonyms())
        console.print(f"  Synonyms:      {syn_count}")
