"""Namespace management API endpoints."""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body, Depends

from ..models.namespace import Namespace, IdGeneratorConfig, WIP_INTERNAL_NAMESPACES
from ..models.api_models import (
    NamespaceCreate,
    NamespaceUpdate,
    NamespaceResponse,
    NamespaceBulkResponse,
)
from ..services.auth import require_api_key, require_admin_key

router = APIRouter()


def namespace_to_response(ns: Namespace) -> NamespaceResponse:
    """Convert a Namespace document to a response model."""
    return NamespaceResponse(
        namespace_id=ns.namespace_id,
        name=ns.name,
        description=ns.description,
        id_generator=ns.id_generator,
        source_endpoint=ns.source_endpoint,
        status=ns.status,
        created_at=ns.created_at,
        updated_at=ns.updated_at,
        metadata=ns.metadata,
    )


@router.get(
    "",
    response_model=List[NamespaceResponse],
    summary="List all namespaces"
)
async def list_namespaces(
    include_inactive: bool = False,
    api_key: str = Depends(require_api_key)
) -> List[NamespaceResponse]:
    """List all namespaces, optionally including inactive ones."""
    query = {} if include_inactive else {"status": "active"}
    namespaces = await Namespace.find(query).to_list()
    return [namespace_to_response(ns) for ns in namespaces]


@router.get(
    "/{namespace_id}",
    response_model=NamespaceResponse,
    summary="Get a namespace by ID"
)
async def get_namespace(
    namespace_id: str,
    api_key: str = Depends(require_api_key)
) -> NamespaceResponse:
    """Get a specific namespace by its ID."""
    ns = await Namespace.find_one({"namespace_id": namespace_id})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {namespace_id}")
    return namespace_to_response(ns)


@router.post(
    "",
    response_model=List[NamespaceBulkResponse],
    summary="Create namespaces (bulk)"
)
async def create_namespaces(
    items: List[NamespaceCreate] = Body(...),
    api_key: str = Depends(require_admin_key)
) -> List[NamespaceBulkResponse]:
    """Create one or more namespaces."""
    responses = []

    for i, item in enumerate(items):
        try:
            # Check if namespace already exists
            existing = await Namespace.find_one({"namespace_id": item.namespace_id})
            if existing:
                responses.append(NamespaceBulkResponse(
                    input_index=i,
                    status="error",
                    namespace_id=item.namespace_id,
                    error="Namespace already exists"
                ))
                continue

            # Create namespace
            ns = Namespace(
                namespace_id=item.namespace_id,
                name=item.name,
                description=item.description,
                id_generator=item.id_generator or IdGeneratorConfig(),
                source_endpoint=item.source_endpoint,
                metadata=item.metadata,
            )
            await ns.create()

            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="created",
                namespace_id=item.namespace_id,
            ))

        except Exception as e:
            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="error",
                namespace_id=item.namespace_id,
                error=str(e)
            ))

    return responses


@router.put(
    "",
    response_model=List[NamespaceBulkResponse],
    summary="Update namespaces (bulk)"
)
async def update_namespaces(
    items: List[tuple[str, NamespaceUpdate]] = Body(..., description="List of (namespace_id, update) tuples"),
    api_key: str = Depends(require_admin_key)
) -> List[NamespaceBulkResponse]:
    """Update one or more namespaces."""
    responses = []

    for i, (namespace_id, update) in enumerate(items):
        try:
            ns = await Namespace.find_one({"namespace_id": namespace_id})
            if not ns:
                responses.append(NamespaceBulkResponse(
                    input_index=i,
                    status="error",
                    namespace_id=namespace_id,
                    error="Namespace not found"
                ))
                continue

            # Apply updates
            update_data = update.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                if value is not None:
                    setattr(ns, field, value)

            ns.updated_at = datetime.now(timezone.utc)
            await ns.save()

            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="updated",
                namespace_id=namespace_id,
            ))

        except Exception as e:
            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="error",
                namespace_id=namespace_id,
                error=str(e)
            ))

    return responses


@router.put(
    "/{namespace_id}",
    response_model=NamespaceResponse,
    summary="Update a single namespace"
)
async def update_namespace(
    namespace_id: str,
    update: NamespaceUpdate,
    api_key: str = Depends(require_admin_key)
) -> NamespaceResponse:
    """Update a specific namespace."""
    ns = await Namespace.find_one({"namespace_id": namespace_id})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {namespace_id}")

    # Apply updates
    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None:
            setattr(ns, field, value)

    ns.updated_at = datetime.now(timezone.utc)
    await ns.save()

    return namespace_to_response(ns)


@router.delete(
    "",
    response_model=List[NamespaceBulkResponse],
    summary="Delete namespaces (bulk, soft delete)"
)
async def delete_namespaces(
    namespace_ids: List[str] = Body(..., embed=True),
    api_key: str = Depends(require_admin_key)
) -> List[NamespaceBulkResponse]:
    """Deactivate one or more namespaces (soft delete)."""
    responses = []

    for i, namespace_id in enumerate(namespace_ids):
        try:
            # Prevent deletion of WIP internal namespaces
            if namespace_id in WIP_INTERNAL_NAMESPACES:
                responses.append(NamespaceBulkResponse(
                    input_index=i,
                    status="error",
                    namespace_id=namespace_id,
                    error="Cannot delete internal WIP namespace"
                ))
                continue

            ns = await Namespace.find_one({"namespace_id": namespace_id})
            if not ns:
                responses.append(NamespaceBulkResponse(
                    input_index=i,
                    status="error",
                    namespace_id=namespace_id,
                    error="Namespace not found"
                ))
                continue

            ns.status = "inactive"
            ns.updated_at = datetime.now(timezone.utc)
            await ns.save()

            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="deleted",
                namespace_id=namespace_id,
            ))

        except Exception as e:
            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="error",
                namespace_id=namespace_id,
                error=str(e)
            ))

    return responses


@router.delete(
    "/{namespace_id}",
    response_model=NamespaceBulkResponse,
    summary="Delete a single namespace (soft delete)"
)
async def delete_namespace(
    namespace_id: str,
    api_key: str = Depends(require_admin_key)
) -> NamespaceBulkResponse:
    """Deactivate a specific namespace (soft delete)."""
    # Prevent deletion of WIP internal namespaces
    if namespace_id in WIP_INTERNAL_NAMESPACES:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete internal WIP namespace"
        )

    ns = await Namespace.find_one({"namespace_id": namespace_id})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {namespace_id}")

    ns.status = "inactive"
    ns.updated_at = datetime.now(timezone.utc)
    await ns.save()

    return NamespaceBulkResponse(
        input_index=0,
        status="deleted",
        namespace_id=namespace_id,
    )


@router.post(
    "/initialize-wip",
    response_model=List[NamespaceBulkResponse],
    summary="Initialize WIP internal namespaces"
)
async def initialize_wip_namespaces(
    api_key: str = Depends(require_admin_key)
) -> List[NamespaceBulkResponse]:
    """
    Initialize the WIP internal namespaces.

    This creates the default, wip-terminologies, wip-terms, wip-templates,
    wip-documents, and wip-files namespaces if they don't exist.
    """
    responses = []

    for i, (ns_id, config) in enumerate(WIP_INTERNAL_NAMESPACES.items()):
        try:
            existing = await Namespace.find_one({"namespace_id": ns_id})
            if existing:
                responses.append(NamespaceBulkResponse(
                    input_index=i,
                    status="already_exists",
                    namespace_id=ns_id,
                ))
                continue

            ns = Namespace(
                namespace_id=ns_id,
                name=config["name"],
                description=config["description"],
                id_generator=config["id_generator"],
            )
            await ns.create()

            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="created",
                namespace_id=ns_id,
            ))

        except Exception as e:
            responses.append(NamespaceBulkResponse(
                input_index=i,
                status="error",
                namespace_id=ns_id,
                error=str(e)
            ))

    return responses
