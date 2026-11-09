"""Synonym management API endpoints."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Body, Depends

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
from ..models.entry import RegistryEntry, Synonym
from ..services.auth import require_api_key
from ..services.claims import (
    claim_keys_pending_bulk,
    confirm_keys_bulk,
    new_claim_token,
    release_keys_bulk,
)
from ..services.entry_writes import UNCHANGED, apply_to_entry, write_entry
from ..services.errors import error_fields, invalid_entity_type_fields
from ..services.event_bus import entries_changed
from ..services.hash import HashService
from ..services.key_scope import key_scope_refusal
from ..services.merge import MergedEntryChanged, MergeKeyConflict, merge_into
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
    plan = _AddPlan(results)
    for i, item in enumerate(items):
        try:
            results[i] = await _decide_add(i, item, loaded.get(item.target_id), access, identity, plan)
        except Exception as e:
            results[i] = AddSynonymResponse(index=i, status="error", **error_fields(e))

    # Atomic uniqueness gate (CASE-427, two-phase since CASE-554): claim every
    # new hash pending before writing; the unique index is the lock. A claim
    # the entry itself already holds (its write died mid-flight) is fine, a
    # stale holder is healed and retried once, a genuine owner is never
    # stolen from.
    token = new_claim_token()
    blocked = await claim_keys_pending_bulk(
        [[_add_claim_key(entry, synonym)] for _, entry, synonym, _ in plan.planned], token,
    )
    changed = _apply_claimed(plan, blocked)
    failed, redecided = await _write_synonym_lists(
        changed, lambda entry_id, entry: _reapply_adds(plan, entry_id, entry),
    )
    to_confirm, to_release = _settle_written_adds(plan, failed, redecided)
    # A failed write releases the claims this request inserted (anything
    # missed is pending and dies in the reconcile pass); a committed one
    # confirms them, best-effort.
    await release_keys_bulk(to_release, token)
    await confirm_keys_bulk(to_confirm)

    return _add_response(results, len(items))


def _add_response(results: list[AddSynonymResponse | None], total: int) -> BulkSynonymAddResponse:
    done = [r for r in results if r is not None]
    return BulkSynonymAddResponse(
        results=done, total=total,
        succeeded=sum(1 for r in done if r.status == "added"),
        failed=sum(1 for r in done if r.status in ("target_not_found", "forbidden", "error")),
    )


class _AddPlan:
    """The additions a synonym-add request decided to make, in item order.

    ``planned`` holds (item index, entry, synonym, the inactive copy it
    replaces); ``pending`` the planned index of each (entry_id, hash) so a
    repeat rides on it; ``dependents`` the items riding on each planned one;
    ``by_entry`` the planned indices whose claims held, per entry.
    """

    def __init__(self, results: list[AddSynonymResponse | None]) -> None:
        self.results = results
        self.planned: list[tuple[int, RegistryEntry, Synonym, Synonym | None]] = []
        self.pending: dict[tuple[str, str], int] = {}
        self.dependents: dict[int, list[int]] = {}
        self.by_entry: dict[str, list[int]] = {}

    def settle(self, p: int, response: AddSynonymResponse) -> None:
        """Answer a planned item and the repeats riding on it: a repeat of an
        added synonym is already_exists, any other outcome is shared."""
        i = self.planned[p][0]
        self.results[i] = response.model_copy(update={"index": i})
        for d in self.dependents.get(p, []):
            follow = response
            if response.status == "added":
                follow = AddSynonymResponse(
                    index=d, status="already_exists", registry_id=response.registry_id,
                )
            self.results[d] = follow.model_copy(update={"index": d})


def _add_claim_key(entry: RegistryEntry, synonym: Synonym) -> tuple[str, str, str, str, str]:
    return (entry.namespace, entry.entity_type, synonym.composite_key_hash, entry.entry_id, "synonym")


def _holds_key(entry: RegistryEntry, synonym_hash: str) -> tuple[bool, Synonym | None]:
    """Whether the entry already owns the hash (its primary key, or an active
    synonym), and the synonym recorded under it. An inactive copy (released
    when the entity was retired) does not count: the add claims the key
    again, or reports who holds it now."""
    recorded = entry.find_synonym_by_hash(synonym_hash)
    held = entry.primary_composite_key_hash == synonym_hash or (
        recorded is not None and recorded.status != "inactive"
    )
    return held, recorded


async def _add_refusal(
    i: int,
    item: AddSynonymItem,
    entry: RegistryEntry | None,
    access: NamespaceAccess,
    identity: UserIdentity,
) -> AddSynonymResponse | None:
    """The answer refusing an item before its key is looked at: an invalid
    entity type, a target the caller cannot read (or that is not active), no
    write on its namespace, or a key it may not hold (_add_key_refusal)."""
    invalid = invalid_entity_type_fields(item.synonym_entity_type)
    if invalid:
        return AddSynonymResponse(index=i, status="error", **invalid)
    if not entry or not await access.allows(entry.namespace, "read"):
        return AddSynonymResponse(index=i, status="target_not_found")
    refusal = await _write_refusal(access, entry.namespace)
    if refusal:
        return AddSynonymResponse(index=i, status="forbidden", error=refusal)
    return _add_key_refusal(i, item, entry, identity)


def _add_key_refusal(
    i: int, item: AddSynonymItem, entry: RegistryEntry, identity: UserIdentity,
) -> AddSynonymResponse | None:
    """A synonym scoped to another namespace or entity type, or a reserved
    '@' key from a caller that is not a system one."""
    mismatch = _scope_mismatch(
        entry, item.synonym_namespace, item.synonym_entity_type,
    ) or key_scope_refusal(
        item.synonym_composite_key, entry.namespace, rule="synonym_scope_mismatch",
    )
    if mismatch:
        return AddSynonymResponse(index=i, status="error", registry_id=entry.entry_id, **mismatch)
    # Keys starting with "@" qualify system-generated synonyms (a document's
    # "@template"). Only system callers may write them, so an explicit
    # synonym cannot claim the key a document will need.
    reserved = sorted(k for k in item.synonym_composite_key if k.startswith("@"))
    if reserved and not identity.has_any_group(["wip-admins", "wip-services"]):
        return AddSynonymResponse(
            index=i, status="forbidden",
            error=(
                f"Synonym keys {reserved} are reserved for system-generated "
                "synonyms (keys starting with '@')"
            ),
        )
    return None


async def _decide_add(
    i: int,
    item: AddSynonymItem,
    entry: RegistryEntry | None,
    access: NamespaceAccess,
    identity: UserIdentity,
    plan: _AddPlan,
) -> AddSynonymResponse | None:
    """Answer an item at once (refused, or the entry holds the key already),
    or plan it: riding on an earlier item with the same synonym for the same
    entry, or as an addition to claim. None when it is answered later."""
    refusal = await _add_refusal(i, item, entry, access, identity)
    if refusal is not None:
        return refusal
    entry = cast(RegistryEntry, entry)
    synonym_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)

    # The same synonym for the same entry earlier in this request: it shares
    # that item's outcome (already_exists once it is added).
    earlier = plan.pending.get((entry.entry_id, synonym_hash))
    if earlier is not None:
        plan.dependents.setdefault(earlier, []).append(i)
        return None

    # Fast path: the entry already owns the hash → idempotent already_exists,
    # no claim churn.
    held, recorded = _holds_key(entry, synonym_hash)
    if held:
        return AddSynonymResponse(index=i, status="already_exists", registry_id=entry.entry_id)

    synonym = Synonym(
        namespace=entry.namespace,
        entity_type=entry.entity_type,
        composite_key=item.synonym_composite_key,
        composite_key_hash=synonym_hash,
        source_info=item.synonym_source_info,
        created_by=item.created_by,
    )
    plan.pending[(entry.entry_id, synonym_hash)] = len(plan.planned)
    plan.planned.append((i, entry, synonym, recorded))
    return None


def _apply_claimed(plan: _AddPlan, blocked: list[str | None]) -> dict[str, RegistryEntry]:
    """Add each synonym whose claim held to its entry (replacing the inactive
    copy it re-claims), and answer the others as conflicts. Returns the
    entries that changed."""
    changed: dict[str, RegistryEntry] = {}
    for p, ((_i, entry, synonym, recorded), owner) in enumerate(zip(plan.planned, blocked, strict=True)):
        if owner is not None:
            plan.settle(p, AddSynonymResponse(
                index=0, status="error",
                error=f"Synonym already registered under different entry: {owner}",
                error_code="synonym_conflict",
            ))
            continue
        if recorded is not None:
            entry.synonyms.remove(recorded)  # the inactive copy it replaces
        entry.synonyms.append(synonym)
        changed[entry.entry_id] = entry
        plan.by_entry.setdefault(entry.entry_id, []).append(p)
    return changed


def _reapply_adds(plan: _AddPlan, entry_id: str, entry: RegistryEntry) -> "SynonymOutcomes":
    """Apply an entry's additions again to the entry as re-read after another
    writer changed it, deciding each against it as it now is."""
    outcomes: SynonymOutcomes = {}
    for p in plan.by_entry[entry_id]:
        synonym = plan.planned[p][2]
        h = synonym.composite_key_hash
        held, recorded = _holds_key(entry, h)
        if held:
            outcomes[h] = "already_exists"
            continue
        if recorded is not None:
            entry.synonyms.remove(recorded)
        entry.synonyms.append(synonym)
        outcomes[h] = "added"
    return outcomes


def _settle_written_adds(
    plan: _AddPlan,
    failed: dict[str, dict],
    redecided: "dict[str, SynonymOutcomes]",
) -> tuple[list[tuple[str, str, str, str, str]], list[tuple[str, str, str, str, str]]]:
    """Answer each written addition: its entry's write error, or added (or
    what a re-read re-decided). Returns (claims to confirm, claims to
    release)."""
    to_confirm: list[tuple[str, str, str, str, str]] = []
    to_release: list[tuple[str, str, str, str, str]] = []
    for entry_id, positions in plan.by_entry.items():
        for p in positions:
            _, entry, synonym, _ = plan.planned[p]
            key = _add_claim_key(entry, synonym)
            if entry_id in failed:
                to_release.append(key)
                plan.settle(p, AddSynonymResponse(index=0, status="error", **failed[entry_id]))
            else:
                # The claim is this entry's either way: confirm it.
                to_confirm.append(key)
                status = redecided.get(entry_id, {}).get(synonym.composite_key_hash, "added")
                plan.settle(p, AddSynonymResponse(index=0, status=status, registry_id=entry_id))
    return to_confirm, to_release



async def _load_active_entries(entry_ids: set[str]) -> dict[str, RegistryEntry]:
    """The active entries among ``entry_ids``, in one read."""
    return {
        e.entry_id: e
        async for e in RegistryEntry.find({"entry_id": {"$in": list(entry_ids)}, "status": "active"})
    }


SynonymOutcomes = dict[str, str]  # composite_key_hash -> per-item status

# updated_by is always among them: an entry a request does not name it for
# keeps the value it was read with.
_SYNONYM_FIELDS = ("synonyms", "search_values", "updated_at", "updated_by")


async def _write_synonym_lists(
    changed: dict[str, RegistryEntry],
    reapply: Callable[[str, RegistryEntry], SynonymOutcomes],
    updated_by: dict[str, str | None] | None = None,
) -> tuple[dict[str, dict], dict[str, SynonymOutcomes]]:
    """Write each changed entry's synonyms, search_values and updated_at, each
    write filtered on the revision the entry was read with (entry_writes), so
    two requests changing one entry's synonyms cannot drop each other's
    change.

    An entry another writer changed in the meantime is read again and the
    request's changes are applied to it afresh by ``reapply(entry_id, entry)``,
    which changes the entry in place and returns each synonym's outcome as
    decided against the entry as it now is — a synonym another request added
    first is already_exists, one it removed first is not_found. An entry no
    longer active fails its items, as one that was not active at the start
    would have.

    Returns (failed entries with their per-item error fields, the outcomes
    re-decided on a re-read, by entry).
    """
    if not changed:
        return {}, {}
    now = datetime.now(UTC)

    def finish(entry: RegistryEntry) -> None:
        entry.rebuild_search_values()
        entry.updated_at = now
        if updated_by is not None and entry.entry_id in updated_by:
            entry.updated_by = updated_by[entry.entry_id]

    async def first_write(entry: RegistryEntry) -> bool | BaseException:
        finish(entry)
        try:
            return await write_entry(entry, _SYNONYM_FIELDS, {"status": "active"})
        except Exception as e:  # outcome unknown: settled by a re-read below
            return e

    order = list(changed)
    landed = await asyncio.gather(*(first_write(changed[eid]) for eid in order))

    failed: dict[str, dict] = {}
    redecided: dict[str, SynonymOutcomes] = {}
    for entry_id, outcome in zip(order, landed, strict=True):
        if outcome is True:
            continue
        unknown = outcome if isinstance(outcome, BaseException) else None
        decided: SynonymOutcomes = {}

        def mutate(entry: RegistryEntry, entry_id: str = entry_id, decided: dict = decided) -> Any:
            decided.clear()
            decided.update(reapply(entry_id, entry))
            if not any(status in ("added", "removed") for status in decided.values()):
                return UNCHANGED
            finish(entry)
            return None

        try:
            entry, _ = await apply_to_entry(
                entry_id, mutate, _SYNONYM_FIELDS, read_filter={"status": "active"},
            )
        except Exception as e:
            failed[entry_id] = error_fields(unknown or e)
            continue
        if entry is None:
            failed[entry_id] = {
                "error": f"Entry {entry_id} is no longer active", "error_code": "not_found",
            }
            continue
        if unknown is not None:
            # The first write may have landed before its error: a change found
            # already made is this request's own.
            decided = {h: {"already_exists": "added", "not_found": "removed"}.get(st, st)
                       for h, st in decided.items()}
        redecided[entry_id] = dict(decided)
    return failed, redecided


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

    plan = _RemovePlan()
    for i, item in enumerate(items):
        try:
            results[i] = await _decide_removal(i, item, loaded.get(item.target_id), access, plan)
        except Exception as e:
            results[i] = RemoveSynonymResponse(index=i, status="error", **error_fields(e))

    failed, redecided = await _write_synonym_lists(
        plan.changed,
        lambda entry_id, entry: _reapply_removals(plan, entry_id, entry),
        updated_by=plan.updated_by,
    )
    released = _settle_removals(plan, failed, redecided, results)
    # Release the removed synonyms' claims (CASE-427), each scoped to its
    # owner so a claim that belongs elsewhere is never deleted. A leftover
    # claim is a benign orphan that reconciliation clears.
    await release_keys_bulk(released)
    # The removed synonyms no longer resolve to their entries.
    await entries_changed(key[3] for key in released)
    return _remove_response(results, len(items))


def _remove_response(
    results: list[RemoveSynonymResponse | None], total: int,
) -> BulkSynonymRemoveResponse:
    done = [r for r in results if r is not None]
    return BulkSynonymRemoveResponse(
        results=done, total=total,
        succeeded=sum(1 for r in done if r.status == "removed"),
        failed=sum(1 for r in done if r.status in ("not_found", "forbidden", "error")),
    )


class _RemovePlan:
    """The removals a synonym-remove request applied in memory: each entry's
    (item index, claim key) pairs, the changed entries, and who updated
    each."""

    def __init__(self) -> None:
        self.removals: dict[str, list[tuple[int, tuple]]] = {}
        self.changed: dict[str, RegistryEntry] = {}
        self.updated_by: dict[str, str | None] = {}


def _drop_synonym(entry: RegistryEntry, namespace: str, entity_type: str, synonym_hash: str) -> bool:
    """Remove the entry's synonym with this key; whether there was one."""
    before = len(entry.synonyms)
    entry.synonyms = [
        s for s in entry.synonyms
        if not (s.namespace == namespace and s.entity_type == entity_type
                and s.composite_key_hash == synonym_hash)
    ]
    return len(entry.synonyms) < before


async def _decide_removal(
    i: int,
    item: RemoveSynonymItem,
    entry: RegistryEntry | None,
    access: NamespaceAccess,
    plan: _RemovePlan,
) -> RemoveSynonymResponse | None:
    """Answer an item at once (not found, forbidden, no such synonym), or
    remove its synonym from the entry in memory and plan the write. None
    when it is answered after the write."""
    if not entry or not await access.allows(entry.namespace, "read"):
        return RemoveSynonymResponse(index=i, status="not_found", registry_id=item.target_id)
    synonym_namespace = item.synonym_namespace or entry.namespace
    synonym_entity_type = item.synonym_entity_type or entry.entity_type
    refusal = await _write_refusal(access, entry.namespace, synonym_namespace)
    if refusal:
        return RemoveSynonymResponse(
            index=i, status="forbidden", registry_id=entry.entry_id, error=refusal,
        )
    synonym_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)
    if not _drop_synonym(entry, synonym_namespace, synonym_entity_type, synonym_hash):
        return RemoveSynonymResponse(
            index=i, status="not_found", registry_id=entry.entry_id,
            error="Synonym not found in entry",
        )
    plan.changed[entry.entry_id] = entry
    plan.updated_by[entry.entry_id] = item.updated_by
    plan.removals.setdefault(entry.entry_id, []).append((i, (
        synonym_namespace, synonym_entity_type, synonym_hash, entry.entry_id, "synonym",
    )))
    return None


def _reapply_removals(plan: _RemovePlan, entry_id: str, entry: RegistryEntry) -> "SynonymOutcomes":
    """Apply an entry's removals again to the entry as re-read after another
    writer changed it, deciding each against it as it now is."""
    outcomes: SynonymOutcomes = {}
    for _i, (ns, entity_type, h, _owner, _kind) in plan.removals[entry_id]:
        outcomes[h] = "removed" if _drop_synonym(entry, ns, entity_type, h) else "not_found"
    return outcomes


def _settle_removals(
    plan: _RemovePlan,
    failed: dict[str, dict],
    redecided: "dict[str, SynonymOutcomes]",
    results: list[RemoveSynonymResponse | None],
) -> list[tuple]:
    """Answer each planned removal by its entry's write: an error, not_found
    when another request removed it first (its claim went with it), else
    removed. Returns the claim keys to release."""
    released = []
    for entry_id, entry_removals in plan.removals.items():
        for i, key in entry_removals:
            if entry_id in failed:
                results[i] = RemoveSynonymResponse(index=i, status="error", **failed[entry_id])
            elif redecided.get(entry_id, {}).get(key[2], "removed") == "not_found":
                results[i] = RemoveSynonymResponse(
                    index=i, status="not_found", registry_id=entry_id,
                    error="Synonym not found in entry",
                )
            else:
                released.append(key)
                results[i] = RemoveSynonymResponse(index=i, status="removed", registry_id=entry_id)
    return released


@router.post(
    "/merge",
    response_model=BulkMergeResponse,
    summary="Merge entries (ID-as-synonym) (bulk)"
)
async def merge_entries(
    items: list[MergeItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkMergeResponse:
    """Merge entries: each deprecated entry becomes an alias of its preferred one.

    The deprecated entry goes inactive and records the entry it was merged
    into; resolving its id answers that entry. Its keys (its id, its primary
    key and its synonyms) move to the preferred entry with their claims, so
    re-registering the deprecated identity answers the preferred entry. A
    key some third entry holds refuses the merge (``already_exists``, the
    holder in ``details``) and nothing is merged. Repeating a merge that
    completed answers ``merged``. Merges are one-way.

    Needs write on both entries' namespaces; an entry the caller cannot read
    is reported as not found.
    """
    results = []
    access = NamespaceAccess(identity)
    for i, item in enumerate(items):
        try:
            results.append(await _merge_one(i, item, access, identity))
        except Exception as e:
            results.append(MergeResponse(index=i, status="error", **error_fields(e)))

    # A merged-away entry's id and names now resolve to the preferred entry;
    # caches holding them for the deprecated one drop them.
    await entries_changed(
        r.deprecated_id for r in results if r.status == "merged" and r.deprecated_id
    )
    return BulkMergeResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "merged"),
        failed=sum(1 for r in results if r.status in (
            "preferred_not_found", "deprecated_not_found", "forbidden", "error",
        )),
    )


async def _merge_one(
    i: int, item: MergeItem, access: NamespaceAccess, identity: UserIdentity,
) -> MergeResponse:
    """Merge one deprecated entry into its preferred one, or say why not."""
    pair = await _merge_pair(i, item, access)
    if isinstance(pair, MergeResponse):
        return pair
    preferred, deprecated = pair

    refusal = await _write_refusal(access, preferred.namespace, deprecated.namespace)
    if refusal:
        return MergeResponse(
            index=i, status="forbidden",
            preferred_id=preferred.entry_id, deprecated_id=deprecated.entry_id, error=refusal,
        )
    invalid = _merge_refusal(i, item, preferred, deprecated, identity)
    if invalid is not None:
        return invalid

    try:
        await merge_into(preferred, deprecated, item.updated_by)
    except (MergeKeyConflict, MergedEntryChanged) as e:
        return MergeResponse(
            index=i, status="error",
            preferred_id=preferred.entry_id, deprecated_id=deprecated.entry_id,
            error=str(e), error_code=e.error_code, details=getattr(e, "details", None),
        )
    return MergeResponse(
        index=i, status="merged",
        preferred_id=preferred.entry_id, deprecated_id=deprecated.entry_id,
    )


async def _merge_pair(
    i: int, item: MergeItem, access: NamespaceAccess,
) -> tuple[RegistryEntry, RegistryEntry] | MergeResponse:
    """The (preferred, deprecated) entries a merge item names, or its answer
    when either is not there to merge: not found (or unreadable), or merged
    into this preferred entry already (a retry, answered as done)."""
    preferred = await RegistryEntry.find_one({"entry_id": item.preferred_id, "status": "active"})
    if not preferred or not await access.allows(preferred.namespace, "read"):
        return MergeResponse(index=i, status="preferred_not_found", preferred_id=item.preferred_id)
    deprecated = await RegistryEntry.find_one({"entry_id": item.deprecated_id})
    readable = deprecated is not None and await access.allows(deprecated.namespace, "read")
    if deprecated is None or not readable:
        return MergeResponse(index=i, status="deprecated_not_found", deprecated_id=item.deprecated_id)
    if deprecated.merged_into == preferred.entry_id:
        return MergeResponse(
            index=i, status="merged",
            preferred_id=preferred.entry_id, deprecated_id=deprecated.entry_id,
        )
    if deprecated.status != "active":
        return MergeResponse(index=i, status="deprecated_not_found", deprecated_id=item.deprecated_id)
    return preferred, deprecated


def _merge_refusal(
    i: int,
    item: MergeItem,
    preferred: RegistryEntry,
    deprecated: RegistryEntry,
    identity: UserIdentity,
) -> MergeResponse | None:
    """A merge the Registry refuses whoever asks: an entry with itself, two
    entries of different namespaces or entity types, or terms (which only
    def-store merges)."""
    if str(preferred.id) == str(deprecated.id):
        return MergeResponse(
            index=i, status="error",
            error="Cannot merge an entry with itself",
            error_code="invalid_request", details={"rule": "self_merge"},
        )
    # Two entries are "the same real-world entity" only within one namespace
    # and entity type: merging across either would make a document's old ID
    # resolve to a template, or one namespace's key resolve into another.
    if (preferred.namespace, preferred.entity_type) != (deprecated.namespace, deprecated.entity_type):
        return MergeResponse(
            index=i, status="error",
            preferred_id=preferred.entry_id,
            deprecated_id=deprecated.entry_id,
            error=(
                "Only entries of one namespace and entity type merge: "
                f"preferred is {preferred.namespace}/{preferred.entity_type}, "
                f"deprecated is {deprecated.namespace}/{deprecated.entity_type}"
            ),
            error_code="invalid_request", details={"rule": "merge_scope_mismatch"},
        )
    # A term's identity lives in two places — this entry and the def-store
    # term whose value and names drive validation — so only def-store may
    # merge terms: a Registry-only merge would leave the absorbed term active
    # and its value still resolving to it.
    if "terms" in (preferred.entity_type, deprecated.entity_type) and not (
        item.owner_merge and identity.has_any_group(["wip-admins", "wip-services"])
    ):
        return MergeResponse(
            index=i, status="error",
            error=(
                "Terms are merged by def-store (POST /api/def-store/terms/merge), "
                "which moves the absorbed term's value and names to the survivor "
                "and then merges these entries."
            ),
            error_code="invalid_request", details={"rule": "terms_merge_in_def_store"},
        )
    return None
