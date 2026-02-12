"""Synonym management API endpoints."""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body, Depends

from ..models.entry import RegistryEntry, Synonym
from ..models.api_models import (
    AddSynonymItem,
    AddSynonymResponse,
    RemoveSynonymItem,
    RemoveSynonymResponse,
    MergeItem,
    MergeResponse,
    SetPreferredItem,
    SetPreferredResponse,
)
from ..services.hash import HashService
from ..services.auth import require_api_key

router = APIRouter()


@router.post(
    "/add",
    response_model=List[AddSynonymResponse],
    summary="Add synonyms to existing entries (bulk)"
)
async def add_synonyms(
    items: List[AddSynonymItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> List[AddSynonymResponse]:
    """
    Add one or more synonyms to existing registry entries.

    Each synonym is a composite key in a potentially different pool
    that resolves to the same entity.
    """
    results = []

    for i, item in enumerate(items):
        try:
            # Find the target entry
            if item.target_id:
                # Look up by ID
                entry = await RegistryEntry.find_one({
                    "primary_pool_id": item.target_pool_id,
                    "entry_id": item.target_id,
                    "status": "active"
                })
            elif item.target_composite_key:
                # Look up by composite key
                target_hash = HashService.compute_composite_key_hash(item.target_composite_key)
                entry = await RegistryEntry.find_one({
                    "$or": [
                        {
                            "primary_pool_id": item.target_pool_id,
                            "primary_composite_key_hash": target_hash
                        },
                        {
                            "synonyms": {
                                "$elemMatch": {
                                    "pool_id": item.target_pool_id,
                                    "composite_key_hash": target_hash
                                }
                            }
                        }
                    ],
                    "status": "active"
                })
            else:
                results.append(AddSynonymResponse(
                    input_index=i,
                    status="error",
                    error="Must provide either target_id or target_composite_key"
                ))
                continue

            if not entry:
                results.append(AddSynonymResponse(
                    input_index=i,
                    status="target_not_found",
                ))
                continue

            # Compute hash for the new synonym
            synonym_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)

            # Check if this synonym already exists anywhere
            existing = await RegistryEntry.find_one({
                "$or": [
                    {"primary_composite_key_hash": synonym_hash},
                    {"synonyms.composite_key_hash": synonym_hash}
                ]
            })

            if existing:
                if str(existing.id) == str(entry.id):
                    results.append(AddSynonymResponse(
                        input_index=i,
                        status="already_exists",
                        registry_id=entry.entry_id,
                    ))
                else:
                    results.append(AddSynonymResponse(
                        input_index=i,
                        status="error",
                        error=f"Synonym already registered under different entry: {existing.entry_id}"
                    ))
                continue

            # Create and add the synonym
            synonym = Synonym(
                pool_id=item.synonym_pool_id,
                composite_key=item.synonym_composite_key,
                composite_key_hash=synonym_hash,
                source_info=item.synonym_source_info,
                created_by=item.created_by,
            )

            entry.synonyms.append(synonym)
            entry.rebuild_search_values()
            entry.updated_at = datetime.now(timezone.utc)
            await entry.save()

            results.append(AddSynonymResponse(
                input_index=i,
                status="added",
                registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(AddSynonymResponse(
                input_index=i,
                status="error",
                error=str(e)
            ))

    return results


@router.post(
    "/remove",
    response_model=List[RemoveSynonymResponse],
    summary="Remove synonyms from entries (bulk)"
)
async def remove_synonyms(
    items: List[RemoveSynonymItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> List[RemoveSynonymResponse]:
    """
    Remove one or more synonyms from registry entries.

    Note: Cannot remove the primary composite key, only synonyms.
    """
    results = []

    for i, item in enumerate(items):
        try:
            # Find the target entry
            entry = await RegistryEntry.find_one({
                "primary_pool_id": item.target_pool_id,
                "entry_id": item.target_id,
                "status": "active"
            })

            if not entry:
                results.append(RemoveSynonymResponse(
                    input_index=i,
                    status="not_found",
                    registry_id=item.target_id,
                ))
                continue

            # Find the synonym to remove
            synonym_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)

            # Filter out the synonym
            original_count = len(entry.synonyms)
            entry.synonyms = [
                s for s in entry.synonyms
                if not (s.pool_id == item.synonym_pool_id and s.composite_key_hash == synonym_hash)
            ]

            if len(entry.synonyms) == original_count:
                results.append(RemoveSynonymResponse(
                    input_index=i,
                    status="not_found",
                    registry_id=entry.entry_id,
                    error="Synonym not found in entry"
                ))
                continue

            entry.rebuild_search_values()
            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(RemoveSynonymResponse(
                input_index=i,
                status="removed",
                registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(RemoveSynonymResponse(
                input_index=i,
                status="error",
                error=str(e)
            ))

    return results


@router.post(
    "/merge",
    response_model=List[MergeResponse],
    summary="Merge entries (ID-as-synonym) (bulk)"
)
async def merge_entries(
    items: List[MergeItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> List[MergeResponse]:
    """
    Merge two registry entries, making one a synonym of the other.

    This is used to resolve duplicate registrations. The deprecated entry's
    ID becomes an additional_id on the preferred entry, and all its synonyms
    are moved to the preferred entry.

    Both IDs will continue to resolve to the same entity.
    """
    results = []

    for i, item in enumerate(items):
        try:
            # Find the preferred entry
            preferred = await RegistryEntry.find_one({
                "primary_pool_id": item.preferred_pool_id,
                "entry_id": item.preferred_id,
                "status": "active"
            })

            if not preferred:
                results.append(MergeResponse(
                    input_index=i,
                    status="preferred_not_found",
                    preferred_id=item.preferred_id,
                ))
                continue

            # Find the deprecated entry
            deprecated = await RegistryEntry.find_one({
                "primary_pool_id": item.deprecated_pool_id,
                "entry_id": item.deprecated_id,
                "status": "active"
            })

            if not deprecated:
                results.append(MergeResponse(
                    input_index=i,
                    status="deprecated_not_found",
                    deprecated_id=item.deprecated_id,
                ))
                continue

            # Prevent merging an entry with itself
            if str(preferred.id) == str(deprecated.id):
                results.append(MergeResponse(
                    input_index=i,
                    status="error",
                    error="Cannot merge an entry with itself"
                ))
                continue

            # Add the deprecated ID to additional_ids
            preferred.additional_ids.append({
                "pool_id": deprecated.primary_pool_id,
                "id": deprecated.entry_id
            })

            # Also add any additional_ids from the deprecated entry
            preferred.additional_ids.extend(deprecated.additional_ids)

            # Add the deprecated entry's primary key as a synonym
            deprecated_as_synonym = Synonym(
                pool_id=deprecated.primary_pool_id,
                composite_key=deprecated.primary_composite_key,
                composite_key_hash=deprecated.primary_composite_key_hash,
                source_info=deprecated.source_info,
                created_by=item.updated_by,
            )
            preferred.synonyms.append(deprecated_as_synonym)

            # Move all synonyms from deprecated to preferred
            for syn in deprecated.synonyms:
                # Check for duplicates
                if not any(s.composite_key_hash == syn.composite_key_hash for s in preferred.synonyms):
                    preferred.synonyms.append(syn)

            # Mark the deprecated entry as inactive
            deprecated.status = "inactive"
            deprecated.is_preferred = False
            deprecated.updated_at = datetime.now(timezone.utc)
            deprecated.updated_by = item.updated_by
            await deprecated.save()

            # Save the preferred entry
            preferred.rebuild_search_values()
            preferred.updated_at = datetime.now(timezone.utc)
            preferred.updated_by = item.updated_by
            await preferred.save()

            results.append(MergeResponse(
                input_index=i,
                status="merged",
                preferred_id=preferred.entry_id,
                deprecated_id=deprecated.entry_id,
            ))

        except Exception as e:
            results.append(MergeResponse(
                input_index=i,
                status="error",
                error=str(e)
            ))

    return results


@router.post(
    "/set-preferred",
    response_model=List[SetPreferredResponse],
    summary="Change the preferred ID (bulk)"
)
async def set_preferred_ids(
    items: List[SetPreferredItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> List[SetPreferredResponse]:
    """
    Change which ID is the preferred/canonical ID for an entry.

    The new preferred ID must either be:
    - The current entry_id (no change)
    - One of the additional_ids

    This operation swaps the IDs but keeps all synonyms intact.
    """
    results = []

    for i, item in enumerate(items):
        try:
            # Find the entry
            entry = await RegistryEntry.find_one({
                "primary_pool_id": item.pool_id,
                "entry_id": item.entry_id,
                "status": "active"
            })

            if not entry:
                results.append(SetPreferredResponse(
                    input_index=i,
                    status="not_found",
                ))
                continue

            # Check if new preferred is already the current
            if (item.new_preferred_pool_id == entry.primary_pool_id and
                item.new_preferred_id == entry.entry_id):
                results.append(SetPreferredResponse(
                    input_index=i,
                    status="updated",
                    new_preferred_id=entry.entry_id,
                ))
                continue

            # Check if new preferred is in additional_ids
            found_in_additional = None
            for idx, add_id in enumerate(entry.additional_ids):
                if (add_id["pool_id"] == item.new_preferred_pool_id and
                    add_id["id"] == item.new_preferred_id):
                    found_in_additional = idx
                    break

            if found_in_additional is None:
                results.append(SetPreferredResponse(
                    input_index=i,
                    status="id_not_in_entry",
                    error="New preferred ID not found in entry's additional_ids"
                ))
                continue

            # Swap the IDs
            old_primary_pool_id = entry.primary_pool_id
            old_primary_id = entry.entry_id

            # Remove from additional_ids
            entry.additional_ids.pop(found_in_additional)

            # Add old primary to additional_ids
            entry.additional_ids.append({
                "pool_id": old_primary_pool_id,
                "id": old_primary_id
            })

            # Set new primary
            entry.primary_pool_id = item.new_preferred_pool_id
            entry.entry_id = item.new_preferred_id

            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(SetPreferredResponse(
                input_index=i,
                status="updated",
                new_preferred_id=item.new_preferred_id,
            ))

        except Exception as e:
            results.append(SetPreferredResponse(
                input_index=i,
                status="error",
                error=str(e)
            ))

    return results
