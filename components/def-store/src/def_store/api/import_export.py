"""API endpoints for import/export operations."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
    check_readable,
    miss_detail,
    resolve_accessible_namespaces,
    resolve_or_404,
)

from ..services.errors import error_fields
from ..services.import_export import ImportExportService
from .auth import require_api_key

router = APIRouter(prefix="/import-export", tags=["Import/Export"])


@router.get(
    "/export/{terminology_id}",
    summary="Export a terminology"
)
async def export_terminology(
    terminology_id: str,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    format: str = Query("json", description="Export format: json, csv"),
    include_metadata: bool = Query(True, description="Include metadata"),
    include_inactive: bool = Query(False, description="Include inactive terms"),
    include_relations: bool = Query(False, description="Include ontology relations"),
    languages: str | None = Query(None, description="Comma-separated language codes"),
    identity: UserIdentity = Depends(require_api_key)
):
    """
    Export a terminology with all its terms.

    Supports JSON and CSV formats. Use include_relations=true to include
    ontology relations (is_a, part_of, etc.) in JSON exports.
    """
    raw_id = terminology_id
    terminology_id = await resolve_or_404(terminology_id, "terminology", namespace=namespace, param_name="terminology_id")

    # CASE-384 — gate export by read permission on the terminology's
    # namespace. Without this, any authenticated key could exfiltrate
    # any terminology by ID.
    from ..models.terminology import Terminology as _T
    existing = await _T.find_one({"terminology_id": terminology_id})
    if existing:
        await check_readable(identity, existing.namespace, miss_detail(
            raw_id, "terminology", namespace, "Terminology not found", "terminology_id",
        ))

    try:
        language_list = languages.split(",") if languages else None

        result = await ImportExportService.export_terminology(
            terminology_id=terminology_id,
            format=format,
            include_metadata=include_metadata,
            include_inactive=include_inactive,
            include_relations=include_relations,
            languages=language_list
        )

        if format == "csv":
            return PlainTextResponse(
                content=result["csv_content"],
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename={result['terminology']['value']}.csv"
                }
            )

        return JSONResponse(content=result)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get(
    "/export",
    summary="Export all terminologies"
)
async def export_all_terminologies(
    format: str = Query("json", description="Export format: json"),
    include_inactive: bool = Query(False, description="Include inactive terminologies"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Export all terminologies with their terms."""
    # Restrict to the namespaces the caller can read (for an administrator,
    # every namespace but one locked for deletion).
    accessible = await resolve_accessible_namespaces(identity)

    results = await ImportExportService.export_all_terminologies(
        format=format,
        include_inactive=include_inactive,
        namespaces=accessible,
    )
    return JSONResponse(content={"terminologies": results, "count": len(results)})


@router.post(
    "/import",
    summary="Import a terminology"
)
async def import_terminology(
    data: dict[str, Any] = Body(...),
    format: str = Query(
        "json",
        description=(
            "Import format: json, csv. In a csv import, a text cell that starts "
            "with an apostrophe followed by =, +, -, @, tab, carriage return or "
            "another apostrophe loses that one apostrophe: it is the escape WIP's "
            "CSV exports write so a spreadsheet does not run the cell as a "
            "formula. To import such a value literally, double the apostrophe."
        ),
    ),
    namespace: str | None = Query(
        None,
        description=(
            "Destination namespace. Required for CSV imports unless the "
            "API key is scoped to exactly one namespace (then derived). "
            "For JSON imports it must match terminology.namespace when "
            "both are provided."
        ),
    ),
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    batch_size: int = Query(
        1000, ge=1, le=10_000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100, ge=1, le=1_000,
        description="Number of terms per registry HTTP call (default 100). "
        "Reduce if experiencing timeouts on large imports."
    ),
    identity: UserIdentity = Depends(require_api_key)
):
    """
    Import a terminology with terms.

    JSON format expected:
    ```json
    {
      "terminology": {
        "value": "DOC_STATUS",
        "label": "Document Status",
        "description": "...",
        "case_sensitive": false
      },
      "terms": [
        {"value": "draft", "label": "Draft"},
        {"value": "approved", "label": "Approved"}
      ]
    }
    ```

    Every term item is checked before anything is written: a malformed one
    refuses the import (400) with nothing created. A term carries its
    lifecycle: `status` (active, deprecated or inactive),
    `deprecated_reason`, and its parent and replacement by value
    (`parent_term_value`, `replaced_by_term_value`) — the import creates
    terms parents-first and then deprecates or deactivates them, as an
    export with include_inactive=true describes them. An import into an
    existing terminology adds terms only; a retired one is refused
    (restore it first). Relations whose ends are not both terms of the
    imported terminology are counted as `dropped`, with reasons.

    CSV format requires terminology_value and terminology_label in the data,
    plus csv_content with columns: value, label, description, sort_order,
    status. A CSV with rows but no `value` column is refused.
    The destination namespace comes from the `namespace` query parameter
    (or is derived when the key is scoped to a single namespace) — the CSV
    payload itself carries no namespace field by design.

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)

    If you experience timeouts, try reducing `registry_batch_size` to 50 or lower.
    """
    # Resolve the destination namespace from its three legitimate sources,
    # then gate the write on it UNCONDITIONALLY. The old form only checked
    # when data.terminology.namespace existed — the CSV payload is flat by
    # design (no terminology block), so every CSV import silently skipped
    # the write-permission check.
    body_namespace: str | None = None
    if isinstance(data, dict):
        terminology_block = data.get("terminology")
        if isinstance(terminology_block, dict):
            ns_val = terminology_block.get("namespace")
            if isinstance(ns_val, str):
                body_namespace = ns_val

    # Two explicit sources must agree — silently preferring one would let a
    # payload smuggle the write past a differently-scoped query param.
    if namespace and body_namespace and namespace != body_namespace:
        raise HTTPException(
            status_code=400,
            detail=(
                f"namespace query parameter ({namespace!r}) contradicts "
                f"terminology.namespace in the body ({body_namespace!r})"
            ),
        )

    target_namespace = namespace or body_namespace
    if target_namespace is None:
        # Platform convention: an API key scoped to exactly one namespace
        # implies it (only a key's record carries a scope; another
        # identity's namespaces claim is not one); multi-namespace keys
        # must say where the import goes.
        from wip_auth.permissions import _key_scope

        key_namespaces = _key_scope(identity)
        if key_namespaces is not None and len(key_namespaces) == 1:
            target_namespace = key_namespaces[0]

    if target_namespace is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Destination namespace is required: pass ?namespace=, "
                "include terminology.namespace in a JSON body, or use an "
                "API key scoped to a single namespace"
            ),
        )

    await check_namespace_permission(identity, target_namespace, "write")

    try:
        options = {
            "skip_duplicates": skip_duplicates,
            "update_existing": update_existing,
            "batch_size": batch_size,
            "registry_batch_size": registry_batch_size,
        }

        result = await ImportExportService.import_terminology(
            data=data,
            format=format,
            options=options,
            namespace=target_namespace,
        )

        return JSONResponse(content=result)

    except ValueError as e:
        fields = error_fields(e)
        status = 409 if fields["error_code"] == "already_exists" else 400
        raise HTTPException(status_code=status, detail=fields["error"]) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {e!s}") from e


@router.post(
    "/import-ontology",
    summary="Import OBO Graph JSON ontology"
)
async def import_ontology(
    data: dict[str, Any] = Body(...),
    terminology_value: str | None = Query(None, description="WIP terminology value (e.g., HPO, GO). Auto-detected if not set."),
    terminology_label: str | None = Query(None, description="Display label. Auto-detected if not set."),
    namespace: str = Query(..., description="Target namespace"),
    prefix_filter: str | None = Query(None, description="Only import nodes with this OBO prefix"),
    include_deprecated: bool = Query(False, description="Import deprecated/obsolete nodes"),
    max_synonyms: int = Query(10, description="Max aliases per term"),
    batch_size: int = Query(1000, ge=1, le=10_000, description="Terms per MongoDB batch"),
    registry_batch_size: int = Query(50, ge=1, le=1_000, description="Terms per registry HTTP call"),
    relation_batch_size: int = Query(500, ge=1, le=10_000, description="Relations per batch"),
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    identity: UserIdentity = Depends(require_api_key),
):
    """
    Import an OBO Graph JSON ontology (HP, GO, CHEBI, etc.).

    Accepts standard OBO Graph JSON format with `graphs[0].nodes[]` and
    `graphs[0].edges[]`. Parses nodes into terms and edges into relations.

    Auto-detects the ontology prefix and metadata from the graph structure.
    For large ontologies, use the CLI script `scripts/import_obo_graph.py` instead.
    """
    # Outside the try: a refusal (403/404) or a Registry outage (503) keeps
    # its own status instead of turning into the 500 below.
    await check_namespace_permission(identity, namespace, "write")

    try:
        if "graphs" not in data or not data["graphs"]:
            raise ValueError("Invalid OBO Graph JSON: missing 'graphs' array")

        options = {
            "terminology_value": terminology_value,
            "terminology_label": terminology_label,
            "namespace": namespace,
            "prefix_filter": prefix_filter,
            "include_deprecated": include_deprecated,
            "max_synonyms": max_synonyms,
            "batch_size": batch_size,
            "registry_batch_size": registry_batch_size,
            "relation_batch_size": relation_batch_size,
            "skip_duplicates": skip_duplicates,
            "update_existing": update_existing,
        }

        result = await ImportExportService.import_ontology(data, options)
        return JSONResponse(content=result)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ontology import failed: {e!s}") from e


@router.post(
    "/import/url",
    summary="Import from URL"
)
async def import_from_url(
    url: str = Query(..., description="URL to fetch terminology from"),
    format: str = Query("json", description="Expected format: json, csv"),
    terminology_value: str | None = Query(None, description="Value for CSV import"),
    terminology_label: str | None = Query(None, description="Label for CSV import"),
    namespace: str | None = Query(
        None,
        description=(
            "Destination namespace. Required for CSV; for JSON it wins over "
            "the fetched terminology.namespace."
        ),
    ),
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    batch_size: int = Query(
        1000, ge=1, le=10_000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100, ge=1, le=1_000,
        description="Number of terms per registry HTTP call (default 100). "
        "Reduce if experiencing timeouts on large imports."
    ),
    identity: UserIdentity = Depends(require_api_key)
):
    """
    Import a terminology from a URL.

    Fetches the data from the URL and imports it.

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)
    """
    # A URL import makes def-store fetch a URL the caller chooses, and its
    # destination may only be known once the payload is fetched. It is
    # therefore open to superadmins (the admin groups) only; a scoped key
    # fetches the document itself and POSTs it to /import, where the
    # namespace is checked before anything is read.
    from wip_auth import is_superadmin
    if not is_superadmin(identity):
        raise HTTPException(
            status_code=403,
            detail=(
                "URL imports require admin privileges. Fetch the URL and "
                "POST the body to /import-export/import to allow per-namespace "
                "permission checking."
            ),
        )
    if namespace:
        await check_namespace_permission(identity, namespace, "write")

    try:
        options = {
            "skip_duplicates": skip_duplicates,
            "update_existing": update_existing,
            "terminology_value": terminology_value,
            "terminology_label": terminology_label,
            "batch_size": batch_size,
            "registry_batch_size": registry_batch_size,
        }

        result = await ImportExportService.import_from_url(
            url=url,
            format=format,
            options=options,
            namespace=namespace,
        )

        return JSONResponse(content=result)

    except ValueError as e:
        fields = error_fields(e)
        status = 409 if fields["error_code"] == "already_exists" else 400
        raise HTTPException(status_code=status, detail=fields["error"]) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {e!s}") from e
