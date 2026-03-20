"""Synonym management API endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends

from ..models.api_models import (
    AddSynonymItem,
    AddSynonymResponse,
    BulkMergeResponse,
    BulkSynonymAddResponse,
    BulkSynonymRemoveResponse,
    MergeItem,
    MergeResponse,
    RemoveSynonymItem,
    RemoveSynonymResponse,
)
from ..models.entry import RegistryEntry, Synonym
from ..services.auth import require_api_key
from ..services.hash import HashService

router = APIRouter()


@router.post(
    "/add",
    response_model=BulkSynonymAddResponse,
    summary="Add synonyms to existing entries (bulk)"
)
async def add_synonyms(
    items: list[AddSynonymItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkSynonymAddResponse:
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
            entry.updated_at = datetime.now(UTC)
            await entry.save()

            results.append(AddSynonymResponse(
                input_index=i, status="added", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(AddSynonymResponse(
                input_index=i, status="error", error=str(e)
            ))

    return BulkSynonymAddResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "added"),
        failed=sum(1 for r in results if r.status in ("target_not_found", "error")),
    )


@router.post(
    "/remove",
    response_model=BulkSynonymRemoveResponse,
    summary="Remove synonyms from entries (bulk)"
)
async def remove_synonyms(
    items: list[RemoveSynonymItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkSynonymRemoveResponse:
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
            entry.updated_at = datetime.now(UTC)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(RemoveSynonymResponse(
                input_index=i, status="removed", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(RemoveSynonymResponse(
                input_index=i, status="error", error=str(e)
            ))

    return BulkSynonymRemoveResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "removed"),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
    )


@router.post(
    "/merge",
    response_model=BulkMergeResponse,
    summary="Merge entries (ID-as-synonym) (bulk)"
)
async def merge_entries(
    items: list[MergeItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkMergeResponse:
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

            # Add deprecated entry_id as a synonym (so lookups by old ID resolve)
            entry_id_key = {"entry_id": deprecated.entry_id}
            entry_id_hash = HashService.compute_composite_key_hash(entry_id_key)
            entry_id_synonym = Synonym(
                namespace=deprecated.namespace,
                entity_type=deprecated.entity_type,
                composite_key=entry_id_key,
                composite_key_hash=entry_id_hash,
                created_by=item.updated_by,
            )
            if not any(s.composite_key_hash == entry_id_hash for s in preferred.synonyms):
                preferred.synonyms.append(entry_id_synonym)

            # Transfer all deprecated synonyms
            for syn in deprecated.synonyms:
                if not any(s.composite_key_hash == syn.composite_key_hash for s in preferred.synonyms):
                    preferred.synonyms.append(syn)

            deprecated.status = "inactive"
            deprecated.updated_at = datetime.now(UTC)
            deprecated.updated_by = item.updated_by
            await deprecated.save()

            preferred.rebuild_search_values()
            preferred.updated_at = datetime.now(UTC)
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

    return BulkMergeResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "merged"),
        failed=sum(1 for r in results if r.status in ("preferred_not_found", "deprecated_not_found", "error")),
    )
