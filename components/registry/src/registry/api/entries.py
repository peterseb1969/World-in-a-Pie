"""Registry entry management API endpoints."""

from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Body, Depends

from ..models.namespace import Namespace, IdGeneratorConfig, IdGeneratorType
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


async def get_namespace_config(namespace_id: str) -> Optional[Namespace]:
    """Get namespace configuration, returning None if not found."""
    return await Namespace.find_one({"namespace_id": namespace_id, "status": "active"})


def build_lookup_response(
    input_index: int,
    entry: Optional[RegistryEntry],
    status: str = "found",
    matched_namespace: Optional[str] = None,
    matched_composite_key: Optional[dict] = None,
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
        preferred_namespace=entry.primary_namespace,
        additional_ids=entry.additional_ids,
        matched_namespace=matched_namespace or entry.primary_namespace,
        matched_composite_key=matched_composite_key or entry.primary_composite_key,
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
    - If new, generates an ID based on namespace configuration and creates entry
    """
    results = []
    created_count = 0
    exists_count = 0
    error_count = 0

    for i, item in enumerate(items):
        try:
            # Compute hash for the composite key
            key_hash = HashService.compute_composite_key_hash(item.composite_key)

            # Check if this exact key already exists
            existing = await RegistryEntry.find_one({
                "$or": [
                    {"primary_composite_key_hash": key_hash},
                    {"synonyms.composite_key_hash": key_hash}
                ]
            })

            if existing:
                results.append(RegisterKeyResponse(
                    input_index=i,
                    status="already_exists",
                    registry_id=existing.entry_id,
                    namespace=existing.primary_namespace,
                ))
                exists_count += 1
                continue

            # Get namespace configuration
            ns_config = await get_namespace_config(item.namespace)
            if not ns_config:
                # Use default config if namespace not found
                id_gen_config = IdGeneratorConfig()
            else:
                id_gen_config = ns_config.id_generator

            # Generate ID based on namespace config
            if id_gen_config.type == IdGeneratorType.EXTERNAL:
                # External IDs must be provided in metadata or composite key
                entry_id = item.metadata.get("external_id") or item.composite_key.get("id")
                if not entry_id:
                    results.append(RegisterKeyResponse(
                        input_index=i,
                        status="error",
                        error="External namespace requires 'external_id' in metadata or 'id' in composite_key"
                    ))
                    error_count += 1
                    continue
            else:
                entry_id = IdGeneratorService.generate(id_gen_config, item.namespace)

            # Create the registry entry
            entry = RegistryEntry(
                entry_id=entry_id,
                primary_namespace=item.namespace,
                primary_composite_key=item.composite_key,
                primary_composite_key_hash=key_hash,
                source_info=item.source_info,
                created_by=item.created_by,
                metadata=item.metadata,
            )
            await entry.create()

            results.append(RegisterKeyResponse(
                input_index=i,
                status="created",
                registry_id=entry_id,
                namespace=item.namespace,
            ))
            created_count += 1

        except Exception as e:
            results.append(RegisterKeyResponse(
                input_index=i,
                status="error",
                error=str(e)
            ))
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
                # Find by entry_id and namespace
                entry = await RegistryEntry.find_one({
                    "primary_namespace": item.namespace,
                    "entry_id": item.entry_id,
                    "status": "active"
                })

                # Also check additional_ids if not found as primary
                if not entry:
                    entry = await RegistryEntry.find_one({
                        "additional_ids": {
                            "$elemMatch": {
                                "namespace": item.namespace,
                                "id": item.entry_id
                            }
                        },
                        "status": "active"
                    })

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
                                "primary_namespace": item.namespace,
                                "primary_composite_key_hash": key_hash
                            },
                            {
                                "synonyms": {
                                    "$elemMatch": {
                                        "namespace": item.namespace,
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
                        "primary_namespace": item.namespace,
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
                matched_namespace = item.namespace
                matched_composite_key = item.composite_key
                if entry.primary_composite_key_hash != key_hash:
                    # Match was in a synonym
                    for syn in entry.synonyms:
                        if syn.composite_key_hash == key_hash:
                            matched_namespace = syn.namespace
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
                    matched_namespace=matched_namespace,
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
                "primary_namespace": item.namespace,
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
                "primary_namespace": item.namespace,
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
