"""Namespace management API endpoints.

This module handles user-facing namespaces (e.g., "wip", "dev", "prod").
Each namespace automatically creates 5 ID pools for ID generation.
"""

import os
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException, Query, Depends, UploadFile, File
from fastapi.responses import FileResponse

from ..models.id_pool import IdPool, IdGeneratorConfig, IdGeneratorType
from ..models.namespace import Namespace
from ..models.entry import RegistryEntry
from ..models.api_models import (
    NamespaceCreate,
    NamespaceUpdate,
    NamespaceResponse,
    NamespaceStatsResponse,
    ExportResponse,
    ImportRequest,
    ImportResponse,
)
from ..services.auth import require_api_key, require_admin_key

router = APIRouter()


# ID pool configuration for each entity type in a namespace
ID_POOL_CONFIGS = {
    "terminologies": {
        "name_suffix": "Terminologies",
        "description_suffix": "terminology IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="TERM-"),
    },
    "terms": {
        "name_suffix": "Terms",
        "description_suffix": "individual term IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="T-"),
    },
    "templates": {
        "name_suffix": "Templates",
        "description_suffix": "template IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="TPL-"),
    },
    "documents": {
        "name_suffix": "Documents",
        "description_suffix": "document IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.UUID7),
    },
    "files": {
        "name_suffix": "Files",
        "description_suffix": "file attachment IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="FILE-"),
    },
}


def namespace_to_response(ns: Namespace) -> NamespaceResponse:
    """Convert a Namespace document to a response model."""
    return NamespaceResponse(
        prefix=ns.prefix,
        description=ns.description,
        isolation_mode=ns.isolation_mode,
        allowed_external_refs=ns.allowed_external_refs,
        status=ns.status,
        created_at=ns.created_at,
        created_by=ns.created_by,
        updated_at=ns.updated_at,
        updated_by=ns.updated_by,
        terminologies_pool=ns.terminologies_pool,
        terms_pool=ns.terms_pool,
        templates_pool=ns.templates_pool,
        documents_pool=ns.documents_pool,
        files_pool=ns.files_pool,
    )


@router.get(
    "",
    response_model=List[NamespaceResponse],
    summary="List namespaces"
)
async def list_namespaces(
    include_archived: bool = Query(False, description="Include archived namespaces"),
    api_key: str = Depends(require_api_key)
) -> List[NamespaceResponse]:
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
    """Get entity counts for each ID pool in the namespace."""
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    # Count entries in each ID pool
    pool_counts = {}
    for pool_id in ns.get_all_pools():
        count = await RegistryEntry.find({"primary_namespace": pool_id}).count()
        pool_counts[pool_id] = count

    return NamespaceStatsResponse(
        prefix=ns.prefix,
        description=ns.description,
        isolation_mode=ns.isolation_mode,
        status=ns.status,
        pools=pool_counts,
    )


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
    Create a new namespace.

    This creates the namespace and all 5 associated ID pools:
    - {prefix}-terminologies
    - {prefix}-terms
    - {prefix}-templates
    - {prefix}-documents
    - {prefix}-files
    """
    # Check if namespace already exists
    existing = await Namespace.find_one({"prefix": request.prefix})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Namespace already exists: {request.prefix}"
        )

    # Create the namespace
    ns = Namespace(
        prefix=request.prefix,
        description=request.description,
        isolation_mode=request.isolation_mode,
        allowed_external_refs=request.allowed_external_refs,
        created_by=request.created_by,
    )
    await ns.create()

    # Create all 5 ID pools for this namespace
    for pool_type, config in ID_POOL_CONFIGS.items():
        pool_id = f"{request.prefix}-{pool_type}"

        # Check if ID pool already exists
        existing_pool = await IdPool.find_one({"pool_id": pool_id})
        if existing_pool:
            continue

        pool = IdPool(
            pool_id=pool_id,
            name=f"{request.prefix.upper()} {config['name_suffix']}",
            description=f"ID pool for {config['description_suffix']} in {request.prefix} namespace",
            id_generator=config["id_generator"],
        )
        await pool.create()

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

    # Apply updates
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None and field != "updated_by":
            setattr(ns, field, value)

    ns.updated_at = datetime.now(timezone.utc)
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
    """
    Archive a namespace.

    This sets the namespace status to 'archived' and deactivates all associated ID pools.
    The data is preserved but no new entries can be created.
    """
    # Prevent archiving the wip namespace
    if prefix == "wip":
        raise HTTPException(
            status_code=400,
            detail="Cannot archive the default 'wip' namespace"
        )

    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    # Archive the namespace
    ns.status = "archived"
    ns.updated_at = datetime.now(timezone.utc)
    ns.updated_by = archived_by
    await ns.save()

    # Deactivate all ID pools in the namespace
    for pool_id in ns.get_all_pools():
        pool = await IdPool.find_one({"pool_id": pool_id})
        if pool:
            pool.status = "inactive"
            pool.updated_at = datetime.now(timezone.utc)
            await pool.save()

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
    """
    Restore an archived namespace.

    This reactivates the namespace and all associated ID pools.
    """
    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    if ns.status != "archived":
        raise HTTPException(
            status_code=400,
            detail=f"Namespace is not archived: {prefix}"
        )

    # Restore the namespace
    ns.status = "active"
    ns.updated_at = datetime.now(timezone.utc)
    ns.updated_by = restored_by
    await ns.save()

    # Reactivate all ID pools in the namespace
    for pool_id in ns.get_all_pools():
        pool = await IdPool.find_one({"pool_id": pool_id})
        if pool:
            pool.status = "active"
            pool.updated_at = datetime.now(timezone.utc)
            await pool.save()

    return namespace_to_response(ns)


@router.delete(
    "/{prefix}",
    summary="Delete namespace"
)
async def delete_namespace(
    prefix: str,
    confirm: bool = Query(False, description="Confirm permanent deletion"),
    deleted_by: str = Query(None, description="User deleting the namespace"),
    api_key: str = Depends(require_admin_key)
):
    """
    Permanently delete a namespace.

    WARNING: This is a destructive operation. Requires confirm=true.
    The namespace must be archived first. This does NOT delete the actual data
    in the ID pools - only the namespace metadata.
    """
    # Prevent deleting the wip namespace
    if prefix == "wip":
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default 'wip' namespace"
        )

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Deletion requires confirm=true query parameter"
        )

    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    if ns.status != "archived":
        raise HTTPException(
            status_code=400,
            detail="Namespace must be archived before deletion"
        )

    # Mark as deleted (soft delete)
    ns.status = "deleted"
    ns.updated_at = datetime.now(timezone.utc)
    ns.updated_by = deleted_by
    await ns.save()

    return {"status": "deleted", "prefix": prefix}


@router.post(
    "/initialize-wip",
    response_model=NamespaceResponse,
    summary="Initialize WIP namespace"
)
async def initialize_wip_namespace(
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """
    Initialize the default 'wip' namespace.

    This creates the wip namespace and all associated ID pools if they don't exist.
    """
    existing = await Namespace.find_one({"prefix": "wip"})
    if existing:
        return namespace_to_response(existing)

    ns = Namespace(
        prefix="wip",
        description="Default World In a Pie namespace",
        isolation_mode="open",
    )
    await ns.create()

    # Create all ID pools
    for pool_type, config in ID_POOL_CONFIGS.items():
        pool_id = f"wip-{pool_type}"
        existing_pool = await IdPool.find_one({"pool_id": pool_id})
        if existing_pool:
            continue

        pool = IdPool(
            pool_id=pool_id,
            name=f"WIP {config['name_suffix']}",
            description=f"ID pool for {config['description_suffix']}",
            id_generator=config["id_generator"],
        )
        await pool.create()

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
    """
    Export a namespace to a downloadable archive.

    Creates a ZIP file containing all data from the namespace:
    - manifest.json - Export metadata
    - terminologies.jsonl - Terminology definitions
    - terms.jsonl - Term definitions
    - templates.jsonl - Template definitions
    - documents.jsonl - Document data
    - files.jsonl - File metadata
    - files/ - Binary file content (if include_files=true)
    """
    from ..services.export_service import ExportService

    ns = await Namespace.find_one({"prefix": prefix})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}")

    # Get service URLs from environment
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
            namespace=ns,
            include_files=include_files,
        )

        # Generate export ID from filename
        export_id = os.path.basename(zip_path).replace(".zip", "")

        return ExportResponse(
            export_id=export_id,
            prefix=prefix,
            download_url=f"/api/registry/namespaces/exports/{export_id}",
            stats=stats,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


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
    """
    Import a namespace from an exported archive.

    Import modes:
    - create: Fail if the namespace already exists
    - merge: Add new entities, skip existing ones
    - replace: Archive existing namespace and import fresh

    If target_prefix is provided, the namespace is renamed on import.
    """
    from ..services.import_service import ImportService
    import tempfile
    import shutil

    # Validate mode
    if mode not in ("create", "merge", "replace"):
        raise HTTPException(
            status_code=400,
            detail="Invalid mode. Must be one of: create, merge, replace"
        )

    # Save uploaded file to temp location
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        shutil.copyfileobj(file.file, temp_file)
        temp_file.close()

        # Get service URLs from environment
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

        # Read manifest to get source prefix
        import zipfile
        import json
        source_prefix = None
        with zipfile.ZipFile(temp_file.name, "r") as zf:
            with zf.open("manifest.json") as mf:
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
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        # Cleanup temp file
        os.unlink(temp_file.name)
