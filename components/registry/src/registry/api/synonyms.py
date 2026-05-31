"""Synonym management API endpoints."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends
from pymongo.errors import DuplicateKeyError

from wip_auth import UserIdentity

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
from ..models.composite_key_claim import CompositeKeyClaim
from ..models.entry import RegistryEntry, Synonym
from ..services.auth import require_api_key
from ..services.hash import HashService

logger = logging.getLogger("registry.synonyms")

router = APIRouter()


@router.post(
    "/add",
    response_model=BulkSynonymAddResponse,
    summary="Add synonyms to existing entries (bulk)"
)
async def add_synonyms(
    items: list[AddSynonymItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
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

            # Fast path: this entry already owns the hash (primary or embedded
            # synonym) → idempotent already_exists, no claim churn.
            if (
                entry.primary_composite_key_hash == synonym_hash
                or entry.find_synonym_by_hash(synonym_hash) is not None
            ):
                results.append(AddSynonymResponse(
                    input_index=i, status="already_exists",
                    registry_id=entry.entry_id,
                ))
                continue

            # Atomic uniqueness gate (CASE-427): claim the hash before writing.
            # The unique index is the lock — no check-then-insert race.
            try:
                await CompositeKeyClaim.claim(
                    item.synonym_namespace, item.synonym_entity_type,
                    synonym_hash, entry.entry_id, "synonym",
                )
            except DuplicateKeyError:
                existing_claim = await CompositeKeyClaim.find_existing(
                    item.synonym_namespace, item.synonym_entity_type, synonym_hash
                )
                owner = existing_claim.owner_entry_id if existing_claim else "?"
                if existing_claim and owner == entry.entry_id:
                    # Self-owned orphan (claim points at us, embedded missing) —
                    # safe to reclaim by writing the embedded synonym below.
                    pass
                else:
                    # Owned by a different entry. Never auto-steal — even if that
                    # entry doesn't currently embed it (cross-owner orphan,
                    # cleared by reconciliation), the hot path stays safe.
                    results.append(AddSynonymResponse(
                        input_index=i, status="error",
                        error=f"Synonym already registered under different entry: {owner}"
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
            try:
                await entry.save()
            except Exception:
                # Compensate the no-transaction window: release the claim we
                # just took so it doesn't dangle as an orphan, then re-raise.
                await CompositeKeyClaim.release(
                    item.synonym_namespace, item.synonym_entity_type,
                    synonym_hash, owner_entry_id=entry.entry_id,
                )
                raise

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
    identity: UserIdentity = Depends(require_api_key)
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

            # Release the claim (CASE-427), scoped to this owner so we never
            # delete a claim that belongs elsewhere. A leftover claim is a
            # benign orphan reconciliation would clear, so failures here don't
            # fail the user's remove.
            try:
                await CompositeKeyClaim.release(
                    item.synonym_namespace, item.synonym_entity_type,
                    synonym_hash, owner_entry_id=entry.entry_id,
                )
            except Exception as release_err:
                logger.warning(
                    "CASE-427: failed to release claim for %s (%s/%s) on entry "
                    "%s after synonym removal: %s",
                    synonym_hash, item.synonym_namespace, item.synonym_entity_type,
                    entry.entry_id, release_err,
                )

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
    identity: UserIdentity = Depends(require_api_key)
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
                # CASE-427: claim the entry_id-as-synonym for preferred. The
                # hash is unique to deprecated's id, so a conflict can only be a
                # prior partial merge of this pair → transfer to preferred.
                try:
                    await CompositeKeyClaim.claim(
                        deprecated.namespace, deprecated.entity_type,
                        entry_id_hash, preferred.entry_id, "synonym",
                    )
                except DuplicateKeyError:
                    await CompositeKeyClaim.transfer(
                        deprecated.namespace, deprecated.entity_type,
                        entry_id_hash, preferred.entry_id,
                    )
                preferred.synonyms.append(entry_id_synonym)

            # Transfer all deprecated synonyms (claim re-points to preferred).
            for syn in deprecated.synonyms:
                if not any(s.composite_key_hash == syn.composite_key_hash for s in preferred.synonyms):
                    await CompositeKeyClaim.transfer(
                        syn.namespace, syn.entity_type, syn.composite_key_hash,
                        preferred.entry_id,
                    )
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
