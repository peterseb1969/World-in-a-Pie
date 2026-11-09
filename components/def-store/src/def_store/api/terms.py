"""API endpoints for term management."""

import math
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    UserIdentity,
    can_read,
    check_entity_permission,
    check_namespace_permission,
    check_readable,
    entity_write_refusals,
    field_unresolved_detail,
    miss_detail,
    resolution_miss,
    resolve_accessible_namespaces,
    resolve_namespace_filter,
    resolve_or_404,
    resolve_term_by_fields_or_404,
    split_qualified_value,
    terminology_coordinates_by_id,
)

from ..models.api_models import (
    BulkResponse,
    BulkResultItem,
    BulkValidateRequest,
    BulkValidateResponse,
    CreateTermRequest,
    DeleteItem,
    DeprecateTermItem,
    MergeTermsItem,
    TermListResponse,
    TermMatchCandidate,
    TermResponse,
    UpdateTermItem,
    ValidateValueRequest,
    ValidateValueResponse,
)
from ..models.term import Term
from ..models.terminology import Terminology
from ..services.errors import error_fields
from ..services.registry_client import RegistryError
from ..services.term_names import TermNameError
from ..services.term_reconcile import (
    ReconcileRequest,
    ReconcileResponse,
    TermReconcileService,
)
from ..services.term_search import TermSearchResponse, TermSearchService
from ..services.terminology_service import (
    AmbiguousTerminologyValueError,
    EntityExistsError,
    NameCollisionError,
    RegistryEntryNotRemovedError,
    TerminologyService,
    ValueResolution,
    conflict_result,
)
from .auth import require_api_key

router = APIRouter(tags=["Terms"])


# =============================================================================
# TERM CRUD
# =============================================================================

@router.post(
    "/terminologies/{terminology_id}/terms",
    response_model=BulkResponse,
    summary="Create terms"
)
async def create_terms(
    terminology_id: str,
    items: list[CreateTermRequest] = Body(...),
    namespace: str | None = Query(
        default=None,
        description="Namespace for synonym resolution (inferred from terminology if omitted)"
    ),
    batch_size: int = Query(
        1000, ge=1, le=10_000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100, ge=1, le=1_000,
        description="Number of terms per registry HTTP call (default 100). "
        "Reduce if experiencing timeouts on large imports."
    ),
    on_conflict: str = Query(
        "error",
        description=(
            "Duplicate handling on the single-item path (idempotent "
            "bootstrap): 'error' (default) fails the item with "
            "error_code='already_exists'; 'validate' returns "
            "status='unchanged' for an identical re-create and "
            "error_code='incompatible_config' when the existing term "
            "differs. The bulk path (2+ items) skips duplicates "
            "regardless (status='skipped')."
        ),
    ),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """
    Create one or more terms in a terminology.

    Namespace is inherited from the parent terminology.
    For single items, uses direct creation. For multiple items, uses
    optimized batch operations.

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)
    """
    if on_conflict not in ("error", "validate"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid on_conflict value: {on_conflict!r}. Must be 'error' or 'validate'.",
        )

    # Resolve terminology_id synonym (e.g., "STATUS" → UUID)
    raw_id = terminology_id
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )

    # Look up terminology to get namespace for permission check
    term_parent = await Terminology.find_one({"terminology_id": terminology_id})
    if term_parent:
        await check_entity_permission(identity, term_parent.namespace, "write", miss_detail(
            raw_id, "terminology", namespace, f"Terminology '{terminology_id}' not found",
            "terminology_id",
        ))
        # A parent is a term of this terminology: resolve it there (a value
        # is that terminology's value, never colon-parsed; a UUID passes).
        # Write door — the id is persisted, so the cache is bypassed. What
        # does not resolve is refused per item by the service.
        from wip_auth import resolve_bulk_ids
        await resolve_bulk_ids(
            items, "parent_term_id", "term", namespace=term_parent.namespace,
            terminology=terminology_id, bypass_cache=True,
        )

    if len(items) == 1:
        # Single item — use direct create path
        try:
            collision_warnings: list[dict] = []
            result = await TerminologyService.create_term(
                terminology_id, items[0], collision_out=collision_warnings
            )
            results = [BulkResultItem(
                index=0, status="created", id=result.term_id,
                value=items[0].value,
                details={"name_collisions": collision_warnings}
                if collision_warnings else None,
            )]
        except EntityExistsError as e:
            results = [conflict_result(0, e, on_conflict, value=items[0].value)]
        except NameCollisionError as e:
            results = [BulkResultItem(
                index=0, status="error", value=items[0].value,
                error=str(e), error_code="name_collision",
                details={"name_collisions": e.collisions},
            )]
        except TermNameError as e:
            # Same machine-readable code the bulk path emits for a names
            # rule violation — callers branch on the code, not the message
            results = [BulkResultItem(
                index=0, status="error", value=items[0].value,
                error=str(e), error_code="invalid_names",
            )]
        except (ValueError, RegistryError) as e:
            fields = error_fields(e)
            if fields["error_code"] == "not_found":
                raise HTTPException(status_code=404, detail=fields["error"]) from None
            results = [BulkResultItem(index=0, status="error", value=items[0].value, **fields)]
    else:
        # Bulk path — uses batch Registry calls and insert_many
        try:
            results = await TerminologyService.create_terms_bulk(
                terminology_id=terminology_id,
                terms=items,
                batch_size=batch_size,
                registry_batch_size=registry_batch_size,
            )
        except ValueError as e:
            fields = error_fields(e)
            if fields["error_code"] == "invalid_state":
                # The terminology's state refuses every item alike — answered
                # per item, as the single-item path does
                results = [
                    BulkResultItem(index=i, status="error", value=item.value, **fields)
                    for i, item in enumerate(items)
                ]
            else:
                status_code = 404 if fields["error_code"] == "not_found" else 400
                raise HTTPException(status_code=status_code, detail=fields["error"]) from None

    succeeded = sum(1 for r in results if r.status != "error")
    failed = sum(1 for r in results if r.status == "error")
    return BulkResponse(results=results, total=len(results), succeeded=succeeded, failed=failed)


@router.post(
    "/terminologies/{terminology_id}/reconcile",
    response_model=ReconcileResponse,
    summary="Reconcile a terminology against a full desired term set",
)
async def reconcile_terminology(
    terminology_id: str,
    request: ReconcileRequest,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key),
) -> ReconcileResponse:
    """Make the terminology match the submitted snapshot — with a preview.

    Terms are keyed by exact value. The snapshot is authoritative for
    label and names; description, sort_order and parent_term_id apply only
    when the snapshot sets them — an omitted field is kept, an explicit null
    clears it (re-runs stay idempotent either way); metadata is honored on
    create and never rewritten. Active terms absent
    from the snapshot are deprecated — never deleted — and only under
    on_missing='deprecate', optionally with successors. dry_run (the
    default) returns the full plan and writes nothing; unchanged terms are
    counted, not listed. Writes route through the normal term doors, so
    Registry registration, names validation, the collision guard, events
    and audit behave exactly like individual writes.
    """
    raw_id = terminology_id
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )
    terminology = await Terminology.find_one({"terminology_id": terminology_id})
    if not terminology:
        raise HTTPException(status_code=404, detail="Terminology not found")

    await check_entity_permission(identity, terminology.namespace, "write", miss_detail(
        raw_id, "terminology", namespace, "Terminology not found", "terminology_id",
    ))

    return await TermReconcileService.reconcile(terminology, request)


@router.get(
    "/terminologies/{terminology_id}/terms",
    response_model=TermListResponse,
    summary="List terms in a terminology"
)
async def list_terms(
    terminology_id: str,
    namespace: str | None = Query(default=None, description="Namespace to query (omit for all)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    status: str | None = Query(None, description="Filter by status"),
    search: str | None = Query(None, description="Search in value, label, names"),
    sort_by: Literal["value", "label", "sort_order", "created_at", "updated_at"] | None = Query(None, description="Sort field: value, label, sort_order, created_at, updated_at"),
    sort_order: Literal["asc", "desc"] | None = Query(None, description="Sort direction: asc or desc"),
    identity: UserIdentity = Depends(require_api_key)
) -> TermListResponse:
    """List terms in a terminology with pagination."""
    ns_filter = await resolve_namespace_filter(identity, namespace)

    # Resolve terminology_id synonym (e.g., "STATUS" → UUID)
    raw_id = terminology_id
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )

    # Get terminology info
    terminology = await Terminology.find_one({"terminology_id": terminology_id})
    if not terminology:
        # Try by value — unscoped lookups fail loud on an ambiguous value
        try:
            terminology = await TerminologyService.find_by_value(
                terminology_id, namespace, ns_filter.namespaces
            )
        except AmbiguousTerminologyValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not terminology:
            raise HTTPException(status_code=404, detail="Terminology not found")
    # One in a namespace the caller cannot see answers as an unknown id.
    await check_readable(identity, terminology.namespace, miss_detail(
        raw_id, "terminology", namespace, "Terminology not found", "terminology_id",
    ))

    terms, total = await TerminologyService.list_terms(
        terminology_id=terminology.terminology_id,
        status=status,
        page=page,
        page_size=page_size,
        search=search,
        ns_filter=ns_filter.query,
        sort_by=sort_by,
        sort_order=sort_order,
        namespace=terminology.namespace,
    )

    return TermListResponse(
        items=terms,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
        terminology_id=terminology.terminology_id,
        terminology_value=terminology.value
    )


async def _terminology_namespace(identity: UserIdentity, terminology: str) -> str | None:
    """The namespace a terminology named by id or as NS:VALUE lives in.

    One in a namespace the caller cannot see names none — as an id that
    names nothing — so the answer does not confirm that it exists.
    """
    if ":" in terminology:
        return split_qualified_value(terminology)[0]
    coordinates = await terminology_coordinates_by_id(terminology)
    if coordinates is not None and await can_read(identity, coordinates[0]):
        return coordinates[0]
    return None


@router.get(
    "/terms/search",
    response_model=TermSearchResponse,
    summary="Ranked term search (typeahead)",
)
async def search_terms(
    q: str = Query(..., min_length=1, description="The search string (treated as literal text)"),
    terminology: str | None = Query(
        None,
        description="Terminology ID or value to scope the search; omit for namespace-wide",
    ),
    namespace: str | None = Query(
        None,
        description="Namespace to search. Not needed when `terminology` is its id or "
                    "'ns:VALUE' (the terminology's namespace is used) or for "
                    "single-namespace keys; required otherwise",
    ),
    mode: Literal["exact", "prefix", "word-start", "substring"] = Query(
        "prefix",
        description="Match mode. A narrower match still outranks a broader one inside a broad mode.",
    ),
    limit: int = Query(20, ge=1, le=100, description="Maximum hits"),
    statuses: str = Query("active", description="Comma-separated statuses to include"),
    rollup: Literal["none", "root"] = Query(
        "none",
        description="root: replace each hit with its root is_a ancestor (deduped, best rank wins)",
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> TermSearchResponse:
    """Search terms by value, label or any name, ranked for typeahead.

    Rank: match specificity (exact > prefix > word-start > substring),
    then matched field (value > label > preferred name > name), then the
    matched string alphabetically. Each hit carries the exact string that
    matched, where it matched, and — for name matches — the name with its
    type and language. Matching is always case-insensitive: this is the
    discovery surface; strict, case-respecting resolution stays with
    /validate. Exact mode returns every candidate up to `limit` — two terms
    sharing a name both appear, making this the ambiguity-inspection
    surface; `total_matched` says how many there are in all.
    """
    # A terminology named by id or as NS:VALUE names its own namespace, and
    # the terms it scopes live there, so it needs no ?namespace=. Read
    # access to that namespace is still checked below.
    if namespace is None and terminology is not None:
        namespace = await _terminology_namespace(identity, terminology)

    # Search needs one concrete namespace: ranking spans one vocabulary
    # scope and rollup walks that namespace's relations.
    ns_filter = await resolve_namespace_filter(identity, namespace)
    if ns_filter.namespaces is not None and len(ns_filter.namespaces) == 1:
        search_namespace = ns_filter.namespaces[0]
    else:
        raise HTTPException(
            status_code=400,
            detail="Search requires a namespace: pass ?namespace= (keys "
                   "scoped to a single namespace derive it automatically)",
        )

    terminology_id: str | None = None
    if terminology is not None:
        terminology_id = await resolve_or_404(
            terminology, "terminology", search_namespace, param_name="terminology"
        )
        parent = await Terminology.find_one({"terminology_id": terminology_id})
        if not parent:
            # Value-form fallback within the resolved namespace
            try:
                parent = await TerminologyService.find_by_value(terminology, search_namespace)
            except AmbiguousTerminologyValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            if not parent:
                raise HTTPException(status_code=404, detail="Terminology not found")
            terminology_id = parent.terminology_id

    status_list = [s.strip() for s in statuses.split(",") if s.strip()]

    return await TermSearchService.search(
        query=q,
        namespace=search_namespace,
        terminology_id=terminology_id,
        mode=mode,
        limit=limit,
        statuses=status_list,
        rollup=rollup,
    )


@router.get(
    "/terms/{term_id}",
    response_model=TermResponse,
    summary="Get a term by ID"
)
async def get_term(
    term_id: str,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    terminology: str | None = Query(
        None,
        description="Terminology scoping a value-form term identifier: its id, "
                    "'ns:VALUE', or its value (read in `namespace`). When "
                    "present, the identifier is treated as the OPAQUE raw value "
                    "(never colon-parsed) — required for values that themselves "
                    "contain ':' (OBO ids like GO:0000278).",
    ),
    identity: UserIdentity = Depends(require_api_key)
) -> TermResponse:
    """Get a term by canonical UUID, fully qualified 'ns:terminology:value',
    or — with terminology= — the opaque raw value (never colon-parsed)."""
    raw_id = term_id
    if terminology is not None:
        # Field-form door: the identifier is the raw value, uninterpreted
        term_id = await resolve_term_by_fields_or_404(
            term_id, terminology, namespace, param_name="term_id"
        )
        not_found = field_unresolved_detail(raw_id, terminology, namespace, "term_id")
    else:
        # Resolve synonym — supports colon notation for terms
        term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")
        not_found = miss_detail(raw_id, "term", namespace, "Term not found", "term_id")

    result = await TerminologyService.get_term(term_id=term_id)
    if not result:
        raise HTTPException(status_code=404, detail="Term not found")

    # Read on the term's namespace; one the caller cannot see answers as an
    # id that names nothing.
    await check_readable(identity, result.namespace, not_found)
    return result


@router.put(
    "/terms",
    response_model=BulkResponse,
    summary="Update terms"
)
async def update_terms(
    items: list[UpdateTermItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    terminology: str | None = Query(
        None,
        description="Terminology scoping value-form term identifiers. When "
                    "present, every non-UUID term_id in the batch is treated "
                    "as the OPAQUE raw value (never colon-parsed) — required "
                    "for values that themselves contain ':' (OBO ids like "
                    "GO:0000278). The ambiguous 2-part 'TERMINOLOGY:VALUE' "
                    "shorthand is rejected on this endpoint.",
    ),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """Update one or more terms."""
    from wip_auth import resolve_bulk_ids
    # Write door: resolution must ask Registry, not the read cache — the
    # resolved id selects which term the mutation lands on.
    await resolve_bulk_ids(
        items, "term_id", "term", namespace=namespace,
        terminology=terminology, bypass_cache=True,
    )
    # parent_term_id is persisted too; what does not resolve is refused per
    # item by the service
    await resolve_bulk_ids(
        items, "parent_term_id", "term", namespace=namespace,
        terminology=terminology, bypass_cache=True,
    )

    # CASE-384 — batched namespace lookup + permission check.
    from ..models.term import Term as _Term
    ids = [item.term_id for item in items if item.term_id]
    refused: dict[str, tuple[str, str]] = {}
    if ids:
        existing_docs = await _Term.find({"term_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.term_id: d.namespace for d in existing_docs}
        # An item in a namespace the caller cannot see answers as an id
        # that names nothing; one it may only read is forbidden. Both
        # per item, the rest of the batch proceeds.
        refused = await entity_write_refusals(identity, id_to_namespace, "Term not found")

    results = []
    for i, item in enumerate(items):
        if (refusal := refused.get(item.term_id)) is not None:
            results.append(BulkResultItem(
                index=i, status="error", id=item.term_id,
                error=refusal[0], error_code=refusal[1],
            ))
            continue
        try:
            collision_warnings: list[dict] = []
            result = await TerminologyService.update_term(
                item.term_id, item, collision_out=collision_warnings
            )
            if not result:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.term_id,
                    error="Term not found", error_code="not_found",
                ))
            else:
                results.append(BulkResultItem(
                    index=i, status="updated", id=item.term_id,
                    details={"name_collisions": collision_warnings}
                    if collision_warnings else None,
                ))
        except NameCollisionError as e:
            results.append(BulkResultItem(
                index=i, status="error", id=item.term_id,
                error=str(e), error_code="name_collision",
                details={"name_collisions": e.collisions},
            ))
        except TermNameError as e:
            results.append(BulkResultItem(
                index=i, status="error", id=item.term_id,
                error=str(e), error_code="invalid_names",
            ))
        except (ValueError, RegistryError) as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, **error_fields(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/terms/deprecate",
    response_model=BulkResponse,
    summary="Deprecate terms"
)
async def deprecate_terms(
    items: list[DeprecateTermItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    terminology: str | None = Query(
        None,
        description="Terminology scoping value-form term identifiers — "
                    "applies to term_id AND replaced_by_term_id (a "
                    "deprecation's replacement must be an active term of "
                    "the same terminology). When "
                    "present, non-UUID identifiers are treated as OPAQUE "
                    "raw values (never colon-parsed). The ambiguous 2-part "
                    "'TERMINOLOGY:VALUE' shorthand is rejected.",
    ),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """
    Deprecate one or more terms.

    Deprecated terms are kept for historical data but marked as deprecated.
    Optionally specify a replacement term per item.
    """
    from wip_auth import resolve_bulk_ids
    # Write door: bypass the read cache — replaced_by_term_id is persisted
    # as a durable pointer, and a stale cached id would pin a dead
    # reference into the term record.
    await resolve_bulk_ids(
        items, "term_id", "term", namespace=namespace,
        terminology=terminology, bypass_cache=True,
    )
    await resolve_bulk_ids(
        items, "replaced_by_term_id", "term", namespace=namespace,
        terminology=terminology, bypass_cache=True,
    )

    # CASE-384 — batched namespace lookup + permission check.
    from ..models.term import Term as _Term
    ids = [item.term_id for item in items if item.term_id]
    refused: dict[str, tuple[str, str]] = {}
    if ids:
        existing_docs = await _Term.find({"term_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.term_id: d.namespace for d in existing_docs}
        # An item in a namespace the caller cannot see answers as an id
        # that names nothing; one it may only read is forbidden. Both
        # per item, the rest of the batch proceeds.
        refused = await entity_write_refusals(identity, id_to_namespace, "Term not found")

    results = []
    for i, item in enumerate(items):
        if (refusal := refused.get(item.term_id)) is not None:
            results.append(BulkResultItem(
                index=i, status="error", id=item.term_id,
                error=refusal[0], error_code=refusal[1],
            ))
            continue
        try:
            result = await TerminologyService.deprecate_term(item.term_id, item)
            if not result:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.term_id,
                    error="Term not found", error_code="not_found",
                ))
            else:
                results.append(BulkResultItem(index=i, status="updated", id=item.term_id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, **error_fields(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.delete(
    "/terms",
    response_model=BulkResponse,
    summary="Delete terms"
)
async def delete_terms(
    items: list[DeleteItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    terminology: str | None = Query(
        None,
        description="Terminology scoping value-form term identifiers. When "
                    "present, every non-UUID id in the batch is treated as "
                    "the OPAQUE raw value (never colon-parsed). The "
                    "ambiguous 2-part 'TERMINOLOGY:VALUE' shorthand is "
                    "rejected on this endpoint.",
    ),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """Delete one or more terms.

    A term of a mutable terminology is removed permanently, with its
    relations and Registry entry, whatever hard_delete and the namespace's
    deletion_mode say. Otherwise the term is soft-deleted (status
    'inactive'), or removed permanently on hard_delete=true in a namespace
    with deletion_mode 'full'. No dependency check runs and force has no
    effect: documents keep the term ids they hold.
    """
    from wip_auth import resolve_bulk_ids
    # Write door: resolution must ask Registry, not the read cache — the
    # resolved id selects which term gets deleted.
    await resolve_bulk_ids(
        items, "id", "term", namespace=namespace,
        terminology=terminology, bypass_cache=True,
    )

    # CASE-384 — batched namespace lookup + permission check.
    from ..models.term import Term as _Term
    ids = [item.id for item in items if item.id]
    refused: dict[str, tuple[str, str]] = {}
    if ids:
        existing_docs = await _Term.find({"term_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.term_id: d.namespace for d in existing_docs}
        # An item in a namespace the caller cannot see answers as an id
        # that names nothing; one it may only read is forbidden. Both
        # per item, the rest of the batch proceeds.
        refused = await entity_write_refusals(identity, id_to_namespace, "Term not found")

    results = []
    for i, item in enumerate(items):
        if (refusal := refused.get(item.id)) is not None:
            results.append(BulkResultItem(
                index=i, status="error", id=item.id,
                error=refusal[0], error_code=refusal[1],
            ))
            continue
        try:
            success = await TerminologyService.delete_term(
                term_id=item.id, updated_by=item.updated_by,
                hard_delete=item.hard_delete,
            )
            if not success:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.id,
                    error="Term not found", error_code="not_found",
                ))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except RegistryEntryNotRemovedError as e:
            # The term IS deleted; its Registry entry is not — say so.
            results.append(BulkResultItem(
                index=i, status="error", id=item.id, error=str(e),
                error_code="registry_entry_not_removed",
            ))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, **error_fields(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/terms/merge",
    response_model=BulkResponse,
    summary="Merge terms (absorb one term into another)",
)
async def merge_terms(
    items: list[MergeTermsItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    terminology: str | None = Query(
        None,
        description="Terminology scoping value-form term identifiers (both "
                    "survivor_term_id and absorbed_term_id). Non-UUID "
                    "identifiers are then OPAQUE raw values, never colon-parsed.",
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> BulkResponse:
    """Absorb each `absorbed_term_id` into its `survivor_term_id`.

    From then on the absorbed term's value and names resolve to the
    survivor — in validation, in document writes and through the Registry.
    The absorbed term is deprecated (replaced_by = survivor), its value and
    names become names of the survivor, and its Registry entry is merged
    into the survivor's. Existing documents keep the absorbed term's id. A
    merge that cannot complete changes nothing.
    """
    from wip_auth import resolve_bulk_ids
    # Write door: both ids are persisted (replaced_by_term_id, merged entry).
    for field in ("survivor_term_id", "absorbed_term_id"):
        await resolve_bulk_ids(
            items, field, "term", namespace=namespace,
            terminology=terminology, bypass_cache=True,
        )

    ids = [i for item in items for i in (item.survivor_term_id, item.absorbed_term_id) if i]
    refused: dict[str, tuple[str, str]] = {}
    if ids:
        id_to_namespace = {
            d.term_id: d.namespace for d in await Term.find({"term_id": {"$in": ids}}).to_list()
        }
        refused = await entity_write_refusals(identity, id_to_namespace, "")

    results = []
    for i, item in enumerate(items):
        if (refusal := _merge_refusal(item, refused)) is not None:
            results.append(BulkResultItem(
                index=i, status="error", id=item.survivor_term_id,
                error=refusal[0], error_code=refusal[1],
            ))
            continue
        collisions: list[dict[str, Any]] = []
        try:
            _survivor, added = await TerminologyService.merge_terms(
                item.survivor_term_id, item.absorbed_term_id, item.reason, collision_out=collisions,
            )
        except NameCollisionError as e:
            results.append(BulkResultItem(
                index=i, status="error", id=item.survivor_term_id, error=str(e),
                error_code="name_collision", details={"name_collisions": e.collisions},
            ))
            continue
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.survivor_term_id, **error_fields(e)))
            continue
        details: dict[str, Any] = {
            "absorbed_term_id": item.absorbed_term_id,
            "names_added": [n.value for n in added],
        }
        if collisions:
            details["name_collisions"] = collisions
        results.append(BulkResultItem(index=i, status="updated", id=item.survivor_term_id, details=details))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


def _merge_refusal(item: Any, refused: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Why a merge item may not be written: a term in a namespace the caller
    cannot see answers as the merge does for a term that does not exist
    (survivor first), one it may only read is forbidden."""
    for term_id in (item.survivor_term_id, item.absorbed_term_id):
        refusal = refused.get(term_id)
        if refusal is not None:
            error, code = refusal
            return (f"Term not found: {term_id}", code) if code == "not_found" else (error, code)
    return None


@router.post(
    "/maintenance/registry-ghosts",
    summary="Find (and remove) Registry entries left behind by hard deletes",
)
async def registry_ghosts(
    namespace: str = Query(..., description="Namespace to check"),
    dry_run: bool = Query(True, description="Report only (default). false removes the removable ghosts."),
    identity: UserIdentity = Depends(require_api_key),
) -> dict[str, Any]:
    """Registry entries whose terminology or term no longer exists in
    def-store. Their synonyms still resolve to the deleted ids, and
    re-creating a value would reuse its dead id. Entries from mutable
    terminologies (and of deleted terminologies) are removable; a term
    missing from an immutable terminology is only reported. Requires
    namespace admin."""
    await check_namespace_permission(identity, namespace, "admin")
    return await TerminologyService.registry_ghosts(namespace, remove=not dry_run)


def _resolution_to_response(
    res: ValueResolution, terminology, value: str
) -> ValidateValueResponse:
    """Shape a service-level resolution for the API, candidates included.

    matches is the number of terms bearing the matched value or name;
    candidates are listed only when more than one term bears it — a single
    hit keeps the unambiguous response shape.
    """
    to_resp = TerminologyService._to_term_response
    matches = 0
    if res.valid:
        matches = len(res.candidates) if res.candidates else 1
    return ValidateValueResponse(
        valid=res.valid,
        terminology_id=terminology.terminology_id,
        terminology_value=terminology.value,
        value=value,
        matched_term=to_resp(res.matched_term) if res.matched_term else None,
        matched_via=res.matched_via,
        matched_name=res.matched_name,
        matches=matches,
        candidates=[
            TermMatchCandidate(term=to_resp(t), matched_name=n)
            for t, n in res.candidates
        ] if matches > 1 else [],
        suggestion=to_resp(res.suggestion) if res.suggestion else None,
        error_code=None if res.valid else "term_not_found",
    )


# =============================================================================
# VALIDATION
# =============================================================================

async def _visible_terminology(
    identity: UserIdentity, terminology: Any, raw_id: str | None,
) -> tuple[Any, HTTPException | None]:
    """(terminology, None) when the caller may read it; (None, miss) when
    it is absent or in a namespace the caller cannot see.

    A hidden one answers exactly as an id that names nothing: ``miss`` is
    the resolution 404 an unknown id gets in this request (ids resolve in
    the caller's single namespace), or None when the not-found answer is
    the door's own (ids pass through, or the terminology was named by value
    and searched only where the caller can read).
    """
    if terminology is not None and await can_read(identity, terminology.namespace):
        return terminology, None
    if terminology is None or not raw_id:
        return None, None
    return None, resolution_miss(raw_id, "terminology", None, "terminology_id")


async def _terminology_by_id(item: Any, terminologies: dict[str, Any]) -> Any:
    """Resolve the item's terminology_id in place (404 when it names nothing)
    and return its terminology, cached in ``terminologies``."""
    item.terminology_id = await resolve_or_404(
        item.terminology_id, "terminology", namespace=None, param_name="terminology_id"
    )
    if item.terminology_id not in terminologies:
        terminologies[item.terminology_id] = await Terminology.find_one(
            {"terminology_id": item.terminology_id}
        )
    return terminologies[item.terminology_id]


async def _bulk_item_terminology(
    item: Any, identity: UserIdentity, terminologies: dict[str, Any], readable: list[list[str] | None],
) -> tuple[Any, tuple[str, str] | None]:
    """A bulk-validate item's terminology, or (None, (error, error_code)).

    Every refusal is the item's, not the request's (bulk-first): an id that
    names nothing, an ambiguous value, a terminology the caller cannot see
    (answered as an unknown id), or neither id nor value. ``terminologies``
    caches lookups by id; ``readable`` holds the caller's readable
    namespaces once a value-form item asked for them.
    """
    raw_id = item.terminology_id or ""
    if raw_id:
        try:
            terminology = await _terminology_by_id(item, terminologies)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            return None, (str(e.detail), "not_found")
    elif item.terminology_value:
        try:
            if not readable:
                readable.append(await resolve_accessible_namespaces(identity))
            terminology = await TerminologyService.find_by_value(item.terminology_value, None, readable[0])
        except AmbiguousTerminologyValueError as e:
            return None, (str(e), "ambiguous_terminology")
    else:
        return None, ("Must provide terminology_id or terminology_value", "invalid_request")
    terminology, miss = await _visible_terminology(identity, terminology, raw_id)
    if terminology is None:
        return None, (str(miss.detail) if miss is not None else "Terminology not found", "not_found")
    return terminology, None


@router.post(
    "/validate",
    response_model=ValidateValueResponse,
    summary="Validate a value"
)
async def validate_value(
    request: ValidateValueRequest,
    identity: UserIdentity = Depends(require_api_key)
) -> ValidateValueResponse:
    """
    Validate a value against a terminology.

    Returns whether the value is valid and the matching term if found.
    Also provides suggestions for close matches.
    """
    if not request.terminology_id and not request.terminology_value:
        raise HTTPException(
            status_code=400,
            detail="Must provide terminology_id or terminology_value"
        )

    # Resolve terminology_id synonym if provided
    raw_id = request.terminology_id
    if request.terminology_id:
        request.terminology_id = await resolve_or_404(
            request.terminology_id, "terminology", namespace=None, param_name="terminology_id"
        )

    # Get terminology for response — an ambiguous unscoped value is a
    # validation failure, reported in the response shape like not-found
    if request.terminology_id:
        terminology = await Terminology.find_one({"terminology_id": request.terminology_id})
    else:
        try:
            # Only the namespaces the caller can read are searched: a
            # terminology elsewhere reads as not found, and an ambiguity
            # error names none of the others.
            terminology = await TerminologyService.find_by_value(
                request.terminology_value or "", None,
                await resolve_accessible_namespaces(identity),
            )
        except AmbiguousTerminologyValueError as e:
            return ValidateValueResponse(
                valid=False,
                terminology_id="",
                terminology_value=request.terminology_value or "",
                value=request.value,
                error=str(e),
                error_code="ambiguous_terminology",
            )

    terminology, miss = await _visible_terminology(identity, terminology, raw_id)
    if miss is not None:
        raise miss
    if not terminology:
        return ValidateValueResponse(
            valid=False,
            terminology_id=request.terminology_id or "",
            terminology_value=request.terminology_value or "",
            value=request.value,
            error="Terminology not found",
            error_code="not_found",
        )


    res = await TerminologyService.validate_value(
        terminology_id=terminology.terminology_id,
        value=request.value
    )

    return _resolution_to_response(res, terminology, request.value)


@router.post(
    "/validate/bulk",
    response_model=BulkValidateResponse,
    summary="Validate multiple values"
)
async def validate_values_bulk(
    request: BulkValidateRequest,
    identity: UserIdentity = Depends(require_api_key)
) -> BulkValidateResponse:
    """Validate multiple values at once.

    Values are grouped by terminology and each group resolves with one
    query (TerminologyService.resolve_values); results keep the order of
    the request.
    """
    # Read permission is enforced per terminology, against its namespace.
    results: list[ValidateValueResponse | None] = []
    groups: dict[str, tuple[Any, list[tuple[int, str]]]] = {}

    terminologies: dict[str, Any] = {}
    # A value is searched only in the namespaces the caller can read (see
    # validate_value); asked once, on the first value-form item.
    readable: list[list[str] | None] = []

    def _invalid(item, error: str, code: str, terminology_id: str = "", terminology_value: str = ""):
        results.append(ValidateValueResponse(
            valid=False, terminology_id=terminology_id, terminology_value=terminology_value,
            value=item.value, error=error, error_code=code,
        ))

    for index, item in enumerate(request.items):
        terminology, refusal = await _bulk_item_terminology(item, identity, terminologies, readable)
        if refusal is not None:
            _invalid(item, *refusal, item.terminology_id or "", item.terminology_value or "")
            continue

        if terminology.terminology_id not in groups:
            groups[terminology.terminology_id] = (terminology, [])
        groups[terminology.terminology_id][1].append((index, item.value))
        results.append(None)  # filled in below, once the group resolves

    for terminology, members in groups.values():
        resolutions = await TerminologyService.resolve_values(terminology, [v for _, v in members])
        for (index, value), res in zip(members, resolutions, strict=True):
            results[index] = _resolution_to_response(res, terminology, value)

    # Every slot is filled: one per item above, group slots by the zip.
    final = cast(list[ValidateValueResponse], results)
    valid_count = sum(1 for r in final if r.valid)
    return BulkValidateResponse(
        results=final,
        total=len(final),
        valid_count=valid_count,
        invalid_count=len(final) - valid_count,
    )
