"""Document API endpoints."""

import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pymongo.errors import OperationFailure

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
    is_superadmin,
    resolve_accessible_namespaces,
    resolve_bulk_ids,
    resolve_namespace_filter,
    resolve_or_404,
)
from wip_auth.fastapi_helpers import miss_detail
from wip_auth.registry_client import RegistryUnavailableError

from ..models.api_models import (
    MAX_BULK_ITEMS,
    BulkResponse,
    BulkResultItem,
    DeleteItem,
    DocumentCreateRequest,
    DocumentListResponse,
    DocumentMigrateRequest,
    DocumentMigrateResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    DocumentResponse,
    DocumentVersionResponse,
    PatchDocumentItem,
    RelationshipListResponse,
    TemplateFacetsResponse,
    TemplateImpactStatsResponse,
    TraverseResponse,
)
from ..models.document import DocumentStatus
from ..services.document_service import DocumentService, get_document_service
from ..services.errors import DocumentStoreError, error_fields
from ..services.nats_client import get_throttle_delay
from ..services.registry_client import get_registry_client
from .auth import check_readable, require_api_key, write_refusals

router = APIRouter(prefix="/documents", tags=["Documents"])

# MongoDB's codes for an operand it cannot use: BadValue, and an invalid
# regular expression.
_BAD_FILTER_CODES = frozenset({2, 51091})


@router.post(
    "",
    response_model=BulkResponse,
    summary="Create or update documents",
    description="""
Create one or more documents, or update existing ones based on identity hash.

Each document is validated against the specified template before creation.
For single items, uses direct creation. For multiple items, uses optimized
batch operations with cache warmup.
    """
)
async def create_documents(
    items: list[DocumentCreateRequest] = Body(..., max_length=MAX_BULK_ITEMS),
    continue_on_error: bool = Query(True, description="Continue processing if an item fails"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Create or update documents. Namespace is read from each item (required, no default).

    Template IDs accept both canonical UUIDs and human-readable values
    (e.g., "PATIENT" instead of "019..."). Values are resolved via Registry synonyms.
    """
    if not items:
        return BulkResponse(results=[], total=0, succeeded=0, failed=0)
    namespaces = {item.namespace for item in items}
    for ns in namespaces:
        await check_namespace_permission(identity, ns, "write")

    # Resolve template_id synonyms to canonical IDs (e.g., "PATIENT" → UUID).
    # namespace=None is correct here — each item carries its own namespace field,
    # and resolve_bulk_ids reads it per-item. bypass_cache: this is a write
    # door — after a hard-delete + re-create under the same value, the cache
    # serves the dead template id for up to its TTL.
    await resolve_bulk_ids(
        items, "template_id", "template", namespace=None, bypass_cache=True
    )

    service = get_document_service()

    if len(items) == 1:
        # Single item — use direct create path
        response, error = await service.create_document(items[0], namespace=items[0].namespace)
        if error:
            # The service returns the item's error fields, code included, so
            # the single path reports the same codes as the bulk path.
            results = [BulkResultItem(index=0, status="error", **error)]
        else:
            assert response is not None  # paired with error: when error is None, response is set
            if response.is_new:
                status = "created"
            elif response.previous_version is not None:
                status = "updated"
            else:
                # Nothing was written — the submitted data was byte-identical
                # to the current version. "unchanged" matches the bulk and
                # PATCH vocabulary for this outcome; "skipped" is reserved
                # for items that were never attempted (batch aborted after
                # an earlier failure).
                status = "unchanged"
            results = [BulkResultItem(
                index=0, status=status,
                id=response.document_id, document_id=response.document_id,
                identity_hash=response.identity_hash, version=response.version,
                template_version=response.template_version,
                revision=response.revision,
                is_new=response.is_new, warnings=response.warnings,
            )]
        succeeded = sum(1 for r in results if r.status != "error")
        failed = sum(1 for r in results if r.status == "error")
        result = BulkResponse(results=results, total=1, succeeded=succeeded, failed=failed)
    else:
        # Bulk path — uses cache warmup and batch Registry calls
        result = await _bulk_create_per_namespace(service, items, continue_on_error)

    await asyncio.sleep(get_throttle_delay())
    return result


async def _bulk_create_per_namespace(
    service: DocumentService, items: list[DocumentCreateRequest], continue_on_error: bool,
) -> BulkResponse:
    """The bulk path, with every document written into the namespace its
    item names.

    The service writes one batch into one namespace (validation context,
    identity registration and the existing-document lookup are all scoped to
    it), so the request is taken in runs of consecutive items that share a
    namespace — one run in the common case of a single-namespace request.
    Runs go in submission order, and each run's rows are renumbered to the
    items' positions in the request. With ``continue_on_error`` false the
    request stops at its first failing item, as a single batch does: every
    later item is skipped, whichever namespace it names.
    """
    runs: list[list[int]] = []
    for i, item in enumerate(items):
        if runs and items[runs[-1][0]].namespace == item.namespace:
            runs[-1].append(i)
        else:
            runs.append([i])
    if len(runs) == 1:
        return await service.bulk_create(
            items, namespace=items[0].namespace, continue_on_error=continue_on_error,
        )

    results: list[BulkResultItem] = []
    timing: dict[str, float] = {}
    stopped = False
    for run in runs:
        if stopped:
            results.extend(
                BulkResultItem(index=i, status="skipped", error="Stopped due to previous error")
                for i in run
            )
            continue
        part = await service.bulk_create(
            [items[i] for i in run], namespace=items[run[0]].namespace,
            continue_on_error=continue_on_error,
        )
        results.extend(row.model_copy(update={"index": run[row.index]}) for row in part.results)
        for phase, ms in (part.timing or {}).items():
            timing[phase] = round(timing.get(phase, 0.0) + ms, 1)
        if not continue_on_error and part.failed:
            stopped = True
    results.sort(key=lambda r: r.index)
    return BulkResponse(
        results=results,
        total=len(items),
        succeeded=sum(1 for r in results if r.status not in ("error", "skipped")),
        failed=sum(1 for r in results if r.status == "error"),
        timing=timing,
    )


@router.patch(
    "",
    response_model=BulkResponse,
    summary="Patch documents (partial update)",
    description="""
Apply JSON Merge Patches (RFC 7396) to one or more existing documents.

Each item is processed independently and a new document version is created
on success. Errors are returned per item in the BulkResponse — the endpoint
always returns HTTP 200, matching the bulk-first contract.

Merge semantics:
- Objects at the same path are deep-merged
- Arrays are replaced (send the full array)
- A null value deletes the field

Constraints:
- Identity fields cannot be changed (use POST to create a new document)
- Namespace cannot be changed (PATCH modifies `data`, and `metadata.custom` via `metadata_patch`)
- A soft-deleted document is `not_found`; re-post its full data to bring it back
- Optional per-item `if_match` provides optimistic concurrency control: pass the
  document's `revision` (equal to `version`, except on a `versioned: false`
  template, where each in-place write moves it on)

After merge the full document is re-validated against the same template
version recorded on the existing document, term and file references are
re-resolved, and a new version is written. Reporting-sync sees the same
DOCUMENT_UPDATED event as POST-driven version bumps.
"""
)
async def patch_documents(
    items: list[PatchDocumentItem] = Body(..., max_length=MAX_BULK_ITEMS),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key),
):
    """Apply JSON Merge Patches to documents (bulk-first)."""
    # Resolve document_id synonyms in place. Resolution failures are silently
    # left as-is so the per-item flow returns 'not_found' rather than aborting
    # the whole batch. bypass_cache: write door.
    await resolve_bulk_ids(
        items, "document_id", "document", namespace=namespace, bypass_cache=True
    )

    # Write permission on each document's own namespace, asked before any
    # item is patched: a refused item is reported on its row and the rest of
    # the batch proceeds; an authorization outage refuses the request whole,
    # with nothing written.
    id_to_namespace, refusals = await _batch_write_refusals(identity, items, "document_id")
    results: dict[int, BulkResultItem] = {}
    allowed: list[int] = []
    for i, item in enumerate(items):
        refusal = refusals.get(id_to_namespace.get(item.document_id, ""))
        if refusal:
            results[i] = BulkResultItem(
                index=i, status="error", document_id=item.document_id,
                error=refusal[0], error_code=refusal[1],
            )
        else:
            allowed.append(i)

    rows = await _patch_allowed(items, allowed, results)
    await asyncio.sleep(get_throttle_delay())
    return BulkResponse(
        results=rows, total=len(items),
        succeeded=sum(1 for r in rows if r.status != "error"),
        failed=sum(1 for r in rows if r.status == "error"),
    )


async def _batch_write_refusals(
    identity: UserIdentity, items: list, id_field: str,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """For a bulk write: the namespace of each document the items name in
    ``id_field`` (one query), and the write refusals for those namespaces
    (write_refusals), asked before any item is written."""
    from ..models.document import Document as _Doc
    ids = list({getattr(item, id_field) for item in items if getattr(item, id_field)})
    docs = await _Doc.find({"document_id": {"$in": ids}}).to_list() if ids else []
    id_to_namespace = {d.document_id: d.namespace for d in docs}
    refusals = await write_refusals(
        identity, set(id_to_namespace.values()), "Document not found"
    )
    return id_to_namespace, refusals


async def _patch_allowed(
    items: list[PatchDocumentItem], allowed: list[int], results: dict[int, BulkResultItem],
) -> list[BulkResultItem]:
    """Patch the allowed items and merge their rows, renumbered to their
    position in the request, with the refused rows already in ``results``."""
    if allowed:
        patched = await get_document_service().bulk_patch([items[i] for i in allowed])
        for row in patched.results:
            original = allowed[row.index]
            results[original] = row.model_copy(update={"index": original})
    return [results[i] for i in range(len(items))]


@router.post(
    "/migrate",
    response_model=DocumentMigrateResponse,
    summary="Migrate documents to a newer template version",
    description="""
Re-pin every active document currently on `from_version` to `to_version`
The "move" half of the template-version lifecycle.

Each document's existing data is re-validated against the TARGET version
(which must be active; the source may be inactive/frozen). On apply a new
document version is created — or the single version is overwritten in place
for `versioned: false` templates — pinned to `to_version`, keeping the same
`document_id` and `identity_hash`. No data transformation happens here, except
field renames declared on the target template version, which mechanically re-key
each document's data to the new field names.

Identity-preserving only: the two template versions must declare the same
`identity_fields`, otherwise the re-pin would change the identity hash — that
is a fork (create new documents), not a migrate, and the whole operation is
rejected with `identity_fields_changed`.

Workflow: run with `dry_run=true` (default) for a per-document readiness
report — a result with `failed==0` guarantees a successful apply (barring
concurrent writes). Fix any failing documents (e.g. PATCH-null a removed
field) while the source version is still writable, optionally freeze the
source version (deactivate) as a migration lock, then re-run with
`dry_run=false`.

Bulk-first: always HTTP 200, per-document outcome in the `results` array.
Operation-level problems (bad versions, identity mismatch, inactive target)
return a 4xx.
""",
)
async def migrate_documents(
    request: DocumentMigrateRequest = Body(...),
    namespace: str | None = Query(
        None,
        description="Namespace of the cohort. Omittable only for single-namespace API keys.",
    ),
    identity: UserIdentity = Depends(require_api_key),
):
    """Migrate a cohort of documents from one template version to another."""
    # Resolve to a single concrete namespace + enforce write permission.
    nsf = await resolve_namespace_filter(identity, namespace, "write")
    if namespace:
        ns = namespace
    elif len(nsf.namespaces) == 1 and not is_superadmin(identity):
    # A key that can read one namespace has it implied; an administrator names it.
        ns = nsf.namespaces[0]
    else:
        raise HTTPException(
            status_code=422,
            detail="namespace is required (omittable only for single-namespace keys)",
        )

    # Resolve template_id synonym → canonical. strict: a non-UUID value with no
    # namespace context fails loud rather than silently matching nothing.
    # bypass_cache: write door — a migrate must re-pin against the template
    # the Registry names now, never a cached (possibly dead) id.
    template_id = await resolve_or_404(
        request.template_id, "template", ns, param_name="template_id", strict=True,
        bypass_cache=True,
    )

    service = get_document_service()
    response, error_code, error_message = await service.migrate_document_version(
        template_id=template_id,
        from_version=request.from_version,
        to_version=request.to_version,
        namespace=ns,
        dry_run=request.dry_run,
    )
    if error_code:
        status_code = {
            "invalid_migration": 400,
            "template_not_found": 404,
            "target_inactive": 409,
            "identity_fields_changed": 409,
        }.get(error_code, 400)
        raise HTTPException(
            status_code=status_code,
            detail={"error_code": error_code, "error": error_message},
        )
    await asyncio.sleep(get_throttle_delay())
    return response


@router.get(
    "/impact-stats",
    response_model=TemplateImpactStatsResponse,
    summary="Live-document counts for template version-change impact analysis",
    description="""
Advisory, read-only counts consumed by the template create-as-upsert: how many
active documents each version of the template carries, and — for each name in
`fields` — how many active documents hold a non-empty value there. A dropped
field whose non-empty count is zero is safely migratable; a non-zero count
means data would be stranded.
""",
)
async def get_template_impact_stats(
    template_id: str = Query(..., description="Template ID, value, or synonym"),
    fields: str | None = Query(
        None, description="Comma-separated field names to count non-empty occurrences for"
    ),
    namespace: str | None = Query(
        None,
        description="Namespace. Omittable only for single-namespace API keys.",
    ),
    identity: UserIdentity = Depends(require_api_key),
):
    """Per-version and per-field live-document counts for one template."""
    nsf = await resolve_namespace_filter(identity, namespace, "read")
    if namespace:
        ns = namespace
    elif len(nsf.namespaces) == 1 and not is_superadmin(identity):
        ns = nsf.namespaces[0]
    else:
        raise HTTPException(
            status_code=422,
            detail="namespace is required (omittable only for single-namespace keys)",
        )

    resolved_id = await resolve_or_404(
        template_id, "template", ns, param_name="template_id", strict=True,
    )
    field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else []

    service = get_document_service()
    stats = await service.get_template_impact_stats(
        template_id=resolved_id, namespace=ns, fields=field_list,
    )
    return TemplateImpactStatsResponse(**stats)


@router.get(
    "/template-facets",
    response_model=TemplateFacetsResponse,
    summary="Which templates are this namespace's documents instances of?",
    description="""
Grouped from the namespace's documents, not from template ownership: a
document's namespace is independent of its template's namespace, so listing
the templates a namespace owns cannot answer this — a namespace whose
documents sit on shared or foreign templates would look empty. Counts are
distinct logical documents (version rows collapse before counting).
`status=all` disables the default active-only filter. Each facet carries the
template's own namespace, which may differ from the queried one.
""",
)
async def get_template_facets(
    namespace: str | None = Query(
        None,
        description="Namespace. Omittable only for single-namespace API keys.",
    ),
    status: str = Query(
        "active",
        pattern="^(active|inactive|all)$",
        description="Count documents in this status ('all' disables the filter)",
    ),
    identity: UserIdentity = Depends(require_api_key),
):
    """Distinct templates referenced by one namespace's documents."""
    nsf = await resolve_namespace_filter(identity, namespace, "read")
    if namespace:
        ns = namespace
    elif len(nsf.namespaces) == 1 and not is_superadmin(identity):
        ns = nsf.namespaces[0]
    else:
        raise HTTPException(
            status_code=422,
            detail="namespace is required (omittable only for single-namespace keys)",
        )

    service = get_document_service()
    result = await service.get_template_facets(namespace=ns, status=status)
    return TemplateFacetsResponse(**result)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List documents",
    description="List documents with optional filtering and pagination."
)
async def list_documents(
    namespace: str | None = Query(default=None, description="Namespace to query (omit for all)"),
    template_id: str | None = Query(None, description="Filter by template ID"),
    template_value: str | None = Query(None, description="Filter by template value (e.g., PLANNED_VISIT)"),
    template_version: int | None = Query(None, ge=1, description="Filter by the template version a row is pinned to. With status=active: the documents whose current version uses it."),
    status: DocumentStatus | None = Query(None, description="Filter by status. With latest_only=true it filters on each document's latest version, so status=inactive lists soft-deleted documents."),
    latest_only: bool = Query(False, description="Only return the latest version of each document"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    cursor: str | None = Query(None, description="Cursor for cursor-based pagination: 'start' for the first page, then each page's next_cursor"),
    sort_by: str | None = Query(None, description="Sort field: created_at (default), updated_at, version, or data.<path>. Incompatible with cursor."),
    sort_order: str | None = Query(None, description="Sort order: asc | desc (default desc)"),
    identity: UserIdentity = Depends(require_api_key)
):
    """List documents with pagination.

    Use latest_only=true to return only the highest version of each document_id;
    a status filter then applies to that latest version. latest_only pages by
    page/page_size and cannot be combined with cursor (422).
    Use cursor for efficient deep pagination (avoids skip/limit degradation):
    pass cursor=start for the first page, then each response's next_cursor
    (null on the last page). When cursor is provided, page parameter is
    ignored, total is -1 and pages 0, and sort_by/sort_order must be omitted
    (cursor mode is _id-ordered).
    """
    ns_filter = await resolve_namespace_filter(identity, namespace)

    # Resolve template_id synonym if provided (e.g., "PATIENT" → UUID).
    # Filter context with no value fallback — strict=True so a non-UUID
    # template_id with no namespace fails loud (422) rather than silently
    # filtering to zero rows (CASE-457).
    if template_id:
        template_id = await resolve_or_404(
            template_id, "template", namespace, param_name="template_id",
            strict=True,
        )

    service = get_document_service()
    try:
        return await service.list_documents(
            template_id=template_id,
            template_value=template_value,
            template_version=template_version,
            status=status,
            page=page,
            page_size=page_size,
            latest_only=latest_only,
            cursor=cursor,
            ns_filter=ns_filter.query,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document by ID",
    description="Retrieve a document by its stable ID. Returns latest version by default."
)
async def get_document(
    document_id: str,
    version: int | None = Query(None, description="Specific version (default: latest)"),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get a document by stable ID. Returns latest version by default."""
    raw_document_id = document_id
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    document = await service.get_document(document_id, version=version)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_readable(identity, document.namespace, miss_detail(raw_document_id, "document", namespace, "Document not found", "document_id"))

    return document


@router.get(
    "/{document_id}/versions",
    response_model=DocumentVersionResponse,
    summary="Get document versions",
    description="Get all versions of a document by its ID."
)
async def get_document_versions(
    document_id: str,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get all versions of a document."""
    raw_document_id = document_id
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    versions = await service.get_document_versions(document_id)

    if not versions:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check namespace — fetch the document to get its namespace
    doc = await service.get_document(document_id)
    if doc:
        await check_readable(identity, doc.namespace, miss_detail(raw_document_id, "document", namespace, "Document not found", "document_id"))

    return versions


@router.get(
    "/{document_id}/versions/{version}",
    response_model=DocumentResponse,
    summary="Get specific document version",
    description="Get a specific version of a document."
)
async def get_document_version(
    document_id: str,
    version: int,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get a specific version of a document."""
    raw_document_id = document_id
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    document = await service.get_document_version(document_id, version)

    if not document:
        raise HTTPException(status_code=404, detail="Document version not found")

    await check_readable(identity, document.namespace, miss_detail(raw_document_id, "document", namespace, "Document version not found", "document_id"))

    return document


@router.get(
    "/{document_id}/latest",
    response_model=DocumentResponse,
    summary="Get latest version of document",
    description="""
Get the latest version of a document given any document ID.

This is useful when you have a reference to an old version but want
the current data. The response includes the latest document ID and version.
    """
)
async def get_latest_document(
    document_id: str,
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get the latest version of a document."""
    raw_document_id = document_id
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    document = await service.get_latest_document(document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_readable(identity, document.namespace, miss_detail(raw_document_id, "document", namespace, "Document not found", "document_id"))

    return document


_EDGE_TYPE_FILTER_RESOLUTION = (
    " A bare value resolves in the request's namespace (the namespace parameter,"
    " else the seed document's); NS:VALUE names another namespace; a template ID"
    " is accepted. A value that resolves to nothing returns 404."
)


async def _resolve_edge_type_filter(
    raw: str | None, namespace: str, param_name: str,
) -> list[str] | None:
    """Resolve a comma-separated edge-type filter to template IDs via the
    Registry. A bare value names the edge type in `namespace` (never one that
    merely shares the value elsewhere), `NS:VALUE` names another namespace,
    and a UUID passes through. A value that resolves to nothing is a 404 —
    an empty page would read as "no edges" (strict filter-context mode)."""
    if not raw:
        return None
    return [
        await resolve_or_404(value, "template", namespace, param_name=param_name, strict=True)
        for value in (v.strip() for v in raw.split(","))
        if value
    ]


@router.get(
    "/{document_id}/relationships",
    response_model=RelationshipListResponse,
    summary="List relationship documents touching this document",
    description="""
Return relationship documents (templates with usage='relationship')
that point at (incoming) or from (outgoing) the given document.

Backed by Mongo indexes on (template_id, data.source_ref) and
(template_id, data.target_ref) created lazily on first relationship-
document write.

Pass `?include=peers` to embed a compact peer projection on
each item — the entity at the OTHER end of the edge — avoiding an N+1
fetch for relationship-sidebar rendering. Default response shape is
unchanged when `include` is absent or does not contain `peers`.
""",
)
async def get_document_relationships(
    document_id: str,
    direction: str = Query("both", description="incoming | outgoing | both"),
    template: str | None = Query(
        None, description=(
            "Comma-separated relationship template values to include (default: all)."
            + _EDGE_TYPE_FILTER_RESOLUTION
        )),
    namespace: str | None = Query(None, description=(
        "List only the edges stored in this namespace. Default: every namespace "
        "you can read (an edge lives in its source's namespace, so a document's "
        "incoming edges may sit in other namespaces)."
    )),
    active_only: bool = Query(True, description="Exclude inactive relationship docs"),
    page: int = Query(1, ge=1),
    # Deliberately below the platform-wide 1000 ceiling: with include=peers each
    # row fans out into a peer-document header projection, so a page is not a
    # flat single-collection scan like the other list endpoints.
    page_size: int = Query(50, ge=1, le=500),
    include: str | None = Query(
        None,
        description="Comma-separated optional inclusions. Currently supports: 'peers' (embed a compact peer projection on each item).",
    ),
    identity: UserIdentity = Depends(require_api_key),
):
    """List relationships incident to a document."""
    raw_document_id = document_id
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    seed = await service.get_document(document_id)
    if not seed:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_readable(identity, seed.namespace, miss_detail(raw_document_id, "document", namespace, "Document not found", "document_id"))
    if namespace:
        await check_namespace_permission(identity, namespace, "read")

    ns = namespace or seed.namespace
    template_ids = await _resolve_edge_type_filter(template, ns, "template")
    allowed = None if namespace else await resolve_accessible_namespaces(identity)
    include_set = (
        {tok.strip() for tok in include.split(",") if tok.strip()} if include else set()
    )
    try:
        return await service.find_relationships(
            document_id=document_id,
            direction=direction,
            template_ids=template_ids,
            namespace=namespace,
            active_only=active_only,
            page=page,
            page_size=page_size,
            include_peers="peers" in include_set,
            identity=identity,
            allowed_namespaces=allowed,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/{document_id}/traverse",
    response_model=TraverseResponse,
    summary="N-hop relationship traversal from a document",
    description="""
BFS expansion through relationship documents from a seed document.
At each hop, finds relationship documents touching the current
frontier and adds the *other* endpoint document_ids to the next
frontier. Visited docs are skipped (cycles terminate).

Capped at depth=10 and max_nodes=1000 (safety bounds). When a cap
fires, the response sets truncated=true.
""",
)
async def traverse_document_relationships(
    document_id: str,
    depth: int = Query(1, ge=1, le=10, description="Number of relationship hops"),
    types: str | None = Query(
        None, description=(
            "Comma-separated relationship template values to traverse (default: all)."
            + _EDGE_TYPE_FILTER_RESOLUTION
        )),
    direction: str = Query("outgoing", description="outgoing | incoming | both"),
    namespace: str | None = Query(None, description=(
        "Walk only edges and documents in this namespace. Default: every "
        "namespace you can read; the walk stops at a document in a namespace "
        "you cannot read."
    )),
    identity: UserIdentity = Depends(require_api_key),
):
    """Traverse relationship graph from a document."""
    raw_document_id = document_id
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    seed = await service.get_document(document_id)
    if not seed:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_readable(identity, seed.namespace, miss_detail(raw_document_id, "document", namespace, "Document not found", "document_id"))
    if namespace:
        await check_namespace_permission(identity, namespace, "read")

    ns = namespace or seed.namespace
    type_ids = await _resolve_edge_type_filter(types, ns, "types")
    try:
        return await service.traverse_relationships(
            document_id=document_id,
            depth=depth,
            type_ids=type_ids,
            types_filter=[t.strip() for t in types.split(",") if t.strip()] if types else None,
            direction=direction,
            namespace=namespace,
            allowed_namespaces=(
                None if namespace else await resolve_accessible_namespaces(identity)
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


async def _hard_delete_exemptions(service, items: list[DeleteItem], identity: UserIdentity) -> frozenset[str]:
    """Documents this request hard-deletes whole, whose references to each
    other do not block: a document and its edges can go together without
    force. Only those that will be deleted count; one refused here (absent,
    no write, a namespace that keeps its documents, or still referenced from
    outside the request) exempts nobody, or the documents it references would
    go while it stays."""
    candidates: dict[str, bool] = {}
    modes: dict[str, str] = {}
    registry = get_registry_client()
    for item in items:
        if not (item.hard_delete and item.version is None) or item.id in candidates:
            continue
        doc = await service.get_document(item.id)
        if doc is None:
            continue
        try:
            await check_namespace_permission(identity, doc.namespace, "write")
        except Exception:
            continue
        if doc.namespace not in modes:
            modes[doc.namespace] = await registry.get_namespace_deletion_mode(doc.namespace)
        if modes[doc.namespace] != "full":
            continue
        candidates[item.id] = bool(item.force)
    exempt: frozenset[str] = await service.hard_delete_exemptions(candidates)
    return exempt


@router.delete(
    "",
    response_model=BulkResponse,
    summary="Delete documents",
    description="Delete one or more documents. Soft-delete by default. Set hard_delete=true to permanently remove (requires namespace deletion_mode='full'). A hard delete is refused (error_code=invalid_state, details.rule=referenced) while other documents still reference it; details.referrers groups them by namespace and template. Documents hard-deleted in the same request do not count. force=true, with hard_delete, deletes anyway and reports the referrers as details.broken_references."
)
async def delete_documents(
    items: list[DeleteItem] = Body(..., max_length=MAX_BULK_ITEMS),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Delete one or more documents."""
    await resolve_bulk_ids(
        items, "id", "document", namespace=namespace, bypass_cache=True
    )

    service = get_document_service()
    # Write permission on each document's own namespace, asked before any item
    # is deleted (see patch_documents).
    _, refusals = await _batch_write_refusals(identity, items, "id")
    deleted_with = await _hard_delete_exemptions(service, items, identity)
    results = []
    for i, item in enumerate(items):
        try:
            # `force` overrides the hard-delete reference guard. A soft delete
            # has no guard to override (existing references keep resolving),
            # so force there is refused rather than silently ignored.
            if item.force and not item.hard_delete:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.id,
                    error="'force' applies only with hard_delete=true; "
                          "a soft delete keeps existing references resolving",
                    error_code="force_unsupported",
                ))
                continue
            doc = await service.get_document(item.id)
            if not doc:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found", error_code="not_found"))
                continue
            refusal = refusals.get(doc.namespace)
            if refusal:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.id, error=refusal[0], error_code=refusal[1],
                ))
                continue
            warnings: list[str] = []
            broken: list[dict] = []
            success = await service.delete_document(
                item.id, item.updated_by,
                hard_delete=item.hard_delete,
                version=item.version,
                warnings=warnings,
                force=item.force,
                deleted_with=deleted_with,
                broken_references=broken,
            )
            if not success:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found", error_code="not_found"))
            else:
                results.append(BulkResultItem(
                    index=i, status="deleted", id=item.id, warnings=warnings,
                    details={"broken_references": broken} if broken else None,
                ))
        except (ValueError, RegistryUnavailableError) as e:
            # A Registry that cannot record the item's retirement refuses
            # that item (nothing written for it); the items before it stand.
            results.append(BulkResultItem(index=i, status="error", id=item.id, **error_fields(e)))
    await asyncio.sleep(get_throttle_delay())
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/query",
    response_model=DocumentQueryResponse,
    summary="Query documents",
    description="""
Query documents with complex filters.

Supports filtering on any field including nested data fields.
Example filters:
- {"field": "data.status", "operator": "eq", "value": "active"}
- {"field": "created_at", "operator": "gte", "value": "2024-01-01"}
- {"field": "data.tags", "operator": "in", "value": ["important", "urgent"]}
    """
)
async def query_documents(
    request: DocumentQueryRequest,
    namespace: str | None = Query(None, description="Namespace for synonym resolution and filtering"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Query documents with filters."""
    if request.template_id:
        # Filter context, no value fallback: a non-UUID template_id with no
        # namespace would otherwise pass through raw and silently match zero
        # rows (CASE-457). strict=True turns that into a loud 422.
        request.template_id = await resolve_or_404(
            request.template_id, "template", namespace=namespace,
            param_name="template_id", strict=True,
        )

    ns_filter = await resolve_namespace_filter(identity, namespace=namespace)

    service = get_document_service()
    try:
        return await service.query_documents(request, ns_filter=ns_filter.query)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except OperationFailure as e:
        # MongoDB refusing a filter's operand (a pattern it cannot compile,
        # BadValue) is the request's fault, not the server's.
        if e.code in _BAD_FILTER_CODES:
            raise HTTPException(
                status_code=422, detail=f"Invalid filter: {e.details.get('errmsg', e) if e.details else e}",
            ) from e
        raise


@router.get(
    "/by-identity/{identity_hash}",
    response_model=DocumentResponse,
    summary="Get document by identity hash",
    description="""
The latest version of the document with the specified identity hash (the
active one unless include_inactive is set).

An identity hash is unique only within a template and namespace. When the
filters leave several readable documents matching, the answer is 409 with
error_code `ambiguous_reference` and the candidates (`detail.candidates`,
`detail.total`), the platform's shape for an identifier that names several
entities. Pass namespace and template_id to name one. template_id accepts the canonical ID or any synonym (e.g. the
template's value).
""",
)
async def get_document_by_identity(
    identity_hash: str,
    namespace: str | None = Query(None, description="Filter by namespace (recommended to avoid cross-template ambiguity)"),
    template_id: str | None = Query(None, description="Filter by template — canonical ID or synonym (recommended to avoid cross-template ambiguity)"),
    include_inactive: bool = Query(False, description="Include inactive documents"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get a document by identity hash."""
    if template_id:
        # Filter context, no value fallback: an unresolvable template would
        # otherwise filter on its raw spelling and report the document as
        # missing. strict=True makes it a 422 naming the parameter.
        template_id = await resolve_or_404(
            template_id, "template", namespace=namespace,
            param_name="template_id", strict=True,
        )
    ns_filter = await resolve_namespace_filter(identity, namespace=namespace)

    service = get_document_service()
    try:
        document = await service.get_document_by_identity(
            identity_hash, include_inactive,
            namespace=namespace, template_id=template_id,
            namespace_filter=ns_filter.query,
        )
    except DocumentStoreError as e:
        # The platform's one shape for "this identifier names several
        # entities" (wip_auth.fastapi_helpers._ambiguous_409).
        candidates = (e.details or {}).get("candidates", [])
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ambiguous",
                "error_code": "ambiguous_reference",
                "message": str(e),
                "identifier": identity_hash,
                "candidates": candidates,
                "total": len(candidates),
            },
        ) from e

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document
