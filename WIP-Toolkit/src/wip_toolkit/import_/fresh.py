"""Fresh mode import — creates all entities with new IDs and remaps references.

Optionally registers old→new ID mappings as Registry synonyms.

Uses multi-pass dependency resolution for templates: each pass creates
templates whose dependencies (extends, target_templates) are already
resolved in the mapping table. Repeats until all are created or no
progress is made (circular references).
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from .._progress import ProgressCallback
from .._progress import emit as _emit
from ..archive import ArchiveReader
from ..client import WIPClient, WIPClientError
from ..models import ImportStats, ProgressEvent
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
    progress_callback: ProgressCallback | None = None,
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
    _emit(progress_callback, ProgressEvent(
        phase="phase_namespace",
        message=f"Ensuring namespace '{target_namespace}' exists",
        percent=5.0,
    ))
    _ensure_namespace(client, target_namespace, stats)

    # Step 2: Create terminologies (new IDs)
    console.print("\n[bold cyan]Step 1:[/bold cyan] Creating terminologies (new IDs)")
    terminologies = list(reader.read_entities("terminologies"))
    _emit(progress_callback, ProgressEvent(
        phase="phase_terminologies",
        message=f"Creating {len(terminologies)} terminologies (new IDs)",
        percent=10.0,
        total=len(terminologies),
    ))
    _create_terminologies(client, target_namespace, terminologies, remapper, stats, continue_on_error)

    # Step 3: Create terms (new IDs)
    console.print("\n[bold cyan]Step 2:[/bold cyan] Creating terms (new IDs)")
    terms = list(reader.read_entities("terms"))
    _emit(progress_callback, ProgressEvent(
        phase="phase_terms",
        message=f"Creating {len(terms)} terms (new IDs)",
        percent=20.0,
        total=len(terms),
    ))
    _create_terms(client, target_namespace, terms, remapper, batch_size, stats, continue_on_error)

    # Step 3b: Create term_relations (after terms, using remapped IDs)
    term_relations = list(reader.read_entities("term_relations"))
    if term_relations:
        console.print("\n[bold cyan]Step 2b:[/bold cyan] Creating term_relations (remapped IDs)")
        _emit(progress_callback, ProgressEvent(
            phase="phase_term_relations",
            message=f"Creating {len(term_relations)} term_relations",
            percent=30.0,
            total=len(term_relations),
        ))
        _create_term_relations(client, target_namespace, term_relations, remapper, batch_size, stats, continue_on_error)

    # Step 4: Create and activate templates (multi-pass dependency resolution)
    console.print("\n[bold cyan]Step 3:[/bold cyan] Creating templates (multi-pass)")
    templates = list(reader.read_entities("templates"))
    _emit(progress_callback, ProgressEvent(
        phase="phase_templates",
        message=f"Creating {len(templates)} template version(s) (multi-pass)",
        percent=45.0,
        total=len(templates),
    ))
    _create_templates_multipass(client, target_namespace, templates, remapper, stats, continue_on_error)

    if not skip_files:
        # Step 5: Upload files (before documents so file refs can be remapped)
        console.print("\n[bold cyan]Step 4:[/bold cyan] Uploading files (new IDs)")
        files = list(reader.read_entities("files"))
        _emit(progress_callback, ProgressEvent(
            phase="phase_files",
            message=f"Uploading {len(files)} file(s) (new IDs)",
            percent=60.0,
            total=len(files),
        ))
        _upload_files(client, target_namespace, files, reader, remapper, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping files[/dim]")

    if not skip_documents:
        # Step 6: Create documents with remapped references
        console.print("\n[bold cyan]Step 5:[/bold cyan] Creating documents with remapped references")
        documents = list(reader.read_entities("documents"))
        _emit(progress_callback, ProgressEvent(
            phase="phase_documents",
            message=f"Creating {len(documents)} documents with remapped references",
            percent=75.0,
            total=len(documents),
        ))
        _create_documents(client, target_namespace, documents, remapper, batch_size, stats, continue_on_error)
    else:
        console.print("\n[dim]Skipping documents (--skip-documents)[/dim]")

    # Step 7: Register synonyms
    if register_synonyms:
        console.print("\n[bold cyan]Step 6:[/bold cyan] Registering ID synonyms")
        _emit(progress_callback, ProgressEvent(
            phase="phase_synonyms",
            message="Registering ID synonyms",
            percent=92.0,
        ))
        _register_synonyms(
            client, target_namespace, reader, remapper, stats, continue_on_error,
            source_namespace=stats.source_namespace,
        )

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
                    "description": "Fresh import from backup",
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
    """Create terminologies with new IDs, building the ID map."""
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
                "mutable": t.get("mutable", False),
                "metadata": t.get("metadata"),
                "created_by": "wip-toolkit-fresh",
            }
            result = client.post("def-store", "/terminologies", json=[payload])
            r = result["results"][0]
            if r["status"] == "created":
                new_id = r["id"]
                remapper.add_terminology_mapping(old_id, new_id)
                stats.created.terminologies += 1
            elif r["status"] == "error" and "already exists" in r.get("error", ""):
                stats.skipped.terminologies += 1
            else:
                stats.failed.terminologies += 1
                stats.errors.append(f"Failed to create terminology {old_id}: {r.get('error')}")
                if not continue_on_error:
                    raise WIPClientError(r.get("error", "Unknown error"))
        except WIPClientError:
            if not continue_on_error:
                raise

    console.print(
        f"  Created {stats.created.terminologies}, "
        f"skipped {stats.skipped.terminologies}, "
        f"failed {stats.failed.terminologies} "
        f"({len(remapper.terminology_map)} mapped)"
    )


def _create_terms(
    client: WIPClient,
    namespace: str,
    terms: list[dict],
    remapper: IDRemapper,
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create terms with new IDs, building the ID map."""
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
                    "created_by": "wip-toolkit-fresh",
                })

            try:
                result = client.post(
                    "def-store",
                    f"/terminologies/{new_tid}/terms",
                    json=term_payloads,
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


def _ensure_relation_types(
    client: WIPClient,
    namespace: str,
    term_relations: list[dict],
    stats: ImportStats,
) -> None:
    """Ensure all term_relation types used in the data exist in _ONTOLOGY_RELATIONSHIP_TYPES.

    Custom types (e.g. 'targets') may exist on the source instance but not
    on a fresh target.  This adds any missing types before creating term_relations.
    """
    needed = {r["relation_type"] for r in term_relations}

    # Fetch currently valid types
    try:
        # Try creating a dummy to get the error listing valid types
        result = client.post(
            "def-store", "/ontology/term-relations",
            json=[{
                "source_term_id": "00000000-0000-0000-0000-000000000000",
                "target_term_id": "00000000-0000-0000-0000-000000000001",
                "relation_type": "__probe__",
                "metadata": {},
            }],
            params={"namespace": namespace},
        )
        # Parse valid types from error message
        err = result.get("results", [{}])[0].get("error", "")
        if "Valid types:" in err:
            valid_str = err.split("Valid types:")[1].strip()
            existing = {t.strip() for t in valid_str.split(",")}
        else:
            existing = set()
    except WIPClientError:
        existing = set()

    missing = needed - existing
    if not missing:
        return

    # Find the _ONTOLOGY_RELATIONSHIP_TYPES terminology
    try:
        terminologies = client.fetch_all_paginated(
            "def-store", "/terminologies",
            params={"namespace": namespace},
        )
        ort_id = None
        for t in terminologies:
            if t.get("value") == "_ONTOLOGY_RELATIONSHIP_TYPES":
                ort_id = t["terminology_id"]
                break
        if not ort_id:
            stats.warnings.append(
                f"Cannot register term_relation types {missing}: "
                f"_ONTOLOGY_RELATIONSHIP_TYPES terminology not found"
            )
            return

        # Add missing types
        payloads = [{"value": rt, "label": rt.replace("_", " ").title(), "created_by": "wip-toolkit"} for rt in missing]
        result = client.post("def-store", f"/terminologies/{ort_id}/terms", json=payloads, params={"namespace": namespace})
        added = result.get("succeeded", 0)
        console.print(f"  Registered {added} term_relation type(s): {', '.join(sorted(missing))}")
    except WIPClientError as e:
        stats.warnings.append(f"Failed to register term_relation types {missing}: {e}")


def _create_term_relations(
    client: WIPClient,
    namespace: str,
    term_relations: list[dict],
    remapper: IDRemapper,
    batch_size: int,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create term_relations with remapped term IDs."""
    _ensure_relation_types(client, namespace, term_relations, stats)
    created = 0
    failed = 0
    skipped = 0

    for i in range(0, len(term_relations), batch_size):
        batch = term_relations[i:i + batch_size]
        payloads = []
        for r in batch:
            source = remapper.term_map.get(r["source_term_id"], r["source_term_id"])
            target = remapper.term_map.get(r["target_term_id"], r["target_term_id"])
            payloads.append({
                "source_term_id": source,
                "target_term_id": target,
                "relation_type": r["relation_type"],
                "metadata": r.get("metadata") or {},
            })

        try:
            result = client.post(
                "def-store", "/ontology/term-relations",
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
            stats.errors.append(f"Failed to create term_relation batch at index {i}: {e}")
            if not continue_on_error:
                raise

    stats.created.term_relations = created
    stats.failed.term_relations = failed
    stats.skipped.term_relations = skipped
    msg = f"  Created {created}"
    if skipped:
        msg += f", skipped {skipped}"
    if failed:
        msg += f", failed {failed}"
    console.print(msg)


# ---------------------------------------------------------------------------
# Multi-pass template creation
# ---------------------------------------------------------------------------

def _get_template_deps(tpl: dict) -> set[str]:
    """Extract all old template IDs that this template depends on."""
    deps: set[str] = set()
    if tpl.get("extends"):
        deps.add(tpl["extends"])
    for field in tpl.get("fields", []):
        for tid in field.get("target_templates") or []:
            deps.add(tid)
        if field.get("template_ref"):
            deps.add(field["template_ref"])
        if field.get("array_template_ref"):
            deps.add(field["array_template_ref"])
    return deps


def _create_templates_multipass(
    client: WIPClient,
    namespace: str,
    templates: list[dict],
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
) -> None:
    """Create templates in dependency order using multi-pass resolution.

    Pass 1: create templates with no unresolved template dependencies.
    Pass N: create templates whose deps are now all in the mapping table.
    Repeat until done or no progress (circular references).
    After all created, activate in the same dependency order.
    """
    # Group by template_id (handle multi-version)
    by_id: dict[str, list[dict]] = {}
    for t in templates:
        tid = t["template_id"]
        by_id.setdefault(tid, []).append(t)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    # Build set of all old template IDs in the archive
    archive_tpl_ids = set(by_id.keys())

    # Track what's been created and the order for activation
    pending = set(archive_tpl_ids)
    failed_tids: set[str] = set()  # Templates already counted in stats.failed
    activation_order: list[str] = []
    pass_num = 0

    while pending:
        pass_num += 1
        created_this_pass: list[str] = []

        for old_tid in list(pending):
            versions = by_id[old_tid]
            # Use first version to determine dependencies
            deps = _get_template_deps(versions[0])
            # Only consider deps that are within the archive (not external)
            internal_deps = deps & archive_tpl_ids
            # Check if all internal deps are resolved (have a mapping)
            unresolved = internal_deps - set(remapper.template_map.keys())
            if unresolved:
                continue

            # All deps resolved — create this template
            for idx, tpl in enumerate(versions):
                remapped = remapper.remap_template(tpl)
                try:
                    if idx == 0:
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
                        result = client.post("template-store", "/templates", json=[payload])
                        r = result["results"][0]
                        if r["status"] == "error":
                            stats.failed.templates += 1
                            stats.errors.append(
                                f"Failed to create template {old_tid} v{tpl.get('version', '?')}: {r.get('error')}"
                            )
                            if not continue_on_error:
                                raise WIPClientError(r.get("error", "Unknown error"))
                            failed_tids.add(old_tid)
                            break
                        new_tid = r["id"]
                        remapper.add_template_mapping(old_tid, new_tid)
                    else:
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
                        payload["template_id"] = new_tid
                        result = client.put("template-store", "/templates", json=[payload])
                        r = result["results"][0]
                        if r["status"] == "error":
                            stats.failed.templates += 1
                            stats.errors.append(
                                f"Failed to update template {old_tid} v{tpl.get('version', '?')}: {r.get('error')}"
                            )
                            if not continue_on_error:
                                raise WIPClientError(r.get("error", "Unknown error"))
                            failed_tids.add(old_tid)
                            break

                    stats.created.templates += 1
                except WIPClientError:
                    if not continue_on_error:
                        raise
                    failed_tids.add(old_tid)
                    break

            if old_tid in remapper.template_map:
                created_this_pass.append(old_tid)

        # Remove created templates from pending
        for tid in created_this_pass:
            pending.discard(tid)
            activation_order.append(tid)
        # Remove failed templates from pending (already counted in stats.failed)
        for tid in list(failed_tids):
            pending.discard(tid)

        console.print(
            f"  Pass {pass_num}: created {len(created_this_pass)} template(s), "
            f"{len(pending)} remaining"
        )

        # No progress — circular deps or unresolvable
        if not created_this_pass:
            if pending:
                for old_tid in pending:
                    deps = _get_template_deps(by_id[old_tid][0])
                    unresolved = (deps & archive_tpl_ids) - set(remapper.template_map.keys())
                    stats.errors.append(
                        f"Template {by_id[old_tid][0]['value']} has unresolved deps: "
                        f"{[by_id.get(d, [{}])[0].get('value', d) for d in unresolved]}"
                    )
                stats.failed.templates += len(pending)
            break

    console.print(
        f"  Total: {stats.created.templates} created, {stats.failed.templates} failed "
        f"({len(remapper.template_map)} mapped)"
    )

    # Activate in dependency order (parents before children)
    console.print("\n  Activating templates...")
    activated = 0
    already_active = 0
    for old_tid in activation_order:
        new_tid = remapper.template_map.get(old_tid)
        if not new_tid:
            continue
        try:
            result = client.post(
                "template-store",
                f"/templates/{new_tid}/activate",
                params={"namespace": namespace},
            )
            # Activation returns 200 with errors in body — must check
            activation_errors = result.get("errors", [])
            if activation_errors:
                error_msgs = "; ".join(e.get("message", str(e)) for e in activation_errors)
                msg = f"Failed to activate template {by_id[old_tid][0]['value']}: {error_msgs}"
                stats.errors.append(msg)
                console.print(f"  [red]Activation failed:[/red] {by_id[old_tid][0]['value']}: {error_msgs}")
                if not continue_on_error:
                    raise WIPClientError(msg)
            elif result.get("total_activated", 0) > 0:
                activated += 1
            else:
                already_active += 1
        except WIPClientError as e:
            if e.status_code == 400 and "not 'draft'" in str(e):
                already_active += 1
            else:
                msg = f"Failed to activate template {new_tid} ({by_id[old_tid][0]['value']}): {e}"
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
    """Create documents with remapped references and new IDs.

    Uses multi-pass: documents that fail due to unresolved document
    references are retried after each pass (the referenced documents
    may have been created in the same pass).
    """
    # Group by document_id, sort by version
    by_id: dict[str, list[dict]] = {}
    for d in documents:
        did = d["document_id"]
        by_id.setdefault(did, []).append(d)
    for versions in by_id.values():
        versions.sort(key=lambda x: x.get("version", 1))

    # Flatten in version order
    pending = []
    for versions in by_id.values():
        pending.extend(versions)

    max_passes = 5
    for pass_num in range(1, max_passes + 1):
        failed_docs: list[dict] = []
        pass_created = 0
        pass_failed = 0

        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            items = []
            old_doc_ids = []
            batch_originals = []

            for d in batch:
                remapped = remapper.remap_document(d)
                old_doc_ids.append(d["document_id"])
                batch_originals.append(d)
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
                    "document-store", "/documents",
                    json=items,
                    params={"continue_on_error": "true"},
                )
                for r in result.get("results", []):
                    idx = r.get("index", 0)
                    if r.get("status") in ("created", "updated") and idx < len(old_doc_ids):
                        new_doc_id = r.get("document_id")
                        if new_doc_id:
                            remapper.add_document_mapping(old_doc_ids[idx], new_doc_id)
                        pass_created += 1
                    elif r.get("error") and idx < len(batch_originals):
                        # Store the actual error with the doc for diagnostics
                        orig = batch_originals[idx]
                        orig["_last_error"] = r["error"]
                        failed_docs.append(orig)
                        pass_failed += 1
            except WIPClientError:
                # Entire batch failed — add all to retry
                failed_docs.extend(batch_originals)
                pass_failed += len(batch)

        stats.created.documents += pass_created
        if pass_num > 1 or failed_docs:
            console.print(
                f"  Pass {pass_num}: created {pass_created}, failed {pass_failed}"
            )

        if not failed_docs:
            break

        if pass_failed == len(pending):
            # No progress — stop retrying
            stats.failed.documents += pass_failed
            # Summarize unique errors
            error_counts: dict[str, int] = {}
            for d in failed_docs:
                err = d.get("_last_error", "unknown error")
                error_counts[err] = error_counts.get(err, 0) + 1
            for err, count in sorted(error_counts.items(), key=lambda x: -x[1]):
                stats.errors.append(f"{count} document(s): {err}")
                console.print(f"  [red]{count} document(s):[/red] {err}")
            break

        pending = failed_docs

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
    reader: ArchiveReader,
    remapper: IDRemapper,
    stats: ImportStats,
    continue_on_error: bool,
    source_namespace: str | None = None,
) -> None:
    """Register old→new ID mappings and restore _registry synonyms with remapped IDs."""
    all_items: list[dict] = []

    # Part 1: old→new ID mapping synonyms
    pairs = remapper.all_synonym_pairs()
    for old_id, new_id, entity_type in pairs:
        all_items.append({
            "target_id": new_id,
            "synonym_namespace": namespace,
            "synonym_entity_type": entity_type,
            "synonym_composite_key": {"original_id": old_id},
            "created_by": "wip-toolkit-fresh",
        })

    # Part 2: Restore _registry synonyms with remapped IDs
    all_maps = [
        remapper.terminology_map,
        remapper.term_map,
        remapper.template_map,
        remapper.document_map,
        remapper.file_map,
    ]

    id_field_for_type = {
        "terminologies": "terminology_id",
        "terms": "term_id",
        "templates": "template_id",
        "documents": "document_id",
        "files": "file_id",
    }

    seen_ids: set[str] = set()
    for entity_type in id_field_for_type:
        for entity in reader.read_entities(entity_type):
            id_field = id_field_for_type[entity_type]
            old_eid = entity.get(id_field)
            if not old_eid or old_eid in seen_ids:
                continue

            registry = entity.get("_registry", {})
            synonyms = registry.get("synonyms", [])
            if not synonyms:
                continue

            seen_ids.add(old_eid)

            # Get the new ID for this entity
            new_eid = _remap_id(old_eid, all_maps)

            for syn in synonyms:
                # Remap any ID values in the composite key
                remapped_key = _remap_composite_key(
                    syn.get("composite_key", {}), all_maps,
                )
                # Rewrite namespace in composite key
                remapped_key = _rewrite_namespace(remapped_key, source_namespace, namespace)
                all_items.append({
                    "target_id": new_eid,
                    "synonym_namespace": namespace,
                    "synonym_entity_type": syn.get("entity_type", ""),
                    "synonym_composite_key": remapped_key,
                    "created_by": "wip-toolkit-fresh",
                })

    if not all_items:
        console.print("  No synonyms to register")
        return

    batch_size = 100
    total_registered = 0

    for i in range(0, len(all_items), batch_size):
        batch = all_items[i:i + batch_size]
        try:
            response = client.post("registry", "/synonyms/add", json=batch)
            for r in response.get("results", []):
                if r.get("status") in ("added", "already_exists"):
                    total_registered += 1
        except WIPClientError as e:
            stats.errors.append(f"Failed to register synonym batch at index {i}: {e}")
            if not continue_on_error:
                raise

    stats.synonyms_registered = total_registered
    console.print(f"  Registered {total_registered} synonym(s)")


def _rewrite_namespace(
    composite_key: dict[str, Any],
    source_namespace: str | None,
    target_namespace: str,
) -> dict[str, Any]:
    """Rewrite the ``ns`` field in a composite key when namespaces differ."""
    if not source_namespace or source_namespace == target_namespace:
        return composite_key
    result = dict(composite_key)
    if result.get("ns") == source_namespace:
        result["ns"] = target_namespace
    return result


def _remap_id(old_id: str, all_maps: list[dict[str, str]]) -> str:
    """Look up an ID across all remap maps. Returns remapped ID or original."""
    for m in all_maps:
        if old_id in m:
            return m[old_id]
    return old_id


def _remap_composite_key(
    composite_key: dict[str, Any],
    all_maps: list[dict[str, str]],
) -> dict[str, Any]:
    """Remap any ID values found in a composite key dict."""
    result: dict[str, Any] = {}
    for k, v in composite_key.items():
        if isinstance(v, str):
            result[k] = _remap_id(v, all_maps)
        else:
            result[k] = v
    return result
