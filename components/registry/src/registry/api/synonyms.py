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
from ..services.claims import resolve_stale_claim
from ..services.hash import HashService
from .grants import NamespaceAccess

logger = logging.getLogger("registry.synonyms")

router = APIRouter()


async def _write_refusal(
    access: NamespaceAccess, *namespaces: str
) -> str | None:
    """Why the caller may not write a synonym touching these namespaces, or None.

    A synonym change writes the entry's namespace and claims a composite key
    in the synonym's namespace, so it needs write on both.
    """
    for namespace in dict.fromkeys(namespaces):
        if not await access.allows(namespace, "write"):
            return f"Requires write permission on namespace '{namespace}'"
    return None


@router.post(
    "/add",
    response_model=BulkSynonymAddResponse,
    summary="Add synonyms to existing entries (bulk)"
)
async def add_synonyms(
    items: list[AddSynonymItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkSynonymAddResponse:
    """Add one or more synonyms to existing registry entries.

    Needs write on the entry's namespace and on the synonym's. A target the
    caller cannot read is reported target_not_found; one it can read but
    not write is forbidden.
    """
    results = []
    access = NamespaceAccess(identity)

    for i, item in enumerate(items):
        try:
            # Find the target entry by ID
            entry = await RegistryEntry.find_one({
                "entry_id": item.target_id,
                "status": "active"
            })

            if not entry or not await access.allows(entry.namespace, "read"):
                results.append(AddSynonymResponse(
                    index=i, status="target_not_found",
                ))
                continue

            refusal = await _write_refusal(access, entry.namespace, item.synonym_namespace)
            if refusal:
                results.append(AddSynonymResponse(
                    index=i, status="forbidden", error=refusal,
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
                    index=i, status="already_exists",
                    registry_id=entry.entry_id,
                ))
                continue

            # Atomic uniqueness gate (CASE-427, two-phase since CASE-554):
            # claim the hash pending before writing; the unique index is the
            # lock — no check-then-insert race. Confirmed after the save.
            claim_conflict = False
            for attempt in (1, 2):
                try:
                    await CompositeKeyClaim.claim(
                        item.synonym_namespace, item.synonym_entity_type,
                        synonym_hash, entry.entry_id, "synonym",
                        state="pending",
                    )
                    break
                except DuplicateKeyError:
                    existing_claim = await CompositeKeyClaim.find_existing(
                        item.synonym_namespace, item.synonym_entity_type, synonym_hash
                    )
                    owner = existing_claim.owner_entry_id if existing_claim else "?"
                    if existing_claim and owner == entry.entry_id:
                        # Self-owned claim (embedded write died mid-flight) —
                        # safe to reclaim by writing the embedded synonym below.
                        break
                    # Stale claim from a dead request or crashed delete? Heal
                    # inline and retry once; the unique index arbitrates
                    # racing healers. Genuine owner → never auto-steal.
                    if attempt == 1 and await resolve_stale_claim(existing_claim):
                        continue
                    results.append(AddSynonymResponse(
                        index=i, status="error",
                        error=f"Synonym already registered under different entry: {owner}"
                    ))
                    claim_conflict = True
                    break
            if claim_conflict:
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
                # Compensate the no-transaction window: release the pending
                # claim we just took so it doesn't dangle, then re-raise.
                # Anything this release misses is pending and dies in the
                # reconcile pass.
                await CompositeKeyClaim.release(
                    item.synonym_namespace, item.synonym_entity_type,
                    synonym_hash, owner_entry_id=entry.entry_id,
                )
                raise

            # Entry committed — flip the claim to confirmed. Best-effort: a
            # failure leaves a pending-but-backed claim for the reconcile
            # pass; it must not fail the committed synonym.
            await CompositeKeyClaim.confirm(
                item.synonym_namespace, item.synonym_entity_type,
                synonym_hash, entry.entry_id,
            )

            results.append(AddSynonymResponse(
                index=i, status="added", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(AddSynonymResponse(
                index=i, status="error", error=str(e)
            ))

    return BulkSynonymAddResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "added"),
        failed=sum(1 for r in results if r.status in ("target_not_found", "forbidden", "error")),
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
    """Remove one or more synonyms from registry entries.

    Same permission rule as add: write on the entry's namespace and the
    synonym's; an unreadable target is not_found.
    """
    results = []
    access = NamespaceAccess(identity)

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "entry_id": item.target_id,
                "status": "active"
            })

            if not entry or not await access.allows(entry.namespace, "read"):
                results.append(RemoveSynonymResponse(
                    index=i, status="not_found", registry_id=item.target_id,
                ))
                continue

            refusal = await _write_refusal(access, entry.namespace, item.synonym_namespace)
            if refusal:
                results.append(RemoveSynonymResponse(
                    index=i, status="forbidden", registry_id=entry.entry_id,
                    error=refusal,
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
                    index=i, status="not_found", registry_id=entry.entry_id,
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
                index=i, status="removed", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(RemoveSynonymResponse(
                index=i, status="error", error=str(e)
            ))

    return BulkSynonymRemoveResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "removed"),
        failed=sum(1 for r in results if r.status in ("not_found", "forbidden", "error")),
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
    """Merge two entries, making the deprecated one a synonym of the preferred.

    Needs write on both entries' namespaces; an entry the caller cannot read
    is reported as not found.
    """
    results = []
    access = NamespaceAccess(identity)

    for i, item in enumerate(items):
        try:
            preferred = await RegistryEntry.find_one({
                "entry_id": item.preferred_id, "status": "active"
            })
            if not preferred or not await access.allows(preferred.namespace, "read"):
                results.append(MergeResponse(
                    index=i, status="preferred_not_found",
                    preferred_id=item.preferred_id,
                ))
                continue

            deprecated = await RegistryEntry.find_one({
                "entry_id": item.deprecated_id, "status": "active"
            })
            if not deprecated or not await access.allows(deprecated.namespace, "read"):
                results.append(MergeResponse(
                    index=i, status="deprecated_not_found",
                    deprecated_id=item.deprecated_id,
                ))
                continue

            refusal = await _write_refusal(access, preferred.namespace, deprecated.namespace)
            if refusal:
                results.append(MergeResponse(
                    index=i, status="forbidden",
                    preferred_id=preferred.entry_id,
                    deprecated_id=deprecated.entry_id,
                    error=refusal,
                ))
                continue

            if str(preferred.id) == str(deprecated.id):
                results.append(MergeResponse(
                    index=i, status="error",
                    error="Cannot merge an entry with itself"
                ))
                continue
            # A term's identity lives in two places — this entry and the
            # def-store term whose value and names drive validation — so only
            # def-store may merge terms: a Registry-only merge would leave the
            # absorbed term active and its value still resolving to it.
            if "terms" in (preferred.entity_type, deprecated.entity_type) and not (
                item.owner_merge and identity.has_any_group(["wip-admins", "wip-services"])
            ):
                results.append(MergeResponse(
                    index=i, status="error",
                    error=(
                        "Terms are merged by def-store (POST /api/def-store/terms/merge), "
                        "which moves the absorbed term's value and names to the survivor "
                        "and then merges these entries."
                    ),
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
                index=i, status="merged",
                preferred_id=preferred.entry_id,
                deprecated_id=deprecated.entry_id,
            ))

        except Exception as e:
            results.append(MergeResponse(
                index=i, status="error", error=str(e)
            ))

    return BulkMergeResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "merged"),
        failed=sum(1 for r in results if r.status in (
            "preferred_not_found", "deprecated_not_found", "forbidden", "error",
        )),
    )
