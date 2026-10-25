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
from ..services.errors import error_fields, invalid_entity_type_fields
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


def _scope_mismatch(
    entry: RegistryEntry, namespace: str | None, entity_type: str | None
) -> dict | None:
    """The per-item refusal for a synonym scoped other than its entry, or None.

    A synonym names the entry's own identity, so its namespace and entity
    type are the entry's. One scoped elsewhere would resolve another
    namespace's or type's key to this entry (the namespace and type
    boundaries routed around), or match no lookup at all.
    """
    if namespace in (None, entry.namespace) and entity_type in (None, entry.entity_type):
        return None
    return {
        "error": (
            f"A synonym takes its entry's scope: namespace '{entry.namespace}', "
            f"entity_type '{entry.entity_type}' (got namespace '{namespace}', "
            f"entity_type '{entity_type}'). Omit synonym_namespace and "
            "synonym_entity_type, or send the entry's own."
        ),
        "error_code": "invalid_request",
        "details": {"rule": "synonym_scope_mismatch"},
    }


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

    A synonym is another composite key for its entry's own identity, so it
    lives in the entry's namespace and has the entry's entity type. A
    synonym_namespace or synonym_entity_type that differs is refused
    (synonym_scope_mismatch): stored, it would either never resolve or
    resolve across a namespace or type boundary.

    Needs write on the entry's namespace. A target the caller cannot read is
    reported target_not_found; one it can read but not write is forbidden.
    """
    results = []
    access = NamespaceAccess(identity)

    for i, item in enumerate(items):
        try:
            invalid = invalid_entity_type_fields(item.synonym_entity_type)
            if invalid:
                results.append(AddSynonymResponse(index=i, status="error", **invalid))
                continue

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

            refusal = await _write_refusal(access, entry.namespace)
            if refusal:
                results.append(AddSynonymResponse(
                    index=i, status="forbidden", error=refusal,
                ))
                continue

            mismatch = _scope_mismatch(entry, item.synonym_namespace, item.synonym_entity_type)
            if mismatch:
                results.append(AddSynonymResponse(
                    index=i, status="error", registry_id=entry.entry_id, **mismatch,
                ))
                continue
            synonym_namespace, synonym_entity_type = entry.namespace, entry.entity_type

            # Keys starting with "@" qualify system-generated synonyms (a
            # document's "@template"). Only system callers may write them, so
            # an explicit synonym cannot claim the key a document will need.
            reserved = sorted(k for k in item.synonym_composite_key if k.startswith("@"))
            if reserved and not identity.has_any_group(["wip-admins", "wip-services"]):
                results.append(AddSynonymResponse(
                    index=i, status="forbidden",
                    error=(
                        f"Synonym keys {reserved} are reserved for system-generated "
                        "synonyms (keys starting with '@')"
                    ),
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
                        synonym_namespace, synonym_entity_type,
                        synonym_hash, entry.entry_id, "synonym",
                        state="pending",
                    )
                    break
                except DuplicateKeyError:
                    existing_claim = await CompositeKeyClaim.find_existing(
                        synonym_namespace, synonym_entity_type, synonym_hash
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
                        error=f"Synonym already registered under different entry: {owner}",
                        error_code="synonym_conflict",
                    ))
                    claim_conflict = True
                    break
            if claim_conflict:
                continue

            synonym = Synonym(
                namespace=synonym_namespace,
                entity_type=synonym_entity_type,
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
                    synonym_namespace, synonym_entity_type,
                    synonym_hash, owner_entry_id=entry.entry_id,
                )
                raise

            # Entry committed — flip the claim to confirmed. Best-effort: a
            # failure leaves a pending-but-backed claim for the reconcile
            # pass; it must not fail the committed synonym.
            await CompositeKeyClaim.confirm(
                synonym_namespace, synonym_entity_type,
                synonym_hash, entry.entry_id,
            )

            results.append(AddSynonymResponse(
                index=i, status="added", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(AddSynonymResponse(
                index=i, status="error", **error_fields(e)
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

    The synonym's namespace and entity type default to the entry's. Explicit
    ones are honoured as sent — even an entity type that does not exist — so
    a synonym stored under another scope before add refused that can still
    be removed; remove only deletes what is there. Needs write on the entry's
    namespace and the synonym's; an unreadable target is not_found.
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

            synonym_namespace = item.synonym_namespace or entry.namespace
            synonym_entity_type = item.synonym_entity_type or entry.entity_type

            refusal = await _write_refusal(access, entry.namespace, synonym_namespace)
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
                if not (s.namespace == synonym_namespace
                        and s.entity_type == synonym_entity_type
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
                    synonym_namespace, synonym_entity_type,
                    synonym_hash, owner_entry_id=entry.entry_id,
                )
            except Exception as release_err:
                logger.warning(
                    "CASE-427: failed to release claim for %s (%s/%s) on entry "
                    "%s after synonym removal: %s",
                    synonym_hash, synonym_namespace, synonym_entity_type,
                    entry.entry_id, release_err,
                )

            results.append(RemoveSynonymResponse(
                index=i, status="removed", registry_id=entry.entry_id,
            ))

        except Exception as e:
            results.append(RemoveSynonymResponse(
                index=i, status="error", **error_fields(e)
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
                    error="Cannot merge an entry with itself",
                    error_code="invalid_request", details={"rule": "self_merge"},
                ))
                continue
            # Two entries are "the same real-world entity" only within one
            # namespace and entity type: merging across either would make a
            # document's old ID resolve to a template, or one namespace's key
            # resolve into another.
            if (preferred.namespace, preferred.entity_type) != (
                deprecated.namespace, deprecated.entity_type
            ):
                results.append(MergeResponse(
                    index=i, status="error",
                    preferred_id=preferred.entry_id,
                    deprecated_id=deprecated.entry_id,
                    error=(
                        "Only entries of one namespace and entity type merge: "
                        f"preferred is {preferred.namespace}/{preferred.entity_type}, "
                        f"deprecated is {deprecated.namespace}/{deprecated.entity_type}"
                    ),
                    error_code="invalid_request", details={"rule": "merge_scope_mismatch"},
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
                    error_code="invalid_request", details={"rule": "terms_merge_in_def_store"},
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
                index=i, status="error", **error_fields(e)
            ))

    return BulkMergeResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "merged"),
        failed=sum(1 for r in results if r.status in (
            "preferred_not_found", "deprecated_not_found", "forbidden", "error",
        )),
    )
