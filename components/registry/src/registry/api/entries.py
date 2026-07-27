"""Registry entry management API endpoints."""

import logging
import math
import re
from datetime import UTC, datetime
from typing import cast

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import UserIdentity

from ..models.api_models import (
    ActivateBulkResponse,
    ActivateItem,
    ActivateItemResponse,
    BrowseEntriesResponse,
    BrowseEntryItem,
    BulkDeleteResponse,
    BulkResolveResponse,
    BulkUpdateResponse,
    DeleteItem,
    DeleteResponse,
    EntryDetailResponse,
    ExportEntriesRequest,
    ExportEntriesResponse,
    ExportEntryItem,
    LookupBulkResponse,
    LookupByIdItem,
    LookupByKeyItem,
    LookupResponse,
    ProvisionedId,
    ProvisionRequest,
    ProvisionResponse,
    RegisterBulkResponse,
    RegisterKeyItem,
    RegisterKeyResponse,
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
from ..models.id_algorithm import VALID_ENTITY_TYPES, IdFormatValidator
from ..models.namespace import Namespace
from ..services.auth import require_api_key
from ..services.claims import (
    claim_entry_keys_pending,
    confirm_entry_keys,
    release_entry_keys,
)
from ..services.hash import HashService
from ..services.id_generator import IdGeneratorService
from .grants import resolve_accessible_namespaces

logger = logging.getLogger("registry.entries")

router = APIRouter()


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


@router.get(
    "",
    response_model=BrowseEntriesResponse,
    summary="Browse registry entries"
)
async def browse_entries(
    namespace: str | None = Query(None, description="Filter by namespace"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    status: str | None = Query(None, description="Filter by status (active, reserved, inactive)"),
    q: str | None = Query(None, description="Search across entry IDs and composite key values"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Page size (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> BrowseEntriesResponse:
    """Browse registry entries with pagination and optional filters."""
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
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Page size (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> UnifiedSearchResponse:
    """
    Unified search across entry IDs, additional IDs, and all composite key values
    (primary + synonyms). Returns rich results with match context and resolution paths.
    """
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
                for syn in entry.synonyms:
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
    """Get full details for a single registry entry by its entry_id."""
    entry = await RegistryEntry.find_one({"entry_id": entry_id})

    if not entry:
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
    identity: UserIdentity = Depends(require_api_key)
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
            for syn in entry.synonyms:
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

    # Phase 3: Build entries to insert
    entries_to_insert: list[RegistryEntry] = []
    insert_indices: list[int] = []
    # CASE-567: keyed by (namespace, entity_type, key_hash) — the same key
    # under two entity types is two entities (namespace_entity_keyhash_
    # unique_idx). The namespace element is defense-in-depth: Phase 0a
    # already rejects mixed-namespace batches.
    seen_in_batch: dict[tuple[str, str, str], str] = {}

    for i, (item, key_hash, id_hash) in enumerate(zip(items, hashes, identity_hashes, strict=False)):
        try:
            # Validate entity_type
            if item.entity_type not in VALID_ENTITY_TYPES:
                results[i] = RegisterKeyResponse(
                    index=i,
                    status="error",
                    error=f"Invalid entity_type: {item.entity_type}"
                )
                error_count += 1
                continue

            # Validate namespace exists
            if item.namespace in invalid_namespaces:
                results[i] = RegisterKeyResponse(
                    index=i,
                    status="error",
                    error=f"Namespace '{item.namespace}' does not exist or is not active"
                )
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

                # Intra-batch dedup: second item with the same key in the
                # same namespace+entity_type gets already_exists
                batch_key = (item.namespace, item.entity_type, key_hash)
                if batch_key in seen_in_batch:
                    first_entry_id = seen_in_batch[batch_key]
                    results[i] = RegisterKeyResponse(
                        index=i,
                        status="already_exists",
                        registry_id=first_entry_id,
                        namespace=item.namespace,
                        entity_type=item.entity_type,
                        identity_hash=id_hash,
                    )
                    exists_count += 1
                    continue

            # Generate or use provided ID
            if item.entry_id:
                entry_id = item.entry_id
            else:
                entry_id = await IdGeneratorService.generate(item.namespace, item.entity_type)

            # Build synonyms list — add identity_values as a synonym if provided.
            # CASE-430: relationship/edge types set skip_identity_value_synonym
            # so the bare {source_ref, target_ref} synonym (which omits the
            # template and collides across edge types between the same pair) is
            # not created. identity_hash is still computed + injected above, so
            # the primary key and edge dedup/versioning are unaffected.
            synonyms = []
            if item.identity_values and id_hash and not item.skip_identity_value_synonym:
                synonyms.append(Synonym(
                    namespace=item.namespace,
                    entity_type=item.entity_type,
                    composite_key=item.identity_values,
                    composite_key_hash=id_hash,
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
            entries_to_insert.append(entry)
            insert_indices.append(i)
            if key_hash:
                seen_in_batch[(item.namespace, item.entity_type, key_hash)] = entry_id

        except Exception as e:
            results[i] = RegisterKeyResponse(
                index=i,
                status="error",
                error=str(e)
            )
            error_count += 1

    # Phase 3.5: claim-first gate. Every key pair is claimed pending BEFORE
    # the insert (fail-fast on conflict; the item drops out of the batch).
    # An entry without a claim is thereby unrepresentable by ordering.
    gated_entries: list = []
    gated_indices: list[int] = []
    for pos, idx in enumerate(insert_indices):
        entry = entries_to_insert[pos]
        conflict = await claim_entry_keys_pending(entry)
        if conflict is not None:
            results[idx] = RegisterKeyResponse(
                index=idx, status="error", error=conflict,
            )
            error_count += 1
            continue
        gated_entries.append(entry)
        gated_indices.append(idx)

    # Phase 4: Batch insert, then confirm the pending claims.
    if gated_entries:
        try:
            await RegistryEntry.insert_many(gated_entries)
            for pos, idx in enumerate(gated_indices):
                entry = gated_entries[pos]
                await confirm_entry_keys(entry)
                results[idx] = RegisterKeyResponse(
                    index=idx,
                    status="created",
                    registry_id=entry.entry_id,
                    namespace=entry.namespace,
                    entity_type=entry.entity_type,
                    identity_hash=identity_hashes[idx],
                )
                created_count += 1
        except Exception as e:
            for pos, idx in enumerate(gated_indices):
                if results[idx] is None:
                    await release_entry_keys(gated_entries[pos])
                    results[idx] = RegisterKeyResponse(
                        index=idx,
                        status="error",
                        error=f"Batch insert failed: {e!s}"
                    )
                    error_count += 1

    return RegisterBulkResponse(
        results=cast(list[RegisterKeyResponse], results),
        total=len(items),
        created=created_count,
        already_exists=exists_count,
        errors=error_count,
    )


@router.post(
    "/provision",
    response_model=ProvisionResponse,
    summary="Provision IDs (registry generates)"
)
async def provision_ids(
    request: ProvisionRequest,
    identity: UserIdentity = Depends(require_api_key)
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

    config = ns.get_id_algorithm(request.entity_type)
    ids = []
    entries = []

    for i in range(request.count):
        entry_id = await IdGeneratorService.generate_from_config(
            config, request.namespace, request.entity_type
        )

        # Deferred-claim reservation mints the id with an EMPTY key: the final
        # key is set and claimed at activation. Empty is required, not just
        # convenient — the entry-level unique index on
        # (namespace, entity_type, primary_composite_key_hash) is partial to
        # non-empty hashes, so two reservations sharing a to-be key must both
        # carry the empty hash to coexist until activation resolves them.
        composite_key = {}
        if (
            not request.defer_claim
            and request.composite_keys
            and i < len(request.composite_keys)
        ):
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
        for entry in entries:
            conflict = await claim_entry_keys_pending(entry)
            if conflict is not None:
                for e in entries:
                    await release_entry_keys(e)
                raise HTTPException(status_code=409, detail=conflict)
        try:
            await RegistryEntry.insert_many(entries)
        except Exception:
            for entry in entries:
                await release_entry_keys(entry)
            raise
        for entry in entries:
            await confirm_entry_keys(entry)

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
    identity: UserIdentity = Depends(require_api_key)
) -> ReserveBulkResponse:
    """
    Validate and store client-provided IDs as reserved.

    Validates each ID against the namespace's configured format.
    """
    results: list[ReserveItemResponse | None] = []
    reserved_count = 0
    error_count = 0
    entries_to_insert = []
    insert_indices = []

    for i, item in enumerate(items):
        try:
            if item.entity_type not in VALID_ENTITY_TYPES:
                results.append(ReserveItemResponse(
                    index=i, status="error",
                    error=f"Invalid entity_type: {item.entity_type}"
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
                    error=f"Namespace '{item.namespace}' does not exist or is not active"
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
                index=i, status="error", error=str(e)
            ))
            error_count += 1

    # Claim-first gate (pending before insert, fail-fast per item), then
    # insert and confirm — same two-phase shape as the register path.
    gated_entries = []
    gated_indices = []
    for pos, idx in enumerate(insert_indices):
        entry = entries_to_insert[pos]
        conflict = await claim_entry_keys_pending(entry)
        if conflict is not None:
            results[idx] = ReserveItemResponse(
                index=idx, status="error", error=conflict,
            )
            error_count += 1
            continue
        gated_entries.append(entry)
        gated_indices.append(idx)

    if gated_entries:
        try:
            await RegistryEntry.insert_many(gated_entries)
            for pos, idx in enumerate(gated_indices):
                entry = gated_entries[pos]
                await confirm_entry_keys(entry)
                results[idx] = ReserveItemResponse(
                    index=idx, status="reserved", entry_id=entry.entry_id
                )
                reserved_count += 1
        except Exception as e:
            for pos, idx in enumerate(gated_indices):
                if results[idx] is None:
                    await release_entry_keys(gated_entries[pos])
                    results[idx] = ReserveItemResponse(
                        index=idx, status="error",
                        error=f"Batch insert failed: {e!s}"
                    )
                    error_count += 1

    return ReserveBulkResponse(
        results=[r for r in results if r is not None],
        total=len(items),
        reserved=reserved_count,
        errors=error_count,
    )


@router.post(
    "/activate",
    response_model=ActivateBulkResponse,
    summary="Activate reserved entries"
)
async def activate_entries(
    items: list[ActivateItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
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

    for i in keyed:
        item = items[i]
        entry = await RegistryEntry.find_one(RegistryEntry.entry_id == item.entry_id)
        if entry is None:
            results[i] = ActivateItemResponse(
                index=i, status="not_found", entry_id=item.entry_id)
            error_count += 1
            continue
        if entry.status == "active":
            results[i] = ActivateItemResponse(
                index=i, status="already_active", entry_id=item.entry_id)
            continue
        if entry.status != "reserved":
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=item.entry_id,
                error=f"Cannot activate entry with status '{entry.status}'")
            error_count += 1
            continue
        # Set the final key, then run the two-phase claim: pending before the
        # write, confirm after — the same protocol provision uses, just here.
        entry.primary_composite_key = item.composite_key or {}
        entry.primary_composite_key_hash = (
            HashService.compute_composite_key_hash(item.composite_key)
            if item.composite_key else ""
        )
        entry.rebuild_search_values()
        conflict = await claim_entry_keys_pending(entry)
        if conflict is not None:
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=item.entry_id, error=conflict)
            error_count += 1
            continue
        entry.status = "active"
        entry.updated_at = datetime.now(UTC)
        try:
            await entry.save()
        except Exception as e:
            await release_entry_keys(entry)
            results[i] = ActivateItemResponse(
                index=i, status="error", entry_id=item.entry_id, error=str(e))
            error_count += 1
            continue
        await confirm_entry_keys(entry)
        results[i] = ActivateItemResponse(
            index=i, status="activated", entry_id=item.entry_id)
        activated_count += 1

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
                    index=i, status="error", entry_id=items[i].entry_id, error=str(e))
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
        if reserved_ids:
            try:
                update_result = await entries_coll.update_many(
                    {"entry_id": {"$in": reserved_ids}, "status": "reserved"},
                    {"$set": {
                        "status": "active",
                        "updated_at": datetime.now(UTC),
                    }},
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
                for i in keyless:
                    results[i] = ActivateItemResponse(
                        index=i, status="error", entry_id=items[i].entry_id,
                        error=str(e))
                    error_count += 1
                return ActivateBulkResponse(
                    results=[r for r in results if r is not None],
                    total=len(items), activated=activated_count, errors=error_count,
                )

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
                    error=f"Cannot activate entry with status '{known_status}'")
                error_count += 1
            elif item.entry_id in flipped_ids:
                results[i] = ActivateItemResponse(
                    index=i, status="activated", entry_id=item.entry_id)
                activated_count += 1
            else:
                results[i] = ActivateItemResponse(
                    index=i, status="error", entry_id=item.entry_id,
                    error="Entry status changed concurrently; still reserved after update")
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
    (archive download requires admin on every namespace it spans). Ids the
    caller lacks admin on come back per-item `forbidden`, never a
    whole-call 403 — bulk-first.
    """
    from .grants import _is_superadmin, _resolve_permission

    entries_by_id: dict[str, RegistryEntry] = {}
    found_rows = await RegistryEntry.find(
        {"entry_id": {"$in": request.entry_ids}}
    ).to_list()
    for row in found_rows:
        entries_by_id[row.entry_id] = row

    superadmin = _is_superadmin(identity)
    admin_by_namespace: dict[str, bool] = {}

    async def _admin_on(namespace: str) -> bool:
        if superadmin:
            return True
        if namespace not in admin_by_namespace:
            perm = await _resolve_permission(identity, namespace)
            admin_by_namespace[namespace] = perm == "admin"
        return admin_by_namespace[namespace]

    results: list[ExportEntryItem] = []
    found = not_found = forbidden = 0
    for i, entry_id in enumerate(request.entry_ids):
        entry = entries_by_id.get(entry_id)
        if entry is None:
            results.append(ExportEntryItem(
                index=i, entry_id=entry_id, status="not_found"
            ))
            not_found += 1
            continue
        if not await _admin_on(entry.namespace):
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
    """Look up registry entries by their IDs. Only active entries are resolvable."""
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
                matched_via = None

                # 1. Find by entry_id
                q1: dict = {"entry_id": item.entry_id, "status": "active"}
                if item.namespace:
                    q1["namespace"] = item.namespace
                if item.entity_type:
                    q1["entity_type"] = item.entity_type
                entry = await RegistryEntry.find_one(q1)
                if entry:
                    matched_via = "entry_id"

                # 2. Composite key value search (covers merged IDs via search_values)
                if not entry:
                    q3: dict = {"search_values": item.entry_id, "status": "active"}
                    if item.namespace:
                        q3["namespace"] = item.namespace
                    if item.entity_type:
                        q3["entity_type"] = item.entity_type
                    entry = await RegistryEntry.find_one(q3)
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
                results.append(LookupResponse(index=i, status="error", error=str(e)))
                error_count += 1

    return LookupBulkResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count, errors=error_count,
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
    """Look up registry entries by their composite keys."""
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, item in enumerate(items):
            try:
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
                                        "composite_key_hash": key_hash
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

                if not entry:
                    results.append(LookupResponse(index=i, status="not_found"))
                    not_found_count += 1
                    continue

                matched_namespace = item.namespace
                matched_entity_type = item.entity_type
                matched_composite_key = item.composite_key
                if entry.primary_composite_key_hash != key_hash:
                    for syn in entry.synonyms:
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
                results.append(LookupResponse(index=i, status="error", error=str(e)))
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
    identity: UserIdentity = Depends(require_api_key)
) -> BulkUpdateResponse:
    """Update one or more registry entries."""
    results = []

    for i, item in enumerate(items):
        try:
            entry = await RegistryEntry.find_one({
                "entry_id": item.entry_id,
                "status": "active"
            })

            if not entry:
                results.append(UpdateEntryResponse(
                    index=i, status="not_found", registry_id=item.entry_id,
                ))
                continue

            if item.source_info is not None:
                entry.source_info = item.source_info
            if item.metadata is not None:
                entry.metadata.update(item.metadata)

            entry.updated_at = datetime.now(UTC)
            entry.updated_by = item.updated_by
            await entry.save()

            results.append(UpdateEntryResponse(
                index=i, status="updated", registry_id=item.entry_id,
            ))

        except Exception as e:
            results.append(UpdateEntryResponse(
                index=i, status="error", registry_id=item.entry_id, error=str(e)
            ))

    return BulkUpdateResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status == "updated"),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
    )


@router.delete(
    "",
    response_model=BulkDeleteResponse,
    summary="Delete entries (bulk, soft or hard delete)"
)
async def delete_entries(
    items: list[DeleteItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
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

            if item.hard_delete or item.rollback_uncommitted:
                # CASE-436: rollback_uncommitted bypasses the deletion_mode gate
                # for privileged (service/admin) callers — it aborts a
                # just-allocated entry whose backing object was never committed
                # (a document create that failed on synonym registration), which
                # is a trusted write-rollback, not user-data deletion. Ordinary
                # keys never get the bypass; their flag falls through to the gate.
                privileged_rollback = (
                    item.rollback_uncommitted
                    and identity.has_any_group(["wip-admins", "wip-services"])
                )
                if privileged_rollback:
                    logger.info(
                        "CASE-436: rollback_uncommitted hard-delete of entry %s "
                        "(ns=%s) by %s — bypassing deletion_mode gate.",
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
                # Soft-delete: set status to inactive
                entry.status = "inactive"
                entry.updated_at = datetime.now(UTC)
                entry.updated_by = item.updated_by
                await entry.save()

                results.append(DeleteResponse(
                    index=i, status="deactivated", registry_id=item.entry_id,
                ))

        except Exception as e:
            results.append(DeleteResponse(
                index=i, status="error", registry_id=item.entry_id, error=str(e)
            ))

    return BulkDeleteResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status in ("deactivated", "deleted")),
        failed=sum(1 for r in results if r.status in ("not_found", "error")),
    )


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

    Returns entry_id for each found item, "not_found" otherwise.
    """
    results = []
    found_count = 0
    not_found_count = 0
    error_count = 0

    for i, item in enumerate(items):
        try:
            if not item.entry_id and not item.composite_key:
                results.append(ResolveResponse(
                    index=i, status="error",
                    error="Provide either entry_id or composite_key",
                ))
                error_count += 1
                continue

            status_filter = (
                {"$in": item.include_statuses}
                if item.include_statuses
                else "active"
            )
            entry = None

            # Path 1: Canonical ID verification
            if item.entry_id:
                entry = await RegistryEntry.find_one({
                    "entry_id": item.entry_id,
                    "status": status_filter,
                })

            # Path 2: Composite key resolution (synonym or primary key)
            if not entry and item.composite_key:
                key_hash = HashService.compute_composite_key_hash(item.composite_key)
                query = {
                    "$or": [
                        {"primary_composite_key_hash": key_hash},
                        {"synonyms.composite_key_hash": key_hash},
                    ],
                    "status": status_filter,
                }
                if item.namespace:
                    query["namespace"] = item.namespace
                if item.entity_type:
                    query["entity_type"] = item.entity_type
                # CASE-427 defensive guard (see lookup_by_keys): detect+log a
                # key resolving to >1 entry, deterministic [0] pick otherwise.
                matches = await RegistryEntry.find(query).limit(2).to_list()
                if len(matches) > 1:
                    logger.error(
                        "CASE-427 INVARIANT VIOLATION: composite key %s resolved "
                        "to multiple entries %s — uniqueness gate breached.",
                        key_hash, [e.entry_id for e in matches],
                    )
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
                index=i, status="error", error=str(e),
            ))
            error_count += 1

    return BulkResolveResponse(
        results=results, total=len(items),
        found=found_count, not_found=not_found_count, errors=error_count,
    )
