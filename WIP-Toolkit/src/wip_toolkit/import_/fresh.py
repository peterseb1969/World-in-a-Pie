"""Fresh mode import — creates all entities with new IDs and remaps references.

Optionally registers old→new ID mappings as Registry synonyms.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from ..archive import ArchiveReader
from ..client import WIPClient, WIPClientError
from ..models import ImportStats
from .remap import IDRemapper

console = Console(stderr=True)


def fresh_import(
    client: WIPClient,
    reader: ArchiveReader,
    target_namespace: str,
    *,
    register_synonyms: bool = False,
    skip_documents: bool = False,
    skip_files: bool = False,
    batch_size: int = 50,
    continue_on_error: bool = False,
    dry_run: bool = False,
) -> ImportStats:
    """Import archive in fresh mode with new IDs and remapped references."""
    stats = ImportStats(mode="fresh", target_namespace=target_namespace)
    manifest = reader.read_manifest()
    stats.source_namespace = manifest.namespace
    remapper = IDRemapper()

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — no changes will be made")
        console.print(f"  Would create entities with new IDs in namespace '{target_namespace}'")
        return stats

    # Step 1: Ensure namespace exists
    _ensure_namespace(client, target_namespace, stats)

    # Step 2: Create terminologies (new IDs)
    console.print("\n[bold cyan]Step 1:[/bold cyan] Creating terminologies (new IDs)")
    terminologies = list(reader.read_entities("terminologies"))
    _create_terminologies(client, target_namespace, terminologies, remapper, stats, continue_on_error)

    # Step 3: Create terms (new IDs)
    console.print("\n[bold cyan]Step 2:[/bold cyan] Creating terms (new IDs)")
    terms = list(reader.read_entities("terms"))
    _create_terms(client, target_namespace, terms, remapper, batch_size, stats, continue_on_error)

    # Step 4: Remap and create templates (new IDs)
    console.print("\n[bold cyan]Step 3:[/bold cyan] Creating templates with remapped references")
    templates = list(reader.read_entities("templates"))
    _create_templates(client, target_namespace, templates, remapper, stats, continue_on_error)

    # Step 5: Activate templates
    console.print("\n[bold cyan]Step 4:[/bold cyan] Activating templates")
    _activate_templates(client, target_namespace, templates, remapper, stats, continue_on_error)

    if not skip_documents:
        # Step 6: Remap and create documents (new IDs)
        console.print("\n[bold cyan]Step 5:[/bold cyan] Creating documents with remapped references")
        documents = list(reader.read_entities("documents"))
        _create_documents(client, target_namespace, documents, remapper, batch_size, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping documents (--skip-documents)[/dim]")

    if not skip_files and not skip_documents:
        # Step 7: Upload files (new IDs)
        console.print("\n[bold cyan]Step 6:[/bold cyan] Uploading files (new IDs)")
        files = list(reader.read_entities("files"))
        _upload_files(client, target_namespace, files, reader, remapper, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping files[/dim]")

    # Step 8: Register synonyms
    if register_synonyms:
        console.print("\n[bold cyan]Step 7:[/bold cyan] Registering ID synonyms")
        _register_synonyms(client, target_namespace, remapper, stats, continue_on_error)

    stats.id_mappings = remapper.total_mappings
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
                    "description": f"Fresh import from backup",
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
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create terminologies, building the ID map.

    Registry composite key dedup is cross-namespace: same {value, label} always
    returns the same terminology_id. If the terminology already exists (from
    another namespace), creation fails but the entity is shared — we map
    old_id → existing_id (often identity).
    """
    for t in terminologies:
        old_id = t["terminology_id"]
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
                "created_by": "wip-toolkit-fresh",
            }
            result = client.post("def-store", "/terminologies", json=payload)
            new_id = result["terminology_id"]
            remapper.add_terminology_mapping(old_id, new_id)
            stats.created.terminologies += 1
        except WIPClientError:
            # Terminology likely already exists (same composite key from another
            # namespace). Look up by value in the source namespace, or fall back
            # to identity mapping since Registry returns the same ID.
            resolved = _resolve_existing_terminology(client, t, namespace)
            if resolved:
                remapper.add_terminology_mapping(old_id, resolved)
                stats.skipped.terminologies += 1
            else:
                # Last resort: identity mapping — Registry composite key dedup
                # means same {value,label} → same ID across namespaces
                remapper.add_terminology_mapping(old_id, old_id)
                stats.skipped.terminologies += 1

    console.print(
        f"  Created {stats.created.terminologies}, "
        f"skipped {stats.skipped.terminologies}, "
        f"failed {stats.failed.terminologies} "
        f"({len(remapper.terminology_map)} mapped)"
    )


def _resolve_existing_terminology(
    client: WIPClient, t: dict, target_namespace: str,
) -> str | None:
    """Try to find an existing terminology by value. Returns ID or None."""
    value = t["value"]
    # Try target namespace first, then source namespace, then unscoped
    for ns in [target_namespace, t.get("_namespace", t.get("namespace"))]:
        if not ns:
            continue
        try:
            existing = client.get(
                "def-store",
                f"/terminologies/by-value/{value}",
                params={"namespace": ns},
            )
            return existing["terminology_id"]
        except WIPClientError:
            continue
    return None


def _create_terms(
    client: WIPClient,
    namespace: str,
    terms: list[dict],
    remapper: IDRemapper,
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create terms, building the ID map.

    Like terminologies, term IDs are derived from composite key
    {terminology_id, value} which is cross-namespace. If a terminology was
    shared (identity-mapped), its terms are also shared.
    """
    # Group terms by OLD terminology_id, then remap to new
    by_old_tid: dict[str, list[dict]] = {}
    for t in terms:
        old_tid = t["terminology_id"]
        by_old_tid.setdefault(old_tid, []).append(t)

    for old_tid, term_group in by_old_tid.items():
        new_tid = remapper.terminology_map.get(old_tid)
        if not new_tid:
            stats.warnings.append(f"No mapping for terminology {old_tid}, skipping {len(term_group)} terms")
            stats.skipped.terms += len(term_group)
            continue

        # If terminology is identity-mapped (shared), terms are also shared
        if new_tid == old_tid:
            for t in term_group:
                remapper.add_term_mapping(t["term_id"], t["term_id"])
            stats.skipped.terms += len(term_group)
            continue

        for i in range(0, len(term_group), batch_size):
            batch = term_group[i:i + batch_size]
            term_payloads = []
            old_ids = []
            for t in batch:
                old_ids.append(t["term_id"])
                # Remap parent_term_id if present
                parent = t.get("parent_term_id")
                if parent:
                    parent = remapper.term_map.get(parent, parent)
                term_payloads.append({
                    "value": t["value"],
                    "aliases": t.get("aliases", []),
                    "label": t.get("label", t["value"]),
                    "description": t.get("description", ""),
                    "sort_order": t.get("sort_order", 0),
                    "parent_term_id": parent,
                    "translations": t.get("translations", []),
                    "metadata": t.get("metadata", {}),
                })

            try:
                result = client.post(
                    "def-store",
                    f"/terminologies/{new_tid}/terms/bulk",
                    json={"terms": term_payloads, "created_by": "wip-toolkit-fresh"},
                )
                # Map old IDs to new IDs from results
                for r in result.get("results", []):
                    idx = r.get("index", 0)
                    if r.get("status") == "created" and idx < len(old_ids):
                        new_term_id = r.get("id")
                        if new_term_id:
                            remapper.add_term_mapping(old_ids[idx], new_term_id)
                stats.created.terms += result.get("succeeded", 0)
                stats.failed.terms += result.get("failed", 0)
            except WIPClientError as e:
                stats.failed.terms += len(batch)
                stats.errors.append(f"Failed to create terms batch for {new_tid}: {e}")
                if not continue_on_error:
                    raise

    console.print(
        f"  Created {stats.created.terms}, failed {stats.failed.terms} "
        f"({len(remapper.term_map)} mapped)"
    )


def _create_templates(
    client: WIPClient,
    namespace: str,
    templates: list[dict],
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create templates with remapped references and new IDs."""
    # Group by template_id, sort by version
    by_id: dict[str, list[dict]] = {}
    for t in templates:
        tid = t["template_id"]
        by_id.setdefault(tid, []).append(t)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    for old_tid, versions in by_id.items():
        for idx, tpl in enumerate(versions):
            # Remap references in the template
            remapped = remapper.remap_template(tpl)

            try:
                if idx == 0:
                    # First version: create (no template_id → new ID generated)
                    payload = {
                        "value": remapped["value"],
                        "label": remapped.get("label", remapped["value"]),
                        "description": remapped.get("description", ""),
                        "namespace": namespace,
                        "extends": remapped.get("extends"),
                        "extends_version": remapped.get("extends_version"),
                        "identity_fields": remapped.get("identity_fields", []),
                        "fields": remapped.get("fields", []),
                        "rules": remapped.get("rules", []),
                        "metadata": remapped.get("metadata"),
                        "reporting": remapped.get("reporting"),
                        "created_by": "wip-toolkit-fresh",
                        "status": "draft",
                    }
                    result = client.post("template-store", "/templates", json=payload)
                    new_tid = result["template_id"]
                    remapper.add_template_mapping(old_tid, new_tid)
                else:
                    # Subsequent versions: PUT to create new version
                    new_tid = remapper.template_map.get(old_tid, old_tid)
                    payload = {
                        "value": remapped["value"],
                        "label": remapped.get("label", remapped["value"]),
                        "description": remapped.get("description", ""),
                        "extends": remapped.get("extends"),
                        "extends_version": remapped.get("extends_version"),
                        "identity_fields": remapped.get("identity_fields", []),
                        "fields": remapped.get("fields", []),
                        "rules": remapped.get("rules", []),
                        "metadata": remapped.get("metadata"),
                        "reporting": remapped.get("reporting"),
                        "updated_by": "wip-toolkit-fresh",
                    }
                    client.put("template-store", f"/templates/{new_tid}", json=payload)

                stats.created.templates += 1
            except WIPClientError as e:
                stats.failed.templates += 1
                stats.errors.append(
                    f"Failed to create template {old_tid} v{tpl.get('version', '?')}: {e}"
                )
                if not continue_on_error:
                    raise

    console.print(
        f"  Created {stats.created.templates}, failed {stats.failed.templates} "
        f"({len(remapper.template_map)} mapped)"
    )


def _activate_templates(
    client: WIPClient,
    namespace: str,
    templates: list[dict],
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Activate all draft templates using new IDs."""
    seen: set[str] = set()
    activated = 0
    already_active = 0

    for t in templates:
        old_tid = t["template_id"]
        new_tid = remapper.template_map.get(old_tid)
        if not new_tid or new_tid in seen:
            continue
        seen.add(new_tid)

        try:
            client.post(
                "template-store",
                f"/templates/{new_tid}/activate",
                params={"namespace": namespace},
            )
            activated += 1
        except WIPClientError as e:
            if e.status_code == 400 and "not 'draft'" in str(e):
                already_active += 1
            else:
                msg = f"Failed to activate template {new_tid} (was {old_tid}): {e}"
                stats.warnings.append(msg)
                if not continue_on_error:
                    raise

    msg = f"  Activated {activated} template(s)"
    if already_active:
        msg += f" ({already_active} already active from cascade)"
    console.print(msg)


def _create_documents(
    client: WIPClient,
    namespace: str,
    documents: list[dict],
    remapper: IDRemapper,
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create documents with remapped references and new IDs."""
    # Group by document_id, sort by version
    by_id: dict[str, list[dict]] = {}
    for d in documents:
        did = d["document_id"]
        by_id.setdefault(did, []).append(d)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    # Flatten in version order
    ordered_docs = []
    for versions in by_id.values():
        ordered_docs.extend(versions)

    for i in range(0, len(ordered_docs), batch_size):
        batch = ordered_docs[i:i + batch_size]
        items = []
        old_doc_ids = []

        for d in batch:
            remapped = remapper.remap_document(d)
            old_doc_ids.append(d["document_id"])
            items.append({
                "template_id": remapped["template_id"],
                "template_version": remapped.get("template_version"),
                "namespace": namespace,
                "data": remapped["data"],
                "created_by": "wip-toolkit-fresh",
                "metadata": remapped.get("metadata"),
            })

        try:
            result = client.post(
                "document-store", "/documents/bulk",
                json={"items": items, "continue_on_error": continue_on_error},
            )
            for r in result.get("results", []):
                idx = r.get("index", 0)
                if r.get("status") in ("created", "updated") and idx < len(old_doc_ids):
                    new_doc_id = r.get("document_id")
                    if new_doc_id:
                        remapper.add_document_mapping(old_doc_ids[idx], new_doc_id)
                elif r.get("error") and idx < len(old_doc_ids):
                    stats.errors.append(
                        f"Document {old_doc_ids[idx]}: {r['error']}"
                    )
            stats.created.documents += result.get("created", 0) + result.get("updated", 0)
            stats.failed.documents += result.get("failed", 0)
        except WIPClientError as e:
            stats.failed.documents += len(batch)
            stats.errors.append(f"Failed to create document batch at index {i}: {e}")
            if not continue_on_error:
                raise

    console.print(
        f"  Created {stats.created.documents}, failed {stats.failed.documents} "
        f"({len(remapper.document_map)} mapped)"
    )


def _upload_files(
    client: WIPClient,
    namespace: str,
    files: list[dict],
    reader: ArchiveReader,
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Upload files with new IDs."""
    blobs = set(reader.list_blobs())

    for f in files:
        old_fid = f["file_id"]
        if old_fid not in blobs:
            stats.skipped.files += 1
            continue

        blob_data = reader.read_blob(old_fid)
        if not blob_data:
            stats.skipped.files += 1
            continue

        try:
            data: dict[str, str] = {"namespace": namespace}
            if f.get("metadata", {}).get("description"):
                data["description"] = f["metadata"]["description"]
            if f.get("metadata", {}).get("tags"):
                data["tags"] = ",".join(f["metadata"]["tags"])
            if f.get("metadata", {}).get("category"):
                data["category"] = f["metadata"]["category"]

            result = client.post_form(
                "document-store", "/files",
                data=data,
                files={"file": (f["filename"], blob_data, f.get("content_type", "application/octet-stream"))},
            )
            new_fid = result.get("file_id")
            if new_fid:
                remapper.add_file_mapping(old_fid, new_fid)
            stats.created.files += 1
        except WIPClientError as e:
            stats.failed.files += 1
            stats.errors.append(f"Failed to upload file {old_fid}: {e}")
            if not continue_on_error:
                raise

    console.print(
        f"  Uploaded {stats.created.files}, skipped {stats.skipped.files}, "
        f"failed {stats.failed.files} ({len(remapper.file_map)} mapped)"
    )


def _register_synonyms(
    client: WIPClient,
    namespace: str,
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Register all old→new ID mappings as Registry synonyms."""
    pairs = remapper.all_synonym_pairs()
    if not pairs:
        console.print("  No synonyms to register")
        return

    batch_size = 100
    total_registered = 0

    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        items = [
            {
                "target_id": new_id,
                "synonym_namespace": namespace,
                "synonym_entity_type": entity_type,
                "synonym_composite_key": {"original_id": old_id},
                "created_by": "wip-toolkit-fresh",
            }
            for old_id, new_id, entity_type in batch
        ]

        try:
            results = client.post("registry", "/synonyms/add", json=items)
            for r in results if isinstance(results, list) else []:
                if r.get("status") in ("added", "already_exists"):
                    total_registered += 1
        except WIPClientError as e:
            stats.errors.append(f"Failed to register synonym batch at index {i}: {e}")
            if not continue_on_error:
                raise

    stats.synonyms_registered = total_registered
    console.print(f"  Registered {total_registered} synonym(s)")
