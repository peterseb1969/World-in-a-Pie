"""Registry entry management API endpoints."""

from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Body, Depends

from ..models.namespace import Namespace
from ..models.id_algorithm import IdAlgorithmConfig, IdFormatValidator, VALID_ENTITY_TYPES
from ..models.entry import RegistryEntry, Synonym
from ..models.api_models import (
    RegisterKeyItem,
    RegisterKeyResponse,
    RegisterBulkResponse,
    ProvisionRequest,
    ProvisionedId,
    ProvisionResponse,
    ReserveItem,
    ReserveItemResponse,
    ReserveBulkResponse,
    ActivateItem,
    ActivateItemResponse,
    ActivateBulkResponse,
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


def build_lookup_response(
    input_index: int,
    entry: Optional[RegistryEntry],
    status: str = "found",
    matched_namespace: Optional[str] = None,
    matched_entity_type: Optional[str] = None,
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
        namespace=entry.namespace,
        entity_type=entry.entity_type,
        additional_ids=entry.additional_ids,
        matched_namespace=matched_namespace or entry.namespace,
        matched_entity_type=matched_entity_type or entry.entity_type,
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
    summary="Register composite keys (bulk, reserve+activate)"
)
async def register_keys(
    items: List[RegisterKeyItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> RegisterBulkResponse:
    """
    Register one or more composite keys. This is sugar for reserve + immediate activate.

    For each key:
    - If the key already exists, returns the existing registry ID
    - If new, generates an ID (or uses client-provided one) and creates an active entry
    """
    if not items:
        return RegisterBulkResponse(results=[], total=0, created=0, already_exists=0, errors=0)

    results: List[RegisterKeyResponse | None] = [None] * len(items)
    created_count = 0
    exists_count = 0
    error_count = 0

    # Phase 1: Compute hashes for items with composite keys
    hashes = []
    for item in items:
        if item.composite_key:
            hashes.append(HashService.compute_composite_key_hash(item.composite_key))
        else:
            hashes.append("")  # Empty composite key = no dedup

    # Phase 2: Batch check for existing entries (only for non-empty hashes)
    dedup_hashes = [h for h in hashes if h]
    existing_by_hash = {}
    if dedup_hashes:
        existing_entries = await RegistryEntry.find({
            "$or": [
                {"primary_composite_key_hash": {"$in": dedup_hashes}},
                {"synonyms.composite_key_hash": {"$in": dedup_hashes}}
            ]
        }).to_list()

        for entry in existing_entries:
            existing_by_hash[entry.primary_composite_key_hash] = entry
            for syn in entry.synonyms:
                existing_by_hash[syn.composite_key_hash] = entry

    # Phase 3: Build entries to insert
    entries_to_insert: List[RegistryEntry] = []
    insert_indices: List[int] = []

    for i, (item, key_hash) in enumerate(zip(items, hashes)):
        try:
            # Validate entity_type
            if item.entity_type not in VALID_ENTITY_TYPES:
                results[i] = RegisterKeyResponse(
                    input_index=i,
                    status="error",
                    error=f"Invalid entity_type: {item.entity_type}"
                )
                error_count += 1
                continue

            # Check if exists (only when composite key is non-empty)
            if key_hash:
                existing = existing_by_hash.get(key_hash)
                if existing:
                    results[i] = RegisterKeyResponse(
                        input_index=i,
                        status="already_exists",
                        registry_id=existing.entry_id,
                        namespace=existing.namespace,
                        entity_type=existing.entity_type,
                    )
                    exists_count += 1
                    continue

            # Generate or use provided ID
            if item.entry_id:
                entry_id = item.entry_id
            else:
                entry_id = await IdGeneratorService.generate(item.namespace, item.entity_type)

            entry = RegistryEntry(
                entry_id=entry_id,
                namespace=item.namespace,
                entity_type=item.entity_type,
                primary_composite_key=item.composite_key,
                primary_composite_key_hash=key_hash,
                source_info=item.source_info,
                status="active",
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

    # Phase 4: Batch insert
    if entries_to_insert:
        try:
            await RegistryEntry.insert_many(entries_to_insert)
            for pos, idx in enumerate(insert_indices):
                entry = entries_to_insert[pos]
                results[idx] = RegisterKeyResponse(
                    input_index=idx,
                    status="created",
                    registry_id=entry.entry_id,
                    namespace=entry.namespace,
                    entity_type=entry.entity_type,
                )
                created_count += 1
        except Exception as e:
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
    "/provision",
    response_model=ProvisionResponse,
    summary="Provision IDs (registry generates)"
)
async def provision_ids(
    request: ProvisionRequest,
    api_key: str = Depends(require_api_key)
) -> ProvisionResponse:
    """
    Provision (generate + reserve) IDs per namespace config.

    Registry generates IDs according to the namespace's configured algorithm.
    Returns reserved entries that must be activated after entity creation.
    """
    if request.entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid entity_type: {request.entity_type}")

    ns = await Namespace.find_one({"prefix": request.namespace, "status": "active"})
    if not ns:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {request.namespace}")

    config = ns.get_id_algorithm(request.entity_type)
    ids = []
    entries = []

    for i in range(request.count):
        entry_id = await IdGeneratorService.generate_from_config(
            config, request.namespace, request.entity_type
        )

        composite_key = {}
        if request.composite_keys and i < len(request.composite_keys):
            composite_key = request.composite_keys[i]

        key_hash = HashService.compute_composite_key_hash(composite_key) if composite_key else ""

        entry = RegistryEntry(
            entry_id=entry_id,
            namespace=request.namespace,
            entity_type=request.entity_type,
            primary_composite_key=composite_key,
            primary_composite_key_hash=key_hash,
            status="reserved",
            created_by=request.created_by,
        )
        entry.rebuild_search_values()
        entries.append(entry)
        ids.append(ProvisionedId(entry_id=entry_id, status="reserved"))

    if entries:
        await RegistryEntry.insert_many(entries)

    return ProvisionResponse(
        namespace=request.namespace,
        entity_type=request.entity_type,
        ids=ids,
        total=len(ids),
    )


@router.post(
    "/reserve",
    response_model=ReserveBulkResponse,
    summary="Reserve client-provided IDs"
)
async def reserve_ids(
    items: List[ReserveItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> ReserveBulkResponse:
    """
    Validate and store client-provided IDs as reserved.

    Validates each ID against the namespace's configured format.
    """
    results = []
    reserved_count = 0
    error_count = 0
    entries_to_insert = []
    insert_indices = []

    for i, item in enumerate(items):
        try:
            if item.entity_type not in VALID_ENTITY_TYPES:
                results.append(ReserveItemResponse(
                    input_index=i, status="error",
                    error=f"Invalid entity_type: {item.entity_type}"
                ))
                error_count += 1
                continue

            # Check if ID already exists
            existing = await RegistryEntry.find_one({"entry_id": item.entry_id})
            if existing:
                results.append(ReserveItemResponse(
                    input_index=i, status="already_exists", entry_id=item.entry_id
                ))
                error_count += 1
                continue

            # Validate format against namespace config
            ns = await Namespace.find_one({"prefix": item.namespace, "status": "active"})
            if ns:
                config = ns.get_id_algorithm(item.entity_type)
                if not IdFormatValidator.validate(item.entry_id, config):
                    results.append(ReserveItemResponse(
                        input_index=i, status="invalid_format", entry_id=item.entry_id,
                        error=f"ID does not match configured format for {item.entity_type}"
                    ))
                    error_count += 1
                    continue

            composite_key = item.composite_key or {}
            key_hash = HashService.compute_composite_key_hash(composite_key) if composite_key else ""

            entry = RegistryEntry(
                entry_id=item.entry_id,
                namespace=item.namespace,
                entity_type=item.entity_type,
                primary_composite_key=composite_key,
                primary_composite_key_hash=key_hash,
                status="reserved",
                created_by=item.created_by,
            )
            entry.rebuild_search_values()
            entries_to_insert.append(entry)
            insert_indices.append(i)
            results.append(None)  # placeholder

        except Exception as e:
            results.append(ReserveItemResponse(
                input_index=i, status="error", error=str(e)
            ))
            error_count += 1

    if entries_to_insert:
        try:
            await RegistryEntry.insert_many(entries_to_insert)
            for pos, idx in enumerate(insert_indices):
                entry = entries_to_insert[pos]
                results[idx] = ReserveItemResponse(
                    input_index=idx, status="reserved", entry_id=entry.entry_id
                )
                reserved_count += 1
        except Exception as e:
            for pos, idx in enumerate(insert_indices):
                if results[idx] is None:
                    results[idx] = ReserveItemResponse(
                        input_index=idx, status="error",
                        error=f"Batch insert failed: {str(e)}"
                    )
                    error_count += 1

    return ReserveBulkResponse(
        results=[r for r in results if r is not None],
        total=len(items),
        reserved=reserved_count,
        errors=error_count,
    )


@router.post(
    "/activate",
    response_model=ActivateBulkResponse,
    summary="Activate reserved entries"
)
async def activate_entries(
    items: List[ActivateItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> ActivateBulkResponse:
    """Activate reserved entries, making them resolvable."""
    results = []
    activated_count = 0
    error_count = 0

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({"entry_id": item.entry_id})

            if not entry:
                results.append(ActivateItemResponse(
                    input_index=i, status="not_found", entry_id=item.entry_id
                ))
                error_count += 1
                continue

            if entry.status == "active":
                results.append(ActivateItemResponse(
                    input_index=i, status="already_active", entry_id=item.entry_id
                ))
                continue

            if entry.status != "reserved":
                results.append(ActivateItemResponse(
                    input_index=i, status="error", entry_id=item.entry_id,
                    error=f"Cannot activate entry with status '{entry.status}'"
                ))
                error_count += 1
                continue

            entry.status = "active"
            entry.updated_at = datetime.now(timezone.utc)
            await entry.save()

            results.append(ActivateItemResponse(
                input_index=i, status="activated", entry_id=item.entry_id
            ))
            activated_count += 1

        except Exception as e:
            results.append(ActivateItemResponse(
                input_index=i, status="error", entry_id=item.entry_id, error=str(e)
            ))
            error_count += 1

    return ActivateBulkResponse(
        results=results,
        total=len(items),
        activated=activated_count,
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
    """Look up registry entries by their IDs. Only active entries are resolvable."""
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                matched_via = None

                # 1. Find by entry_id
                q1: dict = {"entry_id": item.entry_id, "status": "active"}
                if item.namespace:
                    q1["namespace"] = item.namespace
                entry = await RegistryEntry.find_one(q1)
                if entry:
                    matched_via = "entry_id"

                # 2. Check additional_ids
                if not entry:
                    elem_match: dict = {"id": item.entry_id}
                    q2: dict = {"additional_ids": {"$elemMatch": elem_match}, "status": "active"}
                    if item.namespace:
                        q2["namespace"] = item.namespace
                    entry = await RegistryEntry.find_one(q2)
                    if entry:
                        matched_via = "additional_id"

                # 3. Composite key value search
                if not entry:
                    q3: dict = {"search_values": item.entry_id, "status": "active"}
                    if item.namespace:
                        q3["namespace"] = item.namespace
                    entry = await RegistryEntry.find_one(q3)
                    if entry:
                        matched_via = "composite_key_value"

                if not entry:
                    results.append(LookupResponse(input_index=i, status="not_found"))
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
                    input_index=i, entry=entry,
                    matched_via=matched_via, source_data=source_data
                ))
                found_count += 1

            except Exception as e:
                results.append(LookupResponse(input_index=i, status="error", error=str(e)))
                error_count += 1

    return LookupBulkResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count, errors=error_count,
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
    """Look up registry entries by their composite keys."""
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                key_hash = HashService.compute_composite_key_hash(item.composite_key)

                if item.search_synonyms:
                    query = {
                        "$or": [
                            {
                                "namespace": item.namespace,
                                "entity_type": item.entity_type,
                                "primary_composite_key_hash": key_hash
                            },
                            {
                                "synonyms": {
                                    "$elemMatch": {
                                        "namespace": item.namespace,
                                        "entity_type": item.entity_type,
                                        "composite_key_hash": key_hash
                                    }
                                }
                            }
                        ],
                        "status": "active"
                    }
                else:
                    query = {
                        "namespace": item.namespace,
                        "entity_type": item.entity_type,
                        "primary_composite_key_hash": key_hash,
                        "status": "active"
                    }

                entry = await RegistryEntry.find_one(query)

                if not entry:
                    results.append(LookupResponse(input_index=i, status="not_found"))
                    not_found_count += 1
                    continue

                matched_namespace = item.namespace
                matched_entity_type = item.entity_type
                matched_composite_key = item.composite_key
                if entry.primary_composite_key_hash != key_hash:
                    for syn in entry.synonyms:
                        if syn.composite_key_hash == key_hash:
                            matched_namespace = syn.namespace
                            matched_entity_type = syn.entity_type
                            matched_composite_key = syn.composite_key
                            break

                source_data = None
                if item.fetch_source_data and entry.source_info and entry.source_info.endpoint_url:
                    try:
                        resp = await client.get(entry.source_info.endpoint_url)
                        resp.raise_for_status()
                        source_data = resp.json()
                    except Exception as e:
                        source_data = {"error": f"Failed to fetch: {str(e)}"}

                results.append(build_lookup_response(
                    input_index=i, entry=entry,
                    matched_namespace=matched_namespace,
                    matched_entity_type=matched_entity_type,
                    matched_composite_key=matched_composite_key,
                    source_data=source_data
                ))
                found_count += 1

            except Exception as e:
                results.append(LookupResponse(input_index=i, status="error", error=str(e)))
                error_count += 1

    return LookupBulkResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count, errors=error_count,
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
                "entry_id": item.entry_id,
                "status": "active"
            })

            if not entry:
                results.append(UpdateEntryResponse(
                    input_index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue

            if item.source_info is not None:
                entry.source_info = item.source_info
            if item.metadata is not None:
                entry.metadata.update(item.metadata)

            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(UpdateEntryResponse(
                input_index=i, status="updated", registry_id=item.entry_id,
            ))

        except Exception as e:
            results.append(UpdateEntryResponse(
                input_index=i, status="error", registry_id=item.entry_id, error=str(e)
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
            entry = await RegistryEntry.find_one({"entry_id": item.entry_id})

            if not entry:
                results.append(DeleteResponse(
                    input_index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue

            entry.status = "inactive"
            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(DeleteResponse(
                input_index=i, status="deactivated", registry_id=item.entry_id,
            ))

        except Exception as e:
            results.append(DeleteResponse(
                input_index=i, status="error", registry_id=item.entry_id, error=str(e)
            ))

    return results
