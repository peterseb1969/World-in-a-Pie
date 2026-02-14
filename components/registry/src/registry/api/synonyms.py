"""Synonym management API endpoints."""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Body, Depends

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
    """Add one or more synonyms to existing registry entries."""
    results = []

    for i, item in enumerate(items):
        try:
            # Find the target entry by ID
            entry = await RegistryEntry.find_one({
                "entry_id": item.target_id,
                "status": "active"
            })

            if not entry:
                results.append(AddSynonymResponse(
                    input_index=i, status="target_not_found",
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
                        input_index=i, status="already_exists",
                        registry_id=entry.entry_id,
                    ))
                else:
                    results.append(AddSynonymResponse(
                        input_index=i, status="error",
                        error=f"Synonym already registered under different entry: {existing.entry_id}"
                    ))
                continue

            synonym = Synonym(
                namespace=item.synonym_namespace,
                entity_type=item.synonym_entity_type,
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
                input_index=i, status="added", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(AddSynonymResponse(
                input_index=i, status="error", error=str(e)
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
    """Remove one or more synonyms from registry entries."""
    results = []

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "entry_id": item.target_id,
                "status": "active"
            })

            if not entry:
                results.append(RemoveSynonymResponse(
                    input_index=i, status="not_found", registry_id=item.target_id,
                ))
                continue

            synonym_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)

            original_count = len(entry.synonyms)
            entry.synonyms = [
                s for s in entry.synonyms
                if not (s.namespace == item.synonym_namespace
                        and s.entity_type == item.synonym_entity_type
                        and s.composite_key_hash == synonym_hash)
            ]

            if len(entry.synonyms) == original_count:
                results.append(RemoveSynonymResponse(
                    input_index=i, status="not_found", registry_id=entry.entry_id,
                    error="Synonym not found in entry"
                ))
                continue

            entry.rebuild_search_values()
            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(RemoveSynonymResponse(
                input_index=i, status="removed", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(RemoveSynonymResponse(
                input_index=i, status="error", error=str(e)
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
    """Merge two entries, making the deprecated one a synonym of the preferred."""
    results = []

    for i, item in enumerate(items):
        try:
            preferred = await RegistryEntry.find_one({
                "entry_id": item.preferred_id, "status": "active"
            })
            if not preferred:
                results.append(MergeResponse(
                    input_index=i, status="preferred_not_found",
                    preferred_id=item.preferred_id,
                ))
                continue

            deprecated = await RegistryEntry.find_one({
                "entry_id": item.deprecated_id, "status": "active"
            })
            if not deprecated:
                results.append(MergeResponse(
                    input_index=i, status="deprecated_not_found",
                    deprecated_id=item.deprecated_id,
                ))
                continue

            if str(preferred.id) == str(deprecated.id):
                results.append(MergeResponse(
                    input_index=i, status="error",
                    error="Cannot merge an entry with itself"
                ))
                continue

            preferred.additional_ids.append({
                "namespace": deprecated.namespace,
                "entity_type": deprecated.entity_type,
                "id": deprecated.entry_id
            })
            preferred.additional_ids.extend(deprecated.additional_ids)

            deprecated_as_synonym = Synonym(
                namespace=deprecated.namespace,
                entity_type=deprecated.entity_type,
                composite_key=deprecated.primary_composite_key,
                composite_key_hash=deprecated.primary_composite_key_hash,
                source_info=deprecated.source_info,
                created_by=item.updated_by,
            )
            preferred.synonyms.append(deprecated_as_synonym)

            for syn in deprecated.synonyms:
                if not any(s.composite_key_hash == syn.composite_key_hash for s in preferred.synonyms):
                    preferred.synonyms.append(syn)

            deprecated.status = "inactive"
            deprecated.is_preferred = False
            deprecated.updated_at = datetime.now(timezone.utc)
            deprecated.updated_by = item.updated_by
            await deprecated.save()

            preferred.rebuild_search_values()
            preferred.updated_at = datetime.now(timezone.utc)
            preferred.updated_by = item.updated_by
            await preferred.save()

            results.append(MergeResponse(
                input_index=i, status="merged",
                preferred_id=preferred.entry_id,
                deprecated_id=deprecated.entry_id,
            ))

        except Exception as e:
            results.append(MergeResponse(
                input_index=i, status="error", error=str(e)
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
    """Change which ID is preferred for an entry."""
    results = []

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "entry_id": item.entry_id, "status": "active"
            })

            if not entry:
                results.append(SetPreferredResponse(
                    input_index=i, status="not_found",
                ))
                continue

            if item.new_preferred_id == entry.entry_id:
                results.append(SetPreferredResponse(
                    input_index=i, status="updated",
                    new_preferred_id=entry.entry_id,
                ))
                continue

            found_in_additional = None
            for idx, add_id in enumerate(entry.additional_ids):
                if add_id["id"] == item.new_preferred_id:
                    found_in_additional = idx
                    break

            if found_in_additional is None:
                results.append(SetPreferredResponse(
                    input_index=i, status="id_not_in_entry",
                    error="New preferred ID not found in entry's additional_ids"
                ))
                continue

            old_id = entry.entry_id
            old_ns = entry.namespace
            old_et = entry.entity_type

            new_add_id = entry.additional_ids.pop(found_in_additional)
            entry.additional_ids.append({
                "namespace": old_ns,
                "entity_type": old_et,
                "id": old_id
            })

            entry.entry_id = item.new_preferred_id
            if "namespace" in new_add_id:
                entry.namespace = new_add_id["namespace"]
            if "entity_type" in new_add_id:
                entry.entity_type = new_add_id["entity_type"]

            entry.updated_at = datetime.now(timezone.utc)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(SetPreferredResponse(
                input_index=i, status="updated",
                new_preferred_id=item.new_preferred_id,
            ))

        except Exception as e:
            results.append(SetPreferredResponse(
                input_index=i, status="error", error=str(e)
            ))

    return results
