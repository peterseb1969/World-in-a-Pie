"""Registry entry management API endpoints."""

from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Body, Depends

from ..models.id_pool import IdPool, IdGeneratorConfig, IdGeneratorType
from ..models.entry import RegistryEntry, Synonym, SourceInfo
from ..models.api_models import (
    RegisterKeyItem,
    RegisterKeyResponse,
    RegisterBulkResponse,
    LookupByIdItem,
    LookupByKeyItem,
    LookupResponse,
    LookupBulkResponse,
    UpdateEntryItem,
    UpdateEntryResponse,
    DeleteItem,
    DeleteResponse,
)
from ..services.id_generator import IdGeneratorService
from ..services.hash import HashService
from ..services.auth import require_api_key

router = APIRouter()


async def get_pool_config(pool_id: str) -> Optional[IdPool]:
    """Get ID pool configuration, returning None if not found."""
    return await IdPool.find_one({"pool_id": pool_id, "status": "active"})


def build_lookup_response(
    input_index: int,
    entry: Optional[RegistryEntry],
    status: str = "found",
    matched_pool_id: Optional[str] = None,
    matched_composite_key: Optional[dict] = None,
    matched_via: Optional[str] = None,
    source_data: Optional[dict] = None,
    error: Optional[str] = None
) -> LookupResponse:
    """Build a standardized lookup response."""
    if entry is None:
        return LookupResponse(
            input_index=input_index,
            status=status,
            error=error
        )

    return LookupResponse(
        input_index=input_index,
        status=status,
        preferred_id=entry.entry_id,
        preferred_pool_id=entry.primary_pool_id,
        additional_ids=entry.additional_ids,
        matched_pool_id=matched_pool_id or entry.primary_pool_id,
        matched_composite_key=matched_composite_key or entry.primary_composite_key,
        matched_via=matched_via,
        synonyms=entry.synonyms,
        source_info=entry.source_info,
        source_data=source_data,
        error=error
    )


@router.post(
    "/register",
    response_model=RegisterBulkResponse,
    summary="Register composite keys (bulk)"
)
async def register_keys(
    items: List[RegisterKeyItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> RegisterBulkResponse:
    """
    Register one or more composite keys.

    For each key:
    - If the key already exists, returns the existing registry ID
    - If new, generates an ID based on pool configuration and creates entry

    Uses batch MongoDB operations for efficiency with large imports.
    """
    if not items:
        return RegisterBulkResponse(results=[], total=0, created=0, already_exists=0, errors=0)

    results: List[RegisterKeyResponse | None] = [None] * len(items)
    created_count = 0
    exists_count = 0
    error_count = 0

    # Phase 1: Compute all hashes upfront
    hashes = []
    for item in items:
        hashes.append(HashService.compute_composite_key_hash(item.composite_key))

    # Phase 2: Batch check for existing entries (single query)
    existing_entries = await RegistryEntry.find({
        "$or": [
            {"primary_composite_key_hash": {"$in": hashes}},
            {"synonyms.composite_key_hash": {"$in": hashes}}
        ]
    }).to_list()

    # Build lookup maps for existing entries
    existing_by_hash = {}
    for entry in existing_entries:
        existing_by_hash[entry.primary_composite_key_hash] = entry
        for syn in entry.synonyms:
            existing_by_hash[syn.composite_key_hash] = entry

    # Phase 3: Cache pool configs (typically only 1-2 unique pools per batch)
    pool_configs: dict[str, IdPool | None] = {}

    # Phase 4: Partition into exists/create/error and build entries to insert
    entries_to_insert: List[RegistryEntry] = []
    insert_indices: List[int] = []  # maps insert position -> original index

    for i, (item, key_hash) in enumerate(zip(items, hashes)):
        try:
            # Check if exists
            existing = existing_by_hash.get(key_hash)
            if existing:
                results[i] = RegisterKeyResponse(
                    input_index=i,
                    status="already_exists",
                    registry_id=existing.entry_id,
                    pool_id=existing.primary_pool_id,
                )
                exists_count += 1
                continue

            # Get pool config (cached)
            if item.pool_id not in pool_configs:
                pool_configs[item.pool_id] = await get_pool_config(item.pool_id)
            pool_config = pool_configs[item.pool_id]

            if not pool_config:
                id_gen_config = IdGeneratorConfig()
            else:
                id_gen_config = pool_config.id_generator

            # Generate ID based on pool config
            if id_gen_config.type == IdGeneratorType.EXTERNAL:
                entry_id = item.metadata.get("external_id") or item.composite_key.get("id")
                if not entry_id:
                    results[i] = RegisterKeyResponse(
                        input_index=i,
                        status="error",
                        error="External pool requires 'external_id' in metadata or 'id' in composite_key"
                    )
                    error_count += 1
                    continue
            else:
                entry_id = IdGeneratorService.generate(id_gen_config, item.pool_id)

            # Build entry for batch insert
            entry = RegistryEntry(
                entry_id=entry_id,
                primary_pool_id=item.pool_id,
                primary_composite_key=item.composite_key,
                primary_composite_key_hash=key_hash,
                source_info=item.source_info,
                created_by=item.created_by,
                metadata=item.metadata,
            )
            entry.rebuild_search_values()
            entries_to_insert.append(entry)
            insert_indices.append(i)

        except Exception as e:
            results[i] = RegisterKeyResponse(
                input_index=i,
                status="error",
                error=str(e)
            )
            error_count += 1

    # Phase 5: Batch insert all new entries (single insert_many)
    if entries_to_insert:
        try:
            await RegistryEntry.insert_many(entries_to_insert)
            # All succeeded
            for pos, idx in enumerate(insert_indices):
                entry = entries_to_insert[pos]
                results[idx] = RegisterKeyResponse(
                    input_index=idx,
                    status="created",
                    registry_id=entry.entry_id,
                    pool_id=entry.primary_pool_id,
                )
                created_count += 1
        except Exception as e:
            # If insert_many fails, mark all pending as errors
            # (Could be enhanced to handle partial failures)
            for pos, idx in enumerate(insert_indices):
                if results[idx] is None:
                    results[idx] = RegisterKeyResponse(
                        input_index=idx,
                        status="error",
                        error=f"Batch insert failed: {str(e)}"
                    )
                    error_count += 1

    return RegisterBulkResponse(
        results=results,
        total=len(items),
        created=created_count,
        already_exists=exists_count,
        errors=error_count,
    )


@router.post(
    "/lookup/by-id",
    response_model=LookupBulkResponse,
    summary="Lookup entries by ID (bulk)"
)
async def lookup_by_ids(
    items: List[LookupByIdItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> LookupBulkResponse:
    """
    Look up registry entries by their IDs.

    Always returns the preferred_id and all additional_ids per spec.
    """
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                matched_via = None

                # 1. Find by entry_id (optionally scoped to pool)
                q1 = {"entry_id": item.entry_id, "status": "active"}
                if item.pool_id:
                    q1["primary_pool_id"] = item.pool_id
                entry = await RegistryEntry.find_one(q1)
                if entry:
                    matched_via = "entry_id"

                # 2. Check additional_ids if not found as primary
                if not entry:
                    elem_match = {"id": item.entry_id}
                    if item.pool_id:
                        elem_match["pool_id"] = item.pool_id
                    entry = await RegistryEntry.find_one({
                        "additional_ids": {"$elemMatch": elem_match},
                        "status": "active"
                    })
                    if entry:
                        matched_via = "additional_id"

                # 3. Composite key value search — find by value in search_values array
                if not entry:
                    q3 = {"search_values": item.entry_id, "status": "active"}
                    if item.pool_id:
                        q3["primary_pool_id"] = item.pool_id
                    entry = await RegistryEntry.find_one(q3)
                    if entry:
                        matched_via = "composite_key_value"

                if not entry:
                    results.append(LookupResponse(
                        input_index=i,
                        status="not_found"
                    ))
                    not_found_count += 1
                    continue

                # Optionally fetch source data
                source_data = None
                if item.fetch_source_data and entry.source_info and entry.source_info.endpoint_url:
                    try:
                        resp = await client.get(entry.source_info.endpoint_url)
                        resp.raise_for_status()
                        source_data = resp.json()
                    except Exception as e:
                        source_data = {"error": f"Failed to fetch: {str(e)}"}

                results.append(build_lookup_response(
                    input_index=i,
                    entry=entry,
                    matched_via=matched_via,
                    source_data=source_data
                ))
                found_count += 1

            except Exception as e:
                results.append(LookupResponse(
                    input_index=i,
                    status="error",
                    error=str(e)
                ))
                error_count += 1

    return LookupBulkResponse(
        results=results,
        total=len(items),
        found=found_count,
        not_found=not_found_count,
        errors=error_count,
    )


@router.post(
    "/lookup/by-key",
    response_model=LookupBulkResponse,
    summary="Lookup entries by composite key (bulk)"
)
async def lookup_by_keys(
    items: List[LookupByKeyItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> LookupBulkResponse:
    """
    Look up registry entries by their composite keys.

    Can search in primary keys only or include synonyms.
    Always returns the preferred_id and all additional_ids per spec.
    """
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                key_hash = HashService.compute_composite_key_hash(item.composite_key)

                # Build query based on search options
                if item.search_synonyms:
                    # Search both primary and synonyms
                    query = {
                        "$or": [
                            {
                                "primary_pool_id": item.pool_id,
                                "primary_composite_key_hash": key_hash
                            },
                            {
                                "synonyms": {
                                    "$elemMatch": {
                                        "pool_id": item.pool_id,
                                        "composite_key_hash": key_hash
                                    }
                                }
                            }
                        ],
                        "status": "active"
                    }
                else:
                    # Search primary only
                    query = {
                        "primary_pool_id": item.pool_id,
                        "primary_composite_key_hash": key_hash,
                        "status": "active"
                    }

                entry = await RegistryEntry.find_one(query)

                if not entry:
                    results.append(LookupResponse(
                        input_index=i,
                        status="not_found"
                    ))
                    not_found_count += 1
                    continue

                # Determine where the match was found
                matched_pool_id = item.pool_id
                matched_composite_key = item.composite_key
                if entry.primary_composite_key_hash != key_hash:
                    # Match was in a synonym
                    for syn in entry.synonyms:
                        if syn.composite_key_hash == key_hash:
                            matched_pool_id = syn.pool_id
                            matched_composite_key = syn.composite_key
                            break

                # Optionally fetch source data
                source_data = None
                if item.fetch_source_data and entry.source_info and entry.source_info.endpoint_url:
                    try:
                        resp = await client.get(entry.source_info.endpoint_url)
                        resp.raise_for_status()
                        source_data = resp.json()
                    except Exception as e:
                        source_data = {"error": f"Failed to fetch: {str(e)}"}

                results.append(build_lookup_response(
                    input_index=i,
                    entry=entry,
                    matched_pool_id=matched_pool_id,
                    matched_composite_key=matched_composite_key,
                    source_data=source_data
                ))
                found_count += 1

            except Exception as e:
                results.append(LookupResponse(
                    input_index=i,
                    status="error",
                    error=str(e)
                ))
                error_count += 1

    return LookupBulkResponse(
        results=results,
        total=len(items),
        found=found_count,
        not_found=not_found_count,
        errors=error_count,
    )


@router.put(
    "",
    response_model=List[UpdateEntryResponse],
    summary="Update entries (bulk)"
)
async def update_entries(
    items: List[UpdateEntryItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> List[UpdateEntryResponse]:
    """Update one or more registry entries."""
    results = []

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "primary_pool_id": item.pool_id,
                "entry_id": item.entry_id,
                "status": "active"
            })

            if not entry:
                results.append(UpdateEntryResponse(
                    input_index=i,
                    status="not_found",
                    registry_id=item.entry_id,
                ))
                continue

            # Apply updates
            if item.source_info is not None:
                entry.source_info = item.source_info
            if item.metadata is not None:
                entry.metadata.update(item.metadata)

            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(UpdateEntryResponse(
                input_index=i,
                status="updated",
                registry_id=item.entry_id,
            ))

        except Exception as e:
            results.append(UpdateEntryResponse(
                input_index=i,
                status="error",
                registry_id=item.entry_id,
                error=str(e)
            ))

    return results


@router.delete(
    "",
    response_model=List[DeleteResponse],
    summary="Delete entries (bulk, soft delete)"
)
async def delete_entries(
    items: List[DeleteItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> List[DeleteResponse]:
    """Deactivate one or more registry entries (soft delete)."""
    results = []

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "primary_pool_id": item.pool_id,
                "entry_id": item.entry_id,
            })

            if not entry:
                results.append(DeleteResponse(
                    input_index=i,
                    status="not_found",
                    registry_id=item.entry_id,
                ))
                continue

            entry.status = "inactive"
            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(DeleteResponse(
                input_index=i,
                status="deactivated",
                registry_id=item.entry_id,
            ))

        except Exception as e:
            results.append(DeleteResponse(
                input_index=i,
                status="error",
                registry_id=item.entry_id,
                error=str(e)
            ))

    return results
