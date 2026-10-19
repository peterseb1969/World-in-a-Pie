"""API endpoints for term management."""

import math
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
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
        1000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100,
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
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )

    # Look up terminology to get namespace for permission check
    term_parent = await Terminology.find_one({"terminology_id": terminology_id})
    if term_parent:
        await check_namespace_permission(identity, term_parent.namespace, "write")

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
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg) from None
            results = [BulkResultItem(index=0, status="error", value=items[0].value, error=msg)]
        except RegistryError as e:
            results = [BulkResultItem(index=0, status="error", value=items[0].value, error=f"Registry error: {e!s}")]
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
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg) from None
            raise HTTPException(status_code=400, detail=msg) from None
        except RegistryError as e:
            raise HTTPException(status_code=502, detail=f"Registry error: {e!s}") from e

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
    when set (reconcile never clears a field, so re-runs are idempotent);
    metadata is honored on create and never rewritten. Active terms absent
    from the snapshot are deprecated — never deleted — and only under
    on_missing='deprecate', optionally with successors. dry_run (the
    default) returns the full plan and writes nothing; unchanged terms are
    counted, not listed. Writes route through the normal term doors, so
    Registry registration, names validation, the collision guard, events
    and audit behave exactly like individual writes.
    """
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )
    terminology = await Terminology.find_one({"terminology_id": terminology_id})
    if not terminology:
        raise HTTPException(status_code=404, detail="Terminology not found")

    await check_namespace_permission(identity, terminology.namespace, "write")

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
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )

    # Get terminology info
    terminology = await Terminology.find_one({"terminology_id": terminology_id})
    if not terminology:
        # Try by value — unscoped lookups fail loud on an ambiguous value
        try:
            terminology = await TerminologyService.find_by_value(terminology_id, namespace)
        except AmbiguousTerminologyValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not terminology:
            raise HTTPException(status_code=404, detail="Terminology not found")

    terms, total = await TerminologyService.list_terms(
        terminology_id=terminology.terminology_id,
        status=status,
        page=page,
        page_size=page_size,
        search=search,
        ns_filter=ns_filter.query,
        sort_by=sort_by,
        sort_order=sort_order,
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
    /validate. Exact mode returns ALL candidates — two terms sharing a
    name both appear, making this the ambiguity-inspection surface.
    """
    # A terminology named by id or as NS:VALUE names its own namespace, and
    # the terms it scopes live there, so it needs no ?namespace=. Read
    # access to that namespace is still checked below.
    if namespace is None and terminology is not None:
        if ":" in terminology:
            namespace = split_qualified_value(terminology)[0]
        else:
            coordinates = await terminology_coordinates_by_id(terminology)
            if coordinates is not None:
                namespace = coordinates[0]

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
    if terminology is not None:
        # Field-form door: the identifier is the raw value, uninterpreted
        term_id = await resolve_term_by_fields_or_404(
            term_id, terminology, namespace, param_name="term_id"
        )
    else:
        # Resolve synonym — supports colon notation for terms
        term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    result = await TerminologyService.get_term(term_id=term_id)
    if not result:
        raise HTTPException(status_code=404, detail="Term not found")

    # CASE-384 — gate read by the term's namespace.
    await check_namespace_permission(identity, result.namespace, "read")
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

    # CASE-384 — batched namespace lookup + permission check.
    from ..models.term import Term as _Term
    ids = [item.term_id for item in items if item.term_id]
    if ids:
        existing_docs = await _Term.find({"term_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.term_id: d.namespace for d in existing_docs}
        for item in items:
            ns = id_to_namespace.get(item.term_id)
            if ns:
                await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
        try:
            collision_warnings: list[dict] = []
            result = await TerminologyService.update_term(
                item.term_id, item, collision_out=collision_warnings
            )
            if not result:
                results.append(BulkResultItem(index=i, status="error", id=item.term_id, error="Term not found"))
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
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, error=str(e)))
        except RegistryError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, error=f"Registry error: {e!s}"))
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
                    "deprecation's replacement lives in the same vocabulary; "
                    "a cross-terminology pointer must use a UUID or the "
                    "fully qualified 'ns:terminology:value' form). When "
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
    if ids:
        existing_docs = await _Term.find({"term_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.term_id: d.namespace for d in existing_docs}
        for item in items:
            ns = id_to_namespace.get(item.term_id)
            if ns:
                await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
        try:
            result = await TerminologyService.deprecate_term(item.term_id, item)
            if not result:
                results.append(BulkResultItem(index=i, status="error", id=item.term_id, error="Term not found"))
            else:
                results.append(BulkResultItem(index=i, status="updated", id=item.term_id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, error=str(e)))
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
    """Soft-delete one or more terms (set status to inactive)."""
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
    if ids:
        existing_docs = await _Term.find({"term_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.term_id: d.namespace for d in existing_docs}
        for item in items:
            ns = id_to_namespace.get(item.id)
            if ns:
                await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
        try:
            success = await TerminologyService.delete_term(
                term_id=item.id, updated_by=item.updated_by,
                hard_delete=item.hard_delete,
            )
            if not success:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Term not found"))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except RegistryEntryNotRemovedError as e:
            # The term IS deleted; its Registry entry is not — say so.
            results.append(BulkResultItem(
                index=i, status="error", id=item.id, error=str(e),
                error_code="registry_entry_not_removed",
            ))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error=str(e)))
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
    if ids:
        for ns in {d.namespace for d in await Term.find({"term_id": {"$in": ids}}).to_list()}:
            await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
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
            results.append(BulkResultItem(index=i, status="error", id=item.survivor_term_id, error=str(e)))
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

    matches is 1 for a value match or a single name bearer; candidates are
    listed only when more than one term bears the name — a single hit keeps
    the pre-ambiguity response shape.
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
    )


# =============================================================================
# VALIDATION
# =============================================================================

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
            terminology = await TerminologyService.find_by_value(
                request.terminology_value or "", None
            )
        except AmbiguousTerminologyValueError as e:
            return ValidateValueResponse(
                valid=False,
                terminology_id="",
                terminology_value=request.terminology_value or "",
                value=request.value,
                error=str(e),
            )

    if not terminology:
        return ValidateValueResponse(
            valid=False,
            terminology_id=request.terminology_id or "",
            terminology_value=request.terminology_value or "",
            value=request.value,
            error="Terminology not found"
        )

    # CASE-384 — validation reads from the terminology (returns matched
    # terms, suggestions). Gate by read permission on the terminology's
    # namespace.
    await check_namespace_permission(identity, terminology.namespace, "read")

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

    def _invalid(item, error: str, terminology_id: str = "", terminology_value: str = ""):
        results.append(ValidateValueResponse(
            valid=False, terminology_id=terminology_id, terminology_value=terminology_value,
            value=item.value, error=error,
        ))

    for index, item in enumerate(request.items):
        # Resolve terminology_id synonym
        if item.terminology_id:
            item.terminology_id = await resolve_or_404(
                item.terminology_id, "terminology", namespace=None, param_name="terminology_id"
            )

        # Get terminology — per-item: an ambiguous unscoped value fails
        # that item, not the request (bulk-first)
        if item.terminology_id:
            if item.terminology_id not in terminologies:
                terminologies[item.terminology_id] = await Terminology.find_one(
                    {"terminology_id": item.terminology_id}
                )
            terminology = terminologies[item.terminology_id]
        elif item.terminology_value:
            try:
                terminology = await TerminologyService.find_by_value(item.terminology_value, None)
            except AmbiguousTerminologyValueError as e:
                _invalid(item, str(e), terminology_value=item.terminology_value)
                continue
        else:
            _invalid(item, "Must provide terminology_id or terminology_value")
            continue

        if not terminology:
            _invalid(item, "Terminology not found", item.terminology_id or "", item.terminology_value or "")
            continue

        if terminology.terminology_id not in groups:
            await check_namespace_permission(identity, terminology.namespace, "read")
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
