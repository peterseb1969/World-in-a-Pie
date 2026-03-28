"""Namespace management API endpoints.

This module handles user-facing namespaces (e.g., "wip", "dev", "prod").
Each namespace has configurable ID algorithms per entity type.
"""

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..models.api_models import (
    ExportResponse,
    ImportResponse,
    NamespaceCreate,
    NamespaceResponse,
    NamespaceStatsResponse,
    NamespaceUpdate,
)
from ..models.entry import RegistryEntry
from ..models.id_algorithm import VALID_ENTITY_TYPES
from ..models.namespace import Namespace
from ..services.auth import require_admin_key, require_api_key

router = APIRouter()


def namespace_to_response(ns: Namespace) -> NamespaceResponse:
    """Convert a Namespace document to a response model."""
    # Build id_config for response, merging defaults
    id_config: dict[str, Any] = {}
    for entity_type in VALID_ENTITY_TYPES:
        config = ns.get_id_algorithm(entity_type)
        id_config[entity_type] = config.model_dump()

    return NamespaceResponse(
        prefix=ns.prefix,
        description=ns.description,
        isolation_mode=ns.isolation_mode,
        allowed_external_refs=ns.allowed_external_refs,
        id_config=id_config,
        deletion_mode=ns.deletion_mode,
        status=ns.status,
        created_at=ns.created_at,
        created_by=ns.created_by,
        updated_at=ns.updated_at,
        updated_by=ns.updated_by,
    )


@router.get(
    "",
    response_model=list[NamespaceResponse],
    summary="List namespaces"
)
async def list_namespaces(
    include_archived: bool = Query(False, description="Include archived namespaces"),
    api_key: str = Depends(require_api_key)
) -> list[NamespaceResponse]:
    """List all namespaces."""
    if include_archived:
        query = {"status": {"$ne": "deleted"}}
    else:
        query = {"status": "active"}

    namespaces = await Namespace.find(query).to_list()
    return [namespace_to_response(ns) for ns in namespaces]


@router.get(
    "/{prefix}",
    response_model=NamespaceResponse,
    summary="Get namespace by prefix"
)
async def get_namespace(
    prefix: str,
    api_key: str = Depends(require_api_key)
) -> NamespaceResponse:
    """Get a specific namespace by its prefix."""
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")
    return namespace_to_response(ns)


@router.get(
    "/{prefix}/stats",
    response_model=NamespaceStatsResponse,
    summary="Get namespace statistics"
)
async def get_namespace_stats(
    prefix: str,
    api_key: str = Depends(require_api_key)
) -> NamespaceStatsResponse:
    """Get entity counts for each entity type in the namespace."""
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    entity_counts = {}
    for entity_type in VALID_ENTITY_TYPES:
        count = await RegistryEntry.find({
            "namespace": prefix,
            "entity_type": entity_type,
            "status": "active"
        }).count()
        entity_counts[entity_type] = count

    return NamespaceStatsResponse(
        prefix=ns.prefix,
        description=ns.description,
        isolation_mode=ns.isolation_mode,
        deletion_mode=ns.deletion_mode,
        status=ns.status,
        entity_counts=entity_counts,
    )


@router.get(
    "/{prefix}/id-config",
    summary="Get namespace ID config"
)
async def get_namespace_id_config(
    prefix: str,
    api_key: str = Depends(require_api_key)
) -> dict[str, Any]:
    """Get ID algorithm configuration for a namespace. Services cache this at startup."""
    ns = await Namespace.find_one({"prefix": prefix, "status": "active"})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    config = {}
    for entity_type in VALID_ENTITY_TYPES:
        algo = ns.get_id_algorithm(entity_type)
        config[entity_type] = algo.model_dump()

    return config


@router.post(
    "",
    response_model=NamespaceResponse,
    summary="Create namespace"
)
async def create_namespace(
    request: NamespaceCreate,
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """
    Create a new namespace with optional ID algorithm configuration.

    If id_config is not provided, defaults to UUID7 for all entity types.
    """
    existing = await Namespace.find_one({"prefix": request.prefix})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Namespace already exists: {request.prefix}"
        )

    ns = Namespace(
        prefix=request.prefix,
        description=request.description,
        isolation_mode=request.isolation_mode,
        allowed_external_refs=request.allowed_external_refs,
        id_config=request.id_config or {},
        deletion_mode=request.deletion_mode,
        created_by=request.created_by,
    )
    await ns.create()

    return namespace_to_response(ns)


@router.put(
    "/{prefix}",
    response_model=NamespaceResponse,
    summary="Update namespace"
)
async def update_namespace(
    prefix: str,
    request: NamespaceUpdate,
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """Update a namespace's configuration."""
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None and field != "updated_by":
            setattr(ns, field, value)

    ns.updated_at = datetime.now(UTC)
    ns.updated_by = request.updated_by
    await ns.save()

    return namespace_to_response(ns)


@router.post(
    "/{prefix}/archive",
    response_model=NamespaceResponse,
    summary="Archive namespace"
)
async def archive_namespace(
    prefix: str,
    archived_by: str = Query(None, description="User archiving the namespace"),
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """Archive a namespace. No new entries can be created."""
    if prefix == "wip":
        raise HTTPException(status_code=400, detail="Cannot archive the default 'wip' namespace")

    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    ns.status = "archived"
    ns.updated_at = datetime.now(UTC)
    ns.updated_by = archived_by
    await ns.save()

    return namespace_to_response(ns)


@router.post(
    "/{prefix}/restore",
    response_model=NamespaceResponse,
    summary="Restore archived namespace"
)
async def restore_namespace(
    prefix: str,
    restored_by: str = Query(None, description="User restoring the namespace"),
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """Restore an archived namespace."""
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    if ns.status != "archived":
        raise HTTPException(status_code=400, detail=f"Namespace is not archived: {prefix}")

    ns.status = "active"
    ns.updated_at = datetime.now(UTC)
    ns.updated_by = restored_by
    await ns.save()

    return namespace_to_response(ns)


@router.post(
    "/initialize-wip",
    response_model=NamespaceResponse,
    summary="Initialize WIP namespace"
)
async def initialize_wip_namespace(
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """Initialize the default 'wip' namespace with UUID7 for all entity types."""
    existing = await Namespace.find_one({"prefix": "wip"})
    if existing:
        return namespace_to_response(existing)

    ns = Namespace(
        prefix="wip",
        description="Default World In a Pie namespace",
        isolation_mode="open",
        id_config={},  # Empty = defaults to UUID7 for everything
    )
    await ns.create()

    return namespace_to_response(ns)


# =============================================================================
# Export/Import Endpoints
# =============================================================================

@router.post(
    "/{prefix}/export",
    response_model=ExportResponse,
    summary="Export namespace"
)
async def export_namespace(
    prefix: str,
    include_files: bool = Query(False, description="Include binary file content"),
    api_key: str = Depends(require_admin_key)
) -> ExportResponse:
    """Export a namespace to a downloadable archive."""
    from ..services.export_service import ExportService

    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    def_store_url = os.getenv("DEF_STORE_URL", "http://localhost:8002")
    template_store_url = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
    document_store_url = os.getenv("DOCUMENT_STORE_URL", "http://localhost:8004")

    export_service = ExportService(
        def_store_url=def_store_url,
        template_store_url=template_store_url,
        document_store_url=document_store_url,
        api_key=api_key,
    )

    try:
        zip_path, stats = await export_service.export_namespace(
            namespace=ns, include_files=include_files,
        )
        export_id = os.path.basename(zip_path).replace(".zip", "")
        return ExportResponse(
            export_id=export_id,
            prefix=prefix,
            download_url=f"/api/registry/namespaces/exports/{export_id}",
            stats=stats,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e!s}")


@router.get(
    "/exports/{export_id}",
    summary="Download export file"
)
async def download_export(
    export_id: str,
    api_key: str = Depends(require_api_key)
):
    """Download an exported namespace archive."""
    import tempfile

    zip_path = os.path.join(tempfile.gettempdir(), f"{export_id}.zip")

    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="Export file not found or expired")

    return FileResponse(
        path=zip_path,
        filename=f"{export_id}.zip",
        media_type="application/zip",
    )


@router.post(
    "/import",
    response_model=ImportResponse,
    summary="Import namespace"
)
async def import_namespace(
    file: UploadFile = File(..., description="Export ZIP file to import"),
    target_prefix: str = Query(None, description="Optional new prefix"),
    mode: str = Query("create", description="Import mode: create, merge, replace"),
    imported_by: str = Query(None, description="User performing import"),
    api_key: str = Depends(require_admin_key)
) -> ImportResponse:
    """Import a namespace from an exported archive."""
    import shutil
    import tempfile

    from ..services.import_service import ImportService

    if mode not in ("create", "merge", "replace"):
        raise HTTPException(status_code=400, detail="Invalid mode. Must be one of: create, merge, replace")

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        shutil.copyfileobj(file.file, temp_file)
        temp_file.close()

        def_store_url = os.getenv("DEF_STORE_URL", "http://localhost:8002")
        template_store_url = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
        document_store_url = os.getenv("DOCUMENT_STORE_URL", "http://localhost:8004")

        import_service = ImportService(
            def_store_url=def_store_url,
            template_store_url=template_store_url,
            document_store_url=document_store_url,
            api_key=api_key,
        )

        ns, stats = await import_service.import_namespace(
            zip_path=temp_file.name,
            target_prefix=target_prefix,
            mode=mode,
            imported_by=imported_by,
        )

        import json
        import zipfile
        source_prefix = None
        with zipfile.ZipFile(temp_file.name, "r") as zf, zf.open("manifest.json") as mf:
            manifest = json.load(mf)
            source_prefix = manifest.get("prefix")

        return ImportResponse(
            prefix=ns.prefix,
            mode=mode,
            stats=stats,
            source_prefix=source_prefix if source_prefix != ns.prefix else None,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {e!s}")
    finally:
        os.unlink(temp_file.name)
