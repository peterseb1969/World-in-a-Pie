"""Registry entry management API endpoints."""

import asyncio
import logging
import math
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from beanie.odm.utils.encoder import Encoder
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

from wip_auth import UserIdentity

from ..models.api_models import (
    ActivateBulkResponse,
    ActivateItem,
    ActivateItemResponse,
    ActivateSynonym,
    BrowseEntriesResponse,
    BrowseEntryItem,
    BulkDeleteResponse,
    BulkEntryLifecycleResponse,
    BulkLookupCandidatesResponse,
    BulkResolveResponse,
    BulkUpdateResponse,
    DeleteItem,
    DeleteResponse,
    EntryDetailResponse,
    EntryLifecycleItem,
    EntryLifecycleResponse,
    ExportEntriesRequest,
    ExportEntriesResponse,
    ExportEntryItem,
    LookupBulkResponse,
    LookupByIdItem,
    LookupByKeyItem,
    LookupCandidate,
    LookupCandidatesItem,
    LookupCandidatesResponse,
    LookupResponse,
    ProvisionedId,
    ProvisionRequest,
    ProvisionResponse,
    RegisterBulkResponse,
    RegisterKeyItem,
    RegisterKeyResponse,
    RekeyIdentityBulkResponse,
    RekeyIdentityItem,
    RekeyIdentityResponse,
    ReserveBulkResponse,
    ReserveItem,
    ReserveItemResponse,
    ResolveItem,
    ResolveResponse,
    UnifiedSearchResponse,
    UnifiedSearchResultItem,
    UpdateEntryItem,
    UpdateEntryResponse,
)
from ..models.composite_key_claim import CompositeKeyClaim
from ..models.entry import RegistryEntry, Synonym
from ..models.id_algorithm import VALID_ENTITY_TYPES, IdAlgorithmConfig, IdFormatValidator
from ..models.namespace import Namespace
from ..services.auth import require_api_key, require_privileged_key
from ..services.claims import (
    claim_entries_keys_pending,
    claim_keys_pending_bulk,
    confirm_entries_keys,
    confirm_keys_bulk,
    new_claim_token,
    release_entries_keys,
    release_keys_bulk,
    retire_entry_synonyms,
    revive_entry_synonyms,
)
from ..services.entry_writes import (
    REVISION_INC,
    UNCHANGED,
    EntryWriteConflict,
    apply_to_entry,
)
from ..services.errors import error_fields, invalid_entity_type_fields
from ..services.event_bus import entries_changed
from ..services.hash import HashService
from ..services.id_generator import IdGeneratorService
from ..services.key_scope import key_scope_refusal, named_namespace
from ..services.merge import follow_merges
from .grants import NamespaceAccess, resolve_accessible_namespaces

logger = logging.getLogger("registry.entries")

# Candidates listed when a key or value resolves to several entries.
_RESOLVE_CANDIDATE_LIMIT = 10

router = APIRouter()


def _insert_failure(e: Exception) -> dict[str, Any]:
    """Error fields for a failed batch insert of new entries.

    The database's own error carries other entries' keys, hashes and ids, so
    it goes to the log; the caller is told what failed. A taken entry_id is
    named, since it is the caller's (or a counter's) to correct.
    """
    logger.error("Registry batch insert failed: %s", e)
    if isinstance(e, BulkWriteError):
        for err in e.details.get("writeErrors", []):
            if err.get("code") == 11000 and "entry_id_unique_idx" in str(err.get("errmsg", "")):
                taken = (err.get("keyValue") or {}).get("entry_id")
                return {
                    "error": f"Batch insert failed: entry_id '{taken}' already exists",
                    "error_code": "already_exists",
                    "details": {"rule": "entry_id_taken", "entry_id": taken},
                }
    return {
        "error": f"Batch insert failed ({type(e).__name__}); see the Registry log",
        "error_code": "internal_error",
    }


async def _insert_claimed_entries(
    entries: list[RegistryEntry], token: str,
) -> tuple[int | None, Exception | None]:
    """Insert entries whose keys this request claimed pending (under
    ``token``), then settle those claims. Returns (how many entries were
    written, the insert error or None).

    The batch insert is ordered and has no transaction: when it fails, the
    entries before the failing one are written and the rest are not. The
    written ones keep their claims, confirmed; the others lose the claims
    this request took for them. When the error does not say how far the
    insert got, the count is None and nothing is released — every claim
    stays pending, and the pending reconcile settles each one by whether its
    entry exists.
    """
    try:
        await RegistryEntry.insert_many(entries)
    except Exception as e:
        written: int | None = None
        if isinstance(e, BulkWriteError):
            failing = [err["index"] for err in e.details.get("writeErrors", [])]
            if failing:
                written = min(failing)
        if written is not None:
            await confirm_entries_keys(entries[:written])
            await release_entries_keys(entries[written:], token)
        return written, e
    await confirm_entries_keys(entries)
    return len(entries), None


def build_lookup_response(
    index: int,
    entry: RegistryEntry | None,
    status: str = "found",
    matched_namespace: str | None = None,
    matched_entity_type: str | None = None,
    matched_composite_key: dict | None = None,
    matched_via: str | None = None,
    source_data: dict | None = None,
    error: str | None = None
) -> LookupResponse:
    """Build a standardized lookup response."""
    if entry is None:
        return LookupResponse(
            index=index,
            status=status,
            error=error
        )

    return LookupResponse(
        index=index,
        status=status,
        entry_id=entry.entry_id,
        namespace=entry.namespace,
        entity_type=entry.entity_type,
        matched_namespace=matched_namespace or entry.namespace,
        matched_entity_type=matched_entity_type or entry.entity_type,
        matched_composite_key=matched_composite_key or entry.primary_composite_key,
        matched_via=matched_via,
        synonyms=entry.synonyms,
        source_info=entry.source_info,
        source_data=source_data,
        error=error
    )


# The statuses an entry can hold. A free-text filter would answer an unknown
# value (a typo) with an empty page, indistinguishable from "no such entries".
EntryStatusFilter = Literal["active", "reserved", "inactive"]


def _require_valid_entity_type(entity_type: str | None) -> None:
    """Refuse an unknown entity_type filter: it would list nothing, silently."""
    invalid = invalid_entity_type_fields(entity_type)
    if invalid:
        raise HTTPException(status_code=400, detail=invalid["error"])


@router.get(
    "",
    response_model=BrowseEntriesResponse,
    summary="Browse registry entries"
)
async def browse_entries(
    namespace: str | None = Query(None, description="Filter by namespace"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    status: EntryStatusFilter | None = Query(None, description="Filter by status (active, reserved, inactive)"),
    q: str | None = Query(None, description="Search across entry IDs and composite key values"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Page size (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> BrowseEntriesResponse:
    """Browse registry entries with pagination and optional filters."""
    _require_valid_entity_type(entity_type)
    query: dict = {}

    # Scope the listing to namespaces the caller may read. Enumeration is
    # grant-gated (unlike cross-namespace reference resolution, which stays
    # open) — a key must not list a namespace's entry inventory without a
    # grant on it. An explicit foreign namespace returns an empty page rather
    # than 404, preserving pagination shape while leaking nothing.
    accessible = await resolve_accessible_namespaces(identity)
    if namespace:
        if accessible is not None and namespace not in accessible:
            return BrowseEntriesResponse(
                items=[], total=0, page=page, page_size=page_size, pages=0
            )
        query["namespace"] = namespace
    elif accessible is not None:
        query["namespace"] = {"$in": accessible}

    if entity_type:
        query["entity_type"] = entity_type
    if status:
        query["status"] = status

    if q:
        # CASE-568: q is a literal search string, not a regex — escape it
        # (same as unified_search below).
        escaped_q = re.escape(q.strip())
        query["$or"] = [
            {"entry_id": {"$regex": escaped_q, "$options": "i"}},
            {"search_values": {"$regex": escaped_q, "$options": "i"}},
        ]

    total = await RegistryEntry.find(query).count()
    skip = (page - 1) * page_size
    entries = await RegistryEntry.find(query).sort("-created_at").skip(skip).limit(page_size).to_list()

    items = [
        BrowseEntryItem(
            entry_id=e.entry_id,
            namespace=e.namespace,
            entity_type=e.entity_type,
            primary_composite_key=e.primary_composite_key,
            synonyms_count=len(e.synonyms),
            status=e.status,
            created_at=e.created_at,
            created_by=e.created_by,
            updated_at=e.updated_at,
        )
        for e in entries
    ]

    return BrowseEntriesResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get(
    "/search",
    response_model=UnifiedSearchResponse,
    summary="Unified search across entry IDs, composite keys, and synonyms"
)
async def unified_search(
    q: str = Query(..., min_length=1, description="Search query string"),
    namespace: str | None = Query(None, description="Filter by namespace"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    status: EntryStatusFilter | None = Query(None, description="Filter by status (active, reserved, inactive)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Page size (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> UnifiedSearchResponse:
    """
    Unified search across entry IDs and all composite key values
    (primary + synonyms). Returns rich results with match context and resolution paths.
    """
    _require_valid_entity_type(entity_type)
    q_stripped = q.strip()
    escaped_q = re.escape(q_stripped)

    # Build the search query — search across entry_id and search_values
    or_conditions = [
        {"entry_id": {"$regex": escaped_q, "$options": "i"}},
        {"search_values": {"$regex": escaped_q, "$options": "i"}},
    ]

    query: dict = {"$or": or_conditions}

    # Same namespace-scoping as browse_entries: this is enumeration, so it is
    # grant-gated. Reference resolution (lookup_by_ids / _keys, resolve_synonyms)
    # stays cross-namespace by design; unified search does not.
    accessible = await resolve_accessible_namespaces(identity)
    if namespace:
        if accessible is not None and namespace not in accessible:
            return UnifiedSearchResponse(
                query=q_stripped,
                items=[],
                total=0,
                page=page,
                page_size=page_size,
            )
        query["namespace"] = namespace
    elif accessible is not None:
        query["namespace"] = {"$in": accessible}

    if entity_type:
        query["entity_type"] = entity_type
    if status:
        query["status"] = status

    total = await RegistryEntry.find(query).count()
    skip = (page - 1) * page_size
    entries = await RegistryEntry.find(query).sort("-created_at").skip(skip).limit(page_size).to_list()

    items = []
    q_lower = q_stripped.lower()

    for entry in entries:
        matched_via = "composite_key_value"
        matched_value = ""
        resolution_path = ""

        # Determine how the match occurred
        if q_lower in entry.entry_id.lower():
            matched_via = "entry_id"
            matched_value = entry.entry_id
            resolution_path = f"{entry.entry_id} ({entry.namespace}/{entry.entity_type})"
        else:
            # Check primary composite key values
            primary_match = False
            for v in entry.primary_composite_key.values():
                if isinstance(v, str) and q_lower in v.lower():
                    matched_value = v
                    resolution_path = (
                        f"{v} → primary key → "
                        f"{entry.entry_id} ({entry.namespace}/{entry.entity_type})"
                    )
                    primary_match = True
                    break

            if not primary_match:
                # Check synonym composite key values
                for syn in entry.active_synonyms():
                    for v in syn.composite_key.values():
                        if isinstance(v, str) and q_lower in v.lower():
                            matched_via = "synonym_key_value"
                            matched_value = v
                            resolution_path = (
                                f"{v} → synonym ({syn.namespace}/{syn.entity_type}) → "
                                f"{entry.entry_id} ({entry.namespace}/{entry.entity_type})"
                            )
                            break
                    if matched_value:
                        break

                # Fallback if no specific match found
                if not matched_value:
                    matched_value = q_stripped
                    resolution_path = f"{entry.entry_id} ({entry.namespace}/{entry.entity_type})"

        items.append(UnifiedSearchResultItem(
            entry_id=entry.entry_id,
            namespace=entry.namespace,
            entity_type=entry.entity_type,
            status=entry.status,
            primary_composite_key=entry.primary_composite_key,
            synonyms=entry.synonyms,
            source_info=entry.source_info,
            metadata=entry.metadata,
            created_at=entry.created_at,
            created_by=entry.created_by,
            updated_at=entry.updated_at,
            updated_by=entry.updated_by,
            matched_via=matched_via,
            matched_value=matched_value,
            resolution_path=resolution_path,
        ))

    return UnifiedSearchResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        query=q_stripped,
    )


@router.get(
    "/{entry_id}",
    response_model=EntryDetailResponse,
    summary="Get a single registry entry by ID"
)
async def get_entry_detail(
    entry_id: str,
    identity: UserIdentity = Depends(require_api_key)
) -> EntryDetailResponse:
    """Get full details for a single registry entry by its entry_id.

    An entry in a namespace the caller cannot read answers the same 404 as
    a missing one, so the namespace's contents are not leaked.
    """
    entry = await RegistryEntry.find_one({"entry_id": entry_id})

    if not entry or not await NamespaceAccess(identity).allows(entry.namespace, "read"):
        raise HTTPException(status_code=404, detail=f"Entry not found: {entry_id}")

    return EntryDetailResponse(
        entry_id=entry.entry_id,
        namespace=entry.namespace,
        entity_type=entry.entity_type,
        primary_composite_key=entry.primary_composite_key,
        primary_composite_key_hash=entry.primary_composite_key_hash,
        synonyms=entry.synonyms,
        source_info=entry.source_info,
        search_values=entry.search_values,
        metadata=entry.metadata,
        status=entry.status,
        created_at=entry.created_at,
        created_by=entry.created_by,
        updated_at=entry.updated_at,
        updated_by=entry.updated_by,
    )


@router.post(
    "/register",
    response_model=RegisterBulkResponse,
    summary="Register composite keys (bulk, reserve+activate)"
)
async def register_keys(
    items: list[RegisterKeyItem] = Body(...),
    identity: UserIdentity = Depends(require_privileged_key)
) -> RegisterBulkResponse:
    """
    Register one or more composite keys. This is sugar for reserve + immediate activate.

    All items in a batch must share the same namespace; a mixed-namespace batch is
    rejected up front with a whole-call 422 (not a per-item error).

    For each key:
    - If the key already exists, returns the existing registry ID
    - If new, generates an ID (or uses client-provided one) and creates an active entry
    """
    if not items:
        return RegisterBulkResponse(results=[], total=0, created=0, already_exists=0, errors=0)

    timing: dict[str, float] = {}
    started = time.perf_counter()
    mark = started

    def lap(phase: str) -> None:
        nonlocal mark
        now = time.perf_counter()
        timing[phase] = round((now - mark) * 1000, 1)
        mark = now

    results: list[RegisterKeyResponse | None] = [None] * len(items)
    created_count = 0
    exists_count = 0
    error_count = 0

    # Phase 0a: Reject mixed namespaces — all items in a batch must share the same namespace
    unique_namespaces = {item.namespace for item in items}
    if len(unique_namespaces) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"All items in a register batch must share the same namespace. Got: {sorted(unique_namespaces)}"
        )

    # Phase 0b: Batch-validate that all referenced namespaces exist
    existing_ns_docs = await Namespace.find(
        {"prefix": {"$in": list(unique_namespaces)}, "status": "active"}
    ).to_list()
    valid_namespaces = {ns.prefix for ns in existing_ns_docs}
    invalid_namespaces = unique_namespaces - valid_namespaces
    # New ids are generated from this config: reading the namespace again
    # per item cost one database round trip per new entry.
    namespace_docs = {ns.prefix: ns for ns in existing_ns_docs}
    lap("0_namespace")

    # Phase 1: Compute hashes for items with composite keys.
    # When identity_values is provided, compute identity_hash and inject it
    # into composite_key before hashing the full key for dedup.
    hashes = []
    identity_hashes: list[str | None] = []
    for item in items:
        if item.identity_values:
            # Compute identity_hash from raw values
            computed_hash = HashService.compute_composite_key_hash(item.identity_values)
            identity_hashes.append(computed_hash)
            # Inject identity_hash into composite_key for dedup
            item.composite_key["identity_hash"] = computed_hash
            hashes.append(HashService.compute_composite_key_hash(item.composite_key))
        elif item.composite_key:
            identity_hashes.append(None)
            hashes.append(HashService.compute_composite_key_hash(item.composite_key))
        else:
            identity_hashes.append(None)
            hashes.append("")  # Empty composite key = no dedup

    # Phase 2: Batch check for existing entries (by hash and by entry_id).
    # CASE-567: the map is keyed by (namespace, entity_type, key_hash) — a
    # synonym owns its key under the synonym's OWN namespace/entity_type
    # (which may differ from its parent entry's), exactly as lookup_by_keys'
    # $elemMatch treats it. Flattening by hash alone let a cross-namespace
    # synonym slip past dedup and mint a second live owner for its key.
    lap("1_hashes")
    dedup_hashes = [h for h in hashes if h]
    existing_by_hash: dict[tuple[str, str, str], RegistryEntry] = {}
    if dedup_hashes:
        existing_entries = await RegistryEntry.find({
            "$or": [
                {"primary_composite_key_hash": {"$in": dedup_hashes}},
                {"synonyms.composite_key_hash": {"$in": dedup_hashes}}
            ]
        }).to_list()

        for entry in existing_entries:
            existing_by_hash[
                (entry.namespace, entry.entity_type, entry.primary_composite_key_hash)
            ] = entry
            for syn in entry.active_synonyms():
                existing_by_hash[
                    (syn.namespace, syn.entity_type, syn.composite_key_hash)
                ] = entry

    # Also check for existing entries by entry_id (for restore/migration)
    provided_ids = [item.entry_id for item in items if item.entry_id]
    existing_by_entry_id: dict[str, RegistryEntry] = {}
    if provided_ids:
        id_entries = await RegistryEntry.find({
            "entry_id": {"$in": provided_ids}
        }).to_list()
        for entry in id_entries:
            existing_by_entry_id[entry.entry_id] = entry

    lap("2_find_existing")

    # Phase 2.5: IDs the caller sets (a restore re-registering its IDs) raise
    # their prefix's counter first, so nothing minted below — in this batch or
    # later — repeats one of them.
    await IdGeneratorService.advance_for_ids(
        [item.entry_id for item in items if item.entry_id]
    )

    # Phase 3: Build entries to insert
    entries_to_insert: list[RegistryEntry] = []
    insert_indices: list[int] = []
    # CASE-567: keyed by (namespace, entity_type, key_hash) — the same key
    # under two entity types is two entities (namespace_entity_keyhash_
    # unique_idx). The namespace element is defense-in-depth: Phase 0a
    # already rejects mixed-namespace batches. The value is the index of the
    # first item with the key; a later one is answered from what became of
    # it once the batch is written (duplicates).
    seen_in_batch: dict[tuple[str, str, str], int] = {}
    duplicates: list[tuple[int, int, str | None]] = []
    # New ids are minted for the whole batch after the loop: one counter
    # round trip per prefix, and an item refused here takes no number.
    to_mint: list[tuple[int, IdAlgorithmConfig]] = []

    for i, (item, key_hash, id_hash) in enumerate(zip(items, hashes, identity_hashes, strict=False)):
        try:
            # Validate entity_type
            if item.entity_type not in VALID_ENTITY_TYPES:
                results[i] = RegisterKeyResponse(
                    index=i,
                    status="error",
                    error=f"Invalid entity_type: {item.entity_type}",
                    error_code="invalid_request", details={"rule": "invalid_entity_type"},
                )
                error_count += 1
                continue

            # Validate namespace exists
            if item.namespace in invalid_namespaces:
                results[i] = RegisterKeyResponse(
                    index=i,
                    status="error",
                    error=f"Namespace '{item.namespace}' does not exist or is not active",
                    error_code="not_found", details={"rule": "namespace"},
                )
                error_count += 1
                continue

            refusal = key_scope_refusal(item.composite_key, item.namespace) or (
                key_scope_refusal(
                    item.synonym_values, item.namespace, rule="synonym_scope_mismatch",
                )
                if item.synonym_values and not item.skip_identity_value_synonym
                else None
            )
            if refusal:
                results[i] = RegisterKeyResponse(index=i, status="error", **refusal)
                error_count += 1
                continue

            # Check if provided entry_id already exists (collision detection)
            existing: RegistryEntry | None
            if item.entry_id and item.entry_id in existing_by_entry_id:
                existing = existing_by_entry_id[item.entry_id]
                results[i] = RegisterKeyResponse(
                    index=i,
                    status="error",
                    error=(
                        f"entry_id '{item.entry_id}' already exists "
                        f"(namespace='{existing.namespace}', entity_type='{existing.entity_type}'). "
                        f"Restore mode requires a clean target — delete existing data first."
                    ),
                    error_code="already_exists",
                )
                error_count += 1
                continue

            # Check if exists by composite key hash (dedup) — scoped to
            # namespace+entity_type via the map key (CASE-567): a synonym
            # match hits under the synonym's own namespace, not its parent
            # entry's.
            if key_hash:
                existing = existing_by_hash.get(
                    (item.namespace, item.entity_type, key_hash)
                )
                if existing:
                    results[i] = RegisterKeyResponse(
                        index=i,
                        status="already_exists",
                        registry_id=existing.entry_id,
                        namespace=existing.namespace,
                        entity_type=existing.entity_type,
                        identity_hash=id_hash,
                    )
                    exists_count += 1
                    continue

                # Intra-batch dedup: a later item with the same key in the
                # same namespace+entity_type is answered from the first one.
                batch_key = (item.namespace, item.entity_type, key_hash)
                if batch_key in seen_in_batch:
                    duplicates.append((i, seen_in_batch[batch_key], id_hash))
                    continue

            # Use the provided ID, or mint one with the batch below.
            mint_config = None
            entry_id = item.entry_id or ""
            if not item.entry_id:
                mint_config = namespace_docs[item.namespace].get_id_algorithm(item.entity_type)
                IdGeneratorService.check_config(mint_config, item.namespace, item.entity_type)

            # Build synonyms list — add identity_values as a synonym if provided.
            # CASE-430: relationship/edge types set skip_identity_value_synonym
            # so the bare {source_ref, target_ref} synonym (which omits the
            # template and collides across edge types between the same pair) is
            # not created. identity_hash is still computed + injected above, so
            # the primary key and edge dedup/versioning are unaffected.
            synonyms = []
            if (
                item.identity_values and id_hash and item.synonym_values
                and not item.skip_identity_value_synonym
            ):
                # The synonym carries the human-readable spellings: under
                # resolved-referent identity the hash feed (identity_values)
                # holds resolved referent IDs, while synonym_values keeps the
                # submitted business-key values lookups resolve by. The caller
                # qualifies them (document-store adds the template under
                # "@template"). There is deliberately no fallback to
                # identity_values: an unqualified synonym claims the bare
                # values for every template in the namespace.
                synonym_key = item.synonym_values
                synonyms.append(Synonym(
                    namespace=item.namespace,
                    entity_type=item.entity_type,
                    composite_key=synonym_key,
                    composite_key_hash=HashService.compute_composite_key_hash(synonym_key),
                    created_by=item.created_by,
                ))

            entry = RegistryEntry(
                entry_id=entry_id,
                namespace=item.namespace,
                entity_type=item.entity_type,
                primary_composite_key=item.composite_key,
                primary_composite_key_hash=key_hash,
                synonyms=synonyms,
                source_info=item.source_info,
                status="active",
                created_by=item.created_by,
                metadata=item.metadata,
            )
            entry.rebuild_search_values()
            if mint_config is not None:
                to_mint.append((len(entries_to_insert), mint_config))
            entries_to_insert.append(entry)
            insert_indices.append(i)
            if key_hash:
                seen_in_batch[(item.namespace, item.entity_type, key_hash)] = i

        except Exception as e:
            results[i] = RegisterKeyResponse(
                index=i,
                status="error",
                **error_fields(e)
            )
            error_count += 1

    minted = await IdGeneratorService.generate_many([config for _, config in to_mint])
    for (pos, _), new_id in zip(to_mint, minted, strict=True):
        entries_to_insert[pos].entry_id = new_id

    # Phase 3.5: claim-first gate. Every key pair is claimed pending BEFORE
    # the insert (fail-fast on conflict; the item drops out of the batch).
    # An entry without a claim is thereby unrepresentable by ordering. An
    # item whose own key another request registered meanwhile is answered
    # with that request's entry.
    lap("3_build_entries")
    token = new_claim_token()
    gated_entries, gated_indices, exists, errors = await _gate_on_claims(
        entries_to_insert, insert_indices, identity_hashes, results, token,
    )
    exists_count += exists
    error_count += errors

    lap("3b_claim_keys")

    # Phase 4: Batch insert, then confirm the pending claims.
    if gated_entries:
        written, insert_error = await _insert_claimed_entries(gated_entries, token)
        lap("4_insert_entries")
        failure = _insert_failure(insert_error) if insert_error is not None else {}
        for pos, idx in enumerate(gated_indices):
            entry = gated_entries[pos]
            if written is not None and pos < written:
                results[idx] = RegisterKeyResponse(
                    index=idx,
                    status="created",
                    registry_id=entry.entry_id,
                    namespace=entry.namespace,
                    entity_type=entry.entity_type,
                    identity_hash=identity_hashes[idx],
                )
                created_count += 1
            else:
                results[idx] = RegisterKeyResponse(index=idx, status="error", **failure)
                error_count += 1

    exists, errors = _answer_duplicates(results, duplicates)
    exists_count += exists
    error_count += errors

    timing["total"] = round((time.perf_counter() - started) * 1000, 1)
    return RegisterBulkResponse(
        results=cast(list[RegisterKeyResponse], results),
        total=len(items),
        created=created_count,
        already_exists=exists_count,
        errors=error_count,
        timing=timing,
    )


#: How long register waits for the request that holds an item's key to write
#: its entry: attempts times pause (seconds). The holder writes right after
#: claiming, so this only bridges that gap.
_HOLDER_WAIT_ATTEMPTS = 10
_HOLDER_WAIT_PAUSE = 0.02


async def _holder_of_own_key(entry: RegistryEntry) -> RegistryEntry | None:
    """The existing entry that holds ``entry``'s own primary key, or None.

    Only the item's own key makes it the same entity as the holder: a key
    held as one of the item's synonyms is a conflict with a different
    entity. The holder may be another request that claimed the key and is
    about to write its entry; it is given a moment to do so.
    """
    if not entry.primary_composite_key_hash:
        return None
    claim = await CompositeKeyClaim.find_existing(
        entry.namespace, entry.entity_type, entry.primary_composite_key_hash,
    )
    if claim is None or claim.owner_entry_id == entry.entry_id:
        return None
    for attempt in range(_HOLDER_WAIT_ATTEMPTS):
        holder = await RegistryEntry.find_one({"entry_id": claim.owner_entry_id, "status": "active"})
        if holder is not None:
            return holder if _carries_key(holder, entry) else None
        if attempt + 1 < _HOLDER_WAIT_ATTEMPTS:
            await asyncio.sleep(_HOLDER_WAIT_PAUSE)
    return None


def _carries_key(holder: RegistryEntry, entry: RegistryEntry) -> bool:
    scope = (entry.namespace, entry.entity_type, entry.primary_composite_key_hash)
    if (holder.namespace, holder.entity_type, holder.primary_composite_key_hash) == scope:
        return True
    return any(
        (s.namespace, s.entity_type, s.composite_key_hash) == scope
        for s in holder.active_synonyms()
    )


async def _gate_on_claims(
    entries: list[RegistryEntry], indices: list[int], identity_hashes: list[str | None],
    results: list[RegisterKeyResponse | None], token: str,
) -> tuple[list[RegistryEntry], list[int], int, int]:
    """Claim every entry's keys pending; answer the items that cannot have
    them. An item whose own key another request registered meanwhile is
    answered already_exists with that request's entry; any other conflict is
    a key_claimed error. Returns (entries to insert, their item indices,
    already_exists, errors) counted."""
    gated: list[RegistryEntry] = []
    gated_indices: list[int] = []
    exists = errors = 0
    conflicts = await claim_entries_keys_pending(entries, token)
    for entry, idx, conflict in zip(entries, indices, conflicts, strict=True):
        if conflict is None:
            gated.append(entry)
            gated_indices.append(idx)
            continue
        holder = await _holder_of_own_key(entry)
        if holder is not None:
            results[idx] = RegisterKeyResponse(
                index=idx, status="already_exists", registry_id=holder.entry_id,
                namespace=holder.namespace, entity_type=holder.entity_type,
                identity_hash=identity_hashes[idx],
            )
            exists += 1
        else:
            results[idx] = RegisterKeyResponse(
                index=idx, status="error", error=conflict,
                error_code="already_exists", details={"rule": "key_claimed"},
            )
            errors += 1
    return gated, gated_indices, exists, errors


def _answer_duplicates(
    results: list[RegisterKeyResponse | None], duplicates: list[tuple[int, int, str | None]],
) -> tuple[int, int]:
    """Answer each later item of a batch that repeats an earlier item's key
    from what became of the earlier one: already_exists with its entry when
    it was created or found, its error otherwise. Returns (already_exists,
    errors) counted."""
    exists = errors = 0
    for i, first, id_hash in duplicates:
        answer = cast(RegisterKeyResponse, results[first])
        if answer.status in ("created", "already_exists"):
            results[i] = RegisterKeyResponse(
                index=i, status="already_exists", registry_id=answer.registry_id,
                namespace=answer.namespace, entity_type=answer.entity_type,
                identity_hash=id_hash,
            )
            exists += 1
        else:
            results[i] = answer.model_copy(update={"index": i})
            errors += 1
    return exists, errors


@router.post(
    "/rekey-identity",
    response_model=RekeyIdentityBulkResponse,
    summary="Re-key document entries to new identity hashes (migration)"
)
async def rekey_identity(
    items: list[RekeyIdentityItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> RekeyIdentityBulkResponse:
    """Rewrite document entries' primary composite keys to new identity hashes.

    The sanctioned Registry leg of an identity-hash representation change
    (the resolved-referent feed migration): the entity and its synonyms stay,
    only the composite key's identity_hash member and the derived key hash
    move. This is NOT an identity change — identity-changing moves are forks
    and stay rejected elsewhere. Privileged: wip-admins / wip-services only.
    A target key already held by a different entry is a per-item conflict
    (two formerly-forked documents converging — curation, not migration).
    """
    if not identity.has_any_group(["wip-admins", "wip-services"]):
        raise HTTPException(
            status_code=403,
            detail="Identity re-keying is restricted to wip-admins / wip-services",
        )

    results: list[RekeyIdentityResponse] = []
    rekeyed = 0
    failed = 0

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "entry_id": item.entry_id,
                "entity_type": "documents",
            })
            if not entry:
                results.append(RekeyIdentityResponse(
                    index=i, entry_id=item.entry_id, status="not_found",
                ))
                failed += 1
                continue

            current_key = dict(entry.primary_composite_key or {})
            if current_key.get("identity_hash") == item.identity_hash:
                results.append(RekeyIdentityResponse(
                    index=i, entry_id=item.entry_id, status="unchanged",
                ))
                rekeyed += 1
                continue
            if "identity_hash" not in current_key:
                results.append(RekeyIdentityResponse(
                    index=i, entry_id=item.entry_id, status="error",
                    error="entry has no identity-bearing composite key (append-only document)",
                    error_code="invalid_state", details={"rule": "no_identity"},
                ))
                failed += 1
                continue

            new_key = {**current_key, "identity_hash": item.identity_hash}
            new_hash = HashService.compute_composite_key_hash(new_key)
            old_hash = entry.primary_composite_key_hash

            # The key moves with its claim, in the two-phase order: claim the
            # new key pending, write the entry, confirm, then release the old
            # key. The claim is the uniqueness gate, so it also refuses a key
            # another entry holds as a synonym or is claiming right now.
            holder = await RegistryEntry.find_one({
                "namespace": entry.namespace,
                "entity_type": entry.entity_type,
                "primary_composite_key_hash": new_hash,
                "entry_id": {"$ne": entry.entry_id},
            })
            new_claim = (entry.namespace, entry.entity_type, new_hash, entry.entry_id, "primary")
            token = new_claim_token()
            blocker = (
                holder.entry_id if holder
                else (await claim_keys_pending_bulk([[new_claim]], token))[0]
            )
            if blocker is not None:
                results.append(RekeyIdentityResponse(
                    index=i, entry_id=item.entry_id, status="conflict",
                    error=(
                        f"target key is held by entry '{blocker}' — "
                        "two documents converge on one identity; curate "
                        "before migrating"
                    ),
                ))
                failed += 1
                continue

            now = datetime.now(UTC)

            def rekey(fresh: RegistryEntry, new_key=new_key, new_hash=new_hash,
                      old_hash=old_hash, now=now) -> Any:
                if fresh.primary_composite_key_hash == new_hash:
                    return UNCHANGED
                if fresh.primary_composite_key_hash != old_hash:
                    raise EntryWriteConflict(
                        f"Entry {fresh.entry_id} was re-keyed by another request meanwhile"
                    )
                fresh.primary_composite_key = new_key
                fresh.primary_composite_key_hash = new_hash
                fresh.rebuild_search_values()
                fresh.updated_at = now
                fresh.updated_by = identity.identity_string
                return None

            try:
                await apply_to_entry(
                    entry.entry_id, rekey,
                    ("primary_composite_key", "primary_composite_key_hash", "search_values",
                     "updated_at", "updated_by"),
                    first=entry,
                )
            except Exception:
                await release_keys_bulk([new_claim], token)
                raise
            await confirm_keys_bulk([new_claim])
            if old_hash and not any(
                s.composite_key_hash == old_hash for s in entry.active_synonyms()
            ):
                await release_keys_bulk(
                    [(entry.namespace, entry.entity_type, old_hash, entry.entry_id, "primary")]
                )
            results.append(RekeyIdentityResponse(
                index=i, entry_id=item.entry_id, status="rekeyed",
            ))
            rekeyed += 1
        except Exception as e:
            logger.error("Re-key failed for %s: %s", item.entry_id, e)
            results.append(RekeyIdentityResponse(
                index=i, entry_id=item.entry_id, status="error", **error_fields(e),
            ))
            failed += 1

    return RekeyIdentityBulkResponse(
        results=results, total=len(items), succeeded=rekeyed, failed=failed,
    )


@router.post(
    "/provision",
    response_model=ProvisionResponse,
    summary="Provision IDs (registry generates)"
)
async def provision_ids(
    request: ProvisionRequest,
    identity: UserIdentity = Depends(require_privileged_key)
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

    for composite_key in request.composite_keys or []:
        refusal = key_scope_refusal(composite_key, request.namespace)
        if refusal:
            raise HTTPException(status_code=422, detail=refusal["error"])

    config = ns.get_id_algorithm(request.entity_type)
    try:
        IdGeneratorService.check_config(config, request.namespace, request.entity_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    minted = await IdGeneratorService.generate_many([config] * request.count)
    ids = []
    entries = []

    for i, entry_id in enumerate(minted):
        # Deferred-claim reservation mints the id with an EMPTY key: the final
        # key is set and claimed at activation. Empty is required, not just
        # convenient — the entry-level unique index on
        # (namespace, entity_type, primary_composite_key_hash) is partial to
        # non-empty hashes, so two reservations sharing a to-be key must both
        # carry the empty hash to coexist until activation resolves them.
        composite_key = {}
        if not request.defer_claim and request.composite_keys:
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

    if entries and request.defer_claim:
        # Deferred-claim reservation: mint the ids but DON'T claim their keys.
        # The claim is committed at activation with the final key (a fresh
        # restore doesn't know an id-valued-identity document's final hash until
        # every referenced id is minted). Reserved entries are not resolvable,
        # so an unclaimed reserved key leaks nothing.
        await RegistryEntry.insert_many(entries)
    elif entries:
        # Claim-first (pending) for reserved entries too; a conflict on a
        # provisioned composite key fails the whole provision loudly rather
        # than reserving an entry whose key resolves elsewhere.
        token = new_claim_token()
        conflicts = await claim_entries_keys_pending(entries, token)
        conflict = next((c for c in conflicts if c is not None), None)
        if conflict is not None:
            await release_entries_keys(entries, token)
            raise HTTPException(status_code=409, detail=conflict)
        _written, insert_error = await _insert_claimed_entries(entries, token)
        if insert_error is not None:
            raise insert_error

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
    items: list[ReserveItem] = Body(...),
    identity: UserIdentity = Depends(require_privileged_key)
) -> ReserveBulkResponse:
    """
    Validate and store client-provided IDs as reserved.

    Validates each ID against the namespace's configured format.
    """
    results: list[ReserveItemResponse | None] = []
    # The caller sets these IDs: their prefixes' counters must not mint them.
    await IdGeneratorService.advance_for_ids([item.entry_id for item in items])
    reserved_count = 0
    error_count = 0
    entries_to_insert = []
    insert_indices = []

    for i, item in enumerate(items):
        try:
            if item.entity_type not in VALID_ENTITY_TYPES:
                results.append(ReserveItemResponse(
                    index=i, status="error",
                    error=f"Invalid entity_type: {item.entity_type}",
                    error_code="invalid_request", details={"rule": "invalid_entity_type"},
                ))
                error_count += 1
                continue

            # Check if ID already exists
            existing = await RegistryEntry.find_one({"entry_id": item.entry_id})
            if existing:
                results.append(ReserveItemResponse(
                    index=i, status="already_exists", entry_id=item.entry_id
                ))
                error_count += 1
                continue

            # Validate namespace exists
            ns = await Namespace.find_one({"prefix": item.namespace, "status": "active"})
            if not ns:
                results.append(ReserveItemResponse(
                    index=i, status="error", entry_id=item.entry_id,
                    error=f"Namespace '{item.namespace}' does not exist or is not active",
                    error_code="not_found", details={"rule": "namespace"},
                ))
                error_count += 1
                continue

            # Validate format against namespace config
            config = ns.get_id_algorithm(item.entity_type)
            if not IdFormatValidator.validate(item.entry_id, config):
                results.append(ReserveItemResponse(
                    index=i, status="invalid_format", entry_id=item.entry_id,
                    error=f"ID does not match configured format for {item.entity_type}"
                ))
                error_count += 1
                continue

            composite_key = item.composite_key or {}
            refusal = key_scope_refusal(composite_key, item.namespace)
            if refusal:
                results.append(ReserveItemResponse(
                    index=i, status="error", entry_id=item.entry_id, **refusal,
                ))
                error_count += 1
                continue
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
                index=i, status="error", **error_fields(e)
            ))
            error_count += 1

    # Claim-first gate (pending before insert, fail-fast per item), then
    # insert and confirm — same two-phase shape as the register path.
    gated_entries = []
    gated_indices = []
    token = new_claim_token()
    conflicts = await claim_entries_keys_pending(entries_to_insert, token)
    for pos, idx in enumerate(insert_indices):
        entry = entries_to_insert[pos]
        conflict = conflicts[pos]
        if conflict is not None:
            results[idx] = ReserveItemResponse(
                index=idx, status="error", error=conflict,
                error_code="already_exists", details={"rule": "key_claimed"},
            )
            error_count += 1
            continue
        gated_entries.append(entry)
        gated_indices.append(idx)

    if gated_entries:
        written, insert_error = await _insert_claimed_entries(gated_entries, token)
        failure = _insert_failure(insert_error) if insert_error is not None else {}
        for pos, idx in enumerate(gated_indices):
            entry = gated_entries[pos]
            if written is not None and pos < written:
                results[idx] = ReserveItemResponse(
                    index=idx, status="reserved", entry_id=entry.entry_id
                )
                reserved_count += 1
            else:
                results[idx] = ReserveItemResponse(index=idx, status="error", **failure)
                error_count += 1

    return ReserveBulkResponse(
        results=[r for r in results if r is not None],
        total=len(items),
        reserved=reserved_count,
        errors=error_count,
    )


def _attach_activation_synonyms(
    entry: RegistryEntry, synonyms: list[ActivateSynonym],
) -> dict | None:
    """Give an entry the synonyms its activation item carries, in memory.

    The same rules as /synonyms/add: a synonym takes the entry's namespace
    and entity type (anything else is synonym_scope_mismatch), one equal to
    the primary key or already held is not added twice, and an inactive
    copy of the same key is replaced. The caller's claim covers them, since
    an entry's claims are its primary key plus its active synonyms. Returns
    the refusal fields, or None.
    """
    for item in synonyms:
        invalid = invalid_entity_type_fields(item.synonym_entity_type)
        if invalid:
            return invalid
        if item.synonym_namespace not in (None, entry.namespace) or (
            item.synonym_entity_type not in (None, entry.entity_type)
        ):
            return {
                "error": (
                    f"A synonym takes its entry's scope: namespace '{entry.namespace}', "
                    f"entity_type '{entry.entity_type}' (got namespace "
                    f"'{item.synonym_namespace}', entity_type '{item.synonym_entity_type}')"
                ),
                "error_code": "invalid_request",
                "details": {"rule": "synonym_scope_mismatch"},
            }
        refusal = key_scope_refusal(
            item.synonym_composite_key, entry.namespace, rule="synonym_scope_mismatch",
        )
        if refusal:
            return refusal
    for item in synonyms:
        key_hash = HashService.compute_composite_key_hash(item.synonym_composite_key)
        if key_hash == entry.primary_composite_key_hash:
            continue
        recorded = entry.find_synonym_by_hash(key_hash)
        if recorded is not None and recorded.status != "inactive":
            continue
        if recorded is not None:
            entry.synonyms.remove(recorded)
        entry.synonyms.append(Synonym(
            namespace=entry.namespace,
            entity_type=entry.entity_type,
            composite_key=item.synonym_composite_key,
            composite_key_hash=key_hash,
            source_info=item.synonym_source_info,
            created_by=item.created_by,
        ))
    return None


async def _activate_keyed(
    items: list[ActivateItem],
    keyed: list[int],
    results: list[ActivateItemResponse | None],
) -> tuple[int, int]:
    """Activate the keyed items of a request in bulk; fills ``results`` at
    their indices and returns (activated, errors).

    A keyed item finalises a deferred-claim reservation: the entry gets its
    final key, the key is claimed (a collision fails the item, never
    duplicating an identity), and the entry flips to active. For the whole
    request that is one read of the entries, one claim insert, one guarded
    write and one confirm, instead of a read, a claim, a full save and a
    confirm per item. A repeated entry_id is answered as taking the items
    in order would: the first activates, the rest find it active.
    """
    activated = 0
    errors = 0
    entries_coll = RegistryEntry.get_motor_collection()

    first_index: dict[str, int] = {}
    repeats: list[int] = []
    for i in keyed:
        eid = items[i].entry_id
        if eid in first_index:
            repeats.append(i)
        else:
            first_index[eid] = i

    try:
        loaded = {
            e.entry_id: e
            async for e in RegistryEntry.find({"entry_id": {"$in": list(first_index)}})
        }
    except Exception as e:
        # A failed read decides nothing: every keyed item is an error of its
        # own, never a whole-request 500 (bulk-first, like the keyless path).
        for i in keyed:
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=items[i].entry_id, **error_fields(e))
        return 0, len(keyed)

    candidates: list[tuple[int, RegistryEntry]] = []
    for eid, i in first_index.items():
        entry = loaded.get(eid)
        if entry is None:
            results[i] = ActivateItemResponse(index=i, status="not_found", entry_id=eid)
            errors += 1
        elif entry.status == "active":
            results[i] = ActivateItemResponse(index=i, status="already_active", entry_id=eid)
        elif entry.status != "reserved":
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=eid,
                error=f"Cannot activate entry with status '{entry.status}'",
                error_code="invalid_state", details={"rule": "not_reserved"},
            )
            errors += 1
        else:
            key = items[i].composite_key or {}
            refusal = key_scope_refusal(key, entry.namespace)
            if refusal is not None:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=eid, **refusal,
                )
                errors += 1
                continue
            entry.primary_composite_key = key
            entry.primary_composite_key_hash = (
                HashService.compute_composite_key_hash(key) if key else ""
            )
            refusal = _attach_activation_synonyms(entry, items[i].synonyms or [])
            if refusal is not None:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=eid, **refusal,
                )
                errors += 1
                continue
            entry.rebuild_search_values()
            candidates.append((i, entry))

    # Two-phase claim, as provision does it: pending before the write,
    # confirmed after it.
    claim_error: dict | None = None
    token = new_claim_token()
    try:
        conflicts = await claim_entries_keys_pending([entry for _, entry in candidates], token)
    except Exception as e:
        # Claims this call inserted stay pending; startup's pending-claim
        # reconcile settles them against the entries, which stay reserved.
        claim_error = error_fields(e)
        conflicts = [None] * len(candidates)
    to_write: list[tuple[int, RegistryEntry]] = []
    for (i, entry), conflict in zip(candidates, conflicts, strict=True):
        if claim_error is not None:
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=entry.entry_id, **claim_error)
            errors += 1
        elif conflict is not None:
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=entry.entry_id, error=conflict,
                error_code="already_exists", details={"rule": "key_claimed"},
            )
            errors += 1
        else:
            to_write.append((i, entry))

    now = datetime.now(UTC)
    with_synonyms = {items[i].entry_id for i in keyed if items[i].synonyms}
    ops = []
    for _, entry in to_write:
        fields: dict = {
            "primary_composite_key": entry.primary_composite_key,
            "primary_composite_key_hash": entry.primary_composite_key_hash,
            "search_values": entry.search_values,
            "status": "active",
            "updated_at": now,
        }
        if entry.entry_id in with_synonyms:
            fields["synonyms"] = Encoder().encode(entry)["synonyms"]
        ops.append(UpdateOne(
            {"entry_id": entry.entry_id, "status": "reserved"},
            {"$set": fields, "$inc": dict(REVISION_INC)},
        ))
    failed: dict[int, dict] = {}  # position in to_write -> error fields
    if ops:
        try:
            await entries_coll.bulk_write(ops, ordered=False)
        except BulkWriteError as e:
            for err in e.details.get("writeErrors", []):
                failed[err["index"]] = error_fields(Exception(err.get("errmsg", "write failed")))
        except Exception as e:
            # Outcome unknown: the re-read below decides per entry.
            for pos in range(len(to_write)):
                failed[pos] = error_fields(e)

        # An entry is activated only if it now holds the key this request
        # wrote. Anything else (a write error, or a status that changed
        # between the read and the guarded write) is re-read to tell.
        try:
            written = {
                d["entry_id"]: d
                async for d in entries_coll.find(
                    {"entry_id": {"$in": [entry.entry_id for _, entry in to_write]}},
                    {"entry_id": 1, "status": 1, "primary_composite_key_hash": 1, "_id": 0},
                )
            }
        except Exception as e:
            # Outcome unknown and unconfirmable: report each item as an error.
            # Its claim stays pending for startup's reconcile, which settles
            # it by what the entry holds; a retry finds it active or reserved.
            for i, entry in to_write:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=entry.entry_id, **error_fields(e))
                errors += 1
            to_write = []
            written = {}
        confirmed: list[RegistryEntry] = []
        released: list[RegistryEntry] = []
        for pos, (i, entry) in enumerate(to_write):
            doc = written.get(entry.entry_id) or {}
            ours = (
                doc.get("status") == "active"
                and doc.get("primary_composite_key_hash") == entry.primary_composite_key_hash
            )
            if ours:
                confirmed.append(entry)
                results[i] = ActivateItemResponse(
                    index=i, status="activated", entry_id=entry.entry_id)
                activated += 1
                continue
            released.append(entry)
            fields = failed.get(pos) or {
                "error": "Entry status changed concurrently; not activated",
                "error_code": "concurrency_conflict",
            }
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=entry.entry_id, **fields)
            errors += 1
        try:
            # Token-scoped: a losing request releases only the claims it
            # inserted, never one the entry held already or one the request
            # that won took over.
            await release_entries_keys(released, token)
            await confirm_entries_keys(confirmed)
        except Exception:
            # The entries' states are already decided and reported; a claim
            # left pending here is settled by startup's pending-claim reconcile.
            logger.exception("activate: confirming or releasing key claims failed")

    for i in repeats:
        first = results[first_index[items[i].entry_id]]
        if first is not None and first.status in ("activated", "already_active"):
            results[i] = ActivateItemResponse(
                index=i, status="already_active", entry_id=items[i].entry_id)
        else:
            assert first is not None
            results[i] = first.model_copy(update={"index": i})
            if first.status != "already_active":
                errors += 1
    return activated, errors


@router.post(
    "/activate",
    response_model=ActivateBulkResponse,
    summary="Activate reserved entries"
)
async def activate_entries(
    items: list[ActivateItem] = Body(...),
    identity: UserIdentity = Depends(require_privileged_key)
) -> ActivateBulkResponse:
    """Activate reserved entries, making them resolvable.

    Bulk-shaped internally: one classify read plus one guarded update_many,
    instead of a find_one + full-document save per item. A restore activates
    hundreds of thousands of identities in one job, and two sequential round
    trips per identity made this flip the largest single chunk of restore
    wall time (~50% measured at 235k identities, ~775µs each). The per-item
    status contract (activated / already_active / not_found / error) is
    preserved — it is composed from the classify read rather than from
    per-item lookups.

    The update filter re-asserts status == "reserved", so an entry whose
    status changes between the read and the write is simply not matched;
    the modified-count cross-check below turns any such shortfall into
    per-item errors instead of silently reporting it activated.
    """
    results: list[ActivateItemResponse | None] = [None] * len(items)
    activated_count = 0
    error_count = 0
    entries_coll = RegistryEntry.get_motor_collection()

    # A keyed item (composite_key present) is a deferred-claim reservation
    # being finalized: set the FINAL key on the entry, claim it here (a
    # collision fails the item loudly, never duplicating an identity), then
    # flip to active. Keyless items keep the bulk fast path below unchanged.
    keyed = [i for i, it in enumerate(items) if it.composite_key is not None]
    keyless = [i for i, it in enumerate(items) if it.composite_key is None]

    if keyed:
        keyed_activated, keyed_errors = await _activate_keyed(items, keyed, results)
        activated_count += keyed_activated
        error_count += keyed_errors

    if keyless:
        requested_ids = [items[i].entry_id for i in keyless]
        status_by_id: dict[str, str] = {}
        try:
            # Projected to id + status: activation touches hundreds of thousands
            # of entries per restore, and pulling full documents (synonyms
            # included) just to read status would spend the bulk win on payload.
            async for doc in entries_coll.find(
                {"entry_id": {"$in": requested_ids}},
                {"entry_id": 1, "status": 1, "_id": 0},
            ):
                status_by_id[doc["entry_id"]] = doc["status"]
        except Exception as e:
            for i in keyless:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=items[i].entry_id, **error_fields(e))
                error_count += 1
            return ActivateBulkResponse(
                results=[r for r in results if r is not None],
                total=len(items), activated=activated_count, errors=error_count,
            )

        # Deduplicate: a repeated entry_id must not skew the modified-count
        # cross-check (update_many matches each document once).
        reserved_ids = [
            eid for eid in dict.fromkeys(requested_ids)
            if status_by_id.get(eid) == "reserved"
        ]
        flipped_ids: set[str] = set(reserved_ids)
        update_error: dict | None = None
        if reserved_ids:
            try:
                update_result = await entries_coll.update_many(
                    {"entry_id": {"$in": reserved_ids}, "status": "reserved"},
                    {"$set": {
                        "status": "active",
                        "updated_at": datetime.now(UTC),
                    }, "$inc": dict(REVISION_INC)},
                )
                if update_result.modified_count != len(reserved_ids):
                    # A concurrent status change between classify and update: the
                    # guard kept the write safe; re-read to find which ids missed.
                    still_reserved: set[str] = set()
                    async for doc in entries_coll.find(
                        {"entry_id": {"$in": reserved_ids}, "status": "reserved"},
                        {"entry_id": 1, "_id": 0},
                    ):
                        still_reserved.add(doc["entry_id"])
                    flipped_ids -= still_reserved
            except Exception as e:
                # Only the items this write was for failed; the classify read
                # already decided the rest (not_found, already_active, ...).
                update_error = error_fields(e)
                flipped_ids = set()

        for i in keyless:
            item = items[i]
            known_status = status_by_id.get(item.entry_id)
            if known_status is None:
                results[i] = ActivateItemResponse(
                    index=i, status="not_found", entry_id=item.entry_id)
                error_count += 1
            elif known_status == "active":
                results[i] = ActivateItemResponse(
                    index=i, status="already_active", entry_id=item.entry_id)
            elif known_status != "reserved":
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=item.entry_id,
                    error=f"Cannot activate entry with status '{known_status}'",
                    error_code="invalid_state", details={"rule": "not_reserved"},
                )
                error_count += 1
            elif item.entry_id in flipped_ids:
                results[i] = ActivateItemResponse(
                    index=i, status="activated", entry_id=item.entry_id)
                activated_count += 1
            elif update_error is not None:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=item.entry_id, **update_error)
                error_count += 1
            else:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=item.entry_id,
                    error="Entry status changed concurrently; still reserved after update",
                    error_code="concurrency_conflict",
                )
                error_count += 1

    return ActivateBulkResponse(
        results=[r for r in results if r is not None],
        total=len(items),
        activated=activated_count,
        errors=error_count,
    )


@router.post(
    "/export",
    response_model=ExportEntriesResponse,
    summary="Export raw registry entries by id (bulk, admin-gated)"
)
async def export_entries(
    request: ExportEntriesRequest,
    identity: UserIdentity = Depends(require_api_key)
) -> ExportEntriesResponse:
    """Full raw entry rows for backup/export tooling.

    Unlike the browse and lookup surfaces (trimmed projections for humans
    and resolvers), this returns the row as stored — composite-key hash,
    complete synonyms, search_values, source_info, timestamps, status —
    because an archive that carries anything less cannot restore identity
    byte-faithfully. Entries are returned regardless of status (an archive
    records the instance as it is, inactive rows included).

    Admin-gated per entry namespace, matching the backup/restore surfaces
    (archive download requires admin on every namespace it spans). An id in
    a namespace the caller can read but does not administer comes back
    per-item `forbidden`, never a whole-call 403 (bulk-first), so an export
    key short of admin fails loudly. An id in a namespace the caller cannot
    read is `not_found`, exactly like a missing id: as on /lookup/by-id, this
    route does not confirm that an entry exists to a caller who cannot read
    its namespace.
    """
    from .grants import _is_superadmin, _resolve_permission

    entries_by_id: dict[str, RegistryEntry] = {}
    found_rows = await RegistryEntry.find(
        {"entry_id": {"$in": request.entry_ids}}
    ).to_list()
    for row in found_rows:
        entries_by_id[row.entry_id] = row

    superadmin = _is_superadmin(identity)
    permission_by_namespace: dict[str, str] = {}

    async def _permission_on(namespace: str) -> str:
        if superadmin:
            return "admin"
        if namespace not in permission_by_namespace:
            permission_by_namespace[namespace] = await _resolve_permission(
                identity, namespace
            )
        return permission_by_namespace[namespace]

    results: list[ExportEntryItem] = []
    found = not_found = forbidden = 0
    for i, entry_id in enumerate(request.entry_ids):
        entry = entries_by_id.get(entry_id)
        permission = await _permission_on(entry.namespace) if entry else "none"
        if entry is None or permission == "none":
            results.append(ExportEntryItem(
                index=i, entry_id=entry_id, status="not_found"
            ))
            not_found += 1
            continue
        if permission != "admin":
            results.append(ExportEntryItem(
                index=i, entry_id=entry_id, status="forbidden"
            ))
            forbidden += 1
            continue
        results.append(ExportEntryItem(
            index=i, entry_id=entry_id, status="found",
            entry=entry.model_dump(mode="json", exclude={"id"}),
        ))
        found += 1

    return ExportEntriesResponse(
        results=results, total=len(request.entry_ids),
        found=found, not_found=not_found, forbidden=forbidden,
    )


@router.post(
    "/lookup/by-id",
    response_model=LookupBulkResponse,
    summary="Lookup entries by ID (bulk)"
)
async def lookup_by_ids(
    items: list[LookupByIdItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> LookupBulkResponse:
    """Look up registry entries by their IDs. Only active entries are resolvable.

    An entry in a namespace the caller cannot read is reported not_found.
    A value that is no entry ID is matched against the values of entries'
    keys; when several entries the caller can read carry it, the item is
    ambiguous with the candidates (/lookup/candidates lists them all).
    """
    results = []
    found_count = 0
    not_found_count = 0
    ambiguous_count = 0
    error_count = 0
    access = NamespaceAccess(identity)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                invalid = invalid_entity_type_fields(item.entity_type)
                if invalid:
                    results.append(LookupResponse(index=i, status="error", **invalid))
                    error_count += 1
                    continue

                matched_via = None

                # 1. Find by entry_id
                q1: dict = {"entry_id": item.entry_id, "status": "active"}
                if item.namespace:
                    q1["namespace"] = item.namespace
                if item.entity_type:
                    q1["entity_type"] = item.entity_type
                entry = await RegistryEntry.find_one(q1)
                if entry and not await access.allows(entry.namespace, "read"):
                    entry = None
                if entry:
                    matched_via = "entry_id"

                # 2. Composite key value search (covers merged IDs via
                # search_values), over the entries the caller can read. A
                # value several of them carry is ambiguous: answered with the
                # candidates, never with whichever the database returns first.
                if not entry:
                    q3: dict = {"search_values": item.entry_id, "status": "active"}
                    if item.namespace:
                        q3["namespace"] = item.namespace
                    elif not access.unrestricted:
                        q3["namespace"] = {
                            "$in": await resolve_accessible_namespaces(identity) or [],
                        }
                    if item.entity_type:
                        q3["entity_type"] = item.entity_type
                    matches = await RegistryEntry.find(q3).sort("+created_at").limit(
                        _RESOLVE_CANDIDATE_LIMIT + 1,
                    ).to_list()
                    matches = [m for m in matches if await access.allows(m.namespace, "read")]
                    if len(matches) > 1:
                        results.append(LookupResponse(
                            index=i, status="ambiguous",
                            error=(
                                f"'{item.entry_id}' is carried by {len(matches)} entries; "
                                "qualify it (namespace, entity_type) or use a canonical ID."
                            ),
                            error_code="ambiguous",
                            candidates=[
                                _candidate(m, item.entry_id)
                                for m in matches[:_RESOLVE_CANDIDATE_LIMIT]
                            ],
                        ))
                        ambiguous_count += 1
                        continue
                    entry = matches[0] if matches else None
                    if entry:
                        matched_via = "composite_key_value"

                if not entry:
                    results.append(LookupResponse(index=i, status="not_found"))
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
                        source_data = {"error": f"Failed to fetch: {e!s}"}

                results.append(build_lookup_response(
                    index=i, entry=entry,
                    matched_via=matched_via, source_data=source_data
                ))
                found_count += 1

            except Exception as e:
                results.append(LookupResponse(index=i, status="error", **error_fields(e)))
                error_count += 1

    return LookupBulkResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count,
        ambiguous=ambiguous_count, errors=error_count,
    )


def _candidate(entry: RegistryEntry, value: str) -> LookupCandidate:
    """The candidate for an entry that a value matched, naming the key that
    carried the value: the primary key, or the first active synonym."""
    if value in entry.primary_composite_key.values():
        via, key = "primary_key", entry.primary_composite_key
    else:
        via, key = "synonym", None
        for syn in entry.active_synonyms():
            if value in syn.composite_key.values():
                key = syn.composite_key
                break
    return LookupCandidate(
        entry_id=entry.entry_id, namespace=entry.namespace,
        entity_type=entry.entity_type, matched_via=via, matched_composite_key=key,
    )


@router.post(
    "/lookup/candidates",
    response_model=BulkLookupCandidatesResponse,
    summary="List every entry a value resolves to (bulk)"
)
async def lookup_candidates(
    items: list[LookupCandidatesItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkLookupCandidatesResponse:
    """Every active entry a value resolves to, each with the key that carried
    the value — so a caller sees ambiguity instead of receiving one match
    picked arbitrarily.

    An exact entry ID is the only candidate. Otherwise the value is matched
    against the values of each entry's primary key and active synonyms
    (inactive synonyms belong to retired entities and resolve nothing), in
    creation order. `total` counts every match in the namespaces the caller
    may read; `truncated` says more exist than `limit` returned. A caller
    that needs one entity qualifies the value (a composite-key lookup, a
    canonical ID) or narrows the candidates itself.
    """
    results: list[LookupCandidatesResponse] = []
    access = NamespaceAccess(identity)
    for i, item in enumerate(items):
        try:
            invalid = invalid_entity_type_fields(item.entity_type)
            if invalid:
                results.append(LookupCandidatesResponse(index=i, status="error", **invalid))
                continue

            query: dict = {"status": "active"}
            if item.entity_type:
                query["entity_type"] = item.entity_type
            if item.namespace:
                if not await access.allows(item.namespace, "read"):
                    results.append(LookupCandidatesResponse(index=i, status="not_found"))
                    continue
                query["namespace"] = item.namespace
            elif not access.unrestricted:
                query["namespace"] = {"$in": await resolve_accessible_namespaces(identity) or []}

            exact = await RegistryEntry.find_one({**query, "entry_id": item.value})
            if exact:
                results.append(LookupCandidatesResponse(
                    index=i, status="found", total=1,
                    candidates=[LookupCandidate(
                        entry_id=exact.entry_id, namespace=exact.namespace,
                        entity_type=exact.entity_type, matched_via="entry_id",
                    )],
                ))
                continue

            by_value = {**query, "search_values": item.value}
            total = await RegistryEntry.find(by_value).count()
            entries = await RegistryEntry.find(by_value).sort("+created_at").limit(item.limit).to_list()
            results.append(LookupCandidatesResponse(
                index=i,
                status="found" if entries else "not_found",
                total=total,
                truncated=total > len(entries),
                candidates=[_candidate(e, item.value) for e in entries],
            ))
        except Exception as e:
            results.append(LookupCandidatesResponse(index=i, status="error", **error_fields(e)))

    return BulkLookupCandidatesResponse(
        results=results, total=len(items),
        found=sum(1 for r in results if r.status == "found"),
        not_found=sum(1 for r in results if r.status == "not_found"),
        errors=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/lookup/by-key",
    response_model=LookupBulkResponse,
    summary="Lookup entries by composite key (bulk)"
)
async def lookup_by_keys(
    items: list[LookupByKeyItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> LookupBulkResponse:
    """Look up registry entries by their composite keys.

    A key looked up in a namespace the caller cannot read, or matching an
    entry that lives in one, is reported not_found. An unknown entity_type is
    an error, not not_found: no key can exist under it.
    """
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0
    access = NamespaceAccess(identity)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                invalid = invalid_entity_type_fields(item.entity_type)
                if invalid:
                    results.append(LookupResponse(index=i, status="error", **invalid))
                    error_count += 1
                    continue

                if not await access.allows(item.namespace, "read"):
                    results.append(LookupResponse(index=i, status="not_found"))
                    not_found_count += 1
                    continue

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
                                        "composite_key_hash": key_hash,
                                        "status": {"$ne": "inactive"},
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

                # CASE-427 defensive guard: fetch up to 2 matches in one query
                # so we can detect (and loudly log) the invariant violation of a
                # composite key resolving to more than one entry — which the
                # claim gate + migration make impossible. No client-facing change;
                # [0] is the deterministic pick exactly as find_one returned.
                matches = await RegistryEntry.find(query).limit(2).to_list()
                if len(matches) > 1:
                    logger.error(
                        "CASE-427 INVARIANT VIOLATION: composite key %s (%s/%s) "
                        "resolved to multiple entries %s — uniqueness gate breached.",
                        key_hash, item.namespace, item.entity_type,
                        [e.entry_id for e in matches],
                    )
                entry = matches[0] if matches else None

                # A synonym takes its entry's namespace and type (enforced on
                # add), so a key looked up in one namespace/type can only name
                # an entry of that namespace/type. One stored before that rule
                # would hand back another namespace's or type's entity: refuse
                # it loudly rather than answer with the wrong thing.
                if entry and (entry.namespace, entry.entity_type) != (item.namespace, item.entity_type):
                    logger.error(
                        "Synonym scope violation: key %s looked up as %s/%s matched "
                        "entry %s, which is %s/%s — not returned.",
                        key_hash, item.namespace, item.entity_type,
                        entry.entry_id, entry.namespace, entry.entity_type,
                    )
                    entry = None

                if not entry or not await access.allows(entry.namespace, "read"):
                    results.append(LookupResponse(index=i, status="not_found"))
                    not_found_count += 1
                    continue

                matched_namespace = item.namespace
                matched_entity_type = item.entity_type
                matched_composite_key = item.composite_key
                if entry.primary_composite_key_hash != key_hash:
                    for syn in entry.active_synonyms():
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
                        source_data = {"error": f"Failed to fetch: {e!s}"}

                results.append(build_lookup_response(
                    index=i, entry=entry,
                    matched_namespace=matched_namespace,
                    matched_entity_type=matched_entity_type,
                    matched_composite_key=matched_composite_key,
                    source_data=source_data
                ))
                found_count += 1

            except Exception as e:
                results.append(LookupResponse(index=i, status="error", **error_fields(e)))
                error_count += 1

    return LookupBulkResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count, errors=error_count,
    )


@router.put(
    "",
    response_model=BulkUpdateResponse,
    summary="Update entries (bulk)"
)
async def update_entries(
    items: list[UpdateEntryItem] = Body(...),
    identity: UserIdentity = Depends(require_privileged_key)
) -> BulkUpdateResponse:
    """Update one or more registry entries."""
    results = []

    for i, item in enumerate(items):
        try:
            now = datetime.now(UTC)

            def update(entry: RegistryEntry, item=item, now=now) -> None:
                if item.source_info is not None:
                    entry.source_info = item.source_info
                if item.metadata is not None:
                    entry.metadata.update(item.metadata)
                entry.updated_at = now
                entry.updated_by = item.updated_by

            # Only the fields an update changes, and only over the revision it
            # read: a concurrent synonym change or soft delete is kept.
            entry, _ = await apply_to_entry(
                item.entry_id, update,
                ("source_info", "metadata", "updated_at", "updated_by"),
                read_filter={"status": "active"},
            )
            if not entry:
                results.append(UpdateEntryResponse(
                    index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue

            results.append(UpdateEntryResponse(
                index=i, status="updated", registry_id=item.entry_id,
            ))

        except Exception as e:
            results.append(UpdateEntryResponse(
                index=i, status="error", registry_id=item.entry_id, **error_fields(e)
            ))

    return BulkUpdateResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "updated"),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
    )


async def _soft_delete_entry(index: int, entry: RegistryEntry, updated_by: str | None) -> DeleteResponse:
    """Set an entry inactive — only those fields, over the revision read
    (entry_writes), so a concurrent change to the entry is kept."""
    now = datetime.now(UTC)

    def deactivate(fresh: RegistryEntry) -> None:
        fresh.status = "inactive"
        fresh.updated_at = now
        fresh.updated_by = updated_by

    gone, _ = await apply_to_entry(
        entry.entry_id, deactivate, ("status", "updated_at", "updated_by"), first=entry,
    )
    status = "not_found" if gone is None else "deactivated"
    return DeleteResponse(index=index, status=status, registry_id=entry.entry_id)


@router.delete(
    "",
    response_model=BulkDeleteResponse,
    summary="Delete entries (bulk, soft or hard delete)"
)
async def delete_entries(
    items: list[DeleteItem] = Body(...),
    identity: UserIdentity = Depends(require_privileged_key)
) -> BulkDeleteResponse:
    """Deactivate or hard-delete one or more registry entries.

    Hard-delete (hard_delete=True) permanently removes the entry from MongoDB.
    Requires the entry's namespace to have deletion_mode='full'.
    """
    results = []

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({"entry_id": item.entry_id})

            if not entry:
                results.append(DeleteResponse(
                    index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue

            if item.hard_delete or item.rollback_uncommitted or item.owner_hard_deleted:
                # CASE-436: rollback_uncommitted bypasses the deletion_mode gate
                # — it aborts a just-allocated entry whose backing object was
                # never committed (a document create that failed on synonym
                # registration), which is a trusted write-rollback, not
                # user-data deletion. Only privileged callers reach this route
                # (require_privileged_key), so the flag needs no further check.
                privileged_rollback = item.rollback_uncommitted
                # The owning service already removed the backing object under a
                # deletion_mode exemption (mutable-terminology terms): the entry
                # follows, or its synonyms resolve to nothing.
                privileged_owner_delete = item.owner_hard_deleted
                if privileged_rollback or privileged_owner_delete:
                    logger.info(
                        "%s hard-delete of entry %s (ns=%s) by %s — bypassing "
                        "deletion_mode gate.",
                        "rollback_uncommitted" if privileged_rollback else "owner_hard_deleted",
                        item.entry_id, entry.namespace, identity.user_id,
                    )
                else:
                    # Verify namespace allows hard-delete
                    namespace = await Namespace.find_one({"prefix": entry.namespace})
                    if not namespace or namespace.deletion_mode != "full":
                        mode = namespace.deletion_mode if namespace else "unknown"
                        results.append(DeleteResponse(
                            index=i, status="error", registry_id=item.entry_id,
                            error=f"Hard-delete requires namespace deletion_mode='full' (currently '{mode}')",
                            error_code="invalid_state", details={"rule": "deletion_mode_retain"},
                        ))
                        continue

                # Permanently remove from MongoDB
                await entry.delete()
                # CASE-427: release all claims owned by this entry (hard delete
                # only — soft-delete keeps the entry and its key ownership).
                await CompositeKeyClaim.release_for_owner(entry.entry_id)
                results.append(DeleteResponse(
                    index=i, status="deleted", registry_id=item.entry_id,
                ))
            else:
                results.append(await _soft_delete_entry(i, entry, item.updated_by))

        except Exception as e:
            results.append(DeleteResponse(
                index=i, status="error", registry_id=item.entry_id, **error_fields(e)
            ))

    # Deleted or deactivated entries stop resolving; other services drop
    # what they cached for them.
    await entries_changed(
        r.registry_id for r in results
        if r.status in ("deactivated", "deleted") and r.registry_id
    )
    return BulkDeleteResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status in ("deactivated", "deleted")),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
    )


@router.post(
    "/deactivate",
    response_model=BulkEntryLifecycleResponse,
    summary="Retire entries' synonyms when their entities are retired (bulk)"
)
async def deactivate_entries(
    items: list[EntryLifecycleItem] = Body(...),
    dry_run: bool = Query(False, description="Report what would be released, change nothing"),
    identity: UserIdentity = Depends(require_privileged_key)
) -> BulkEntryLifecycleResponse:
    """Called by the owning service when it retires an entity (soft delete,
    deprecation, deactivation).

    The entity's synonyms are released: each becomes inactive, stays recorded
    on the entry, and neither blocks another entity from claiming its key nor
    resolves. The entry itself stays active — its canonical ID and primary key
    keep resolving, because a retired entity is retired, not gone. The
    auto-synonym (the entity's own {ns, type, …} address) stays claimed too,
    so the entity remains addressable by value. Idempotent: an entry with
    nothing left to release reports unchanged.
    """
    results: list[EntryLifecycleResponse] = []
    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({"entry_id": item.entry_id})
            if not entry:
                results.append(EntryLifecycleResponse(
                    index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue
            released = await retire_entry_synonyms(entry, item.updated_by, dry_run=dry_run)
            results.append(EntryLifecycleResponse(
                index=i,
                status="deactivated" if released else "unchanged",
                registry_id=entry.entry_id,
                released=[s.composite_key for s in released],
            ))
        except Exception as e:
            results.append(EntryLifecycleResponse(
                index=i, status="error", registry_id=item.entry_id, **error_fields(e),
            ))

    if not dry_run:
        # Released synonyms stop resolving to these entries.
        await entries_changed(
            r.registry_id for r in results if r.status == "deactivated" and r.registry_id
        )
    return BulkEntryLifecycleResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status in ("deactivated", "unchanged")),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
        dry_run=dry_run,
    )


@router.post(
    "/reactivate",
    response_model=BulkEntryLifecycleResponse,
    summary="Reclaim entries' synonyms when their entities are revived (bulk)"
)
async def reactivate_entries(
    items: list[EntryLifecycleItem] = Body(...),
    identity: UserIdentity = Depends(require_privileged_key)
) -> BulkEntryLifecycleResponse:
    """Called by the owning service when it revives a retired entity.

    Synonym-level only, like /deactivate: the entry's own status is not
    touched (a retired entity's entry stays active throughout). Each inactive
    synonym is claimed again where its key is free. A key another entity took
    while this one was retired stays with that entity: that synonym stays
    inactive, the others are reclaimed without it, and a warning names the
    synonym and its holder. Idempotent: an entry with nothing to reclaim
    reports unchanged.
    """
    results: list[EntryLifecycleResponse] = []
    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({"entry_id": item.entry_id})
            if not entry:
                results.append(EntryLifecycleResponse(
                    index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue
            reclaimed, taken = await revive_entry_synonyms(entry, item.updated_by)
            results.append(EntryLifecycleResponse(
                index=i,
                status="reactivated" if reclaimed or taken else "unchanged",
                registry_id=entry.entry_id,
                reclaimed=[s.composite_key for s in reclaimed],
                not_reclaimed=[
                    {"composite_key": s.composite_key, "held_by": holder}
                    for s, holder in taken
                ],
                warnings=[
                    f"Synonym {s.composite_key} is now held by entry {holder}; "
                    "the other synonyms were reclaimed without it"
                    for s, holder in taken
                ],
            ))
        except Exception as e:
            results.append(EntryLifecycleResponse(
                index=i, status="error", registry_id=item.entry_id, **error_fields(e),
            ))

    return BulkEntryLifecycleResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status in ("reactivated", "unchanged")),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
    )


async def _resolve_entry_id(
    entry_id: str, entity_type: str | None, status_filter: Any,
) -> RegistryEntry | None:
    """The entry a canonical id names, following merges: a merged-away
    entry's id answers the entry it was merged into, whose status the filter
    then judges."""
    query: dict[str, Any] = {"entry_id": entry_id}
    if entity_type:
        query["entity_type"] = entity_type
    found = await RegistryEntry.find_one({**query, "$or": [
        {"status": status_filter}, {"merged_into": {"$nin": [None, ""]}},
    ]})
    found = await follow_merges(found)
    if found is None:
        return None
    wanted = status_filter["$in"] if isinstance(status_filter, dict) else [status_filter]
    return found if found.status in wanted else None


@router.post(
    "/resolve",
    response_model=BulkResolveResponse,
    summary="Resolve synonym composite keys to entry IDs (bulk)"
)
async def resolve_synonyms(
    items: list[ResolveItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResolveResponse:
    """
    Resolve synonyms or verify canonical IDs.

    Each item may provide ``entry_id`` (canonical ID verification),
    ``composite_key`` (synonym resolution), or both.  When both are
    given, ``entry_id`` is tried first.

    Returns entry_id for each found item, "not_found" otherwise — including
    for an entry in a namespace the caller cannot read.

    A composite key is looked up in ``namespace`` when given, else in the
    namespace the key itself names (its ``ns`` member), else in every
    namespace the caller can read. When more than one entry the caller can
    read holds the key, the item is ``ambiguous`` with the candidates —
    never one of them picked.
    """
    results = []
    found_count = 0
    not_found_count = 0
    ambiguous_count = 0
    error_count = 0
    access = NamespaceAccess(identity)

    for i, item in enumerate(items):
        try:
            invalid = invalid_entity_type_fields(item.entity_type)
            if invalid:
                results.append(ResolveResponse(index=i, status="error", **invalid))
                error_count += 1
                continue

            if not item.entry_id and not item.composite_key:
                results.append(ResolveResponse(
                    index=i, status="error",
                    error="Provide either entry_id or composite_key", error_code="invalid_request",
                ))
                error_count += 1
                continue

            status_filter = (
                {"$in": item.include_statuses}
                if item.include_statuses
                else "active"
            )
            entry = None

            # Path 1: Canonical ID verification. With entity_type given, an id
            # resolves only as its own type: a template's id is not a
            # terminology. A miss falls through to the composite key, so a
            # value equal to another type's id still finds its own entry.
            # Namespace stays out: canonical ids are namespace-free. The id of
            # an entry merged into another resolves to that entry.
            if item.entry_id:
                entry = await _resolve_entry_id(item.entry_id, item.entity_type, status_filter)

            if entry and not await access.allows(entry.namespace, "read"):
                entry = None

            # Path 2: Composite key resolution (synonym or primary key).
            # Scoped to the namespace asked for, else the one the key names
            # (its 'ns'): key claims are unique per namespace, so a key that
            # names its namespace has one possible holder there. Only
            # entries the caller can read take part, and more than one
            # match is refused as ambiguous, never answered with one of them.
            if not entry and item.composite_key:
                key_hash = HashService.compute_composite_key_hash(item.composite_key)
                query: dict[str, Any] = {
                    "$or": [
                        {"primary_composite_key_hash": key_hash},
                        {"synonyms": {"$elemMatch": {
                            "composite_key_hash": key_hash,
                            "status": {"$ne": "inactive"},
                        }}},
                    ],
                    "status": status_filter,
                }
                scope = item.namespace or named_namespace(item.composite_key)
                if scope:
                    query["namespace"] = scope
                elif not access.unrestricted:
                    query["namespace"] = {"$in": await resolve_accessible_namespaces(identity) or []}
                if item.entity_type:
                    query["entity_type"] = item.entity_type
                matches = await RegistryEntry.find(query).sort("+created_at").limit(
                    _RESOLVE_CANDIDATE_LIMIT + 1,
                ).to_list()
                matches = [m for m in matches if await access.allows(m.namespace, "read")]
                if len(matches) > 1:
                    logger.warning(
                        "Composite key %s resolves to %d entries %s: answered ambiguous.",
                        key_hash, len(matches), [e.entry_id for e in matches],
                    )
                    results.append(ResolveResponse(
                        index=i,
                        status="ambiguous",
                        composite_key=item.composite_key,
                        error=(
                            f"The key resolves to {len(matches)} entries; qualify it "
                            "(namespace, a canonical ID) to name one."
                        ),
                        error_code="ambiguous",
                        candidates=[
                            LookupCandidate(
                                entry_id=m.entry_id, namespace=m.namespace,
                                entity_type=m.entity_type, matched_via=(
                                    "primary_key" if m.primary_composite_key_hash == key_hash
                                    else "synonym"
                                ),
                                matched_composite_key=item.composite_key,
                            )
                            for m in matches[:_RESOLVE_CANDIDATE_LIMIT]
                        ],
                    ))
                    ambiguous_count += 1
                    continue
                entry = matches[0] if matches else None

            if entry:
                results.append(ResolveResponse(
                    index=i,
                    status="found",
                    composite_key=item.composite_key,
                    entry_id=entry.entry_id,
                ))
                found_count += 1
            else:
                results.append(ResolveResponse(
                    index=i,
                    status="not_found",
                    composite_key=item.composite_key,
                    entry_id=item.entry_id,
                ))
                not_found_count += 1

        except Exception as e:
            results.append(ResolveResponse(
                index=i, status="error", **error_fields(e),
            ))
            error_count += 1

    return BulkResolveResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count,
        ambiguous=ambiguous_count, errors=error_count,
    )
