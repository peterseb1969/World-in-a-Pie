"""Synonym management API endpoints."""

import logging
from datetime import UTC, datetime

from beanie.odm.utils.encoder import Encoder
from fastapi import APIRouter, Body, Depends
from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError

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
from ..services.claims import (
    claim_keys_pending_bulk,
    confirm_keys_bulk,
    new_claim_token,
    release_keys_bulk,
)
from ..services.errors import error_fields, invalid_entity_type_fields
from ..services.event_bus import entries_changed
from ..services.hash import HashService
from ..services.key_scope import key_scope_refusal
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
    resolve across a namespace or type boundary. So is a key whose own 'ns'
    member names another namespace: it would be held here while naming
    there, and a lookup by that key would find this entry for the other
    namespace.

    Needs write on the entry's namespace. A target the caller cannot read is
    reported target_not_found; one it can read but not write is forbidden.

    The request is handled in bulk: one read of the target entries, one
    claim insert for the new keys, one write per changed entry and one
    confirm. The items are still decided in order, so the per-item results
    are those of taking them one at a time: a later item for the same entry
    sees what the earlier ones added.
    """
    results: list[AddSynonymResponse | None] = [None] * len(items)
    access = NamespaceAccess(identity)

    loaded = await _load_active_entries({item.target_id for item in items})

    # Decide every item in order, in memory. An addition needs a claim; it is
    # applied to its entry only once the claim holds.
    planned: list[tuple[int, RegistryEntry, Synonym, Synonym | None]] = []
    pending: dict[tuple[str, str], int] = {}  # (entry_id, hash) -> planned index
    dependents: dict[int, list[int]] = {}  # planned index -> items riding on it
    for i, item in enumerate(items):
        try:
            invalid = invalid_entity_type_fields(item.synonym_entity_type)
            if invalid:
                results[i] = AddSynonymResponse(index=i, status="error", **invalid)
                continue

            entry = loaded.get(item.target_id)
            if not entry or not await access.allows(entry.namespace, "read"):
                results[i] = AddSynonymResponse(index=i, status="target_not_found")
                continue

            refusal = await _write_refusal(access, entry.namespace)
            if refusal:
                results[i] = AddSynonymResponse(index=i, status="forbidden", error=refusal)
                continue

            mismatch = _scope_mismatch(
                entry, item.synonym_namespace, item.synonym_entity_type,
            ) or key_scope_refusal(
                item.synonym_composite_key, entry.namespace, rule="synonym_scope_mismatch",
            )
            if mismatch:
                results[i] = AddSynonymResponse(
                    index=i, status="error", registry_id=entry.entry_id, **mismatch,
                )
                continue

            # Keys starting with "@" qualify system-generated synonyms (a
            # document's "@template"). Only system callers may write them, so
            # an explicit synonym cannot claim the key a document will need.
            reserved = sorted(k for k in item.synonym_composite_key if k.startswith("@"))
            if reserved and not identity.has_any_group(["wip-admins", "wip-services"]):
                results[i] = AddSynonymResponse(
                    index=i, status="forbidden",
                    error=(
                        f"Synonym keys {reserved} are reserved for system-generated "
                        "synonyms (keys starting with '@')"
                    ),
                )
                continue

            synonym_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)

            # The same synonym for the same entry earlier in this request: it
            # shares that item's outcome (already_exists once it is added).
            earlier = pending.get((entry.entry_id, synonym_hash))
            if earlier is not None:
                dependents.setdefault(earlier, []).append(i)
                continue

            # Fast path: this entry already owns the hash (primary or embedded
            # synonym) → idempotent already_exists, no claim churn. An inactive
            # copy (released when the entity was retired) does not count: the
            # add claims the key again, or reports who holds it now.
            recorded = entry.find_synonym_by_hash(synonym_hash)
            if entry.primary_composite_key_hash == synonym_hash or (
                recorded is not None and recorded.status != "inactive"
            ):
                results[i] = AddSynonymResponse(
                    index=i, status="already_exists", registry_id=entry.entry_id,
                )
                continue

            synonym = Synonym(
                namespace=entry.namespace,
                entity_type=entry.entity_type,
                composite_key=item.synonym_composite_key,
                composite_key_hash=synonym_hash,
                source_info=item.synonym_source_info,
                created_by=item.created_by,
            )
            pending[(entry.entry_id, synonym_hash)] = len(planned)
            planned.append((i, entry, synonym, recorded))
        except Exception as e:
            results[i] = AddSynonymResponse(index=i, status="error", **error_fields(e))

    def settle(p: int, response: AddSynonymResponse) -> None:
        i = planned[p][0]
        results[i] = response.model_copy(update={"index": i})
        for d in dependents.get(p, []):
            follow = response
            if response.status == "added":
                follow = AddSynonymResponse(
                    index=d, status="already_exists", registry_id=response.registry_id,
                )
            results[d] = follow.model_copy(update={"index": d})

    # Atomic uniqueness gate (CASE-427, two-phase since CASE-554): claim every
    # new hash pending before writing; the unique index is the lock. A claim
    # the entry itself already holds (its write died mid-flight) is fine, a
    # stale holder is healed and retried once, a genuine owner is never
    # stolen from.
    token = new_claim_token()
    blocked = await claim_keys_pending_bulk([
        [(entry.namespace, entry.entity_type, synonym.composite_key_hash, entry.entry_id, "synonym")]
        for _, entry, synonym, _ in planned
    ], token)
    changed: dict[str, RegistryEntry] = {}
    by_entry: dict[str, list[int]] = {}
    for p, ((_i, entry, synonym, recorded), owner) in enumerate(zip(planned, blocked, strict=True)):
        if owner is not None:
            settle(p, AddSynonymResponse(
                index=0, status="error",
                error=f"Synonym already registered under different entry: {owner}",
                error_code="synonym_conflict",
            ))
            continue
        if recorded is not None:
            entry.synonyms.remove(recorded)  # the inactive copy it replaces
        entry.synonyms.append(synonym)
        changed[entry.entry_id] = entry
        by_entry.setdefault(entry.entry_id, []).append(p)

    failed = await _write_synonym_lists(
        changed, lambda entry_id: [planned[p][2].composite_key_hash for p in by_entry[entry_id]],
    )
    claims_to_confirm, claims_to_release = [], []
    for entry_id, positions in by_entry.items():
        for p in positions:
            _, entry, synonym, _ = planned[p]
            key = (entry.namespace, entry.entity_type, synonym.composite_key_hash, entry_id, "synonym")
            if entry_id in failed:
                claims_to_release.append(key)
                settle(p, AddSynonymResponse(index=0, status="error", **failed[entry_id]))
            else:
                claims_to_confirm.append(key)
                settle(p, AddSynonymResponse(index=0, status="added", registry_id=entry_id))
    # A failed write releases the claims this request inserted (anything
    # missed is pending and dies in the reconcile pass); a committed one
    # confirms them, best-effort.
    await release_keys_bulk(claims_to_release, token)
    await confirm_keys_bulk(claims_to_confirm)

    done = [r for r in results if r is not None]
    return BulkSynonymAddResponse(
        results=done, total=len(items),
        succeeded=sum(1 for r in done if r.status == "added"),
        failed=sum(1 for r in done if r.status in ("target_not_found", "forbidden", "error")),
    )


async def _load_active_entries(entry_ids: set[str]) -> dict[str, RegistryEntry]:
    """The active entries among ``entry_ids``, in one read."""
    return {
        e.entry_id: e
        async for e in RegistryEntry.find({"entry_id": {"$in": list(entry_ids)}, "status": "active"})
    }


async def _write_synonym_lists(
    changed: dict[str, RegistryEntry],
    expected_hashes,
    updated_by: dict[str, str | None] | None = None,
    absent: bool = False,
) -> dict[str, dict]:
    """Write each changed entry's synonyms, search_values and updated_at in one
    bulk write, instead of a full save per item. Returns the entries whose
    write failed, with the per-item error fields.

    When the bulk write does not say which writes failed, each entry is
    re-read: it counts as written if the synonym hashes this request changed
    are present and active (``absent=False``) or gone (``absent=True``) on it.
    """
    if not changed:
        return {}
    now = datetime.now(UTC)
    ops = []
    order = list(changed)
    for entry_id in order:
        entry = changed[entry_id]
        entry.rebuild_search_values()
        entry.updated_at = now
        fields = {"synonyms", "search_values", "updated_at"}
        if updated_by is not None and entry_id in updated_by:
            entry.updated_by = updated_by[entry_id]
            fields.add("updated_by")
        encoded = Encoder().encode(entry)
        ops.append(UpdateOne(
            {"entry_id": entry_id}, {"$set": {f: encoded[f] for f in fields}},
        ))
    failed: dict[str, dict] = {}
    try:
        await RegistryEntry.get_motor_collection().bulk_write(ops, ordered=False)
    except BulkWriteError as e:
        for err in e.details.get("writeErrors", []):
            failed[order[err["index"]]] = error_fields(Exception(err.get("errmsg", "write failed")))
    except Exception as e:
        # Outcome unknown: re-read and decide per entry.
        stored = {
            d["entry_id"]: {
                s.get("composite_key_hash")
                for s in d.get("synonyms") or []
                if s.get("status") != "inactive"
            }
            async for d in RegistryEntry.get_motor_collection().find(
                {"entry_id": {"$in": order}},
                {"entry_id": 1, "synonyms.composite_key_hash": 1, "synonyms.status": 1, "_id": 0},
            )
        }
        for entry_id in order:
            hashes = set(expected_hashes(entry_id))
            have = stored.get(entry_id, set())
            written = not (hashes & have) if absent else hashes <= have
            if not written:
                failed[entry_id] = error_fields(e)
    return failed


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

    Handled in bulk, like add: one read, one write per changed entry, one
    release of the removed synonyms' claims. Items are decided in order, so
    removing the same synonym twice answers not_found the second time.
    """
    results: list[RemoveSynonymResponse | None] = [None] * len(items)
    access = NamespaceAccess(identity)

    loaded = await _load_active_entries({item.target_id for item in items})

    removals: dict[str, list[tuple[int, tuple]]] = {}  # entry_id -> (item, claim key)
    changed: dict[str, RegistryEntry] = {}
    updated_by: dict[str, str | None] = {}
    for i, item in enumerate(items):
        try:
            entry = loaded.get(item.target_id)
            if not entry or not await access.allows(entry.namespace, "read"):
                results[i] = RemoveSynonymResponse(
                    index=i, status="not_found", registry_id=item.target_id,
                )
                continue

            synonym_namespace = item.synonym_namespace or entry.namespace
            synonym_entity_type = item.synonym_entity_type or entry.entity_type

            refusal = await _write_refusal(access, entry.namespace, synonym_namespace)
            if refusal:
                results[i] = RemoveSynonymResponse(
                    index=i, status="forbidden", registry_id=entry.entry_id, error=refusal,
                )
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
                results[i] = RemoveSynonymResponse(
                    index=i, status="not_found", registry_id=entry.entry_id,
                    error="Synonym not found in entry",
                )
                continue

            changed[entry.entry_id] = entry
            updated_by[entry.entry_id] = item.updated_by
            removals.setdefault(entry.entry_id, []).append((i, (
                synonym_namespace, synonym_entity_type, synonym_hash, entry.entry_id, "synonym",
            )))
        except Exception as e:
            results[i] = RemoveSynonymResponse(index=i, status="error", **error_fields(e))

    failed = await _write_synonym_lists(
        changed,
        lambda entry_id: [key[2] for _, key in removals[entry_id]],
        updated_by=updated_by,
        absent=True,
    )
    released = []
    for entry_id, entry_removals in removals.items():
        for i, key in entry_removals:
            if entry_id in failed:
                results[i] = RemoveSynonymResponse(index=i, status="error", **failed[entry_id])
            else:
                released.append(key)
                results[i] = RemoveSynonymResponse(
                    index=i, status="removed", registry_id=entry_id,
                )
    # Release the removed synonyms' claims (CASE-427), each scoped to its
    # owner so a claim that belongs elsewhere is never deleted. A leftover
    # claim is a benign orphan that reconciliation clears.
    await release_keys_bulk(released)
    # The removed synonyms no longer resolve to their entries.
    await entries_changed(key[3] for key in released)

    done = [r for r in results if r is not None]
    return BulkSynonymRemoveResponse(
        results=done, total=len(items),
        succeeded=sum(1 for r in done if r.status == "removed"),
        failed=sum(1 for r in done if r.status in ("not_found", "forbidden", "error")),
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
            # An inactive synonym holds no claim — the key may belong to
            # another entry by now — so it moves as a record only.
            for syn in deprecated.synonyms:
                if not any(s.composite_key_hash == syn.composite_key_hash for s in preferred.synonyms):
                    if syn.status != "inactive":
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

    # A merged-away entry's id and names now resolve to the preferred entry;
    # caches holding them for the deprecated one drop them.
    await entries_changed(r.deprecated_id for r in results if r.status == "merged")
    return BulkMergeResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "merged"),
        failed=sum(1 for r in results if r.status in (
            "preferred_not_found", "deprecated_not_found", "forbidden", "error",
        )),
    )
