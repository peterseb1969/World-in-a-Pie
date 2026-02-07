"""Namespace Group management API endpoints."""

import os
from datetime import datetime, timezone
from typing import List, Literal

from fastapi import APIRouter, HTTPException, Query, Depends, UploadFile, File
from fastapi.responses import FileResponse

from ..models.namespace import Namespace, IdGeneratorConfig, IdGeneratorType
from ..models.namespace_group import NamespaceGroup
from ..models.entry import RegistryEntry
from ..models.api_models import (
    NamespaceGroupCreate,
    NamespaceGroupUpdate,
    NamespaceGroupResponse,
    NamespaceGroupStatsResponse,
    ExportResponse,
    ImportRequest,
    ImportResponse,
)
from ..services.auth import require_api_key, require_admin_key

router = APIRouter()


# Namespace configuration for each type in a group
NAMESPACE_CONFIGS = {
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


def group_to_response(group: NamespaceGroup) -> NamespaceGroupResponse:
    """Convert a NamespaceGroup document to a response model."""
    return NamespaceGroupResponse(
        prefix=group.prefix,
        description=group.description,
        isolation_mode=group.isolation_mode,
        allowed_external_refs=group.allowed_external_refs,
        status=group.status,
        created_at=group.created_at,
        created_by=group.created_by,
        updated_at=group.updated_at,
        updated_by=group.updated_by,
        terminologies_ns=group.terminologies_ns,
        terms_ns=group.terms_ns,
        templates_ns=group.templates_ns,
        documents_ns=group.documents_ns,
        files_ns=group.files_ns,
    )


@router.get(
    "",
    response_model=List[NamespaceGroupResponse],
    summary="List namespace groups"
)
async def list_namespace_groups(
    include_archived: bool = Query(False, description="Include archived groups"),
    api_key: str = Depends(require_api_key)
) -> List[NamespaceGroupResponse]:
    """List all namespace groups."""
    if include_archived:
        query = {"status": {"$ne": "deleted"}}
    else:
        query = {"status": "active"}

    groups = await NamespaceGroup.find(query).to_list()
    return [group_to_response(g) for g in groups]


@router.get(
    "/{prefix}",
    response_model=NamespaceGroupResponse,
    summary="Get namespace group by prefix"
)
async def get_namespace_group(
    prefix: str,
    api_key: str = Depends(require_api_key)
) -> NamespaceGroupResponse:
    """Get a specific namespace group by its prefix."""
    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")
    return group_to_response(group)


@router.get(
    "/{prefix}/stats",
    response_model=NamespaceGroupStatsResponse,
    summary="Get namespace group statistics"
)
async def get_namespace_group_stats(
    prefix: str,
    api_key: str = Depends(require_api_key)
) -> NamespaceGroupStatsResponse:
    """Get entity counts for each namespace in the group."""
    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")

    # Count entries in each namespace
    namespace_counts = {}
    for ns_id in group.get_all_namespaces():
        count = await RegistryEntry.find({"primary_namespace": ns_id}).count()
        namespace_counts[ns_id] = count

    return NamespaceGroupStatsResponse(
        prefix=group.prefix,
        description=group.description,
        isolation_mode=group.isolation_mode,
        status=group.status,
        namespaces=namespace_counts,
    )


@router.post(
    "",
    response_model=NamespaceGroupResponse,
    summary="Create namespace group"
)
async def create_namespace_group(
    request: NamespaceGroupCreate,
    api_key: str = Depends(require_admin_key)
) -> NamespaceGroupResponse:
    """
    Create a new namespace group.

    This creates the group and all 5 associated namespaces:
    - {prefix}-terminologies
    - {prefix}-terms
    - {prefix}-templates
    - {prefix}-documents
    - {prefix}-files
    """
    # Check if group already exists
    existing = await NamespaceGroup.find_one({"prefix": request.prefix})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Namespace group already exists: {request.prefix}"
        )

    # Create the group
    group = NamespaceGroup(
        prefix=request.prefix,
        description=request.description,
        isolation_mode=request.isolation_mode,
        allowed_external_refs=request.allowed_external_refs,
        created_by=request.created_by,
    )
    await group.create()

    # Create all 5 namespaces
    created_namespaces = []
    for ns_type, config in NAMESPACE_CONFIGS.items():
        ns_id = f"{request.prefix}-{ns_type}"

        # Check if namespace already exists
        existing_ns = await Namespace.find_one({"namespace_id": ns_id})
        if existing_ns:
            continue

        ns = Namespace(
            namespace_id=ns_id,
            name=f"{request.prefix.upper()} {config['name_suffix']}",
            description=f"Namespace for {config['description_suffix']} in {request.prefix} group",
            id_generator=config["id_generator"],
        )
        await ns.create()
        created_namespaces.append(ns_id)

    return group_to_response(group)


@router.put(
    "/{prefix}",
    response_model=NamespaceGroupResponse,
    summary="Update namespace group"
)
async def update_namespace_group(
    prefix: str,
    request: NamespaceGroupUpdate,
    api_key: str = Depends(require_admin_key)
) -> NamespaceGroupResponse:
    """Update a namespace group's configuration."""
    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")

    # Apply updates
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None and field != "updated_by":
            setattr(group, field, value)

    group.updated_at = datetime.now(timezone.utc)
    group.updated_by = request.updated_by
    await group.save()

    return group_to_response(group)


@router.post(
    "/{prefix}/archive",
    response_model=NamespaceGroupResponse,
    summary="Archive namespace group"
)
async def archive_namespace_group(
    prefix: str,
    archived_by: str = Query(None, description="User archiving the group"),
    api_key: str = Depends(require_admin_key)
) -> NamespaceGroupResponse:
    """
    Archive a namespace group.

    This sets the group status to 'archived' and deactivates all associated namespaces.
    The data is preserved but no new entries can be created.
    """
    # Prevent archiving the wip group
    if prefix == "wip":
        raise HTTPException(
            status_code=400,
            detail="Cannot archive the default 'wip' namespace group"
        )

    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")

    # Archive the group
    group.status = "archived"
    group.updated_at = datetime.now(timezone.utc)
    group.updated_by = archived_by
    await group.save()

    # Deactivate all namespaces in the group
    for ns_id in group.get_all_namespaces():
        ns = await Namespace.find_one({"namespace_id": ns_id})
        if ns:
            ns.status = "inactive"
            ns.updated_at = datetime.now(timezone.utc)
            await ns.save()

    return group_to_response(group)


@router.post(
    "/{prefix}/restore",
    response_model=NamespaceGroupResponse,
    summary="Restore archived namespace group"
)
async def restore_namespace_group(
    prefix: str,
    restored_by: str = Query(None, description="User restoring the group"),
    api_key: str = Depends(require_admin_key)
) -> NamespaceGroupResponse:
    """
    Restore an archived namespace group.

    This reactivates the group and all associated namespaces.
    """
    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")

    if group.status != "archived":
        raise HTTPException(
            status_code=400,
            detail=f"Namespace group is not archived: {prefix}"
        )

    # Restore the group
    group.status = "active"
    group.updated_at = datetime.now(timezone.utc)
    group.updated_by = restored_by
    await group.save()

    # Reactivate all namespaces in the group
    for ns_id in group.get_all_namespaces():
        ns = await Namespace.find_one({"namespace_id": ns_id})
        if ns:
            ns.status = "active"
            ns.updated_at = datetime.now(timezone.utc)
            await ns.save()

    return group_to_response(group)


@router.delete(
    "/{prefix}",
    summary="Delete namespace group"
)
async def delete_namespace_group(
    prefix: str,
    confirm: bool = Query(False, description="Confirm permanent deletion"),
    deleted_by: str = Query(None, description="User deleting the group"),
    api_key: str = Depends(require_admin_key)
):
    """
    Permanently delete a namespace group.

    WARNING: This is a destructive operation. Requires confirm=true.
    The group must be archived first. This does NOT delete the actual data
    in the namespaces - only the group metadata.
    """
    # Prevent deleting the wip group
    if prefix == "wip":
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default 'wip' namespace group"
        )

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Deletion requires confirm=true query parameter"
        )

    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")

    if group.status != "archived":
        raise HTTPException(
            status_code=400,
            detail="Namespace group must be archived before deletion"
        )

    # Mark as deleted (soft delete)
    group.status = "deleted"
    group.updated_at = datetime.now(timezone.utc)
    group.updated_by = deleted_by
    await group.save()

    return {"status": "deleted", "prefix": prefix}


@router.post(
    "/initialize-wip-group",
    response_model=NamespaceGroupResponse,
    summary="Initialize WIP namespace group"
)
async def initialize_wip_group(
    api_key: str = Depends(require_admin_key)
) -> NamespaceGroupResponse:
    """
    Initialize the default 'wip' namespace group.

    This creates the wip namespace group if it doesn't exist.
    The actual wip-* namespaces are created by /namespaces/initialize-wip.
    """
    existing = await NamespaceGroup.find_one({"prefix": "wip"})
    if existing:
        return group_to_response(existing)

    group = NamespaceGroup(
        prefix="wip",
        description="Default World In a Pie namespace group",
        isolation_mode="open",
    )
    await group.create()

    return group_to_response(group)


# =============================================================================
# Export/Import Endpoints
# =============================================================================

@router.post(
    "/{prefix}/export",
    response_model=ExportResponse,
    summary="Export namespace group"
)
async def export_namespace_group(
    prefix: str,
    include_files: bool = Query(False, description="Include binary file content"),
    api_key: str = Depends(require_admin_key)
) -> ExportResponse:
    """
    Export a namespace group to a downloadable archive.

    Creates a ZIP file containing all data from the namespace group:
    - manifest.json - Export metadata
    - terminologies.jsonl - Terminology definitions
    - terms.jsonl - Term definitions
    - templates.jsonl - Template definitions
    - documents.jsonl - Document data
    - files.jsonl - File metadata
    - files/ - Binary file content (if include_files=true)
    """
    from ..services.export_service import ExportService

    group = await NamespaceGroup.find_one({"prefix": prefix})
    if not group:
        raise HTTPException(status_code=404, detail=f"Namespace group not found: {prefix}")

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
        zip_path, stats = await export_service.export_namespace_group(
            group=group,
            include_files=include_files,
        )

        # Generate export ID from filename
        export_id = os.path.basename(zip_path).replace(".zip", "")

        return ExportResponse(
            export_id=export_id,
            prefix=prefix,
            download_url=f"/api/registry/namespace-groups/exports/{export_id}",
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
    """Download an exported namespace group archive."""
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
    summary="Import namespace group"
)
async def import_namespace_group(
    file: UploadFile = File(..., description="Export ZIP file to import"),
    target_prefix: str = Query(None, description="Optional new prefix"),
    mode: str = Query("create", description="Import mode: create, merge, replace"),
    imported_by: str = Query(None, description="User performing import"),
    api_key: str = Depends(require_admin_key)
) -> ImportResponse:
    """
    Import a namespace group from an exported archive.

    Import modes:
    - create: Fail if the namespace group already exists
    - merge: Add new entities, skip existing ones
    - replace: Archive existing group and import fresh

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

        group, stats = await import_service.import_namespace_group(
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
            prefix=group.prefix,
            mode=mode,
            stats=stats,
            source_prefix=source_prefix if source_prefix != group.prefix else None,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        # Cleanup temp file
        os.unlink(temp_file.name)
