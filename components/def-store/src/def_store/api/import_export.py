"""API endpoints for import/export operations."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
    resolve_accessible_namespaces,
    resolve_or_404,
)

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
    terminology_id = await resolve_or_404(terminology_id, "terminology", namespace=namespace, param_name="terminology_id")

    # CASE-384 — gate export by read permission on the terminology's
    # namespace. Without this, any authenticated key could exfiltrate
    # any terminology by ID.
    from ..models.terminology import Terminology as _T
    existing = await _T.find_one({"terminology_id": terminology_id})
    if existing:
        await check_namespace_permission(identity, existing.namespace, "read")

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
    # CASE-384 — restrict to namespaces the caller can read. Superadmin
    # gets None back from resolve_accessible_namespaces and the service
    # treats that as "no filter" (all namespaces). Non-admin scoped keys
    # get only their own namespaces' terminologies.
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
    format: str = Query("json", description="Import format: json, csv"),
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
    created_by: str | None = Query(None, description="User performing import"),
    batch_size: int = Query(
        1000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100,
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

    CSV format requires terminology_value and terminology_label in the data,
    plus csv_content with columns: value, label, description, sort_order.
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
        # Platform convention: a key scoped to exactly one namespace
        # implies it; multi-namespace keys must say where the import goes.
        key_namespaces = (identity.raw_claims or {}).get("namespaces")
        if isinstance(key_namespaces, list) and len(key_namespaces) == 1:
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
            "created_by": created_by,
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
        msg = str(e)
        status = 409 if "already exists" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from e
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
    batch_size: int = Query(1000, description="Terms per MongoDB batch"),
    registry_batch_size: int = Query(50, description="Terms per registry HTTP call"),
    relation_batch_size: int = Query(500, description="Relations per batch"),
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    created_by: str | None = Query(None, description="User performing import"),
    identity: UserIdentity = Depends(require_api_key),
):
    """
    Import an OBO Graph JSON ontology (HP, GO, CHEBI, etc.).

    Accepts standard OBO Graph JSON format with `graphs[0].nodes[]` and
    `graphs[0].edges[]`. Parses nodes into terms and edges into relations.

    Auto-detects the ontology prefix and metadata from the graph structure.
    For large ontologies, use the CLI script `scripts/import_obo_graph.py` instead.
    """
    try:
        await check_namespace_permission(identity, namespace, "write")

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
            "created_by": created_by,
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
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    created_by: str | None = Query(None, description="User performing import"),
    batch_size: int = Query(
        1000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100,
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
    # CASE-384 — URL imports don't expose the destination namespace at the
    # API layer (it comes from the fetched payload). The service-side
    # validation will surface 4xx if the payload is missing namespace,
    # but we can't pre-authorise without fetching the URL ourselves —
    # which would double the work. Conservative compromise: require the
    # caller to be a privileged identity (admin/services group) until
    # the service grows a "pre-flight namespace extraction" hook.
    # Filed as a follow-up consideration in CASE-384.
    from wip_auth.permissions import _is_superadmin
    if not _is_superadmin(identity):
        # Non-admin URL imports are refused. Operators with scoped keys
        # should fetch the URL locally, then call POST /import with the
        # body so the namespace is visible at the API layer.
        raise HTTPException(
            status_code=403,
            detail=(
                "URL imports require admin privileges. Fetch the URL and "
                "POST the body to /import-export/import to allow per-namespace "
                "permission checking."
            ),
        )

    try:
        options = {
            "skip_duplicates": skip_duplicates,
            "update_existing": update_existing,
            "created_by": created_by,
            "terminology_value": terminology_value,
            "terminology_label": terminology_label,
            "batch_size": batch_size,
            "registry_batch_size": registry_batch_size,
        }

        result = await ImportExportService.import_from_url(
            url=url,
            format=format,
            options=options
        )

        return JSONResponse(content=result)

    except ValueError as e:
        msg = str(e)
        status = 409 if "already exists" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {e!s}") from e
