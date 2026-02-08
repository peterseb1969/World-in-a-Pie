"""ID Pool management API endpoints.

ID Pools are internal constructs for ID generation. Users typically don't
interact with these directly - they work with Namespaces. ID Pools are
auto-created when a Namespace is created.

This API is primarily for internal/advanced use.
"""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException, Body, Depends

from ..models.id_pool import IdPool, IdGeneratorConfig, WIP_ID_POOLS
from ..models.namespace import Namespace
from ..models.api_models import (
    IdPoolCreate,
    IdPoolUpdate,
    IdPoolResponse,
    IdPoolBulkResponse,
)
from ..services.auth import require_api_key, require_admin_key

router = APIRouter()


def pool_to_response(pool: IdPool) -> IdPoolResponse:
    """Convert an IdPool document to a response model."""
    return IdPoolResponse(
        pool_id=pool.pool_id,
        name=pool.name,
        description=pool.description,
        id_generator=pool.id_generator,
        source_endpoint=pool.source_endpoint,
        status=pool.status,
        created_at=pool.created_at,
        updated_at=pool.updated_at,
        metadata=pool.metadata,
    )


@router.get(
    "",
    response_model=List[IdPoolResponse],
    summary="List all ID pools"
)
async def list_id_pools(
    include_inactive: bool = False,
    api_key: str = Depends(require_api_key)
) -> List[IdPoolResponse]:
    """List all ID pools, optionally including inactive ones."""
    query = {} if include_inactive else {"status": "active"}
    pools = await IdPool.find(query).to_list()
    return [pool_to_response(p) for p in pools]


@router.get(
    "/{pool_id}",
    response_model=IdPoolResponse,
    summary="Get an ID pool by ID"
)
async def get_id_pool(
    pool_id: str,
    api_key: str = Depends(require_api_key)
) -> IdPoolResponse:
    """Get a specific ID pool by its ID."""
    pool = await IdPool.find_one({"pool_id": pool_id})
    if not pool:
        raise HTTPException(status_code=404, detail=f"ID pool not found: {pool_id}")
    return pool_to_response(pool)


@router.post(
    "",
    response_model=List[IdPoolBulkResponse],
    summary="Create ID pools (bulk)"
)
async def create_id_pools(
    items: List[IdPoolCreate] = Body(...),
    api_key: str = Depends(require_admin_key)
) -> List[IdPoolBulkResponse]:
    """Create one or more ID pools."""
    responses = []

    for i, item in enumerate(items):
        try:
            # Check if pool already exists
            existing = await IdPool.find_one({"pool_id": item.pool_id})
            if existing:
                responses.append(IdPoolBulkResponse(
                    input_index=i,
                    status="error",
                    pool_id=item.pool_id,
                    error="ID pool already exists"
                ))
                continue

            # Create pool
            pool = IdPool(
                pool_id=item.pool_id,
                name=item.name,
                description=item.description,
                id_generator=item.id_generator or IdGeneratorConfig(),
                source_endpoint=item.source_endpoint,
                metadata=item.metadata,
            )
            await pool.create()

            responses.append(IdPoolBulkResponse(
                input_index=i,
                status="created",
                pool_id=item.pool_id,
            ))

        except Exception as e:
            responses.append(IdPoolBulkResponse(
                input_index=i,
                status="error",
                pool_id=item.pool_id,
                error=str(e)
            ))

    return responses


@router.put(
    "/{pool_id}",
    response_model=IdPoolResponse,
    summary="Update an ID pool"
)
async def update_id_pool(
    pool_id: str,
    update: IdPoolUpdate,
    api_key: str = Depends(require_admin_key)
) -> IdPoolResponse:
    """Update a specific ID pool."""
    pool = await IdPool.find_one({"pool_id": pool_id})
    if not pool:
        raise HTTPException(status_code=404, detail=f"ID pool not found: {pool_id}")

    # Apply updates
    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None:
            setattr(pool, field, value)

    pool.updated_at = datetime.now(timezone.utc)
    await pool.save()

    return pool_to_response(pool)


@router.delete(
    "/{pool_id}",
    response_model=IdPoolBulkResponse,
    summary="Delete an ID pool (soft delete)"
)
async def delete_id_pool(
    pool_id: str,
    api_key: str = Depends(require_admin_key)
) -> IdPoolBulkResponse:
    """Deactivate an ID pool (soft delete)."""
    # Prevent deletion of WIP internal pools
    if pool_id in WIP_ID_POOLS:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete internal WIP ID pool"
        )

    pool = await IdPool.find_one({"pool_id": pool_id})
    if not pool:
        raise HTTPException(status_code=404, detail=f"ID pool not found: {pool_id}")

    pool.status = "inactive"
    pool.updated_at = datetime.now(timezone.utc)
    await pool.save()

    return IdPoolBulkResponse(
        input_index=0,
        status="deleted",
        pool_id=pool_id,
    )


@router.post(
    "/initialize-wip",
    response_model=List[IdPoolBulkResponse],
    summary="Initialize WIP internal ID pools"
)
async def initialize_wip_pools(
    api_key: str = Depends(require_admin_key)
) -> List[IdPoolBulkResponse]:
    """
    Initialize the WIP internal ID pools.

    This creates the default, wip-terminologies, wip-terms, wip-templates,
    wip-documents, and wip-files ID pools if they don't exist.
    """
    responses = []

    for i, (pool_id, config) in enumerate(WIP_ID_POOLS.items()):
        try:
            existing = await IdPool.find_one({"pool_id": pool_id})
            if existing:
                responses.append(IdPoolBulkResponse(
                    input_index=i,
                    status="already_exists",
                    pool_id=pool_id,
                ))
                continue

            pool = IdPool(
                pool_id=pool_id,
                name=config["name"],
                description=config["description"],
                id_generator=config["id_generator"],
            )
            await pool.create()

            responses.append(IdPoolBulkResponse(
                input_index=i,
                status="created",
                pool_id=pool_id,
            ))

        except Exception as e:
            responses.append(IdPoolBulkResponse(
                input_index=i,
                status="error",
                pool_id=pool_id,
                error=str(e)
            ))

    # Also create the wip namespace if it doesn't exist
    existing_ns = await Namespace.find_one({"prefix": "wip"})
    if not existing_ns:
        ns = Namespace(
            prefix="wip",
            description="Default World In a Pie namespace",
            isolation_mode="open",
        )
        await ns.create()

    return responses
