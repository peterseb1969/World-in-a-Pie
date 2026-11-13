"""Document service for CRUD operations and upsert logic."""

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar, NamedTuple, cast

from beanie import PydanticObjectId
from beanie.odm.enums import SortDirection

from wip_auth.composite_key import compute_composite_key_hash
from wip_auth.document_identity import (
    build_referent_resolutions,
    substitute_resolved_referents,
)
from wip_auth.registry_client import DependencyUnavailableError
from wip_auth.synonym_lifecycle import (
    LifecycleRefused,
    record_entry_lifecycle,
    undo_entry_lifecycle,
)
from wip_auth.term_resolution import term_resolution_batch

# Import identity helper from wip-auth
# This returns the authenticated identity, not the client-provided value
from ..api.auth import get_identity_string
from ..models.api_models import (
    BulkResponse,
    BulkResultItem,
    DocumentCreateRequest,
    DocumentCreateResponse,
    DocumentListResponse,
    DocumentMigrateResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    DocumentResponse,
    DocumentVersionResponse,
    DocumentVersionSummary,
    PatchDocumentItem,
    PeerProjection,
    RelationshipItem,
    RelationshipListResponse,
    TraverseNode,
    TraverseResponse,
    ValidationError,
    ValidationResponse,
    operator_key_in,
    operator_path_segment,
)
from ..models.document import Document, DocumentMetadata, DocumentStatus, current_revision
from .errors import DocumentStoreError, dependency_unavailable, error_fields
from .file_storage_client import is_file_storage_enabled
from .identity_service import IdentityService
from .inbound_gate import InboundGate, carried_ids, placement_batch
from .nats_client import EventType, publish_document_event
from .reference_validator import (
    ReferenceValidationError,
    RegistryUnavailableError,
    get_reference_validator,
)
from .registry_client import RegistryError, get_registry_client, qualified_synonym_values
from .template_store_client import TemplateStoreError, get_template_store_client
from .timing_stats import TimingStats
from .validation_service import ValidationService

logger = logging.getLogger(__name__)


class _PlannedWrite(NamedTuple):
    """A row stage 4 will write in bulk (DocumentService._write_planned).

    existing is the version it replaces, if any; validation_result carries
    the item's warnings.
    """

    item_index: int
    document: "Document"
    existing: "Document | None"
    validation_result: Any


# Rows per insert when stage 4 writes planned rows in bulk.
BULK_WRITE_CHUNK = 1000


class _ItemOutcome(NamedTuple):
    """Result of applying one validated item in bulk_create's Stage 4.

    counter is one of "created" | "updated" | "unchanged" | "failed" — tells
    the coordinator which tally to bump. pending_event is the deferred NATS
    event (published after the loop) or None.
    """

    result: "BulkResultItem"
    pending_event: tuple | None
    counter: str


def _registry_error_outcome(i: int, registry_result: dict) -> _ItemOutcome:
    """The failed outcome of an item the Registry answered with an error."""
    return _ItemOutcome(
        BulkResultItem(
            index=i, status="error",
            error=registry_result.get("error", "Failed to generate ID"),
            error_code=registry_result.get("error_code") or "registry_error",
            details=registry_result.get("details"),
        ),
        None,
        "failed",
    )


def _retired_stamps(doc: "Document") -> dict[Any, tuple[datetime | None, str | None]]:
    """What a write that retires ``doc`` must restore if its successor
    insert fails: the row's _id and its own updated_at / updated_by, when the
    write takes it from active to inactive. Empty when the row is already
    inactive (the deleted head of a revival): there is nothing to give back,
    and reactivating it would undelete the document."""
    if doc.status != DocumentStatus.ACTIVE:
        return {}
    return {doc.id: (doc.updated_at, doc.updated_by)}


async def _retire_version(
    existing: "Document", actor: str, now: datetime,
) -> dict[Any, tuple[datetime | None, str | None]]:
    """Mark the version a new one replaces inactive; returns what a rollback
    restores (_retired_stamps)."""
    prior = _retired_stamps(existing)
    existing.status = DocumentStatus.INACTIVE
    existing.updated_at = now
    existing.updated_by = actor
    await existing.save()
    return prior


def _custom_metadata(item: "DocumentCreateRequest", existing: "Document | None") -> dict:
    """The custom metadata a written version carries.

    metadata=None on an update of an existing document carries the existing
    custom metadata forward (the caller did not address it); a supplied dict
    — including {} — replaces wholesale. Brand-new documents and restores
    start from {} when nothing is supplied.
    """
    if item.metadata is not None:
        return item.metadata
    if existing is not None and existing.metadata:
        return dict(existing.metadata.custom)
    return {}


_UNKNOWN_PREVIOUS: Any = object()


class PatchError(Exception):
    """Per-item error during PATCH /documents processing.

    Carries a machine-readable code so the bulk endpoint can populate
    BulkResultItem.error_code per design doc §"Per-item error codes".
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def json_merge_patch(target: Any, patch: Any) -> Any:
    """Apply a JSON Merge Patch (RFC 7396) to a target value.

    - dict-on-dict: deep merge recursively
    - dict-on-non-dict: replace target with empty dict, then merge
    - any non-dict patch value (scalar, list, etc.): replaces target
    - null patch value at any key: deletes that key from target

    Returns a new value; the input target is not mutated at the top level.
    """
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    result = dict(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = json_merge_patch(result.get(key), value)
        else:
            result[key] = value
    return result


_CORE_SORT_FIELDS = ("created_at", "updated_at", "version")


def name_every_status(query: dict[str, Any]) -> None:
    """Spell "any status" out as every status, on a one-template query.

    A document's status is always one of DocumentStatus (startup rewrites
    the retired `archived` value), so this changes no result. It lets the
    template indexes serve the query: they hold status between template_id
    and the sort keys, and an unconstrained status there forces MongoDB to
    sort the whole template in memory. Named, each status is one sorted
    index range, and MongoDB merges them.
    """
    if "template_id" in query and "status" not in query:
        query["status"] = {"$in": [s.value for s in DocumentStatus]}


def build_sort_clauses(
    sort_by: str | None,
    sort_order: str | None,
    *,
    default_sort_by: str = "created_at",
    default_sort_order: str = "desc",
) -> list[tuple[str, SortDirection]]:
    """Build a list of (field, direction) sort clauses for Beanie / aggregation.

    Always appends ("document_id", 1) as a deterministic tiebreaker so
    pagination is stable when primary-sort values tie (CASE-304).

    Allows three core fields plus any "data.<path>" string. Forward-compat
    with a future "sortable_fields" template extension: today data.<path>
    is accepted permissively (matches existing POST behaviour); when the
    template-side declaration lands, validation can tighten without
    breaking the wire shape.
    """
    field = (sort_by or default_sort_by).strip() or default_sort_by
    order = (sort_order or default_sort_order).strip().lower() or default_sort_order

    if order not in ("asc", "desc"):
        raise ValueError(
            f"Invalid sort_order '{sort_order}'. Must be 'asc' or 'desc'."
        )

    if field not in _CORE_SORT_FIELDS and not field.startswith("data."):
        # CASE-317: sort_by is a declarative slot — the platform
        # commits to the field's meaning for pagination order.
        # metadata.* is caller-attached audit context with no
        # platform-side commitments and is not indexed by reporting-
        # sync, so it cannot carry pagination order. Surface the
        # metadata case explicitly because it's the most-likely
        # mistake — operators reach for it after seeing
        # metadata.<x> work in POST /documents/query filters (which
        # remain free; only declarative slots reject metadata).
        if field.startswith("metadata."):
            raise ValueError(
                f"sort_by must reference a structural field, not a "
                f"metadata path. Got '{field}'. metadata.* is caller-"
                f"attached context with no schema commitments and is "
                f"not indexed by reporting-sync. Supported: "
                f"{', '.join(_CORE_SORT_FIELDS)}, or data.<path>. "
                f"POST /documents/query filters can still use "
                f"metadata.<path> — only declarative slots reject it."
            )
        raise ValueError(
            f"Unknown sort field '{field}'. Supported: "
            f"{', '.join(_CORE_SORT_FIELDS)}, "
            f"or data.<path> for template-declared sortable fields."
        )

    direction = SortDirection.ASCENDING if order == "asc" else SortDirection.DESCENDING
    return [(field, direction), ("document_id", SortDirection.ASCENDING)]


def _declared_field_names(tpl: dict[str, Any]) -> set[str]:
    """Set of `data.*` field names declared on a template.

    Reads the template's `fields` list (inheritance-resolved by
    template-store before this point — `get_template` defaults to
    `resolve_inheritance=True`, so inherited `title`/`doc_status`
    from a parent template counts). Used by the tier-2 peer-projection
    auto-include (CASE-354) to gate `title`/`doc_status` inclusion
    on whether the template actually has them.

    Returns an empty set for malformed templates rather than raising —
    the resolver caller treats missing fields as "auto-include doesn't
    fire," which is the safe default.
    """
    out: set[str] = set()
    for f in tpl.get("fields") or []:
        if isinstance(f, dict):
            name = f.get("name")
            if isinstance(name, str):
                out.add(name)
    return out


# The cursor value that opens cursor pagination at the first page.
CURSOR_START = "start"


def _filter_value_problem(operator: str, value: Any) -> str | None:
    """Why a filter operand cannot be used with its operator, or None."""
    if operator in ("in", "nin") and not isinstance(value, list):
        return f"'{operator}' takes a list of values"
    if operator == "exists" and not isinstance(value, bool):
        return "'exists' takes true or false"
    if operator == "regex" and not isinstance(value, str):
        # Only the type: the pattern is MongoDB's (PCRE) to judge, and the
        # query route turns its refusal into a 422 as well.
        return "'regex' takes a string pattern"
    return None


def _document_id_mismatch(
    request: DocumentCreateRequest, resolved_id: str, is_new: bool,
) -> dict[str, Any] | None:
    """The refusal for a ``document_id`` the identity does not resolve to.

    A create that names a ``document_id`` without a ``version`` is an upsert
    of that document: its identity decides which document it is, and the
    named id must be that one. Without the check the id was dropped — a new
    identity silently got a fresh id, and the caller's id was never used.
    A restore (``document_id`` + ``version``) asks the Registry for the id
    itself and never comes here. Returns None when there is nothing to refuse.
    """
    if not request.document_id or request.version is not None:
        return None
    if resolved_id == request.document_id:
        return None
    found = (
        "no document has this identity yet" if is_new
        else f"this identity is document '{resolved_id}'"
    )
    return {
        "error": (
            f"document_id '{request.document_id}' is not the document this "
            f"identity resolves to: {found}. Omit document_id to let the "
            "identity decide, or pass version as well for a restore."
        ),
        "error_code": "invalid_request",
        "details": {
            "rule": "document_id_mismatch",
            "document_id": request.document_id,
            "resolved_document_id": None if is_new else resolved_id,
        },
    }


def _identity_hash_of(identity_values: dict[str, Any] | None) -> str | None:
    """The identity hash the Registry stores for these identity values: the
    composite-key hash of the feed, or "" when there is none (an identity-less
    template). None when the values are not JSON, which the Registry, fed over
    JSON, could never have hashed."""
    if not identity_values:
        return ""
    try:
        return compute_composite_key_hash(identity_values)
    except TypeError:
        return None


def _peer_id(item: Any, seed_document_id: str) -> str | None:
    """The OTHER end of an edge, direction-agnostic; a self-loop resolves to
    the seed itself."""
    data = item.data or {}
    source_ref = data.get("source_ref")
    target_ref = data.get("target_ref")
    return cast("str | None", target_ref if source_ref == seed_document_id else source_ref)


def _peer_ids(items: list[Any], seed_document_id: str) -> set[str]:
    """The peers a page of edges names, each once."""
    return {peer_id for item in items if (peer_id := _peer_id(item, seed_document_id))}


async def _latest_peer_documents(peer_ids: set[str]) -> dict[str, Document]:
    """Batched lookup: latest version of each peer document_id. PoNIF #1 —
    inactive peers still resolve, so no status filter. The aggregation
    pipeline mirrors list_documents(latest_only=True)."""
    peer_pipeline: list[dict] = [
        {"$match": {"document_id": {"$in": list(peer_ids)}}},
        {"$sort": {"document_id": 1, "version": -1}},
        {"$group": {"_id": "$document_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    peer_docs = await Document.aggregate(peer_pipeline).to_list()
    return {doc["document_id"]: Document(**doc) for doc in peer_docs}


async def _can_read_namespace(identity: Any, ns: str, cache: dict[str, str]) -> bool:
    """Read on a peer's namespace, resolved once per namespace.

    A Registry that cannot answer fails the request (503) instead of reading
    as "no access": the peer would otherwise be reported as not_found.
    """
    from wip_auth.permissions import (
        PERMISSION_UNAVAILABLE,
        permission_sufficient,
        resolve_permission,
    )

    if identity is None:
        return False  # nothing vouches for the caller
    if ns not in cache:
        cache[ns] = await resolve_permission(identity, ns)
    if cache[ns] == PERMISSION_UNAVAILABLE:
        raise RegistryUnavailableError(
            f"Registry unavailable (read permission on {ns!r} for a peer)", "permission check",
        )
    return permission_sufficient(cache[ns], "read")


async def _projection_paths_by_template(template_ids: set[str]) -> dict[str, list[str]]:
    """Each peer template's projection paths; an unreadable template gets
    the legacy compact default."""
    template_client = get_template_store_client()
    paths_by_tid: dict[str, list[str]] = {}
    for tid in template_ids:
        try:
            tpl = await template_client.get_template(template_id=tid)
        except Exception:
            tpl = None
        paths_by_tid[tid] = _projection_paths(tpl) if tpl else ["title", "doc_status"]
    return paths_by_tid


def _projection_paths(tpl: dict[str, Any]) -> list[str]:
    """Template-aware header_fields lookup (CASE-343, refined by CASE-354).
    Three-tier resolution per peer template:

      1. `header_fields` (explicit) — tier 1, no auto-include. Use this to
         opt out of auto-include too: setting `header_fields=["case_number"]`
         projects identity ONLY.
      2. `identity_fields` + auto-include `title`/`doc_status` if the
         template declares those fields (tier 2, CASE-354). The "if
         declared" guard reads inheritance-resolved fields (get_template
         default resolves the chain) so inherited title/doc_status counts.
         Applies uniformly to entity and edge-type templates.
      3. `["title", "doc_status"]` legacy compact default — tier 3, fires
         only when both header_fields and identity_fields are empty.

    Edge case: a template that sets `header_fields=[]` (explicitly empty, as
    opposed to absent) falls through to tier 2 today — the auto-include WILL
    fire. To project identity-only, populate `header_fields` with the
    desired field names explicitly (tier 1 wins).
    """
    header_fields = tpl.get("header_fields") or []
    identity_fields = tpl.get("identity_fields") or []
    if header_fields:
        # Tier 1 — explicit override wins, no auto-include.
        return list(header_fields)
    if not identity_fields:
        # Tier 3 — legacy compact default.
        return ["title", "doc_status"]
    # Tier 2 — identity + display-label sugar (CASE-354).
    paths = list(identity_fields)
    declared = _declared_field_names(tpl)
    for extra in ("title", "doc_status"):
        if extra in declared and extra not in paths:
            paths.append(extra)
    return paths


def _metadata_custom(doc: Document) -> dict[str, Any]:
    """A document's metadata.custom, empty when it has none."""
    peer_metadata: dict[str, Any] = doc.metadata.model_dump() if doc.metadata else {}
    return cast(dict[str, Any], (
        peer_metadata.get("custom", {})
        if isinstance(peer_metadata, dict)
        else getattr(peer_metadata, "custom", {}) or {}
    ))


def _project_peer(peer_doc: Document, paths: list[str]) -> PeerProjection:
    """Template-declared projection (CASE-343). Bare names target peer.data;
    "metadata.custom.<name>" paths target peer.metadata.custom. Missing
    fields are silently skipped (no error). Applied uniformly to entity
    templates and edge types — both can declare header_fields."""
    peer_data = peer_doc.data or {}
    peer_metadata_custom = _metadata_custom(peer_doc)
    projected_data: dict[str, Any] = {}
    projected_metadata_custom: dict[str, Any] = {}
    for path in paths:
        if path.startswith("metadata.custom."):
            key = path[len("metadata.custom."):]
            if key in peer_metadata_custom:
                projected_metadata_custom[key] = peer_metadata_custom[key]
        elif "." in path:
            # Other metadata.* paths or nested data paths — out of
            # scope for v1; skip silently. Future: dotted traversal.
            continue
        elif path in peer_data:
            projected_data[path] = peer_data[path]

    return PeerProjection(
        document_id=peer_doc.document_id,
        namespace=peer_doc.namespace,
        template_id=peer_doc.template_id,
        template_value=peer_doc.template_value,
        status=peer_doc.status,
        data=projected_data,
        metadata={"custom": projected_metadata_custom} if projected_metadata_custom else None,
    )


async def _traverse_seed_namespace(
    document_id: str, direction: str, depth: int, namespace: str | None,
) -> str:
    """Check a traversal's arguments and its seed; returns the namespace a
    missing endpoint is recorded under (the given one, else the seed's)."""
    if direction not in ("incoming", "outgoing", "both"):
        raise ValueError(f"direction must be incoming|outgoing|both (got '{direction}')")
    if depth < 1 or depth > 10:
        raise ValueError("depth must be between 1 and 10")

    # Seed doc must exist; namespace defaults to its namespace if not given.
    seed = await Document.find_one({"document_id": document_id})
    if seed is None:
        raise ValueError(f"Document not found: {document_id}")
    return namespace or seed.namespace


def _hop_query(
    frontier: set[str],
    direction: str,
    namespace: str | None,
    allowed_namespaces: list[str] | None,
) -> dict[str, Any]:
    """The per-hop query: relationship docs whose source/target references
    one of the frontier doc_ids."""
    base: dict[str, Any] = {"status": DocumentStatus.ACTIVE.value}
    if namespace:
        base["namespace"] = namespace
    elif allowed_namespaces is not None:
        base["namespace"] = {"$in": allowed_namespaces}
    by_source = {"data.source_ref": {"$in": list(frontier)}}
    by_target = {"data.target_ref": {"$in": list(frontier)}}
    if direction == "outgoing":
        return {**base, **by_source}
    if direction == "incoming":
        return {**base, **by_target}
    return {**base, "$or": [by_source, by_target]}


def _hop_endpoints(rel: Document, frontier: set[str], direction: str) -> list[tuple[str, str]]:
    """(endpoint, parent) pairs an edge leads to from the frontier. For
    "both", an edge touched by the frontier could be reached via either
    endpoint; every endpoint is reported."""
    rel_data = rel.data or {}
    src_ref = rel_data.get("source_ref")
    tgt_ref = rel_data.get("target_ref")
    candidates: list[tuple[str, str]] = []
    if direction in ("outgoing", "both") and src_ref in frontier and tgt_ref:
        candidates.append((tgt_ref, src_ref))
    if direction in ("incoming", "both") and tgt_ref in frontier and src_ref:
        candidates.append((src_ref, tgt_ref))
    return candidates


async def _collect_hop(
    hop_query: dict[str, Any],
    frontier: set[str],
    direction: str,
    visited: set[str],
    room: int,
) -> tuple[list[tuple[str, str, str]], bool]:
    """New endpoints of this hop, in discovery order: (other, parent, edge).
    Reading stops once ``room`` (the node cap less the nodes found) is used;
    returns the endpoints and whether the cap cut the hop short."""
    found: list[tuple[str, str, str]] = []
    claimed: set[str] = set()
    async for rel in Document.find(hop_query):
        for other_id, parent_id in _hop_endpoints(rel, frontier, direction):
            if other_id in visited or other_id in claimed:
                continue
            if len(found) >= room:
                return found, True
            claimed.add(other_id)
            found.append((other_id, parent_id, rel.document_id))
    return found, False


def _record_hop(
    found: list[tuple[str, str, str]],
    endpoint_docs: dict[str, Document],
    nodes: list[TraverseNode],
    visited: set[str],
    path_by_id: dict[str, list[str]],
    *,
    depth: int,
    fallback_namespace: str,
    namespace: str | None,
    allowed_namespaces: list[str] | None,
) -> set[str]:
    """Add a hop's endpoints to ``nodes``; returns the next frontier. The
    walk stops at a namespace the caller cannot read."""
    next_frontier: set[str] = set()
    for other_id, parent_id, rel_id in found:
        visited.add(other_id)
        other_doc = endpoint_docs.get(other_id)
        if other_doc is not None and not _node_readable(
            other_doc.namespace, namespace, allowed_namespaces,
        ):
            continue
        new_path = [*path_by_id[parent_id], other_id]
        path_by_id[other_id] = new_path
        next_frontier.add(other_id)
        # A reference to something missing records what is known of it.
        nodes.append(TraverseNode(
            document_id=other_doc.document_id if other_doc else other_id,
            template_id=other_doc.template_id if other_doc else "",
            template_value=other_doc.template_value if other_doc else None,
            namespace=other_doc.namespace if other_doc else fallback_namespace,
            depth=depth,
            via_relationship=rel_id,
            path=new_path,
        ))
    return next_frontier


def _node_readable(
    node_ns: str, namespace: str | None, allowed_namespaces: list[str] | None,
) -> bool:
    if namespace:
        return node_ns == namespace
    return allowed_namespaces is None or node_ns in allowed_namespaces


async def _read_patch_target(document_id: str) -> Document:
    """1. Read latest version (regardless of status — a soft-deleted
    document must answer not_found, not be patched). Re-posting its full
    data brings it back as a successor version."""
    latest = await Document.find(
        {"document_id": document_id}
    ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
    if not latest or latest[0].status == DocumentStatus.INACTIVE:
        raise PatchError("not_found", "Document not found")
    return latest[0]


async def _check_patch_permission(namespace: str) -> None:
    """2. Permission check on the document's namespace. Uses
    resolve_permission directly to produce a per-item 'forbidden' error
    instead of an HTTP 403 from check_namespace_permission."""
    # Lazy imports to avoid module-level coupling with FastAPI/wip-auth
    from wip_auth import (
        permission_sufficient,
        require_current_identity,
        resolve_permission,
    )
    from wip_auth.permissions import PERMISSION_UNAVAILABLE

    permission = await resolve_permission(require_current_identity(), namespace)
    if permission == PERMISSION_UNAVAILABLE:
        raise PatchError(
            "registry_error",
            "Could not check write access: the Registry is unreachable",
        )
    if permission == "none":
        # Invisible namespace: answer as an id that names nothing, so
        # the response does not confirm that the document exists.
        raise PatchError("not_found", "Document not found")
    if not permission_sufficient(permission, "write"):
        raise PatchError(
            "forbidden",
            f"Requires write access to namespace '{namespace}'",
        )


def _same_referent(field_path: str, stored_feed: dict[str, Any] | None, validation_result: Any) -> bool:
    """Whether a changed term/reference identity field still names the
    referent the document's stored identity feed holds."""
    return bool(
        field_path in validation_result.referent_identity_fields
        and stored_feed is not None
        and field_path in stored_feed
        and stored_feed[field_path] == validation_result.identity_values.get(field_path)
    )


def _patch_result(
    index: int, doc: Document, status: str, warnings: list[Any], *, revision: bool = True,
) -> BulkResultItem:
    """A PATCH item's result row. A new version's row carries no revision."""
    return BulkResultItem(
        index=index,
        status=status,
        id=doc.document_id,
        document_id=doc.document_id,
        identity_hash=doc.identity_hash,
        version=doc.version,
        template_version=doc.template_version,
        revision=current_revision(doc) if revision else None,
        is_new=False,
        warnings=warnings,
    )


async def _remove_registry_entry(client: Any, document_id: str, actor: str) -> None:
    """Remove a hard-deleted document's Registry entry; a failure is logged,
    the delete stands."""
    try:
        await client.hard_delete_entry(document_id, updated_by=actor)
    except Exception as e:
        logger.warning(f"Failed to hard-delete Registry entry for document {document_id}: {e}")


def _add_validation_timing(validation_results: list[Any], timing: dict[str, float]) -> None:
    """Aggregate per-stage validation timing from all individual results."""
    val_stage_totals: dict[str, float] = {}
    for _, _, vr in validation_results:
        for stage, ms in vr.timing.items():
            val_stage_totals[stage] = val_stage_totals.get(stage, 0) + ms
    for stage, total_ms in sorted(val_stage_totals.items()):
        timing[f"1v_{stage}"] = round(total_ms, 1)


def _assign_identity_hashes(
    validation_results: list[Any], registry_results: list[dict[str, Any]],
) -> None:
    """Assign registry-returned identity_hashes back to validation results."""
    for (_, _, vr), reg_result in zip(validation_results, registry_results, strict=False):
        if reg_result.get("status") != "error":
            vr.identity_hash = reg_result.get("identity_hash")


async def _add_latest_of_retired(
    namespace: str, registry_doc_ids: list[Any], existing_by_doc_id: dict[str, Document],
) -> None:
    """Add the latest version of each known identity that has no active one."""
    # A known identity with no active version is soft-deleted: the write
    # follows its latest version and brings it back as the successor (see
    # _find_latest_by_document_id). Rare, so looked up only for those.
    retired_ids = [
        doc_id for doc_id in dict.fromkeys(registry_doc_ids)
        if isinstance(doc_id, str) and doc_id not in existing_by_doc_id
    ]
    # One query per chunk, as for the active rows above; the newest row
    # of each comes first.
    for first in range(0, len(retired_ids), BULK_WRITE_CHUNK):
        rows = await Document.find({
            "namespace": namespace,
            "document_id": {"$in": retired_ids[first:first + BULK_WRITE_CHUNK]},
        }).sort([("version", SortDirection.DESCENDING)]).to_list()
        for row in rows:
            existing_by_doc_id.setdefault(row.document_id, row)


async def _publish_bulk_events(
    pending_events: list[tuple[Any, dict]], actor: str, timing: dict[str, float],
) -> None:
    """Publish a batch's NATS events concurrently. Events are fire-and-forget
    within gather — individual failures are logged by publish_document_event()
    but don't fail the batch."""
    if not pending_events:
        return
    start = time.perf_counter()
    await asyncio.gather(*(
        publish_document_event(et, payload, changed_by=actor)
        for et, payload in pending_events
    ))
    timing["5_nats_publish"] = (time.perf_counter() - start) * 1000


def _collect_outcome(
    outcome: Any, results: list[Any], pending_events: list[tuple[Any, dict]],
) -> None:
    """One written item's result row, and its event when it has one."""
    results.append(outcome.result)
    if outcome.pending_event:
        pending_events.append(outcome.pending_event)


async def _exception_rows(base: dict[str, Any], exceptions: list[dict]) -> tuple[list, list]:
    """The rows of the documents whose latest version is not simply their one
    active row: (active rows that are not their document's latest, latest
    rows that are inactive). A document is (namespace, document_id)."""
    hidden_active: list = []
    latest_inactive: list = []
    if not exceptions:
        return hidden_active, latest_inactive
    active = DocumentStatus.ACTIVE.value
    top = {(e["_id"]["ns"], e["_id"]["doc"]): e["top"] for e in exceptions}
    rows = Document.get_motor_collection().find(
        {**base, "document_id": {"$in": list({doc for _ns, doc in top})}},
        {"_id": 1, "namespace": 1, "document_id": 1, "version": 1, "status": 1},
    )
    async for row in rows:
        key = (row.get("namespace"), row["document_id"])
        if key not in top:
            continue  # the same id in another namespace, a normal document
        is_latest = row["version"] == top[key]
        if row["status"] == active and not is_latest:
            hidden_active.append(row["_id"])
        elif row["status"] != active and is_latest:
            latest_inactive.append(row["_id"])
    return hidden_active, latest_inactive


# How many documents a "latest version" filter names by id (those whose
# latest row is not simply their one active row). Kept well under the size
# a query document may reach.
MAX_LATEST_EXCEPTIONS = 200_000


class DocumentService:
    """
    Service for document CRUD operations and upsert logic.

    Implements the identity-based upsert pattern:
    - If no active document with same identity_hash exists: create new document
    - If active document exists: deactivate old, create new version

    Includes timing instrumentation for performance analysis.
    """

    # Class-level timing statistics for document creation, in bounded
    # memory (timing_stats).
    _creation_timing: ClassVar[TimingStats] = TimingStats()

    @classmethod
    def get_creation_timing_stats(cls) -> dict[str, Any]:
        """Get aggregated timing statistics for document creation."""
        return {
            "creation_count": cls._creation_timing.runs,
            "stages": cls._creation_timing.stages(),
        }

    @classmethod
    def reset_creation_timing_stats(cls):
        """Reset creation timing statistics."""
        cls._creation_timing = TimingStats()

    @classmethod
    def _record_creation_timing(cls, timing: dict[str, float]):
        """Record timing from a document creation."""
        cls._creation_timing.record(timing)

    def __init__(self):
        self.validation_service = ValidationService()
        # Tracks which (namespace, template_id) pairs have already had
        # relationship-template Mongo indexes ensured this process — saves
        # an idempotent ensure_index call on every subsequent write.
        # Cleared on process restart; ensure_index is itself idempotent.
        self._relationship_indexes_ensured: set[tuple[str, str]] = set()

    # =========================================================================
    # PHASE 2 — RELATIONSHIP-TEMPLATE WRITE-TIME VALIDATION
    # =========================================================================

    async def _validate_as_writer(
        self,
        template_id: str,
        data: dict[str, Any],
        namespace: str,
        *,
        template_version: int | None = None,
        doc_ref_cache: dict | None = None,
        template_override: dict[str, Any] | None = None,
        previous: Any = _UNKNOWN_PREVIOUS,
    ) -> Any:
        """Validate a write (or a dry run of one) as the calling writer.

        A reference into a closed namespace the writer cannot read is
        reported exactly as a missing target (inbound_gate). References the
        document's previous version already held are exempt. When the caller
        knows that version (PATCH, migrate), one enforcing pass. Otherwise the
        previous version is known only once validation has computed the
        identity, so a recording pass runs first, and a second, enforcing
        pass only when it refused something the previous version did not
        hold.
        """
        def run(gate: InboundGate) -> Any:
            return self.validation_service.validate(
                template_id, data, namespace=namespace,
                template_version=template_version, doc_ref_cache=doc_ref_cache,
                template_override=template_override, inbound=gate,
            )

        if previous is not _UNKNOWN_PREVIOUS:
            return await run(InboundGate(namespace, carried=carried_ids(previous)))
        recording = InboundGate(namespace, enforce=False)
        result = await run(recording)
        if not recording.refused:
            return result
        carried: frozenset[str] = frozenset()
        if result.valid and result.identity_values and template_override is None:
            found = await self._latest_version_by_identity(
                namespace, template_id, result.identity_values,
            )
            carried = carried_ids(found)
        if recording.refused <= carried:
            return result
        return await run(InboundGate(namespace, carried=carried))

    async def _check_new_references(
        self, namespace: str, template_id: str, vr: Any,
        placements: dict[str, str] | None = None,
    ) -> None:
        """Apply the cross-namespace reference gates to a create or upsert.

        Only references the document's previous version did not already hold
        are checked. The previous version is looked up only when the first
        check refuses something: most writes reference nothing outside their
        namespace, or only what they may, and pay nothing extra. The lookup
        is the Registry's read-only primary-key lookup, the key the write is
        about to register, so a refused write leaves nothing behind.

        ``placements`` carries a batch's reference namespaces, looked up once
        for the whole batch (ReferenceValidator.place_references).
        """
        kwargs = {
            "document_namespace": namespace,
            "template_namespace": vr.template_namespace or namespace,
            "term_references": vr.term_references,
            "file_references": vr.file_references,
            "document_references": vr.references,
            # What validation already knows (a template's or terminology's
            # own namespace) wins over a Registry lookup, so the gate does not
            # depend on the Registry to place them.
            "placements": {**(placements or {}), **(getattr(vr, "referent_namespaces", None) or {})},
        }
        validator = get_reference_validator()
        try:
            await validator.validate_document_references(**kwargs)
            return
        except ReferenceValidationError:
            if not vr.identity_values:
                raise
            previous = await self._latest_version_by_identity(
                namespace, template_id, vr.identity_values,
            )
            if previous is None:
                raise
        await validator.validate_document_references(**kwargs, previous=previous)

    async def _latest_version_by_identity(
        self, namespace: str, template_id: str, identity_values: dict[str, Any],
    ) -> Document | None:
        """The latest stored version of the document with this identity, if any."""
        from wip_auth.composite_key import compute_composite_key_hash

        # A Registry that cannot answer raises RegistryUnavailableError here.
        document_id = await get_registry_client().lookup_document_by_primary_key(
            namespace, template_id, compute_composite_key_hash(identity_values),
        )
        if not document_id:
            return None
        return await Document.find_one(
            {"document_id": document_id}, sort=[("version", SortDirection.DESCENDING)],
        )

    @staticmethod
    async def _relationship_template(
        template_id: str, template_version: int | None,
    ) -> dict[str, Any] | None:
        """The template the relationship constraints read. A store that cannot
        answer raises DependencyUnavailableError: the constraints are never
        skipped for an outage. A template it does not serve is None (the
        validation that already ran reported it)."""
        try:
            return await get_template_store_client().get_template(
                template_id=template_id, version=template_version,
            )
        except TemplateStoreError as e:
            if e.unavailable:
                raise dependency_unavailable("template-store", f"template {template_id!r}", e) from e
            return None

    async def _relationship_refusal(
        self, template_id: str, template_version: int, validation_result: Any, namespace: str,
    ) -> tuple[str, str] | None:
        """The relationship constraints for a write to an existing document
        (PATCH, migrate), against the template version it validates under.
        Create and bulk create run the same check inline."""
        template = await self._relationship_template(template_id, template_version)
        if not template:
            return None
        return await self._validate_relationship_constraints(
            template, validation_result, namespace,
        )

    async def _validate_relationship_constraints(
        self,
        template: dict[str, Any],
        validation_result: Any,
        namespace: str,
    ) -> tuple[str, str] | None:
        """Enforce relationship-document constraints beyond reference resolution.

        Phase 2 layer on top of the standard reference validation (which
        already verifies source_ref / target_ref resolve and point at an
        allowed template family). An edge lives in its source's namespace:
        the source_ref document must be in the edge document's namespace.
        The target_ref document may be in any namespace; the reference gates
        that run right after (isolation_mode outbound, inbound_references
        plus the writer's read) decide whether this writer may point there.

        An endpoint that is soft-deleted is accepted: reference validation
        already warns about a non-active target, the same rule a plain
        document reference follows.

        Returns None on success, or (error_code, message) on failure.
        Pre-condition: validation_result.references already contains the
        resolved source_ref / target_ref entries (validation_service
        runs before this).
        """
        if template.get("usage") != "relationship":
            return None

        refs_by_field = {
            r.get("field_path"): r for r in validation_result.references
            if r.get("reference_type") == "document"
        }

        for field in ("source_ref", "target_ref"):
            ref = refs_by_field.get(field)
            if ref is None:
                # Should have been caught by template-store at template
                # create time, but defend anyway.
                return (
                    "validation_failed",
                    f"Relationship template requires field '{field}' (missing in resolved references)",
                )
            resolved = ref.get("resolved") or {}
            ref_namespace = resolved.get("namespace")

            if field == "source_ref" and ref_namespace and ref_namespace != namespace:
                return (
                    "cross_namespace_relationship",
                    f"cross_namespace_relationship: 'source_ref' points to a document in "
                    f"namespace '{ref_namespace}' but the relationship document is in "
                    f"namespace '{namespace}'. An edge lives in its source's namespace: "
                    f"write it in '{ref_namespace}'."
                )

        return None

    async def _ensure_relationship_indexes(
        self,
        template_id: str,
        namespace: str,
    ) -> None:
        """Idempotently create Mongo indexes on data.source_ref / data.target_ref
        for a relationship template. Lazy: runs on first relationship-document
        write per (namespace, template_id), then cached in-process.

        These indexes power the Phase-4 query APIs (/documents/{id}/relationships,
        /traverse). Adding them now keeps that phase's commit small.
        """
        cache_key = (namespace, template_id)
        if cache_key in self._relationship_indexes_ensured:
            return

        try:
            collection = Document.get_motor_collection()
            await collection.create_index(
                [("template_id", 1), ("data.source_ref", 1)],
                name="rel_template_source_ref_idx",
                background=True,
            )
            await collection.create_index(
                [("template_id", 1), ("data.target_ref", 1)],
                name="rel_template_target_ref_idx",
                background=True,
            )
            self._relationship_indexes_ensured.add(cache_key)
            logger.info(
                "Ensured relationship indexes for template %s in namespace %s",
                template_id, namespace,
            )
        except Exception as e:
            # log + continue; missing index slows queries but writes still work
            logger.warning(
                "Failed to ensure relationship indexes for template %s: %s",
                template_id, e,
            )

    async def create_document(
        self,
        request: DocumentCreateRequest,
        namespace: str,
    ) -> tuple[DocumentCreateResponse | None, dict[str, Any] | None]:
        """
        Create or update a document.

        Every document goes through Registry — no exceptions:
        1. Validate document → extract identity_values
        2. Call Registry with identity_values → get (document_id, is_new, identity_hash)
        3. If restore (version provided): create with exact version
        4. If is_new: create document with version=1
        5. If existing: find current version, create new version

        Args:
            request: Document creation request
            namespace: Namespace for the document (required — no default)

        Returns:
            Tuple of (response, error). On failure, error holds the
            per-item result fields — error, error_code and optional details —
            so the caller's error item carries the code.
        """
        timing = {}
        total_start = time.perf_counter()

        # Validate document
        start = time.perf_counter()
        try:
            validation_result = await self._validate_as_writer(
                request.template_id,
                request.data,
                namespace,
                template_version=request.template_version,
            )
        except DependencyUnavailableError as e:
            # The Registry, def-store or template store could not be asked
            # while validating: the item is refused, with the same per-item
            # code the bulk path reports.
            return None, error_fields(e)
        timing["1_validation"] = (time.perf_counter() - start) * 1000

        if not validation_result.valid:
            return None, {
                "error": self._format_validation_errors(validation_result.errors),
                "error_code": "validation_failed",
                "details": {"errors": validation_result.errors},
            }

        # Phase-2 relationship-template constraints. The standard
        # validation_service already verified source_ref / target_ref
        # resolve and point at allowed templates; here we enforce the
        # extra rule that only applies when usage=relationship
        # (same namespace).
        try:
            template = await self._relationship_template(
                request.template_id, request.template_version,
            )
        except DependencyUnavailableError as e:
            return None, error_fields(e)
        if template and template.get("usage") == "relationship":
            rel_error = await self._validate_relationship_constraints(
                template, validation_result, namespace,
            )
            if rel_error:
                code, message = rel_error
                return None, {"error": message, "error_code": code}
            await self._ensure_relationship_indexes(request.template_id, namespace)

        # Validate cross-namespace references (the two gates). Runs after the
        # relationship constraints, so an edge whose source lies in another
        # namespace reports cross_namespace_relationship whatever the
        # settings; its target is gated here like any reference. The
        # template's REAL namespace arms the template-isolation branch — a
        # document may be based on a shared or foreign template, and that use
        # passes the same two gates as any other reference. Passing
        # the document's namespace here made the branch constant-false for its
        # whole life. Falls back to the document's namespace (= skip the
        # template branch) when the template dict carries no namespace.
        try:
            await self._check_new_references(
                namespace, request.template_id, validation_result,
            )
        except ReferenceValidationError as e:
            return None, error_fields(e)
        except RegistryUnavailableError as e:
            # Isolation cannot be checked, so the write is refused rather
            # than let through unchecked.
            return None, error_fields(e)

        # Determine if template has identity fields
        has_identity_fields = bool(validation_result.identity_fields)

        # CASE-430: for relationship/edge types, suppress the bare
        # identity-values synonym ({source_ref, target_ref}) — it omits the
        # template and collides across edge types between the same pair. The
        # template-scoped auto-synonym (register_auto_synonym) still covers
        # restore portability.
        is_relationship = bool(template and template.get("usage") == "relationship")

        # Whether this is a restore (client provides both ID and version)
        version_override = request.version if (request.document_id and request.version is not None) else None

        # Every document goes through Registry — no exceptions.
        # For restore: entry_id=request.document_id tells Registry to use that exact ID.
        # For upsert (document_id but no version): the identity decides, and
        # the submitted id must be the one it resolves to (checked below).
        # For normal: entry_id=None lets Registry generate one.
        restore_entry_id = request.document_id if version_override is not None else None
        start = time.perf_counter()
        try:
            registry = get_registry_client()
            document_id, is_new, identity_hash = await registry.generate_document_id(
                template_id=request.template_id,
                identity_values=validation_result.identity_values or None,
                has_identity_fields=has_identity_fields,
                created_by=get_identity_string(),
                namespace=namespace,
                entry_id=restore_entry_id,
                skip_identity_value_synonym=is_relationship,
                # The raw-values synonym keeps the submitted spellings so
                # business-key references resolve by human value; the hash
                # feed above carries resolved referent IDs instead. It is
                # qualified by the template, so the same spelling under
                # another template is a different synonym.
                synonym_values=qualified_synonym_values(
                    validation_result.template_value,
                    validation_result.submitted_identity_values,
                ),
            )
        except RegistryError as e:
            return None, {"error": f"Failed to generate document ID: {e!s}", "error_code": "registry_error"}
        timing["2_registry"] = (time.perf_counter() - start) * 1000

        mismatch = _document_id_mismatch(request, document_id, is_new)
        if mismatch is not None:
            if is_new:
                await self._release_minted_entry(document_id)
            return None, mismatch

        # Store the registry-returned identity_hash on the validation result
        validation_result.identity_hash = identity_hash

        if version_override is not None:
            # Restore: use the exact version provided
            start = time.perf_counter()
            result = await self._create_new_document(
                request, validation_result, document_id=document_id,
                namespace=namespace, synonyms=request.synonyms,
                version_override=version_override, is_new_entry=is_new,
            )
            timing["3_restore"] = (time.perf_counter() - start) * 1000
        elif is_new:
            # Brand new document
            start = time.perf_counter()
            result = await self._create_new_document(
                request, validation_result, document_id=document_id,
                namespace=namespace, synonyms=request.synonyms,
                is_new_entry=is_new,
            )
            timing["3_create_new"] = (time.perf_counter() - start) * 1000
        else:
            # Existing identity — follow its latest version (active, or
            # soft-deleted, which this write brings back) and write a successor.
            # Use document_id (from Registry) — NOT identity_hash. The Registry is
            # the identity authority; local identity_hash lookups bypass it and can
            # match documents from different templates (CASE-36).
            start = time.perf_counter()
            existing = await self._find_latest_by_document_id(document_id, namespace=namespace)
            if existing:
                result = await self._create_new_version(
                    request, existing, validation_result,
                    document_id=document_id, namespace=namespace
                )
            else:
                # The Registry knows the identity but no document row exists:
                # create it as version 1.
                result = await self._create_new_document(
                    request, validation_result, document_id=document_id,
                    namespace=namespace, synonyms=request.synonyms,
                    is_new_entry=is_new,
                )
            timing["3_create_version"] = (time.perf_counter() - start) * 1000

        timing["total"] = (time.perf_counter() - total_start) * 1000
        self._record_creation_timing(timing)

        return result

    async def _release_minted_entry(self, document_id: str) -> None:
        """Release a Registry entry this request minted and will not use.

        Best effort, like the inline-synonym rollback: a failure is logged and
        the refusal that caused it is what the caller reports.
        """
        try:
            await get_registry_client().hard_delete_entry(
                document_id, rollback_uncommitted=True,
            )
        except (RegistryError, RegistryUnavailableError) as e:
            # A Registry that cannot be reached is no exception: the refusal
            # stands, and the entry stays minted until a later release.
            logger.error("failed to release unused entry %s: %s", document_id, e)

    async def _release_entry_if_minted(self, document_id: str, *, minted: bool) -> None:
        """Release the entry of a document rolled back after its write, when
        this request minted it (with the identity and synonyms it claims); an
        entry the request found already there stays."""
        if minted:
            await self._release_minted_entry(document_id)

    async def _register_inline_synonyms(
        self,
        *,
        document_id: str,
        namespace: str,
        synonyms: list[dict],
        on_synonym_conflict: str,
        is_new_entry: bool,
    ) -> tuple[list[str], str | None, str | None]:
        """Register inline synonyms for a document's registry entry (CASE-434/436).

        Returns (warnings, error_code, error_message):
          - all added / already-owned     -> ([], None, None)
          - on_synonym_conflict == "warn"  -> (warnings, None, None) when the
            Registry refused some synonyms; caller proceeds. A Registry that
            could not answer refuses in either mode.
          - on_synonym_conflict == "fail"  -> ([], code, msg) AFTER rolling back
            the synonyms added in this call (and, if is_new_entry, releasing the
            document's own entry). The caller must then abort without persisting.

        ``already_exists`` is success: re-supplying a synonym the entry already
        owns (idempotent re-create) is not a conflict.
        """
        registry = get_registry_client()
        try:
            results = await registry.add_synonyms(
                entry_id=document_id, namespace=namespace,
                entity_type="documents", synonyms=synonyms,
            )
        except RegistryError as e:
            # The Registry could not answer (unreachable, or an error status):
            # an outage, refused in either mode. "warn" covers only the
            # synonyms it answers it refuses, below.
            await self._rollback_synonym_allocation(document_id, namespace, [], is_new_entry)
            return [], "registry_error", f"Synonym registration failed: {e}"

        added: list[dict] = []
        failures: list[str] = []
        for res in results:
            status = res.get("status")
            # "index" is the platform-canonical per-item key; "input_index"
            # is the registry family's pre-rename spelling, kept as fallback
            # so a mixed-version window (new doc-store, old registry) still
            # maps results back to their inputs.
            idx = res.get("index", res.get("input_index", 0))
            syn = synonyms[idx] if isinstance(idx, int) and 0 <= idx < len(synonyms) else {}
            if status == "added":
                added.append(syn)
            elif status in ("error", "target_not_found"):
                failures.append(res.get("error") or f"synonym {syn} rejected ({status})")

        if not failures:
            return [], None, None

        if on_synonym_conflict == "warn":
            return [f"Synonym not registered: {m}" for m in failures], None, None

        # strict — roll back what landed and abort.
        await self._rollback_synonym_allocation(document_id, namespace, added, is_new_entry)
        return [], "synonym_conflict", "; ".join(failures)

    async def _rollback_synonym_allocation(
        self,
        document_id: str,
        namespace: str,
        added_synonyms: list[dict],
        is_new_entry: bool,
    ) -> None:
        """Best-effort rollback of a failed strict-mode inline-synonym create.

        If this call allocated the document's entry, release that entry
        (CASE-436 rollback_uncommitted — bypasses the deletion_mode gate); the
        hard-delete cascades to its claims and embedded synonyms, so the
        just-added ones go with it. Otherwise (update path) remove only the
        synonyms added in this call. Failures, an unreachable Registry
        included, are logged, never raised, so the original conflict is what
        surfaces. What a failed release leaves (a committed entry or synonyms)
        is not swept by anything: the log line is the record.
        """
        registry = get_registry_client()
        if is_new_entry:
            try:
                await registry.hard_delete_entry(document_id, rollback_uncommitted=True)
                return
            except (RegistryError, RegistryUnavailableError) as e:
                logger.error(
                    "CASE-436: failed to roll back entry %s after synonym conflict: %s",
                    document_id, e,
                )
        if added_synonyms:
            try:
                await registry.remove_synonyms(
                    entry_id=document_id, namespace=namespace,
                    entity_type="documents", synonyms=added_synonyms,
                )
            except (RegistryError, RegistryUnavailableError) as e:
                logger.warning(
                    "CASE-436: failed to remove %d added synonym(s) from %s during "
                    "rollback: %s", len(added_synonyms), document_id, e,
                )

    async def _create_new_document(
        self,
        request: DocumentCreateRequest,
        validation_result: Any,
        document_id: str,
        namespace: str,
        synonyms: list[dict] | None = None,
        version_override: int | None = None,
        is_new_entry: bool = True,
    ) -> tuple[DocumentCreateResponse | None, dict[str, Any] | None]:
        """Create a brand new document with the given stable document_id."""
        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register inline synonyms (CASE-434/436). Strict by default: a refused
        # synonym rolls back (and releases the just-allocated entry) and aborts
        # the create; "warn" creates the doc and surfaces the refusal.
        synonym_warnings: list[str] = []
        if synonyms:
            synonym_warnings, err_code, err_msg = await self._register_inline_synonyms(
                document_id=document_id, namespace=namespace, synonyms=synonyms,
                on_synonym_conflict=request.on_synonym_conflict, is_new_entry=is_new_entry,
            )
            if err_code:
                return None, {"error": f"{err_code}: {err_msg}", "error_code": err_code}

        # Create document
        now = datetime.now(UTC)
        all_warnings = validation_result.warnings + synonym_warnings
        metadata = DocumentMetadata(
            warnings=all_warnings,
            custom=request.metadata or {}
        )

        version = version_override if version_override is not None else 1

        try:
            document = await self._insert_with_retry(
                namespace=namespace,
                document_id=document_id,
                template_id=request.template_id,
                template_version=validation_result.template_version,
                template_value=validation_result.template_value,
                identity_hash=validation_result.identity_hash or "",
                version=version,
                data=request.data,
                term_references=validation_result.term_references,
                references=validation_result.references,
                file_references=validation_result.file_references,
                metadata=metadata,
                actor=actor,
                now=now,
                pinned=version_override is not None,
            )
        except DocumentStoreError as e:
            return None, error_fields(e)

        # Register auto-synonym for migration portability (version 1 only).
        # On failure, roll back the MongoDB document and the Registry entry
        # this request minted (with the inline synonyms it carries), and
        # refuse the item with registry_error, as the bulk path does.
        if version == 1 and not version_override:
            try:
                registry = get_registry_client()
                await registry.register_auto_synonym(
                    document_id=document_id,
                    namespace=namespace,
                    template_value=validation_result.template_value or "",
                    identity_hash=validation_result.identity_hash,
                    has_identity_fields=bool(validation_result.identity_hash),
                    created_by=actor,
                )
            except RegistryError as e:
                logger.error(
                    "Auto-synonym registration failed for document %s — rolling back",
                    document_id,
                )
                await document.delete()
                await self._release_entry_if_minted(document_id, minted=is_new_entry)
                return None, error_fields(e)

        # Publish document created event
        await publish_document_event(
            EventType.DOCUMENT_CREATED,
            await self._document_to_event_payload(document),
            changed_by=actor
        )

        # Update file reference counts (increment for new document)
        await self._update_file_reference_counts(
            validation_result.file_references, delta=1
        )

        return DocumentCreateResponse(
            document_id=document_id,
            namespace=namespace,
            template_id=request.template_id,
            template_value=validation_result.template_value,
            identity_hash=validation_result.identity_hash or "",
            version=document.version,
            template_version=document.template_version,
            is_new=document.version == 1,
            previous_version=None if document.version == 1 else document.version - 1,
            warnings=all_warnings
        ), None

    def _document_has_changed(
        self,
        existing: Document,
        new_data: dict[str, Any],
        new_term_references: list[dict[str, Any]],
        new_references: list[dict[str, Any]],
        new_file_references: list[dict[str, Any]] | None = None,
        new_metadata_custom: dict[str, Any] | None = None,
    ) -> bool:
        """
        Check if any versioned part of the document has changed.

        Compares data, term_references, references, file_references, and
        metadata.custom to determine if a new version should be created.
        Metadata is non-identity document content: it never feeds the
        identity hash (it cannot create or dedup a document), but a change
        to it is a change to the document and versions like any other.

        ``new_file_references`` and ``new_metadata_custom`` are compared
        only when not None — None means the caller did not address that
        part (no opinion), not "clear it". An explicitly supplied empty
        dict/list DOES compare, so callers can clear metadata on purpose.
        """
        import json

        # Compare data (use JSON serialization for consistent comparison)
        existing_data_json = json.dumps(existing.data, sort_keys=True, default=str)
        new_data_json = json.dumps(new_data, sort_keys=True, default=str)

        if existing_data_json != new_data_json:
            return True

        # Compare term_references
        existing_term_refs_json = json.dumps(existing.term_references, sort_keys=True, default=str)
        new_term_refs_json = json.dumps(new_term_references, sort_keys=True, default=str)

        if existing_term_refs_json != new_term_refs_json:
            return True

        # Compare references
        existing_refs_json = json.dumps(existing.references, sort_keys=True, default=str)
        new_refs_json = json.dumps(new_references, sort_keys=True, default=str)

        if existing_refs_json != new_refs_json:
            return True

        # Compare file_references
        if new_file_references is not None:
            existing_file_refs_json = json.dumps(existing.file_references, sort_keys=True, default=str)
            new_file_refs_json = json.dumps(new_file_references, sort_keys=True, default=str)

            if existing_file_refs_json != new_file_refs_json:
                return True

        # Compare metadata.custom (caller-supplied only; platform-owned
        # metadata fields like warnings/source_system never drive versioning)
        if new_metadata_custom is not None:
            existing_custom = existing.metadata.custom if existing.metadata else {}
            existing_custom_json = json.dumps(existing_custom, sort_keys=True, default=str)
            new_custom_json = json.dumps(new_metadata_custom, sort_keys=True, default=str)

            if existing_custom_json != new_custom_json:
                return True

        return False

    @staticmethod
    def _new_document(
        *,
        namespace: str,
        document_id: str,
        template_id: str,
        template_version: int,
        template_value: str,
        identity_hash: str,
        version: int,
        data: dict,
        term_references: list,
        references: list,
        file_references: list,
        metadata: DocumentMetadata,
        actor: str,
        now: datetime,
        entity_created_at: datetime | None = None,
    ) -> "Document":
        """A new active document row, as every write path builds it."""
        return Document(
            namespace=namespace,
            document_id=document_id,
            template_id=template_id,
            template_version=template_version,
            template_value=template_value,
            identity_hash=identity_hash or "",
            version=version,
            data=data,
            term_references=term_references,
            references=references,
            file_references=file_references,
            status=DocumentStatus.ACTIVE,
            created_at=entity_created_at or now,
            created_by=actor,
            updated_at=now,
            updated_by=actor,
            metadata=metadata,
        )

    async def _reactivate_if_orphaned(
        self,
        namespace: str,
        document_id: str,
        prior: Mapping[Any, tuple[datetime | None, str | None]] | None = None,
    ) -> bool:
        """Give a document its active version back after a failed successor write.

        A new version is written by retiring the active one first and then
        inserting its successor, so an entity never has two active rows. When
        the insert fails, the entity is left with none, which reads as a
        retired document. This reactivates its highest stored version, which
        is either the version the write retired or a successor that did land
        inactive behind it. ``prior`` maps each row the write took from active
        to inactive to the (updated_at, updated_by) it had before the write
        stamped it, and those are restored with its status. A document none
        of whose rows is in ``prior`` was not active before the write (a
        revival of a deleted document), and is left as it is. True when a row
        was reactivated.
        """
        coll = Document.get_motor_collection()
        if prior is not None and not await coll.find_one(
            {"_id": {"$in": list(prior)}, "namespace": namespace, "document_id": document_id},
            {"_id": 1},
        ):
            return False
        if await coll.find_one(
            {"namespace": namespace, "document_id": document_id,
             "status": DocumentStatus.ACTIVE.value},
            {"_id": 1},
        ):
            return False
        latest = await coll.find_one(
            {"namespace": namespace, "document_id": document_id},
            {"_id": 1, "version": 1, "status": 1, "updated_at": 1, "updated_by": 1},
            sort=[("version", -1)],
        )
        if latest is None:
            return False
        update: dict[str, Any] = {"status": DocumentStatus.ACTIVE.value}
        stamp = (prior or {}).get(latest["_id"])
        if stamp is not None:
            update["updated_at"], update["updated_by"] = stamp
        result = await coll.update_one(
            {"_id": latest["_id"], "status": {"$ne": DocumentStatus.ACTIVE.value}},
            {"$set": update},
        )
        if result.modified_count == 0:
            return False
        if await self._another_active_row(namespace, document_id, latest["_id"]):
            # A concurrent write gave the document an active version between
            # the check above and this repair: the repair steps aside, so the
            # document keeps one active row, the one that write made.
            await coll.update_one(
                {"_id": latest["_id"], "status": DocumentStatus.ACTIVE.value},
                {"$set": {
                    "status": latest.get("status"),
                    "updated_at": latest.get("updated_at"),
                    "updated_by": latest.get("updated_by"),
                }},
            )
            logger.warning(
                "Document %s/%s: a concurrent write made a version active while a "
                "failed write was repaired; the repair of version %s was undone",
                namespace, document_id, latest.get("version"),
            )
            return False
        logger.warning(
            "Document %s/%s had no active version after a failed write; "
            "reactivated version %s", namespace, document_id, latest.get("version"),
        )
        return True

    @staticmethod
    async def _another_active_row(namespace: str, document_id: str, row_id: Any) -> bool:
        """Whether the document has an active row other than ``row_id``."""
        return await Document.get_motor_collection().find_one(
            {"namespace": namespace, "document_id": document_id,
             "status": DocumentStatus.ACTIVE.value, "_id": {"$ne": row_id}},
            {"_id": 1},
        ) is not None

    async def _insert_or_restore(
        self,
        prior: Mapping[Any, tuple[datetime | None, str | None]] | None,
        **insert: Any,
    ) -> "Document":
        """_insert_with_retry for a successor version whose predecessor this
        write has retired (``prior``: its _id and original stamps). If the
        insert fails, the document gets its active version back
        (_reactivate_if_orphaned) and the failure is raised as before. With
        ``prior`` None nothing was retired, and nothing is repaired."""
        try:
            return await self._insert_with_retry(**insert)
        except Exception:
            if prior is not None:
                await self._reactivate_if_orphaned(
                    insert["namespace"], insert["document_id"], prior,
                )
            raise

    async def _insert_with_retry(
        self,
        namespace: str,
        document_id: str,
        template_id: str,
        template_version: int,
        template_value: str,
        identity_hash: str,
        version: int,
        data: dict,
        term_references: list,
        references: list,
        file_references: list,
        metadata: DocumentMetadata,
        actor: str,
        now: datetime,
        max_retries: int = 3,
        entity_created_at: datetime | None = None,
        pinned: bool = False,
    ) -> "Document":
        """Insert a document, retrying on duplicate key errors.

        ``entity_created_at`` carries the entity's original creation time onto a
        successor version. Each version is its own row, so writing ``now`` into
        ``created_at`` made a document appear to be created again on every edit —
        the field drifted forward and became indistinguishable from
        ``updated_at``. Callers writing version N+1 pass the existing document's
        ``created_at``; callers writing version 1 leave it None and get ``now``.
        (``versioned: false`` templates never came here for updates — they mutate
        the original row in place — so they always had the correct semantics;
        this makes versioned templates agree with them.)

        When two concurrent requests (or duplicate identity hashes in a batch)
        race to create the same version, the loser gets a DuplicateKeyError.
        This method catches that, re-queries the latest version, deactivates
        it, and retries with an incremented version number.

        ``pinned`` marks a version the caller chose (a restore or migration
        writing an exact ``document_id`` + ``version``). That slot is the whole
        request, so a duplicate key refuses the write with ``already_exists``
        instead: bumping it would write a version nobody asked for and retire
        the document's current head to make room.
        """
        from pymongo.errors import DuplicateKeyError

        logger = logging.getLogger(__name__)

        for attempt in range(max_retries):
            document = self._new_document(
                namespace=namespace,
                document_id=document_id,
                template_id=template_id,
                template_version=template_version,
                template_value=template_value,
                identity_hash=identity_hash,
                version=version,
                data=data,
                term_references=term_references,
                references=references,
                file_references=file_references,
                metadata=metadata,
                actor=actor,
                now=now,
                entity_created_at=entity_created_at,
            )
            try:
                await document.insert()
                return document
            except DuplicateKeyError as exc:
                if pinned:
                    raise DocumentStoreError(
                        f"Document '{document_id}' already has version {version}; "
                        "a pinned version is written only into a free slot",
                        "already_exists",
                        rule="version_taken",
                    ) from exc
                if attempt >= max_retries - 1:
                    raise DocumentStoreError(
                        f"Failed after {max_retries} retries due to concurrent "
                        f"writers of document '{document_id}'",
                        "concurrency_conflict",
                    ) from exc
                # Re-query the latest version and retry
                logger.info(
                    "DuplicateKeyError on %s v%d, retrying (attempt %d/%d)",
                    document_id, version, attempt + 1, max_retries,
                )
                latest = await Document.find(
                    {"namespace": namespace, "document_id": document_id}
                ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
                if latest:
                    # Deactivate the current active version if it's not ours
                    if latest[0].status == DocumentStatus.ACTIVE:
                        latest[0].status = DocumentStatus.INACTIVE
                        latest[0].updated_at = now
                        latest[0].updated_by = actor
                        await latest[0].save()
                    version = latest[0].version + 1
                else:
                    version += 1
        raise RuntimeError(f"_insert_with_retry exhausted {max_retries} retries without returning or raising")

    async def _is_template_versioned(self, template_id: str) -> bool:
        """Read template.versioned. Defaults to True (legacy v1.x behaviour)
        on any fetch failure so a degraded template-store never silently
        flips a template to overwrite-in-place semantics."""
        try:
            template = await get_template_store_client().get_template(template_id=template_id)
        except Exception:
            return True
        if not template:
            return True
        # Field is optional in stored documents from before Phase 1; default True.
        return bool(template.get("versioned", True))

    async def _create_new_version(
        self,
        request: DocumentCreateRequest,
        existing: Document,
        validation_result: Any,
        document_id: str,
        namespace: str,
        force_new_version: bool = False,
    ) -> tuple[DocumentCreateResponse | None, dict[str, Any] | None]:
        """Create a new version of an existing document with stable document_id.

        For templates with versioned=False, overwrites the existing
        document in place instead of creating a new version. Same
        document_id, same version number, just newer data/timestamps.

        When ``force_new_version`` is True the unchanged short-circuit is
        bypassed: a new version (or in-place re-pin) is always written even if
        the document is byte-identical. This is the template-version migrate path
        (CASE-491) — the *intent* is to re-pin to a different ``template_version``,
        which the change check (`_document_has_changed`) deliberately
        ignores so it doesn't disturb ordinary create dedup.
        """
        # CASE-434/436 sibling fix: the update path used to ignore inline
        # synonyms entirely. Process them under the same mode, against the
        # EXISTING entry (is_new_entry=False — never release a live doc's entry;
        # rollback removes only the synonyms added in this call).
        synonym_warnings: list[str] = []
        if request.synonyms:
            synonym_warnings, err_code, err_msg = await self._register_inline_synonyms(
                document_id=document_id, namespace=namespace, synonyms=request.synonyms,
                on_synonym_conflict=request.on_synonym_conflict, is_new_entry=False,
            )
            if err_code:
                return None, {"error": f"{err_code}: {err_msg}", "error_code": err_code}

        # Check if the document has actually changed. The migrate path
        # (force_new_version) skips this gate: re-pinning to a new
        # template_version is a real change even when the bytes are identical
        # (the typical migration). metadata=None means the caller did not
        # address metadata — it is not compared and the existing metadata
        # carries forward on any write below.
        revival = self._is_revival(existing)
        if not force_new_version and not revival and not self._document_has_changed(
            existing,
            request.data,
            validation_result.term_references,
            validation_result.references,
            validation_result.file_references,
            new_metadata_custom=request.metadata,
        ):
            # No change - return existing document info without creating new version
            return DocumentCreateResponse(
                document_id=existing.document_id,
                namespace=namespace,
                template_id=existing.template_id,
                template_value=existing.template_value,
                identity_hash=existing.identity_hash,
                version=existing.version,
                template_version=existing.template_version,
                revision=current_revision(existing),
                is_new=False,
                previous_version=None,  # No previous version because nothing changed
                warnings=validation_result.warnings + synonym_warnings
            ), None

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # A revival is recorded in the Registry before anything is written:
        # one it cannot record is not written.
        try:
            lifecycle_warnings = await self._record_revival(document_id, revival, actor)
        except (DocumentStoreError, RegistryUnavailableError) as e:
            return None, error_fields(e)
        try:
            written = await self._write_new_version(
                request, existing, validation_result, document_id, namespace,
                synonym_warnings, actor, lifecycle_warnings,
            )
        except BaseException as exc:
            await self._undo_revival(document_id, revival, namespace, actor)
            if isinstance(exc, DocumentStoreError):
                # A lost write race (concurrency_conflict) is this item's
                # answer, as on the bulk path, not a 500.
                return None, error_fields(exc)
            raise
        if not isinstance(written, Document):
            # Overwritten in place (a versioned=false template): done.
            if written[1] is not None:
                await self._undo_revival(document_id, revival, namespace, actor)
            return written
        document = written
        new_version = document.version  # May have been incremented by retry

        # Publish document updated event
        await publish_document_event(
            EventType.DOCUMENT_UPDATED,
            await self._document_to_event_payload(document),
            changed_by=actor
        )

        # Update file reference counts. A deleted predecessor's references
        # were already released by the delete.
        if not revival:
            await self._update_file_reference_counts(existing.file_references, delta=-1)
        await self._update_file_reference_counts(validation_result.file_references, delta=1)

        return DocumentCreateResponse(
            document_id=document_id,
            namespace=namespace,
            template_id=request.template_id,
            template_value=validation_result.template_value,
            identity_hash=validation_result.identity_hash or "",
            version=new_version,
            template_version=validation_result.template_version,
            is_new=False,
            previous_version=existing.version,
            warnings=validation_result.warnings + synonym_warnings + lifecycle_warnings
        ), None

    async def _write_in_place(
        self, doc: Document, read_revision: int | None, read_status: DocumentStatus,
    ) -> bool:
        """Write ``doc`` over its row only if the row still holds the revision
        and the status that were read (revision None: the row has never been
        overwritten), moving the revision on in the same operation. True if
        written; False if another write landed first, in which case ``doc``
        keeps its old revision.

        The status is part of the condition because a soft delete changes it
        without moving the revision: a write that read the row active must
        not land after the delete and bring the row back, while its Registry
        entry stays retired.

        One conditional replace, so it is atomic on a standalone mongod: the
        token a caller holds can only pass for the write it was read for.
        """
        from beanie.exceptions import DocumentNotFound

        new_revision = (read_revision if read_revision is not None else doc.version) + 1
        doc.revision = new_revision
        try:
            await Document.find_one(
                {"_id": doc.id, "revision": read_revision, "status": read_status.value}
            ).replace_one(doc)
        except DocumentNotFound:
            doc.revision = read_revision
            return False
        return True

    @staticmethod
    async def _reread_for_overwrite(existing: Document, first_status: DocumentStatus) -> Document:
        """The row again, for an in-place overwrite's next attempt. Refused
        (concurrency_conflict) when it is gone, or when it was deleted or
        revived meanwhile: the caller decided revive-or-update (and told the
        Registry) from the status it first read."""
        fresh = await Document.get(existing.id)
        if fresh is None:
            raise DocumentStoreError(
                "Document disappeared during an in-place overwrite",
                "concurrency_conflict",
            )
        if fresh.status != first_status:
            raise DocumentStoreError(
                "The document was deleted or revived during an in-place "
                "overwrite; send the write again",
                "concurrency_conflict",
            )
        return fresh

    async def _announce_update(
        self, doc: Document, actor: str,
        deferred_events: list[tuple[EventType, dict]] | None,
    ) -> None:
        """Publish DOCUMENT_UPDATED for an in-place overwrite, or, for a bulk
        create (deferred_events given), add it to the batch's events, so a
        document its auto-synonym stage rolls back is never announced."""
        event = (EventType.DOCUMENT_UPDATED, await self._document_to_event_payload(doc))
        if deferred_events is not None:
            deferred_events.append(event)
        else:
            await publish_document_event(*event, changed_by=actor)

    async def _overwrite_in_place(
        self,
        request: DocumentCreateRequest,
        existing: Document,
        validation_result: Any,
        namespace: str,
        extra_warnings: list[str] | None = None,
        max_retries: int = 3,
        deferred_events: list[tuple[EventType, dict]] | None = None,
    ) -> tuple[DocumentCreateResponse, dict[str, Any] | None]:
        """Phase-3 overwrite: update the existing document's data and
        references without creating a new version. Used when the
        template has versioned=False (e.g., latest-only relationship
        edges). Same document_id, same version number, fresh
        updated_at / updated_by, and the row's revision moves on. File
        reference counts are adjusted for the delta the same way
        _create_new_version does.

        A POST carries no if_match: its payload replaces the row whatever
        it held. The write is still conditional on the revision read, so a
        concurrent write is never lost by accident: the row is re-read and
        the file-reference delta and preserved metadata are taken from what
        it holds now. A PATCH holding the older revision then conflicts.
        """
        actor = get_identity_string()
        all_warnings = validation_result.warnings + (extra_warnings or [])
        first_status = existing.status

        for attempt in range(max_retries):
            if attempt:
                existing = await self._reread_for_overwrite(existing, first_status)
            read_revision = existing.revision
            read_status = existing.status
            now = datetime.now(UTC)

            # A deleted document overwritten in place comes back; its old
            # references were already released by the delete.
            revival = self._is_revival(existing)
            previous_file_refs = [] if revival else list(existing.file_references)

            # metadata=None carries the existing custom forward (caller did not
            # address it); a supplied dict — including {} — replaces wholesale.
            preserved_custom = dict(existing.metadata.custom if existing.metadata else {})
            existing.data = request.data
            existing.term_references = validation_result.term_references
            existing.references = validation_result.references
            existing.file_references = validation_result.file_references
            existing.template_version = validation_result.template_version or existing.template_version
            existing.template_value = validation_result.template_value or existing.template_value
            existing.metadata = DocumentMetadata(
                warnings=all_warnings,
                custom=request.metadata if request.metadata is not None else preserved_custom,
            )
            existing.status = DocumentStatus.ACTIVE
            existing.updated_at = now
            existing.updated_by = actor
            if await self._write_in_place(existing, read_revision, read_status):
                break
        else:
            raise DocumentStoreError(
                f"Failed after {max_retries} retries due to concurrent writers",
                "concurrency_conflict",
            )

        # Publish an updated event so downstream (reporting-sync, NATS
        # subscribers) sees the change. Same event type / payload as
        # _create_new_version — the only observable difference is the
        # version stays at its prior value.
        await self._announce_update(existing, actor, deferred_events)

        # File-ref counts: subtract the old set, add the new set.
        await self._update_file_reference_counts(previous_file_refs, delta=-1)
        await self._update_file_reference_counts(validation_result.file_references, delta=1)

        return DocumentCreateResponse(
            document_id=existing.document_id,
            namespace=namespace,
            template_id=existing.template_id,
            template_value=existing.template_value,
            identity_hash=existing.identity_hash,
            version=existing.version,
            template_version=existing.template_version,
            revision=current_revision(existing),
            is_new=False,
            # No new version was minted — the replaced payload was at this
            # same version, so previous_version == version is the in-place
            # overwrite signature. It also distinguishes this data-changing
            # write (an update) from the data-unchanged no-op, which returns
            # previous_version=None; with None here the API layer cannot
            # tell the two apart and reports a successful overwrite as a
            # no-op.
            previous_version=existing.version,
            warnings=all_warnings,
        ), None

    async def _find_active_by_document_id(
        self,
        document_id: str,
        namespace: str
    ) -> Document | None:
        """Find the active version of a document by its canonical ID.

        Uses the Registry-resolved document_id, not identity_hash. The
        Registry is the identity authority — local identity_hash lookups
        can match across templates and corrupt data (CASE-36).
        """
        return await Document.find_one({
            "namespace": namespace,
            "document_id": document_id,
            "status": DocumentStatus.ACTIVE.value
        })

    async def _find_latest_by_document_id(
        self,
        document_id: str,
        namespace: str
    ) -> Document | None:
        """The latest version of a document, whatever its status.

        A write to an existing identity follows its latest version: the
        active one, or a soft-deleted one, which the write brings back. An
        active-only lookup missed deleted documents, so the write was taken
        for a brand-new document, collided with version 1, and was pushed to
        the next version by the duplicate-key retry, which resets the
        entity's created_at and drops its metadata.
        """
        latest = await Document.find(
            {"namespace": namespace, "document_id": document_id}
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        return latest[0] if latest else None

    @staticmethod
    def _is_revival(existing: Document) -> bool:
        """True when a write to this identity brings a deleted document back.

        A revival is a change even when the data is identical, and the
        deleted version's file references were already released by the
        delete, so they are not released again.
        """
        return existing.status != DocumentStatus.ACTIVE

    async def _write_new_version(
        self,
        request: DocumentCreateRequest,
        existing: Document,
        validation_result,
        document_id: str,
        namespace: str,
        synonym_warnings: list[str],
        actor: str,
        lifecycle_warnings: list[str],
    ) -> "tuple[DocumentCreateResponse | None, dict[str, Any] | None] | Document":
        """Write the single-item update of ``existing``: overwritten in place
        when its template is not versioned (the (response, error) the caller
        returns), else its successor version (the written document)."""
        # Phase 3: templates with versioned=false overwrite in place.
        if not await self._is_template_versioned(request.template_id):
            overwritten, err = await self._overwrite_in_place(
                request, existing, validation_result, namespace,
                extra_warnings=synonym_warnings,
            )
            if err is None:
                overwritten.warnings += lifecycle_warnings
            return overwritten, err

        # Deactivate old version
        prior = _retired_stamps(existing)
        existing.status = DocumentStatus.INACTIVE
        existing.updated_at = datetime.now(UTC)
        existing.updated_by = actor
        await existing.save()

        # Create new version with SAME document_id (stable).
        # metadata=None carries the existing custom metadata forward (the
        # caller did not address it); a supplied dict replaces it wholesale,
        # including an explicit {} to clear. Mirrors the change gate above —
        # None must mean the same thing in the gate and in the write, or a
        # data-only update would silently wipe metadata.
        metadata = DocumentMetadata(
            warnings=validation_result.warnings + synonym_warnings,
            custom=request.metadata if request.metadata is not None
            else dict(existing.metadata.custom if existing.metadata else {}),
        )
        return await self._insert_or_restore(
            prior,
            namespace=namespace,
            document_id=document_id,
            template_id=request.template_id,
            template_version=validation_result.template_version,
            template_value=validation_result.template_value,
            identity_hash=validation_result.identity_hash or "",
            version=existing.version + 1,
            data=request.data,
            term_references=validation_result.term_references,
            references=validation_result.references,
            file_references=validation_result.file_references,
            metadata=metadata,
            actor=actor,
            now=datetime.now(UTC),
            # Successor version: keep the entity's original creation time
            # rather than stamping this write as a new creation (CASE-802).
            entity_created_at=existing.created_at,
        )

    @staticmethod
    async def _record_lifecycle(document_id: str, *, retired: bool, actor: str | None) -> list[str]:
        """Have the Registry record the document as retired (soft delete) or
        revived (a write to its identity) BEFORE the write; returns the
        Registry's warnings (a revived synonym another entity took meanwhile).

        Retiring releases the document's synonyms — the identity-values
        synonym and any caller-added ones — so a different identity with the
        same spellings is no longer refused; reviving claims them again where
        nobody took them meanwhile. A Registry that refuses raises
        DocumentStoreError (registry_error); one that does not answer raises
        RegistryUnavailableError. Either way the caller writes nothing.
        """
        try:
            return await record_entry_lifecycle(
                get_registry_client(), [document_id], retired=retired, actor=actor,
            )
        except LifecycleRefused as e:
            raise DocumentStoreError(f"Not changed — {e}", "registry_error") from e

    async def _record_revival(self, document_id: str, revival: bool, actor: str) -> list[str]:
        """``_record_lifecycle`` for a write that revives, else nothing."""
        if not revival:
            return []
        return await self._record_lifecycle(document_id, retired=False, actor=actor)

    async def _undo_revival(
        self, document_id: str, revival: bool, namespace: str, actor: str,
    ) -> None:
        """Retire the Registry entry again after a revival whose write failed
        — unless the document came back anyway (the write landed and what
        failed came after it), when the Registry is right as it is."""
        if not revival:
            return
        latest = await self._find_latest_by_document_id(document_id, namespace)
        if latest is not None and latest.status == DocumentStatus.ACTIVE:
            return
        await undo_entry_lifecycle(
            get_registry_client(), [document_id], retired=False, actor=actor,
        )

    def _format_validation_errors(
        self,
        errors: list[dict[str, Any]]
    ) -> str:
        """Format validation errors as a string."""
        messages = [e.get("message", "Validation error") for e in errors]
        return "; ".join(messages)

    async def _document_to_event_payload(self, document: Document) -> dict[str, Any]:
        """Convert Document to event payload for NATS publishing.

        For relationship-template documents the payload is enriched
        (Phase 6, see docs/design/document-relationships.md) so external
        subscribers (Snowflake, BigQuery, …) can rebuild the edge
        without an extra round-trip:

          - top-level `template_usage` mirrors the template's usage flag
          - `data.source_ref_resolved` / `data.target_ref_resolved` carry
            the canonical document_id of each endpoint
          - `data.source_template_value` / `data.target_template_value`
            carry the endpoint template's value code (e.g. "EXPERIMENT")

        Resolved values come from the document's own `references` array
        (populated by validation_service at write time) — no extra DB
        hit is needed beyond a single template-store fetch (cached) to
        learn the template's `usage` flag.
        """
        # Build the base payload first; copy data so we don't mutate the
        # underlying Document.
        base_data = dict(document.data) if document.data else {}
        payload: dict[str, Any] = {
            "document_id": document.document_id,
            "namespace": document.namespace,
            "template_id": document.template_id,
            "template_version": document.template_version,
            "template_value": document.template_value,
            "identity_hash": document.identity_hash,
            "version": document.version,
            # The write counter: an in-place overwrite (versioned:false)
            # keeps the version and moves this, so consumers that keep only
            # newer writes compare it, not the version.
            "revision": current_revision(document),
            "data": base_data,
            "term_references": document.term_references,
            "references": document.references,
            "file_references": document.file_references,
            "status": document.status.value if hasattr(document.status, "value") else document.status,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "created_by": document.created_by,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            "updated_by": document.updated_by,
        }

        # Phase 6 — enrich for relationship templates.
        try:
            template = await get_template_store_client().get_template(
                template_id=document.template_id,
                version=document.template_version,
            )
        except Exception as exc:
            # Log loudly so a degraded template-store (or a test mock that
            # doesn't match the real signature) doesn't silently drop the
            # enrichment for relationship docs. Production behaviour stays
            # safe — a missed enrichment is better than a missed event.
            logger.warning(
                "Phase-6 enrichment skipped: get_template(%s) raised %s",
                document.template_id, exc,
            )
            template = None

        usage = (template or {}).get("usage", "entity")
        if usage != "relationship":
            return payload

        payload["template_usage"] = usage
        # Pull resolved endpoint info from the references array. Each
        # entry has shape:
        #   {"field_path", "reference_type", "lookup_value",
        #    "version_strategy", "resolved": {document_id, namespace,
        #    template_value, status, ...}}
        refs_by_field = {
            r.get("field_path"): (r.get("resolved") or {})
            for r in (document.references or [])
            if r.get("reference_type") == "document"
        }
        for field_name in ("source_ref", "target_ref"):
            resolved = refs_by_field.get(field_name) or {}
            doc_id = resolved.get("document_id")
            tpl_value = resolved.get("template_value")
            if doc_id is not None:
                base_data[f"{field_name}_resolved"] = doc_id
            if tpl_value is not None:
                base_data[f"{field_name.replace('_ref', '_template_value')}"] = tpl_value
        return payload

    async def get_document(
        self,
        document_id: str,
        version: int | None = None
    ) -> DocumentResponse | None:
        """Get a document by ID (stable across versions). Returns latest version by default."""
        if version is not None:
            document = await Document.find_one({
                "document_id": document_id, "version": version
            })
        else:
            # Return latest version
            results = await Document.find(
                {"document_id": document_id}
            ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
            document = results[0] if results else None

        if not document:
            return None
        return await self._to_response(document)

    async def get_document_by_identity(
        self,
        identity_hash: str,
        include_inactive: bool = False,
        namespace: str | None = None,
        template_id: str | None = None,
        namespace_filter: dict[str, Any] | None = None,
    ) -> DocumentResponse | None:
        """The latest version of the one document with this identity hash.

        An identity hash is scoped to its template (and namespace), so one
        hash can be several documents when the filters leave those open.
        Several matching documents raise ``DocumentStoreError`` with code
        ``ambiguous`` and the candidates in its details — the endpoint answers
        "which one?" instead of picking. ``namespace_filter`` is the caller's
        readable-namespace clause, so a document the caller cannot read is
        never a candidate. Within the one document, the highest version
        matching the status filter is returned.
        """
        query: dict[str, Any] = {"identity_hash": identity_hash}
        if not include_inactive:
            query["status"] = DocumentStatus.ACTIVE.value
        if namespace_filter:
            query.update(namespace_filter)
        if namespace:
            query["namespace"] = namespace
        if template_id:
            query["template_id"] = template_id

        latest_by_document: dict[str, Document] = {}
        async for row in Document.find(query).sort(
            [("version", SortDirection.DESCENDING)]
        ):
            latest_by_document.setdefault(row.document_id, row)
        if not latest_by_document:
            return None
        if len(latest_by_document) > 1:
            error = DocumentStoreError(
                f"identity hash '{identity_hash}' matches "
                f"{len(latest_by_document)} documents; pass namespace and "
                "template_id to name one",
                "ambiguous",
            )
            error.details = {
                "candidates": [
                    {
                        "document_id": row.document_id,
                        "namespace": row.namespace,
                        "template_id": row.template_id,
                        "template_value": row.template_value,
                    }
                    for row in latest_by_document.values()
                ],
            }
            raise error
        (document,) = latest_by_document.values()
        return await self._to_response(document)

    # =========================================================================
    # PHASE 4 — RELATIONSHIP QUERY APIS
    # =========================================================================

    async def _edge_type_ids(self, query: dict[str, Any]) -> list[str]:
        """The edge types (usage='relationship') among the templates of the
        documents ``query`` matches.

        A source_ref / target_ref field makes nothing an edge on its own: an
        entity template may declare one. The template store says which
        templates are edge types (cached); a template it cannot read is not
        counted as one.
        """
        candidate_ids = await Document.get_motor_collection().distinct("template_id", query)
        client = get_template_store_client()
        edge_ids: list[str] = []
        for template_id in candidate_ids:
            template = await client.get_template_resolved(template_id)
            if template and template.get("usage") == "relationship":
                edge_ids.append(template_id)
        return edge_ids

    async def find_relationships(
        self,
        document_id: str,
        *,
        direction: str = "both",
        template_ids: list[str] | None = None,
        namespace: str | None = None,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 50,
        include_peers: bool = False,
        identity: Any = None,
        allowed_namespaces: list[str] | None = None,
    ) -> RelationshipListResponse:
        """Return relationship documents that point at (incoming) or from
        (outgoing) the given document.

        Backed by the (template_id, data.source_ref) and
        (template_id, data.target_ref) Mongo indexes that Phase 2's
        DocumentService._ensure_relationship_indexes laid down. Only
        documents of edge types (usage='relationship') count: an entity
        template may declare a field named source_ref too. Without a type
        filter the query is narrowed to the edge types among its matches.

        When include_peers=True, each item is decorated with the peer
        entity at the OTHER end of the edge — direction-agnostic, latest
        active version, projected to {document_id, namespace, template_value,
        template_id, status, data: {title?, doc_status?}}. Closes the N+1
        fetch loop the relationships sidebar would otherwise pay (CASE-303).
        Cross-namespace peers respect read permission: a peer in a
        namespace the caller cannot read reports peer_error_code='not_found'
        on that item (as a missing peer does), rather than 403'ing the whole
        request. A Registry that cannot answer the permission check fails
        the request with 503.

        `template_ids` are edge-type template IDs the caller already
        resolved through the Registry.

        An edge lives in its source's namespace, so the edges incident to a
        document may sit in several namespaces. ``namespace`` lists one of
        them; without it the listing spans ``allowed_namespaces`` (the
        namespaces the caller can read; None = unrestricted).
        """
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError(f"direction must be incoming|outgoing|both (got '{direction}')")

        query: dict[str, Any] = {}
        if namespace:
            query["namespace"] = namespace
        elif allowed_namespaces is not None:
            query["namespace"] = {"$in": allowed_namespaces}
        if active_only:
            query["status"] = DocumentStatus.ACTIVE.value

        if direction == "outgoing":
            query["data.source_ref"] = document_id
        elif direction == "incoming":
            query["data.target_ref"] = document_id
        else:
            query["$or"] = [
                {"data.source_ref": document_id},
                {"data.target_ref": document_id},
            ]

        query["template_id"] = {
            "$in": template_ids if template_ids else await self._edge_type_ids(query)
        }

        total = await Document.find(query).count()
        skip = (page - 1) * page_size
        documents = await Document.find(query).sort(
            [("created_at", SortDirection.DESCENDING)]
        ).skip(skip).limit(page_size).to_list()
        edge_items = await self._batch_to_responses(documents)

        items: list[RelationshipItem] = [
            RelationshipItem(**item.model_dump()) for item in edge_items
        ]

        if include_peers:
            await self._decorate_with_peers(items, document_id, identity)

        pages = math.ceil(total / page_size) if total else 0
        return RelationshipListResponse(
            items=items, total=total, page=page, page_size=page_size, pages=pages,
        )

    async def _decorate_with_peers(
        self,
        items: list[RelationshipItem],
        seed_document_id: str,
        identity: Any,
    ) -> None:
        """Populate peer / peer_error_code on each item in place (CASE-303).

        Strategy:
          1. Collect peer document_ids (the OTHER end of each edge — direction-
             agnostic; self-loops resolve to the seed itself).
          2. Single batched lookup against the documents collection: latest
             active (or inactive — PoNIF #1: inactive peers still resolve)
             version of each peer document_id.
          3. For each edge, attach peer or peer_error_code per the lookup +
             namespace permission check.

        Permission policy: caller already has read on the seed namespace
        (checked at API layer). A peer in a namespace the caller cannot read
        reports exactly like a missing one (peer_error_code=not_found, no
        namespace named): the edge carries the peer's id, nothing else of a
        namespace the caller cannot read is shown.
        """
        if not items:
            return

        peer_ids = _peer_ids(items, seed_document_id)
        if not peer_ids:
            return

        peer_lookup = await _latest_peer_documents(peer_ids)
        projection_paths_by_tid = await _projection_paths_by_template(
            {doc.template_id for doc in peer_lookup.values() if doc.template_id}
        )

        # Permission cache per namespace — avoid re-checking same ns N times.
        ns_permission_cache: dict[str, str] = {}
        for item in items:
            peer_id = _peer_id(item, seed_document_id)
            if not peer_id:
                # Edge with no resolvable peer field — leave peer=None silently.
                continue

            peer_doc = peer_lookup.get(peer_id)
            if peer_doc is None or not await _can_read_namespace(
                identity, peer_doc.namespace, ns_permission_cache,
            ):
                item.peer_error_code = "not_found"
                item.peer_error = f"Peer document '{peer_id}' not found"
                continue

            item.peer = _project_peer(
                peer_doc,
                projection_paths_by_tid.get(peer_doc.template_id, ["title", "doc_status"]),
            )

    @staticmethod
    async def _latest_rows(document_ids: list[str]) -> dict[str, "Document"]:
        """Each document's latest row (active first), in one query per chunk."""
        out: dict[str, Document] = {}
        for first in range(0, len(document_ids), 1000):
            chunk = document_ids[first:first + 1000]
            rows = await Document.find({"document_id": {"$in": chunk}}).sort(
                [("version", SortDirection.DESCENDING)]
            ).to_list()
            for row in rows:
                held = out.get(row.document_id)
                if held is None or (
                    held.status != DocumentStatus.ACTIVE and row.status == DocumentStatus.ACTIVE
                ):
                    out[row.document_id] = row
        return out

    async def traverse_relationships(
        self,
        document_id: str,
        *,
        depth: int = 1,
        type_ids: list[str] | None = None,
        types_filter: list[str] | None = None,
        direction: str = "outgoing",
        namespace: str | None = None,
        max_nodes: int = 1000,
        allowed_namespaces: list[str] | None = None,
    ) -> TraverseResponse:
        """N-hop BFS expansion from a seed document, walking through
        relationship documents.

        At each hop we find relationship docs touching the current
        frontier (via data.source_ref for outgoing, data.target_ref for
        incoming), then add the *other* endpoint document_ids of those
        relationship docs to the next frontier. Visited docs are
        skipped — cycles terminate naturally. Capped at depth=10 and
        max_nodes (default 1000) to keep the cost bounded: a hop stops
        reading edges once the node cap is reached, and the endpoint
        documents of a hop are read in one query. Only documents of edge
        types (usage='relationship') are edges.

        Returns nodes only (a flat list with depth + path). Edges are
        implicit in `path` and `via_relationship`. Callers wanting the
        edges as well can issue follow-up /relationships queries on the
        seed.

        `type_ids` are edge-type template IDs the caller already resolved
        through the Registry; `types_filter` is the submitted spelling,
        echoed back in the response only.

        Edges live in their source's namespace, so a walk may cross
        namespaces. ``namespace`` pins every hop to one namespace; without it
        each hop searches ``allowed_namespaces`` (the caller's readable
        namespaces; None = unrestricted). A node in a namespace the caller
        cannot read is neither returned nor expanded: the walk stops there.
        """
        ns = await _traverse_seed_namespace(document_id, direction, depth, namespace)

        visited: set[str] = {document_id}
        path_by_id: dict[str, list[str]] = {document_id: []}
        nodes: list[TraverseNode] = []
        frontier: set[str] = {document_id}
        truncated = False

        for current_depth in range(1, depth + 1):
            if not frontier:
                break
            hop_query = _hop_query(frontier, direction, namespace, allowed_namespaces)
            hop_query["template_id"] = {
                "$in": type_ids if type_ids else await self._edge_type_ids(hop_query)
            }
            found, truncated = await _collect_hop(
                hop_query, frontier, direction, visited, max_nodes - len(nodes),
            )
            endpoint_docs = await self._latest_rows([other for other, _, _ in found])
            frontier = _record_hop(
                found, endpoint_docs, nodes, visited, path_by_id,
                depth=current_depth, fallback_namespace=ns,
                namespace=namespace, allowed_namespaces=allowed_namespaces,
            )
            if truncated:
                break

        return TraverseResponse(
            seed_document_id=document_id,
            direction=direction,
            depth=depth,
            types_filter=list(types_filter) if types_filter else [],
            nodes=nodes,
            total_nodes=len(nodes),
            truncated=truncated,
        )

    async def list_documents(
        self,
        template_id: str | None = None,
        template_value: str | None = None,
        status: DocumentStatus | None = None,
        page: int = 1,
        page_size: int = 20,
        latest_only: bool = False,
        template_version: int | None = None,
        cursor: str | None = None,
        ns_filter: dict | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> DocumentListResponse:
        """List documents with pagination.

        When latest_only=True, uses aggregation to return only the highest
        version of each document_id (like template-store's latest_only).

        When cursor is provided, uses cursor-based pagination (more efficient
        for deep pages). The cursor is the MongoDB _id of the last item from
        the previous page, or CURSOR_START for the first page. When using
        cursor, skip/total count are avoided.
        Cursor mode is _id-ordered by construction; sort_by is incompatible.

        sort_by / sort_order honour the same allow-list as POST /documents/query
        (created_at, updated_at, version, or data.<path>). Default is
        created_at DESC for stable offset pagination. CASE-304: a deterministic
        document_id ASC tiebreaker is always appended so identical primary-sort
        values do not scramble across page boundaries.
        """
        from bson import ObjectId

        query: dict = {}
        if ns_filter:
            query.update(ns_filter)
        if template_id:
            query["template_id"] = template_id
        if template_value:
            query["template_value"] = template_value
        if template_version is not None:
            query["template_version"] = template_version
        if latest_only:
            if cursor:
                raise ValueError(
                    "cursor is incompatible with latest_only (latest_only pages by "
                    "page/page_size over the grouped result)."
                )
            # Each document's LATEST version, then the status filter on that
            # row — rewritten as a plain filter so the page and count below
            # run as ordinary indexed finds.
            query = await self._latest_versions_query(query, status)
        elif status:
            query["status"] = status.value
        name_every_status(query)

        if cursor:
            if sort_by is not None or sort_order is not None:
                raise ValueError(
                    "sort_by/sort_order are incompatible with cursor pagination "
                    "(cursor mode is _id-ordered by construction)."
                )
            # Cursor-based pagination: use _id > cursor, sorted by _id ASC.
            # CURSOR_START opens it at the beginning: a cursor is otherwise
            # only ever handed out by a cursor-mode page.
            if cursor != CURSOR_START:
                try:
                    cursor_oid = ObjectId(cursor)
                except Exception as e:
                    raise ValueError(f"Invalid cursor: {cursor}") from e
                query["_id"] = {"$gt": cursor_oid}

            # No total count for cursor mode (expensive and unnecessary)
            total = -1

            documents = await Document.find(query).sort(
                [("_id", SortDirection.ASCENDING)]
            ).limit(page_size).to_list()
        else:
            sort_clauses = build_sort_clauses(sort_by, sort_order)
            # Count total
            total = await Document.find(query).count()

            # Fetch page — sort + tiebreaker for stable offset pagination
            skip = (page - 1) * page_size
            documents = await Document.find(query).sort(
                sort_clauses
            ).skip(skip).limit(page_size).to_list()

        # Convert to responses in batch (single aggregation instead of N queries)
        items = await self._batch_to_responses(documents)

        # Compute next_cursor from the last document's MongoDB _id
        next_cursor: str | None = None
        if cursor is not None and len(documents) == page_size:
            last_doc = documents[-1]
            if hasattr(last_doc, 'id') and last_doc.id is not None:
                next_cursor = str(last_doc.id)

        return DocumentListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if total > 0 else 0,
            next_cursor=next_cursor,
        )

    async def get_document_versions(
        self,
        document_id: str
    ) -> DocumentVersionResponse | None:
        """Get all versions of a document by its stable document_id."""
        # document_id is stable — query directly
        versions = await Document.find(
            {"document_id": document_id}
        ).sort([("version", SortDirection.DESCENDING)]).to_list()

        if not versions:
            return None

        return DocumentVersionResponse(
            identity_hash=versions[0].identity_hash,
            current_version=max(v.version for v in versions),
            versions=[
                DocumentVersionSummary(
                    document_id=v.document_id,
                    version=v.version,
                    revision=current_revision(v),
                    status=v.status,
                    created_at=v.created_at,
                    created_by=v.created_by,
                    updated_at=v.updated_at,
                    updated_by=v.updated_by,
                )
                for v in versions
            ]
        )

    async def get_document_version(
        self,
        document_id: str,
        version: int
    ) -> DocumentResponse | None:
        """Get a specific version of a document by stable document_id."""
        version_doc = await Document.find_one({
            "document_id": document_id,
            "version": version
        })

        if not version_doc:
            return None

        return await self._to_response(version_doc)

    async def get_latest_document(
        self,
        document_id: str
    ) -> DocumentResponse | None:
        """
        Get the latest version of a document.

        With stable IDs, document_id is the same across all versions,
        so this just returns the highest version.

        Args:
            document_id: The stable document ID

        Returns:
            The latest version of the document, or None if not found
        """
        latest = await Document.find(
            {"document_id": document_id}
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()

        if not latest:
            return None

        return await self._to_response(latest[0])

    async def delete_document(
        self,
        document_id: str,
        deleted_by: str | None = None,  # Deprecated: uses authenticated identity
        hard_delete: bool = False,
        version: int | None = None,
        warnings: list[str] | None = None,
        force: bool = False,
        deleted_with: frozenset[str] = frozenset(),
        broken_references: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Delete a document. Soft-delete by default, hard-delete if requested
        and namespace deletion_mode is 'full'.

        A hard delete is refused while other documents, in any namespace and
        of any version or status, still reference what it removes: their
        references would stop resolving. ``force`` deletes anyway.

        Args:
            document_id: Document to delete
            deleted_by: Deprecated - uses authenticated identity
            hard_delete: Permanently remove (requires namespace deletion_mode='full')
            version: Specific version to hard-delete (None = all). Ignored for soft-delete.
            warnings: When given, receives the warnings of a soft delete whose
                retirement did not fully reach the Registry, and of a forced
                hard delete that broke references.
            force: Hard-delete even while other documents reference it.
            deleted_with: Documents hard-deleted (all versions) in the same
                request; their references do not block this delete.
            broken_references: When given, receives the referrers a forced
                hard delete left without a target.
        """
        # Use authenticated identity (not client-provided)
        actor = get_identity_string()
        if hard_delete:
            return await self._hard_delete_document(
                document_id, actor, version=version, warnings=warnings, force=force,
                deleted_with=deleted_with, broken_references=broken_references,
            )
        return await self._soft_delete_document(document_id, actor, warnings)

    async def _soft_delete_document(
        self, document_id: str, actor: str, warnings: list[str] | None,
    ) -> bool:
        """Retire the document's active version; False when it has none."""
        results = await Document.find(
            {"document_id": document_id, "status": DocumentStatus.ACTIVE.value}
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        if not results:
            return False
        document = results[0]

        # The retirement is recorded in the Registry first: one it cannot
        # record is not written.
        lifecycle_warnings = await self._record_lifecycle(document_id, retired=True, actor=actor)
        try:
            retired = await self._retire_row(document, actor)
        except BaseException:
            await undo_entry_lifecycle(
                get_registry_client(), [document_id], retired=True, actor=actor,
            )
            raise
        if retired is None:
            # Retired by a concurrent delete between the read and this write;
            # the Registry entry is retired either way.
            return False
        document = retired
        if warnings is not None:
            warnings.extend(lifecycle_warnings)

        # Publish document deleted event
        await publish_document_event(
            EventType.DOCUMENT_DELETED,
            await self._document_to_event_payload(document),
            changed_by=actor
        )

        # Update file reference counts (decrement for deleted document)
        await self._update_file_reference_counts(
            document.file_references, delta=-1
        )
        return True

    @staticmethod
    async def _retire_row(document: Document, actor: str) -> Document | None:
        """Set the row inactive as it is now, not as it was read: only the
        status fields are written, so an in-place write that landed after the
        read keeps its data, and the row returned (what the event and the
        file-reference counts use) is the one retired. None when the row was
        no longer active."""
        from pymongo import ReturnDocument

        raw = await Document.get_motor_collection().find_one_and_update(
            {"_id": document.id, "status": DocumentStatus.ACTIVE.value},
            {"$set": {"status": DocumentStatus.INACTIVE.value,
                      "updated_at": datetime.now(UTC), "updated_by": actor}},
            return_document=ReturnDocument.AFTER,
        )
        return None if raw is None else Document.model_validate(raw)

    async def _hard_delete_document(
        self,
        document_id: str,
        actor: str,
        *,
        version: int | None,
        warnings: list[str] | None,
        force: bool,
        deleted_with: frozenset[str],
        broken_references: list[dict[str, Any]] | None,
    ) -> bool:
        """Remove one version, or every version, for good; False when there
        is nothing to remove."""
        # Find any version to get namespace
        any_doc = await Document.find_one({"document_id": document_id})
        if not any_doc:
            return False

        client = get_registry_client()
        deletion_mode = await client.get_namespace_deletion_mode(any_doc.namespace)
        if deletion_mode != "full":
            raise DocumentStoreError(
                f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')",
                "invalid_state", rule="deletion_mode_retain",
            )

        target = None
        if version is not None:
            target = await Document.find_one({"document_id": document_id, "version": version})
            if not target:
                return False
        # Removing the last remaining version removes the document.
        whole = version is None or await Document.find({"document_id": document_id}).count() == 1
        await self._refuse_or_note_referrers(
            document_id, version, whole=whole, force=force, deleted_with=deleted_with,
            warnings=warnings, broken_references=broken_references,
        )

        # A document holds the file references of its latest version while
        # that version is active: superseding a version releases its set,
        # and a soft delete releases the active one. A hard delete releases
        # only what the document still holds; releasing every removed
        # row's set again took counts other documents hold, and deleted
        # files they still reference.
        latest = await Document.find({"document_id": document_id}).sort(
            [("version", SortDirection.DESCENDING)]
        ).limit(1).to_list()
        held = latest[0] if latest and latest[0].status == DocumentStatus.ACTIVE else None

        if target is not None:
            event_payload = await self._hard_delete_version(
                document_id, target, held, client, actor, warnings,
            )
        else:
            event_payload = await self._hard_delete_all_versions(
                document_id, any_doc, held, client, actor, warnings,
            )

        # Publish document deleted event
        await publish_document_event(
            EventType.DOCUMENT_DELETED,
            event_payload,
            changed_by=actor,
        )
        return True

    async def _refuse_or_note_referrers(
        self,
        document_id: str,
        version: int | None,
        *,
        whole: bool,
        force: bool,
        deleted_with: frozenset[str],
        warnings: list[str] | None,
        broken_references: list[dict[str, Any]] | None,
    ) -> None:
        """A hard delete is refused while other documents reference what it
        removes; with force it goes ahead, and says what it broke."""
        referrers = await self._hard_delete_referrers(
            document_id, None if whole else version, deleted_with,
        )
        if not referrers:
            return
        total = sum(group["documents"] for group in referrers)
        what = "document" if whole else f"version {version} of document"
        if not force:
            error = DocumentStoreError(
                f"Hard-delete refused: {total} other document(s) reference this "
                f"{what}, and their references would stop resolving. "
                "Pass force=true to delete anyway.",
                "invalid_state", rule="referenced",
            )
            error.details = {"rule": "referenced", "referrers": referrers}
            raise error
        if warnings is not None:
            warnings.append(
                f"Hard-deleted with force: references from {total} other "
                f"document(s) to this {what} no longer resolve"
            )
        if broken_references is not None:
            broken_references.extend(referrers)

    async def _hard_delete_version(
        self,
        document_id: str,
        target: Document,
        held: Document | None,
        client: Any,
        actor: str,
        warnings: list[str] | None,
    ) -> dict[str, Any]:
        """Version-specific hard-delete; the document's Registry entry goes
        with its last version. Returns the deleted event's payload."""
        from .file_service import get_file_service

        event_payload = await self._document_to_event_payload(target)
        event_payload["hard_delete"] = True
        event_payload["version"] = target.version

        if held is not None and held.version == target.version:
            await self._update_file_reference_counts(target.file_references, delta=-1)
        await self._hard_delete_orphaned_files(
            target.file_references, get_file_service(),
            deleting=document_id, deleting_version=target.version, warnings=warnings,
        )

        await target.delete()

        # Check if any versions remain
        remaining = await Document.find({"document_id": document_id}).count()
        if remaining == 0:
            await _remove_registry_entry(client, document_id, actor)
        return event_payload

    async def _hard_delete_all_versions(
        self,
        document_id: str,
        any_doc: Document,
        held: Document | None,
        client: Any,
        actor: str,
        warnings: list[str] | None,
    ) -> dict[str, Any]:
        """Every version's hard delete. Returns the deleted event's payload."""
        from .file_service import get_file_service

        all_versions = await Document.find({"document_id": document_id}).to_list()

        event_payload = await self._document_to_event_payload(any_doc)
        event_payload["hard_delete"] = True
        # Every version goes: no version, so the consumer removes the
        # document's rows of every version, not one picked at random.
        event_payload["version"] = None

        if held is not None:
            await self._update_file_reference_counts(held.file_references, delta=-1)
        # Each file any version referenced, once: the ones nothing holds
        # now (count 0) go, including those a supersede left unheld.
        every_file = list({
            ref["file_id"]: ref for doc in all_versions
            for ref in doc.file_references or [] if ref.get("file_id")
        }.values())
        await self._hard_delete_orphaned_files(
            every_file, get_file_service(), deleting=document_id, warnings=warnings,
        )

        await Document.find({"document_id": document_id}).delete()
        await _remove_registry_entry(client, document_id, actor)
        return event_payload

    async def hard_delete_exemptions(self, candidates: dict[str, bool]) -> frozenset[str]:
        """The documents of one request whose references to each other do not
        block their hard deletes.

        ``candidates`` maps each document the request hard-deletes whole, and
        that passed the per-item checks, to its ``force``. A candidate whose
        delete would still be refused (referrers outside the set, no force)
        will not be deleted, so it exempts nobody: it is dropped, and the rest
        is checked again until nothing changes. Exempting it would let a
        document it references go while it stays, pointing at nothing.

        One aggregation reads who references each unforced candidate (every
        version and status of a referrer counts, as for the delete itself);
        the fixpoint then runs in memory, so a chain of n candidates costs one
        query, not one per candidate per pass.
        """
        remaining = dict(candidates)
        unforced = sorted(d for d, force in candidates.items() if not force)
        if not unforced:
            return frozenset(remaining)
        pipeline = [
            {"$match": {"references.resolved.document_id": {"$in": unforced}}},
            {"$unwind": "$references"},
            {"$match": {"references.resolved.document_id": {"$in": unforced}}},
            {"$group": {
                "_id": "$references.resolved.document_id",
                "referrers": {"$addToSet": "$document_id"},
            }},
        ]
        referrers_of: dict[str, set[str]] = {
            row["_id"]: set(row["referrers"]) - {row["_id"]}
            for row in await Document.aggregate(pipeline).to_list()
        }
        changed = True
        while changed:
            changed = False
            for document_id in unforced:
                if document_id in remaining and referrers_of.get(document_id, set()) - remaining.keys():
                    del remaining[document_id]
                    changed = True
        return frozenset(remaining)

    _REFERRER_SAMPLE = 5

    async def _hard_delete_referrers(
        self,
        document_id: str,
        version: int | None,
        deleted_with: frozenset[str],
    ) -> list[dict[str, Any]]:
        """Other documents whose references a hard delete would break, one
        group per (namespace, template) with a count of distinct documents.

        ``version`` None means the whole document goes: every reference to it
        breaks. With a version, other versions remain, so a ``latest``
        reference still resolves and only a ``pinned`` reference recorded
        against that version breaks. Every version and status of a referrer
        counts: a retired document's references must keep resolving too.
        Sample document ids are listed only for namespaces the caller can
        read; elsewhere the group gives the count alone. A read check the
        Registry cannot answer raises RegistryUnavailableError.
        """
        target: dict[str, Any] = {"resolved.document_id": document_id}
        if version is not None:
            target.update({"version_strategy": "pinned", "resolved.version": version})
        pipeline = [
            {"$match": {
                "references": {"$elemMatch": target},
                "document_id": {"$nin": [document_id, *deleted_with]},
            }},
            {"$group": {
                "_id": {"namespace": "$namespace", "template_id": "$template_id"},
                "ids": {"$addToSet": "$document_id"},
            }},
            {"$sort": {"_id.namespace": 1, "_id.template_id": 1}},
        ]
        groups = await Document.aggregate(pipeline).to_list()
        if not groups:
            return []

        from wip_auth import get_current_identity
        identity = get_current_identity()
        permissions: dict[str, str] = {}

        async def can_read(ns: str) -> bool:
            if identity is None:
                return True  # internal caller, no request identity
            # A Registry that cannot answer fails the delete (registry_error)
            # rather than reading as "no read" and dropping the sample ids.
            return await _can_read_namespace(identity, ns, permissions)

        referrers: list[dict[str, Any]] = []
        for group in groups:
            ns = group["_id"]["namespace"]
            ids = sorted(group["ids"])
            entry: dict[str, Any] = {
                "namespace": ns,
                "template_id": group["_id"].get("template_id"),
                "documents": len(ids),
            }
            if await can_read(ns):
                entry["sample_document_ids"] = ids[: self._REFERRER_SAMPLE]
            referrers.append(entry)
        return referrers

    async def _hard_delete_orphaned_files(
        self,
        file_references: list[dict[str, Any]],
        file_service,
        *,
        deleting: str,
        deleting_version: int | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        """Hard-delete the files this hard delete leaves unreferenced.

        A file goes only when its count is 0 AND no active document other
        than the one being deleted (``deleting``, every version or only
        ``deleting_version``) references it: the count is a running tally and
        passes through 0 during an update that keeps a file, so it alone must
        never decide that bytes can be reclaimed. The file is marked inactive
        first, guarded on its count still being 0 — from then on a write that
        validates against it refuses it — and only then are referrers looked
        for. One that turns up returns the file to active, its count raised to
        the referrers found. A file whose bytes the store refused to delete
        stays recorded (inactive) and is named in ``warnings``.
        """
        from ..models.file import File, FileStatus
        from ..services.file_storage_client import is_file_storage_enabled

        if not file_references or not is_file_storage_enabled():
            return
        files = File.get_motor_collection()
        for ref in file_references:
            file_id = ref.get("file_id")
            if not file_id:
                continue
            try:
                retired = await files.update_one(
                    {"file_id": file_id, "reference_count": {"$lte": 0}},
                    {"$set": {"status": FileStatus.INACTIVE.value, "updated_at": datetime.now(UTC)}},
                )
                if retired.matched_count != 1:
                    continue  # still counted, or gone
                holders = await file_service.live_referrers(
                    file_id, except_document=deleting, except_version=deleting_version,
                )
                if holders:
                    logger.warning(
                        "File %s has reference count 0 but active documents %s reference it; "
                        "kept, and its count raised to match",
                        file_id, holders,
                    )
                    await files.update_one(
                        {"file_id": file_id},
                        {"$set": {"status": FileStatus.ACTIVE.value},
                         "$max": {"reference_count": len(holders)}},
                    )
                    continue
                await file_service.hard_delete_file(
                    file_id, except_document=deleting, except_version=deleting_version,
                )
            except Exception as e:
                logger.warning(f"Failed to hard-delete orphaned file {file_id}: {e}")
                if warnings is not None:
                    warnings.append(f"File {file_id} was left inactive, not deleted: {e}")

    async def query_documents(
        self,
        request: DocumentQueryRequest,
        ns_filter: dict | None = None,
    ) -> DocumentQueryResponse:
        """Query documents with complex filters."""
        query = self._build_query(request)
        if ns_filter:
            query.update(ns_filter)

        # Count total
        total = await Document.find(query).count()

        sort_clauses = build_sort_clauses(request.sort_by, request.sort_order)

        # Fetch page
        skip = (request.page - 1) * request.page_size
        documents = await Document.find(query)\
            .sort(sort_clauses)\
            .skip(skip)\
            .limit(request.page_size)\
            .to_list()

        # Convert to responses in batch
        items = await self._batch_to_responses(documents)

        return DocumentQueryResponse(
            items=items,
            total=total,
            page=request.page,
            page_size=request.page_size,
            pages=math.ceil(total / request.page_size) if total > 0 else 0,
            query=request
        )

    # Document-level timestamp fields whose filter values are parsed and
    # compared type-independently. The corpus stores these in two BSON types
    # (service writes store dates; restore inserts archive JSON rows, which
    # carry them as ISO strings), and Mongo comparisons are type-bracketed —
    # a raw value of either type silently skips rows stored as the other.
    _TIMESTAMP_QUERY_FIELDS = ("created_at", "updated_at")
    _TIMESTAMP_QUERY_OPS: ClassVar[dict[str, str]] = {
        "eq": "$eq", "ne": "$ne", "gt": "$gt", "gte": "$gte",
        "lt": "$lt", "lte": "$lte", "in": "$in", "nin": "$in",
    }

    @classmethod
    def _parse_timestamp_filter_value(cls, field: str, value: Any) -> datetime:
        """Parse one timestamp filter value, failing loud on anything else."""
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        raise ValueError(
            f"Filter value for '{field}' must be an ISO-8601 timestamp "
            f"string, got: {value!r}"
        )

    @classmethod
    def _timestamp_condition(
        cls, field: str, operator: str, value: Any
    ) -> dict[str, Any]:
        """Build a storage-type-independent condition on a timestamp field.

        Compares through $convert(to: date) so date-stored and string-stored
        rows are measured on the same axis — neither cohort can silently
        drop out of a window while mixed storage exists, and once storage is
        normalized the conversion is a no-op. Rows whose value is missing or
        unparseable convert to null and never match any timestamp filter
        (the guard below), rather than sorting below every date and leaking
        into $lt windows.
        """
        if operator in ("in", "nin"):
            if not isinstance(value, list):
                raise ValueError(
                    f"Filter value for '{field}' with operator '{operator}' "
                    f"must be a list of ISO-8601 timestamp strings."
                )
            parsed: Any = [
                cls._parse_timestamp_filter_value(field, v) for v in value
            ]
        else:
            parsed = cls._parse_timestamp_filter_value(field, value)

        converted = {"$convert": {"input": f"${field}", "to": "date", "onError": None}}
        comparison: dict[str, Any] = {
            cls._TIMESTAMP_QUERY_OPS[operator]: [converted, parsed]
        }
        if operator == "nin":
            comparison = {"$not": [comparison]}
        return {"$expr": {"$and": [{"$ne": [converted, None]}, comparison]}}

    def _build_query(self, request: DocumentQueryRequest) -> dict[str, Any]:
        """Build MongoDB query from request."""
        query: dict[str, Any] = {}

        if request.template_id:
            query["template_id"] = request.template_id

        if request.status:
            query["status"] = request.status.value

        # Apply filters
        for filter_item in request.filters:
            field = filter_item.field
            operator = filter_item.operator
            value = filter_item.value

            # QueryFilter refuses these at parse time; a filter built without
            # validation (model_construct, a direct caller) must not put an
            # operator, or server-side JavaScript, into the query either.
            if (
                operator_path_segment(field) is not None
                or operator_key_in(value) is not None
            ):
                raise ValueError(
                    f"Filter on {field!r} carries a MongoDB operator or an "
                    "unusable path in its field or value; field segments may "
                    "not be empty, start with '$' or hold a NUL byte, and "
                    "value keys may not start with '$'."
                )

            if (
                field in self._TIMESTAMP_QUERY_FIELDS
                and operator in self._TIMESTAMP_QUERY_OPS
            ):
                # $and accumulation: two range filters on the same field
                # (gte + lt window) must both apply, not last-write-win on
                # the query dict key.
                query.setdefault("$and", []).append(
                    self._timestamp_condition(field, operator, value)
                )
                continue

            mongo_ops = {
                "ne": "$ne", "gt": "$gt", "gte": "$gte", "lt": "$lt", "lte": "$lte",
                "in": "$in", "nin": "$nin", "exists": "$exists", "regex": "$regex",
            }
            if operator == "eq" or operator in mongo_ops:
                if (problem := _filter_value_problem(operator, value)) is not None:
                    # Refused here, as a 422: handed to MongoDB, an ill-formed
                    # operand fails the query as a server error.
                    raise ValueError(f"Filter on {field!r}: {problem}")
                condition = value if operator == "eq" else {mongo_ops[operator]: value}
                # Filters are ANDed: a second filter on a field the query
                # already constrains (another filter, or the request's own
                # template_id / status) joins $and instead of replacing it.
                if field in query:
                    query.setdefault("$and", []).append({field: condition})
                else:
                    query[field] = condition
            else:
                # `operator` is a free str on QueryFilter (not an enum), so an
                # unrecognized value reaches here. Before CASE-466 it fell
                # through silently: the filter was never applied and the query
                # over-returned (no filtering on that field) with no error —
                # the same silent-empty/silent-wrong class the case names, on
                # the operator axis. Fail loud; the route maps ValueError->422.
                # Unknown filter FIELDS stay free by design (query filters are
                # ad-hoc reads, not declarative commitments); only a field or
                # value that carries a MongoDB operator is refused, above.
                raise ValueError(
                    f"Unsupported filter operator '{operator}' on field "
                    f"'{field}'. Supported: eq, ne, gt, gte, lt, lte, in, "
                    f"nin, exists, regex."
                )

        name_every_status(query)
        return query

    def _finalize_bulk_response(
        self,
        results: list[BulkResultItem],
        total: int,
        timing: dict[str, float],
        total_start: float,
    ) -> BulkResponse:
        """Stamp total timing, record it, and build the sorted BulkResponse.

        Used at every bulk_create exit — the early-return error paths and the
        success path — so the envelope shape, the succeeded/failed tally, and
        timing finalization live in exactly one place. succeeded/failed are
        derived from the result rows (equivalent to the old per-site tallies;
        the error paths only ever held error/skipped rows at their exit).
        """
        timing["total"] = (time.perf_counter() - total_start) * 1000
        self._record_creation_timing(timing)
        sorted_results = sorted(results, key=lambda r: r.index)
        # Bulk-response contract (CASE-413): every input item maps to exactly
        # one result row. A mismatch means a code path dropped or duplicated an
        # item — fail loud rather than return a silently incomplete envelope.
        assert len(sorted_results) == total, (
            f"bulk response invariant violated: {len(sorted_results)} result "
            f"rows for {total} input items"
        )
        return BulkResponse(
            results=sorted_results,
            total=total,
            succeeded=sum(1 for r in sorted_results if r.status not in ("error", "skipped")),
            failed=sum(1 for r in sorted_results if r.status == "error"),
            timing={k: round(v, 1) for k, v in timing.items()},
        )

    async def _validate_batch(
        self,
        items: list[DocumentCreateRequest],
        namespace: str,
        continue_on_error: bool,
        timing: dict[str, float],
    ) -> tuple[list, list[BulkResultItem], int]:
        """Stage 1 of bulk_create: validate items.

        Returns (validation_results, results, failed):
        - validation_results: (i, item, validation_result) for each VALID item
        - results: error/skipped BulkResultItems accumulated during validation
        - failed: count of error rows

        continue_on_error=True validates every item concurrently, collecting
        per-item errors. continue_on_error=False validates sequentially and
        stops at the first failure: that item gets an error row and every item
        after it gets a skipped row. Items that validated BEFORE the failure
        are returned in validation_results and created downstream — CASE-413,
        Option 2 (create-what-validated-then-stop).

        Sets timing["1_validation"].
        """
        start = time.perf_counter()
        validation_results: list = []
        results: list[BulkResultItem] = []
        failed = 0

        validation_semaphore = asyncio.Semaphore(10)
        # Shared cache for document reference lookups across the batch —
        # avoids repeated MongoDB queries when many docs reference the same
        # target (e.g., 50 FIN_TRANSACTIONs all pointing to one FIN_ACCOUNT).
        doc_ref_cache: dict = {}

        async def _validate_one(i: int, item: DocumentCreateRequest):
            async with validation_semaphore:
                return i, item, await self._validate_as_writer(
                    item.template_id,
                    item.data,
                    namespace,
                    template_version=getattr(item, 'template_version', None),
                    doc_ref_cache=doc_ref_cache,
                )

        def _skipped_rows(after_index: int) -> list[BulkResultItem]:
            return [
                BulkResultItem(index=j, status="skipped", error="Stopped due to previous error")
                for j in range(after_index + 1, len(items))
            ]

        if continue_on_error:
            # All validations run concurrently — errors collected per-item
            tasks = [_validate_one(i, item) for i, item in enumerate(items)]
            settled = await asyncio.gather(*tasks, return_exceptions=True)

            for task_idx, entry in enumerate(settled):
                if isinstance(entry, BaseException):
                    failed += 1
                    results.append(BulkResultItem(
                        index=task_idx, status="error", **error_fields(entry)
                    ))
                else:
                    i, item, validation_result = entry
                    if validation_result.valid:
                        validation_results.append((i, item, validation_result))
                    else:
                        failed += 1
                        results.append(BulkResultItem(
                            index=i,
                            status="error",
                            error=self._format_validation_errors(validation_result.errors),
                            error_code="validation_failed",
                            details={"errors": validation_result.errors},
                        ))
        else:
            # Sequential when continue_on_error=False — stop on first failure
            for i, item in enumerate(items):
                try:
                    validation_result = await self._validate_as_writer(
                        item.template_id,
                        item.data,
                        namespace,
                        template_version=getattr(item, 'template_version', None),
                        doc_ref_cache=doc_ref_cache,
                    )
                    if validation_result.valid:
                        validation_results.append((i, item, validation_result))
                    else:
                        failed += 1
                        results.append(BulkResultItem(
                            index=i,
                            status="error",
                            error=self._format_validation_errors(validation_result.errors),
                            error_code="validation_failed",
                            details={"errors": validation_result.errors},
                        ))
                        results.extend(_skipped_rows(i))
                        break
                except Exception as e:
                    failed += 1
                    results.append(BulkResultItem(
                        index=i, status="error", **error_fields(e)
                    ))
                    results.extend(_skipped_rows(i))
                    break

        timing["1_validation"] = (time.perf_counter() - start) * 1000
        return validation_results, results, failed

    async def _per_item_document_ids(
        self,
        validation_results: list,
        registry_results: list[dict],
        existing_by_doc_id: dict,
    ) -> set[str]:
        """Document ids whose items must keep the per-item write path.

        An item needs it when it carries inline synonyms, pins its version
        (restore/migration), revives a deleted document, or belongs to a
        template that is not versioned (updates overwrite in place). Every
        other item of the same document follows it, so one document's
        version chain is never split between the two paths.
        """
        versioned: dict[str, bool] = {}
        ids: set[str] = set()
        for (_, item, _), registry_result in zip(validation_results, registry_results, strict=False):
            if registry_result.get("status") == "error":
                continue
            document_id = registry_result["registry_id"]
            if item.template_id not in versioned:
                versioned[item.template_id] = await self._is_template_versioned(item.template_id)
            existing = (
                existing_by_doc_id.get(document_id)
                if registry_result.get("status") != "created" else None
            )
            if (
                item.synonyms
                or (item.document_id and item.version is not None)
                or not versioned[item.template_id]
                or (existing is not None and self._is_revival(existing))
            ):
                ids.add(document_id)
        return ids

    @staticmethod
    def _bulk_eligible(registry_result: dict, per_item_ids: set[str]) -> bool:
        return (
            registry_result.get("status") != "error"
            and registry_result["registry_id"] not in per_item_ids
        )

    def _plan_validated_item(
        self,
        i: int,
        item: DocumentCreateRequest,
        validation_result,
        registry_result: dict,
        existing_by_doc_id: dict,
        namespace: str,
        actor: str,
        now: datetime,
        retiring: dict[Any, "Document"],
    ) -> "_PlannedWrite | _ItemOutcome":
        """Stage 4 for a plain create or new version, without writing.

        The same decisions as _apply_validated_item for an item without
        inline synonyms, version pin or revival, on a versioned template:
        unchanged is reported at once; otherwise the new row is built and the
        version it replaces is marked inactive. A replaced version stored
        before this request goes into ``retiring`` for one bulk update; one
        planned earlier in this batch is simply never written as active.
        """
        document_id = cast(str, registry_result["registry_id"])
        identity_hash = validation_result.identity_hash
        is_new_from_registry = registry_result.get("status") == "created"
        existing = existing_by_doc_id.get(document_id) if not is_new_from_registry else None

        if existing is not None:
            if not self._document_has_changed(
                existing,
                item.data,
                validation_result.term_references,
                validation_result.references,
                validation_result.file_references,
                new_metadata_custom=item.metadata,
            ):
                return _ItemOutcome(
                    BulkResultItem(
                        index=i,
                        status="unchanged",
                        document_id=existing.document_id,
                        identity_hash=identity_hash,
                        version=existing.version,
                        template_version=existing.template_version,
                        revision=current_revision(existing),
                        is_new=False,
                        warnings=validation_result.warnings,
                    ),
                    None,
                    "unchanged",
                )
            if existing.id is not None and existing.id not in retiring:
                # As it was before this write, so a failed successor insert
                # can put it back (_reactivate_if_orphaned).
                retiring[existing.id] = existing.model_copy()
            existing.status = DocumentStatus.INACTIVE
            existing.updated_at = now
            existing.updated_by = actor
            version = existing.version + 1
        else:
            version = 1

        if item.metadata is not None:
            custom_metadata = item.metadata
        elif existing is not None and existing.metadata:
            custom_metadata = dict(existing.metadata.custom)
        else:
            custom_metadata = {}
        document = self._new_document(
            namespace=namespace,
            document_id=document_id,
            template_id=item.template_id,
            template_version=validation_result.template_version,
            template_value=validation_result.template_value,
            identity_hash=identity_hash,
            version=version,
            data=item.data,
            term_references=validation_result.term_references,
            references=validation_result.references,
            file_references=validation_result.file_references,
            metadata=DocumentMetadata(warnings=validation_result.warnings, custom=custom_metadata),
            actor=actor,
            now=now,
            entity_created_at=existing.created_at if existing is not None else None,
        )
        document.id = PydanticObjectId()
        existing_by_doc_id[document_id] = document
        return _PlannedWrite(i, document, existing, validation_result)

    async def _write_planned(
        self,
        planned: list["_PlannedWrite"],
        retiring: dict[Any, "Document"],
        namespace: str,
        actor: str,
        now: datetime,
        timing: dict[str, float],
    ) -> list["_ItemOutcome"]:
        """Write the planned rows: one update retiring the versions they
        replace, then one unordered insert per BULK_WRITE_CHUNK rows.

        A row refused for a duplicate key (a concurrent writer took that
        version) is written again through _insert_with_retry, which reads the
        latest version and retries; any other refused row reports its error.
        Then each written row gets its result, event and file reference
        counts, as on the per-item path.
        """
        from pymongo.errors import BulkWriteError

        if not planned:
            return []
        start = time.perf_counter()
        if retiring:
            await Document.get_motor_collection().update_many(
                {"_id": {"$in": list(retiring)}},
                {"$set": {"status": DocumentStatus.INACTIVE.value,
                          "updated_at": now, "updated_by": actor}},
            )
        timing["4a_retire_versions"] = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        refused: dict[int, dict] = {}
        for first in range(0, len(planned), BULK_WRITE_CHUNK):
            chunk = planned[first:first + BULK_WRITE_CHUNK]
            try:
                await Document.insert_many([p.document for p in chunk], ordered=False)
            except BulkWriteError as e:
                for err in e.details.get("writeErrors", []):
                    refused[first + err["index"]] = err
            except Exception as exc:
                refused.update(await self._refuse_unlanded(namespace, chunk, first, exc))
        timing["4b_insert_documents"] = (time.perf_counter() - start) * 1000

        outcomes: list[_ItemOutcome] = []
        for pos, plan in enumerate(planned):
            document = plan.document
            err = refused.get(pos)
            if err is not None:
                if err.get("code") != 11000:
                    outcomes.append(_ItemOutcome(
                        BulkResultItem(
                            index=plan.item_index, status="error", error_code="internal_error",
                            error=f"Document write failed: {err.get('errmsg', err)}",
                        ),
                        None,
                        "failed",
                    ))
                    continue
                try:
                    document = await self._insert_with_retry(
                        namespace=namespace,
                        document_id=document.document_id,
                        template_id=document.template_id,
                        template_version=document.template_version,
                        template_value=plan.validation_result.template_value,
                        identity_hash=document.identity_hash,
                        version=document.version,
                        data=document.data,
                        term_references=document.term_references,
                        references=document.references,
                        file_references=document.file_references,
                        metadata=document.metadata,
                        actor=actor,
                        now=now,
                        entity_created_at=document.created_at,
                    )
                except Exception as exc:
                    outcomes.append(_ItemOutcome(
                        BulkResultItem(index=plan.item_index, status="error", **error_fields(exc)),
                        None,
                        "failed",
                    ))
                    continue

            is_new = document.version == 1
            event_type = EventType.DOCUMENT_CREATED if is_new else EventType.DOCUMENT_UPDATED
            if plan.existing is not None:
                await self._update_file_reference_counts(plan.existing.file_references, delta=-1)
            await self._update_file_reference_counts(document.file_references, delta=1)
            outcomes.append(_ItemOutcome(
                BulkResultItem(
                    index=plan.item_index,
                    status="created" if is_new else "updated",
                    document_id=document.document_id,
                    identity_hash=document.identity_hash,
                    version=document.version,
                    template_version=document.template_version,
                    is_new=is_new,
                    warnings=plan.validation_result.warnings,
                ),
                # A row this batch superseded is stored inactive, but its
                # event reports it active: an event describes a version as it
                # was while it was the live one, as the per-item paths publish
                # each row before a later write supersedes it. Reporting keeps
                # the latest version live regardless of arrival order (its
                # latest_only upsert only replaces a row with a higher
                # version), and all_versions keeps each version's row as
                # first written, on every write path alike.
                (event_type, await self._document_to_event_payload(
                    document if document.status == DocumentStatus.ACTIVE
                    else document.model_copy(update={"status": DocumentStatus.ACTIVE})
                )),
                "created" if is_new else "updated",
            ))
        await self._repair_failed_writes(namespace, planned, outcomes, retiring)
        return outcomes

    async def _refuse_unlanded(
        self, namespace: str, chunk: list["_PlannedWrite"], first: int, exc: Exception,
    ) -> dict[int, dict]:
        """The rows of a chunk whose insert_many raised (connection, step-down,
        timeout) that are not stored, keyed by position in the write, each
        refused with the failure: which rows landed is unknown, so ask."""
        logger.exception("Bulk insert of %d documents failed", len(chunk))
        rows = await Document.get_motor_collection().find(
            {"namespace": namespace,
             "$or": [{"document_id": p.document.document_id, "version": p.document.version}
                     for p in chunk]},
            {"document_id": 1, "version": 1},
        ).to_list(None)
        landed = {(r["document_id"], r["version"]) for r in rows}
        return {
            first + offset: {"errmsg": str(exc)}
            for offset, plan in enumerate(chunk)
            if (plan.document.document_id, plan.document.version) not in landed
        }

    async def _repair_failed_writes(
        self,
        namespace: str,
        planned: list["_PlannedWrite"],
        outcomes: list["_ItemOutcome"],
        retiring: dict[Any, "Document"],
    ) -> None:
        """Every entity with a failed row in this write gets an active version
        back if the write left it with none (_reactivate_if_orphaned)."""
        failed = {
            plan.document.document_id
            for plan, outcome in zip(planned, outcomes, strict=True)
            if outcome.counter == "failed"
        }
        if not failed:
            return
        prior: dict[Any, tuple[datetime | None, str | None]] = {}
        for doc in retiring.values():
            prior.update(_retired_stamps(doc))
        for document_id in sorted(failed):
            await self._reactivate_if_orphaned(namespace, document_id, prior)

    async def _apply_validated_item(
        self,
        i: int,
        item: DocumentCreateRequest,
        validation_result,
        registry_result: dict,
        existing_by_doc_id: dict,
        namespace: str,
        actor: str,
        now: datetime,
    ) -> "_ItemOutcome":
        """Stage 4 per-item: persist one validated item given its Registry result.

        Returns an _ItemOutcome (result row + optional pending NATS event +
        which counter to bump). Mutates existing_by_doc_id in place so later
        items in the same batch targeting the same document_id observe the
        version this call wrote. Exceptions propagate to the caller, which
        turns them into a per-item error (and stop-on-first when
        continue_on_error=False) — matching the previous inline behaviour.
        """
        if registry_result.get("status") == "error":
            return _registry_error_outcome(i, registry_result)

        document_id = cast(str, registry_result["registry_id"])
        is_new_from_registry = registry_result.get("status") == "created"

        # For existing identity (Registry returned already_exists), look up by
        # document_id — NOT identity_hash (CASE-36).
        existing = existing_by_doc_id.get(document_id) if not is_new_from_registry else None

        # Version override for restore/migration: if the request supplies both
        # document_id and version, use them as-is.
        version_override = (
            item.version if item.document_id and item.version is not None else None
        )

        synonym_warnings, refused = await self._apply_item_synonyms(
            i, item, document_id, namespace, is_new_from_registry,
        )
        if refused is not None:
            return refused

        revival = existing is not None and self._is_revival(existing)
        # A revival is recorded in the Registry before anything is written:
        # one it cannot record fails this item and writes nothing.
        lifecycle_warnings = await self._record_revival(document_id, revival, actor)
        try:
            outcome = await self._write_validated_item(
                i, item, validation_result, document_id, existing, revival,
                version_override, synonym_warnings, existing_by_doc_id,
                namespace, actor, now,
            )
        except BaseException:
            await self._undo_revival(document_id, revival, namespace, actor)
            raise
        outcome.result.warnings = (outcome.result.warnings or []) + lifecycle_warnings
        return outcome

    async def _write_validated_item(
        self,
        i: int,
        item: DocumentCreateRequest,
        validation_result,
        document_id: str,
        existing: Document | None,
        revival: bool,
        version_override: int | None,
        synonym_warnings: list[str],
        existing_by_doc_id: dict,
        namespace: str,
        actor: str,
        now: datetime,
    ) -> "_ItemOutcome":
        """Write one validated item: unchanged, overwritten in place, or a
        new document or version."""
        prior: dict[Any, tuple[datetime | None, str | None]] | None = None
        if existing and version_override is None:
            settled = await self._settle_without_new_version(
                i, item, validation_result, existing, revival, synonym_warnings,
                namespace, actor,
            )
            if settled is not None:
                return settled
            prior = await _retire_version(existing, actor, now)

        return await self._write_item_version(
            i, item, validation_result, document_id, existing, revival, prior,
            version_override, synonym_warnings, existing_by_doc_id,
            namespace, actor, now,
        )

    async def _apply_item_synonyms(
        self,
        i: int,
        item: DocumentCreateRequest,
        document_id: str,
        namespace: str,
        is_new_entry: bool,
    ) -> tuple[list[str], "_ItemOutcome | None"]:
        """Register the item's inline synonyms: (their warnings, None), or
        (_, the error outcome) when one is refused.

        CASE-434/436: strict by default — a refused synonym fails THIS item
        (and rolls back its entry/synonyms) without affecting the rest of the
        batch; "warn" surfaces it in the item's warnings.
        """
        if not item.synonyms:
            return [], None
        warnings, err_code, err_msg = await self._register_inline_synonyms(
            document_id=document_id, namespace=namespace, synonyms=item.synonyms,
            on_synonym_conflict=item.on_synonym_conflict,
            is_new_entry=is_new_entry,
        )
        if err_code:
            return warnings, _ItemOutcome(
                BulkResultItem(index=i, status="error", error_code=err_code, error=err_msg),
                None,
                "failed",
            )
        return warnings, None

    async def _settle_without_new_version(
        self,
        i: int,
        item: DocumentCreateRequest,
        validation_result,
        existing: Document,
        revival: bool,
        synonym_warnings: list[str],
        namespace: str,
        actor: str,
    ) -> "_ItemOutcome | None":
        """An update of an existing document that writes no new version row:
        unchanged when nothing changed (and it is no revival), or overwritten
        in place when its template is not versioned. None when a new version
        must be written.
        """
        identity_hash = validation_result.identity_hash
        if not revival and not self._document_has_changed(
            existing,
            item.data,
            validation_result.term_references,
            validation_result.references,
            validation_result.file_references,
            new_metadata_custom=item.metadata,
        ):
            # No change — report existing without creating a new version.
            return _ItemOutcome(
                BulkResultItem(
                    index=i,
                    status="unchanged",
                    document_id=existing.document_id,
                    identity_hash=identity_hash,
                    version=existing.version,
                    template_version=existing.template_version,
                    revision=current_revision(existing),
                    is_new=False,
                    warnings=validation_result.warnings + synonym_warnings,
                ),
                None,
                "unchanged",
            )

        # Templates with versioned=false overwrite in place — the same
        # branch the single-item and PATCH paths take. Without it, the
        # deactivate-and-insert flow would mint version 2 and retain the old
        # row as inactive, creating exactly the version history the flag
        # forbids.
        if await self._is_template_versioned(item.template_id):
            return None
        deferred: list[tuple[EventType, dict]] = []
        response, _ = await self._overwrite_in_place(
            item, existing, validation_result, namespace,
            extra_warnings=synonym_warnings, deferred_events=deferred,
        )
        # existing_by_doc_id already holds this Document object, mutated in
        # place — later same-identity items in the batch see the fresh data.
        # Its event is the batch's pending one, published after the
        # auto-synonym stage, which drops it if that stage rolls the
        # document back.
        return _ItemOutcome(
            BulkResultItem(
                index=i,
                status="updated",
                document_id=response.document_id,
                identity_hash=identity_hash,
                version=response.version,
                template_version=response.template_version,
                revision=response.revision,
                is_new=False,
                warnings=response.warnings,
            ),
            deferred[0] if deferred else None,
            "updated",
        )

    async def _write_item_version(
        self,
        i: int,
        item: DocumentCreateRequest,
        validation_result,
        document_id: str,
        existing: Document | None,
        revival: bool,
        prior: dict[Any, tuple[datetime | None, str | None]] | None,
        version_override: int | None,
        synonym_warnings: list[str],
        existing_by_doc_id: dict,
        namespace: str,
        actor: str,
        now: datetime,
    ) -> "_ItemOutcome":
        """Write the item as a new document or a new version of ``existing``
        (whose row ``prior`` retired), then do what follows a write: the
        deferred event, the file reference counts and, on a revival, the
        Registry lifecycle.

        The version is the one after ``existing``'s, the pinned one of a
        restore or migration, or 1; the insert's own answer is final (a lost
        race lands on the next free version).
        """
        if prior is not None and existing is not None:
            new_version = existing.version + 1
        elif version_override is not None:
            new_version = version_override
        else:
            new_version = 1

        # Create document with retry on duplicate key (handles concurrent
        # requests racing on the same identity hash).
        metadata = DocumentMetadata(
            warnings=validation_result.warnings + synonym_warnings,
            custom=_custom_metadata(item, existing),
        )
        document = await self._insert_or_restore(
            prior,
            namespace=namespace,
            document_id=document_id,
            template_id=item.template_id,
            template_version=validation_result.template_version,
            template_value=validation_result.template_value,
            identity_hash=validation_result.identity_hash,
            version=new_version,
            data=item.data,
            term_references=validation_result.term_references,
            references=validation_result.references,
            file_references=validation_result.file_references,
            metadata=metadata,
            actor=actor,
            now=now,
            # An update carries the entity's creation time forward; a brand-new
            # document (existing is None) gets `now` (CASE-802).
            entity_created_at=existing.created_at if existing is not None else None,
            pinned=version_override is not None,
        )
        is_new = document.version == 1

        # Update existing_by_doc_id so subsequent items in this batch with the
        # same document_id see the correct latest version.
        if document_id:
            existing_by_doc_id[document_id] = document

        pending_event = await self._after_item_write(
            document, existing, revival, validation_result,
        )
        return _ItemOutcome(
            BulkResultItem(
                index=i,
                status="created" if is_new else "updated",
                document_id=document_id,
                identity_hash=validation_result.identity_hash,
                version=document.version,
                template_version=document.template_version,
                is_new=is_new,
                warnings=validation_result.warnings + synonym_warnings,
            ),
            pending_event,
            "created" if is_new else "updated",
        )

    async def _after_item_write(
        self,
        document: Document,
        existing: Document | None,
        revival: bool,
        validation_result,
    ) -> tuple:
        """The deferred event of a written item.

        The event is published concurrently after the loop. The written
        version's file references are counted, and the replaced version's
        released — unless it was deleted, whose references the delete already
        released.
        """
        is_new = document.version == 1
        event_type = EventType.DOCUMENT_CREATED if is_new else EventType.DOCUMENT_UPDATED
        pending_event = (event_type, await self._document_to_event_payload(document))

        if not is_new and existing and not revival:
            await self._update_file_reference_counts(existing.file_references, delta=-1)
        await self._update_file_reference_counts(validation_result.file_references, delta=1)
        return pending_event

    async def _refuse_document_id_mismatches(
        self,
        validation_results: list[tuple[int, DocumentCreateRequest, Any]],
        registry_results: list[dict],
    ) -> None:
        """Turn each item whose document_id its identity does not resolve to
        into an error row, in place — the single-item path's check.

        An entry the Registry minted for such an item is released, unless
        another item of the batch resolved to it too (it is then that item's).
        """
        claimed: dict[str, int] = {}
        for reg in registry_results:
            if reg.get("status") != "error" and reg.get("registry_id"):
                claimed[reg["registry_id"]] = claimed.get(reg["registry_id"], 0) + 1
        for (_i, item, _vr), reg in zip(validation_results, registry_results, strict=False):
            if reg.get("status") == "error":
                continue
            is_new = reg.get("status") == "created"
            mismatch = _document_id_mismatch(item, reg["registry_id"], is_new)
            if mismatch is None:
                continue
            if is_new and claimed.get(reg["registry_id"], 0) == 1:
                await self._release_minted_entry(reg["registry_id"])
            reg.clear()
            reg.update({"status": "error", **mismatch})

    @staticmethod
    def _stored_identity_feed(
        current: Document, validation_result: Any,
    ) -> dict[str, Any] | None:
        """The identity feed of the stored version: its identity values with
        each referent field replaced by the canonical ID its stored
        resolutions name — the same rule the write's own feed follows.

        Built from the row itself, never from its stored identity hash: a
        hash written under older feed rules would make an unchanged identity
        look changed. None when the row cannot give it (an identity value
        missing, or a referent the row holds no resolution for); the guard
        then compares spellings, as it did before the feed rule.
        """
        try:
            values = IdentityService.extract_identity_values(
                current.data, validation_result.identity_fields or [],
            )
            if not validation_result.referent_identity_fields:
                return values
            return substitute_resolved_referents(
                values,
                validation_result.referent_identity_fields,
                build_referent_resolutions(current.term_references, current.references),
            )
        except ValueError:
            return None

    async def _register_bulk_auto_synonyms(
        self,
        results: list[BulkResultItem],
        validation_results: list[tuple[int, DocumentCreateRequest, Any]],
        namespace: str,
        actor: str,
        minted: set[str],
    ) -> set[str]:
        """Register the auto-synonym of each document a bulk create made new.

        The candidates are the items reported ``created`` at version 1 that
        were not pinned to a version (a restore carries its archived synonyms
        instead) — the same rule as _create_new_document. One Registry call
        covers them all; a document whose synonym is refused is rolled back
        (its row deleted, its file references released) and its result turns
        into a ``registry_error`` item. Returns the rolled-back document ids.
        """
        by_index = {i: (item, vr) for i, item, vr in validation_results}
        candidates: list[tuple[int, DocumentCreateRequest, Any]] = []
        for pos, result in enumerate(results):
            if result.status != "created" or result.version != 1 or not result.document_id:
                continue
            item, vr = by_index[result.index]
            if item.document_id and item.version is not None:
                continue
            candidates.append((pos, item, vr))
        if not candidates:
            return set()

        refusals = await get_registry_client().register_auto_synonyms_per_item(
            [
                {
                    "document_id": results[pos].document_id,
                    "template_value": vr.template_value or "",
                    "identity_hash": vr.identity_hash,
                    "has_identity_fields": bool(vr.identity_hash),
                }
                for pos, _item, vr in candidates
            ],
            namespace=namespace,
            created_by=actor,
        )
        reasons: dict[str, str] = {}
        for (pos, _item, _vr), refusal in zip(candidates, refusals, strict=True):
            if refusal is not None:
                reasons[cast(str, results[pos].document_id)] = refusal
        # The document was born in this batch (its version 1 is), so every row
        # of it is this batch's: later items of the same identity wrote its
        # successors. All of them go, and every result naming it fails.
        for document_id in reasons:
            logger.error(
                "Auto-synonym registration failed for document %s — rolling back",
                document_id,
            )
            rows = await Document.find(
                {"namespace": namespace, "document_id": document_id}
            ).to_list()
            for row in rows:
                if row.status == DocumentStatus.ACTIVE:
                    await self._update_file_reference_counts(row.file_references, delta=-1)
            await Document.find(
                {"namespace": namespace, "document_id": document_id}
            ).delete()
            await self._release_entry_if_minted(document_id, minted=document_id in minted)
        for pos, result in enumerate(results):
            if result.document_id in reasons and result.status != "error":
                results[pos] = BulkResultItem(
                    index=result.index, status="error",
                    error=reasons[result.document_id],
                    error_code="registry_error",
                )
        return set(reasons)

    async def bulk_create(
        self,
        items: list[DocumentCreateRequest],
        namespace: str,
        continue_on_error: bool = True,
    ) -> BulkResponse:
        """Create multiple documents; the batch shares term resolutions
        (term_resolution_batch) and the inbound gate's Registry placements
        (placement_batch)."""
        with term_resolution_batch(), placement_batch():
            return await self._bulk_create(items, namespace, continue_on_error)

    @staticmethod
    async def _gate_template(template_client: Any, template_id: str) -> dict[str, Any] | None:
        """The template Stage 1b checks an item against. A store that cannot
        answer raises DependencyUnavailableError; a template it does not
        serve is None (the validation that already ran reported it)."""
        try:
            return cast(dict[str, Any] | None, await template_client.get_template_resolved(template_id))
        except TemplateStoreError as e:
            if e.unavailable:
                raise dependency_unavailable("template-store", f"template {template_id!r}", e) from e
            return None

    async def _gate_refusal(
        self, template_client: Any, idx: int, req: Any, vr: Any,
        namespace: str, placements: dict[str, str] | None,
    ) -> BulkResultItem | None:
        """Stage 1b for one validated item: its refusal row, or None to write it.

        The single-item create path (create_document) enforces the
        cross-namespace rule for usage=relationship templates and namespace
        isolation; the bulk path applies the identical checks, in the same
        order, or the guardrail holds on one path only.
        """
        try:
            tmpl = await self._gate_template(template_client, req.template_id)
        except DependencyUnavailableError as e:
            # The relationship constraints cannot be checked: the item is
            # refused as an outage, never written unchecked.
            return BulkResultItem(index=idx, status="error", **error_fields(e))
        if tmpl and tmpl.get("usage") == "relationship":
            rel_error = await self._validate_relationship_constraints(tmpl, vr, namespace)
            if rel_error:
                code, message = rel_error
                return BulkResultItem(index=idx, status="error", error=message, error_code=code)
            # Relationship indexes are created lazily on first write. Doing it
            # only on the single-item path left a namespace that ingests edges
            # exclusively in bulk without the data.source_ref / data.target_ref
            # indexes the /relationships and /traverse queries depend on
            # (CASE-795 F21). Idempotent and per-template-cached, so the cost
            # is one call per distinct template in the batch.
            await self._ensure_relationship_indexes(req.template_id, namespace)
        # Namespace isolation, after the relationship constraints, so an edge's
        # cross-namespace source reports cross_namespace_relationship. The
        # validator caches each namespace's settings, so a batch costs one
        # lookup per namespace.
        try:
            await self._check_new_references(
                namespace, req.template_id, vr, placements=placements,
            )
        except (ReferenceValidationError, RegistryUnavailableError) as e:
            # RegistryUnavailableError: isolation cannot be checked, so the
            # item is refused rather than written unchecked.
            return BulkResultItem(index=idx, status="error", **error_fields(e))
        return None

    async def _apply_reference_gates(
        self,
        validation_results: list,
        namespace: str,
        placements: dict[str, str] | None,
        continue_on_error: bool,
        results: list[BulkResultItem],
    ) -> tuple[list, int]:
        """Stage 1b over the validated items: the ones the reference gates let
        through, and how many they refused (their rows go into ``results``).
        Without continue_on_error the first refusal stops the stage, as stages
        1 and 4 stop: the items before it are still written, the later ones
        are reported skipped.
        """
        template_client = get_template_store_client()
        checked: list = []
        refused = 0
        for pos, entry in enumerate(validation_results):
            idx, req, vr = entry
            refusal = await self._gate_refusal(
                template_client, idx, req, vr, namespace, placements,
            )
            if refusal is None:
                checked.append(entry)
                continue
            refused += 1
            results.append(refusal)
            if not continue_on_error:
                results.extend(
                    BulkResultItem(index=later, status="skipped",
                                   error="Stopped due to previous error")
                    for later, _, _ in validation_results[pos + 1:]
                )
                break
        return checked, refused

    async def _bulk_create(
        self,
        items: list[DocumentCreateRequest],
        namespace: str,
        continue_on_error: bool = True,
    ) -> BulkResponse:
        """
        Create multiple documents with optimized bulk operations.

        Optimizations:
        - All validations run first (cached templates; term values resolved
          by def-store, shared across the batch)
        - Single bulk Registry call for all document IDs
        - Batch MongoDB operations for existing document checks

        The response's counts come from the per-item results
        (_finalize_bulk_response), so nothing here keeps a tally.
        """
        timing: dict[str, float] = {}
        total_start = time.perf_counter()

        # Stage 0: Pre-warm the template cache for the batch — each unique
        # template once, including nested template_refs. Term values are not
        # warmed: they resolve through def-store at validation time, shared
        # across the batch by bulk_create's term_resolution_batch.
        start = time.perf_counter()
        await self._warm_templates({item.template_id for item in items})
        timing["0_cache_warmup"] = (time.perf_counter() - start) * 1000

        # Stage 0t: resolve every term value of the batch up front, so each
        # document's term validation is answered from the batch instead of
        # sending its own def-store request.
        start = time.perf_counter()
        await self.validation_service.prefetch_term_values(
            [(item.template_id, item.template_version, item.data) for item in items],
            namespace,
        )
        timing["0t_term_prefetch"] = (time.perf_counter() - start) * 1000

        # Stage 1: Validate all documents (see _validate_batch — concurrent
        # when continue_on_error, sequential stop-on-first otherwise). On the
        # sequential stop path, items that validated before the failure are in
        # validation_results and proceed to creation (CASE-413, Option 2); the
        # failing item's error row and the trailing skipped rows are already in
        # results.
        validation_results, results, _ = await self._validate_batch(
            items, namespace, continue_on_error, timing
        )
        _add_validation_timing(validation_results, timing)

        # Stage 1b: relationship-document constraints (CASE-788). The single-item
        # create path (create_document) enforces the cross-namespace rule
        # for usage=relationship templates; the bulk path
        # must apply the identical check or the guardrail holds on one path only.
        # validation_service.validate already populated each reference's resolved
        # {namespace, status}, so this reuses _validate_relationship_constraints.
        start = time.perf_counter()
        placements = await self._place_batch_references(namespace, validation_results)
        validation_results, _ = await self._apply_reference_gates(
            validation_results, namespace, placements, continue_on_error, results,
        )
        timing["1b_reference_gates"] = (time.perf_counter() - start) * 1000

        if not validation_results:
            return self._finalize_bulk_response(results, len(items), timing, total_start)

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Stage 2: Bulk request IDs from Registry (sends identity_values,
        # gets back identity_hash for each item)
        start = time.perf_counter()
        try:
            registry_results = await get_registry_client().generate_document_ids_bulk(
                self._registry_items(validation_results),
                created_by=actor,
                namespace=namespace,
            )
        except RegistryError as e:
            # All valid documents fail due to Registry error
            results.extend(
                BulkResultItem(index=i, status="error", **error_fields(e))
                for i, _item, _ in validation_results
            )
            timing["2_registry_bulk"] = (time.perf_counter() - start) * 1000
            return self._finalize_bulk_response(results, len(items), timing, total_start)

        timing["2_registry_bulk"] = (time.perf_counter() - start) * 1000

        _assign_identity_hashes(validation_results, registry_results)
        await self._refuse_document_id_mismatches(validation_results, registry_results)

        # Stage 3: Batch check for existing documents using Registry-resolved IDs.
        # Uses document_id (from Registry), NOT identity_hash — local
        # identity_hash lookups can match across templates (CASE-36).
        start = time.perf_counter()
        existing_by_doc_id = await self._find_existing_for_batch(namespace, registry_results)
        timing["3_find_existing"] = (time.perf_counter() - start) * 1000

        # Stage 4: Create all documents
        # Track documents created within this batch so that duplicate identity
        # hashes in the same batch use the correct latest version instead of
        # the stale Stage 3 snapshot.
        # NATS events are collected and published concurrently after the loop
        # to avoid per-document ACK wait (~10ms each).
        start = time.perf_counter()
        pending_events = await self._create_validated_items(
            validation_results, registry_results, existing_by_doc_id,
            namespace, actor, datetime.now(UTC), continue_on_error, results, timing,
        )
        timing["4_create_documents"] = (time.perf_counter() - start) * 1000

        # Stage 4c: every document this batch created at version 1 gets the
        # template-scoped auto-synonym the single-item path registers — its
        # portable address, the one a restore carries forward (and, for an
        # edge or an identity-less document, the only synonym it has). A
        # refused synonym fails that document, as it fails a single create.
        start = time.perf_counter()
        minted = {
            reg["registry_id"] for reg in registry_results
            if reg.get("status") == "created" and reg.get("registry_id")
        }
        pending_events = await self._register_batch_auto_synonyms(
            results, validation_results, namespace, actor, pending_events, minted,
        )
        timing["4c_auto_synonyms"] = (time.perf_counter() - start) * 1000

        # Stage 5: Publish all NATS events concurrently
        await _publish_bulk_events(pending_events, actor, timing)
        return self._finalize_bulk_response(results, len(items), timing, total_start)

    async def _register_batch_auto_synonyms(
        self,
        results: list[BulkResultItem],
        validation_results: list[Any],
        namespace: str,
        actor: str,
        pending_events: list[tuple[EventType, dict]],
        minted: set[str],
    ) -> list[tuple[EventType, dict]]:
        """Register the batch's auto-synonyms; a document whose synonym was
        refused is rolled back (its Registry entry too, when ``minted`` says
        this batch created it), and its event is not published.

        The documents are written before this runs, so an unexpected failure
        here is not the batch's answer (a 500 would hide what was written):
        each new document's row carries a warning that its auto-synonym's
        state is unknown, and its event is published as written."""
        try:
            rolled_back = await self._register_bulk_auto_synonyms(
                results, validation_results, namespace, actor, minted,
            )
        except Exception as e:
            logger.exception("Auto-synonym registration of a bulk create failed")
            note = (
                f"Auto-synonym registration did not complete ({e}); the document "
                "is written, but whether it resolves by its identity synonym is unknown"
            )
            for result in results:
                if result.status == "created" and result.version == 1:
                    result.warnings = [*(result.warnings or []), note]
            return pending_events
        if not rolled_back:
            return pending_events
        return [
            (et, payload) for et, payload in pending_events
            if payload.get("document_id") not in rolled_back
        ]

    async def _warm_templates(self, template_ids: set[str]) -> None:
        """Load each template of a batch the store answers for into the
        cache once, nested template_refs included, concurrently. Used by
        bulk create and validate-bulk."""
        client = get_template_store_client()
        warmed: set[str] = set()
        await asyncio.gather(*(self._warm_template(client, tid, warmed) for tid in template_ids))

    async def _warm_template(self, client: Any, tid: str, warmed: set[str]) -> None:
        """Recursively warm a template and its nested template_refs."""
        if tid in warmed:
            return
        warmed.add(tid)
        try:
            template = await client.get_template_resolved(tid)
        except Exception as e:  # the warm-up only saves time
            # A template it cannot load, for whatever reason, is left for
            # each item's validation, which refuses that item on its own
            # (dependency_unavailable) instead of failing the batch as a 500.
            logger.warning("Template warm-up skipped %s: %s", tid, e)
            return
        if not template:
            return
        for field in template.get("fields", []):
            for key in ("template_ref", "array_template_ref"):
                ref = field.get(key)
                if ref:
                    await self._warm_template(client, ref, warmed)

    @staticmethod
    async def _place_batch_references(
        namespace: str, validation_results: list[Any],
    ) -> dict[str, str] | None:
        """Namespace isolation can only refuse in a strict namespace; there the
        whole batch's references are placed with one Registry lookup per 500
        ids, not one per document. If that lookup fails (None), each item falls
        back to its own lookup and is refused on its own."""
        try:
            return await get_reference_validator().place_references(
                namespace,
                [
                    {
                        "term_references": vr.term_references,
                        "file_references": vr.file_references,
                        "document_references": vr.references,
                    }
                    for _, _, vr in validation_results
                ],
            )
        except RegistryUnavailableError:
            return None

    @staticmethod
    def _registry_items(validation_results: list[Any]) -> list[dict[str, Any]]:
        """The Registry registration item of each validated document."""
        registry_items = []
        for _, item, vr in validation_results:
            tid = item.template_id
            reg_item: dict[str, Any] = {
                "identity_values": vr.identity_values or None,
                # Submitted spellings, qualified by the template, mint the
                # raw-values synonym; the hash feed (identity_values) carries
                # resolved referent IDs.
                "synonym_values": qualified_synonym_values(
                    vr.template_value, vr.submitted_identity_values,
                ),
                "template_id": tid,
                "has_identity_fields": bool(vr.identity_fields),
                # CASE-430: an edge type gets no bare identity-values
                # synonym. Usage is the one validation read (immutable), so
                # this stage asks template-store nothing.
                "skip_identity_value_synonym": vr.template_usage == "relationship",
            }
            # A restore/migration (document_id + version) asks for its exact
            # id. A document_id without a version is an upsert: the identity
            # decides and the id is checked against it below, as on the
            # single-item path.
            if item.document_id and item.version is not None:
                reg_item["entry_id"] = item.document_id
            registry_items.append(reg_item)
        return registry_items

    @staticmethod
    async def _find_existing_for_batch(
        namespace: str, registry_results: list[dict[str, Any]],
    ) -> dict[str, Document]:
        """The stored document each known identity of the batch continues:
        its active version, or for a soft-deleted identity its latest one."""
        registry_doc_ids = [
            r.get("registry_id") for r in registry_results
            if r.get("status") == "already_exists" and r.get("registry_id")
        ]
        existing_docs = await Document.find({
            "namespace": namespace,
            "document_id": {"$in": registry_doc_ids},
            "status": DocumentStatus.ACTIVE.value
        }).to_list() if registry_doc_ids else []
        existing_by_doc_id = {doc.document_id: doc for doc in existing_docs}
        await _add_latest_of_retired(namespace, registry_doc_ids, existing_by_doc_id)
        return existing_by_doc_id


    async def _create_validated_items(
        self,
        validation_results: list[Any],
        registry_results: list[dict[str, Any]],
        existing_by_doc_id: dict[str, Document],
        namespace: str,
        actor: str,
        now: datetime,
        continue_on_error: bool,
        results: list[BulkResultItem],
        timing: dict[str, float],
    ) -> list[tuple[EventType, dict]]:
        """Write every validated document, appending its result row; returns
        the events to publish.

        Plain creates and new versions are planned in memory and written in
        bulk after the loop (_write_planned); everything else, and every item
        of a document that has such an item, keeps the per-item path.
        Stop-on-first-error keeps the per-item path for the whole batch: a
        bulk write cannot stop at the failing item.
        """
        pending_events: list[tuple[EventType, dict]] = []
        per_item_ids = (
            await self._per_item_document_ids(
                validation_results, registry_results, existing_by_doc_id,
            )
            if continue_on_error else None
        )
        planned: list[_PlannedWrite] = []
        retiring: dict[Any, Document] = {}

        for idx, ((i, item, validation_result), registry_result) in enumerate(
            zip(validation_results, registry_results, strict=False)
        ):
            try:
                outcome = await self._create_one_validated(
                    i, item, validation_result, registry_result, existing_by_doc_id,
                    namespace, actor, now, per_item_ids, retiring,
                )
            except Exception as e:
                results.append(BulkResultItem(
                    index=i, status="error", **error_fields(e)
                ))
                if not continue_on_error:
                    # Mark remaining as skipped
                    results.extend(
                        BulkResultItem(
                            index=remaining[0], status="skipped",
                            error="Stopped due to previous error",
                        )
                        for remaining in validation_results[idx + 1:]
                    )
                    break
                continue
            if isinstance(outcome, _PlannedWrite):
                planned.append(outcome)
            else:
                _collect_outcome(outcome, results, pending_events)

        for outcome in await self._write_planned(planned, retiring, namespace, actor, now, timing):
            _collect_outcome(outcome, results, pending_events)
        return pending_events

    async def _create_one_validated(
        self,
        i: int,
        item: DocumentCreateRequest,
        validation_result: Any,
        registry_result: dict[str, Any],
        existing_by_doc_id: dict[str, Document],
        namespace: str,
        actor: str,
        now: datetime,
        per_item_ids: Any,
        retiring: dict[Any, Document],
    ) -> Any:
        """One validated item: planned for the bulk write when it is eligible
        (a _PlannedWrite, or the outcome the plan settled), else written on
        its own."""
        if per_item_ids is not None and self._bulk_eligible(registry_result, per_item_ids):
            return self._plan_validated_item(
                i, item, validation_result, registry_result,
                existing_by_doc_id, namespace, actor, now, retiring,
            )
        return await self._apply_validated_item(
            i, item, validation_result, registry_result,
            existing_by_doc_id, namespace, actor, now,
        )

    @staticmethod
    def _result_to_validation_response(result) -> ValidationResponse:
        """Build a ValidationResponse from a ValidationResult.

        Dry-run: identity_hash is computed locally (no Registry side effects).
        Shared by the singular validate_document and the bulk
        validate_documents_bulk (CASE-419) so both produce identical per-item
        shapes.
        """
        from .identity_service import IdentityService

        identity_hash = None
        if result.valid and result.identity_values:
            identity_hash = IdentityService.compute_hash(result.identity_values)

        return ValidationResponse(
            valid=result.valid,
            errors=[
                ValidationError(
                    field=e.get("field"),
                    code=e.get("code", "error"),
                    message=e.get("message", "Validation error"),
                    details=e.get("details")
                )
                for e in result.errors
            ],
            warnings=result.warnings,
            identity_hash=identity_hash,
            template_version=result.template_version,
            term_references=result.term_references,
            references=result.references,
            file_references=result.file_references
        )

    async def validate_document(
        self,
        template_id: str,
        data: dict[str, Any],
        namespace: str,
    ) -> ValidationResponse:
        """Validate document without saving."""
        result = await self._validate_as_writer(template_id, data, namespace)
        return self._result_to_validation_response(result)

    async def validate_candidate(
        self,
        template_definition: dict[str, Any],
        namespace: str,
        documents: list[dict[str, Any]] | None = None,
        sample_template_id: str | None = None,
        sample_limit: int = 100,
    ) -> list[tuple[str | None, ValidationResponse]]:
        """Validate documents against an INLINE candidate template definition.

        The what-if half of schema evolution: "would my documents validate
        against this draft version?" — answered without creating anything.
        The candidate is never persisted, never cached, never registered; it
        exists only for the duration of this call (deliberately NOT a
        throwaway draft version, which would be an entity with lifecycle and
        would pollute the version catalog per what-if question).

        Two input modes: explicit ``documents`` payloads, or a sample of the
        most recently updated active documents of ``sample_template_id``.
        Returns (document_id, ValidationResponse) pairs in input order
        (document_id is None for explicit payloads).
        """
        payloads: list[tuple[str | None, dict[str, Any]]]
        if documents is not None:
            payloads = [(None, d) for d in documents]
        else:
            docs = await Document.find({
                "namespace": namespace,
                "template_id": sample_template_id,
                "status": DocumentStatus.ACTIVE.value,
            }).sort(
                [("updated_at", SortDirection.DESCENDING)]
            ).limit(sample_limit).to_list()
            payloads = [(d.document_id, d.data) for d in docs]

        # Declared renames on the candidate re-key each document's data
        # exactly as an applied migration would (matching old key moves to
        # the new name) — without this, the sanctioned rename flow
        # false-fails the what-if loop with unknown_field on the old name
        # plus a missing-mandatory on the new one. Same semantics as the
        # migrate path: only re-key when the old key is present and the
        # new one is not.
        renames = dict(template_definition.get("renames") or {})

        out: list[tuple[str | None, ValidationResponse]] = []
        for doc_id, data in payloads:
            if renames:
                data = dict(data)
                for new_key, old_key in renames.items():
                    if old_key in data and new_key not in data:
                        data[new_key] = data.pop(old_key)
            result = await self._validate_as_writer(
                "candidate",
                data,
                namespace,
                template_override=template_definition,
            )
            out.append((doc_id, self._result_to_validation_response(result)))
        return out

    async def validate_documents_bulk(
        self,
        template_id: str,
        items: list[dict[str, Any]],
        namespace: str,
        template_version: int | None = None,
    ) -> list[ValidationResponse]:
        """Validate multiple documents against ONE template without saving (CASE-419).

        Single-template batch: warm the template (and its nested template refs)
        into cache once, then validate every item, term values resolving
        through def-store shared across the batch — mirroring bulk_create. Side-effect-free: identity hashes are
        computed locally, no Registry writes. Results are returned in input
        order. The caller (endpoint) has already resolved template_id and
        checked the namespace read permission.
        """
        # Stage 0: pre-warm the template + its nested template refs once,
        # as bulk_create does. An outage there is left to each item's
        # validation, which refuses it (dependency_unavailable).
        await self._warm_templates({template_id})

        # Shared document-reference cache across the batch (same as
        # _validate_batch) so repeated reference lookups hit cache.
        doc_ref_cache: dict = {}
        responses: list[ValidationResponse] = []
        with term_resolution_batch(), placement_batch():
            for data in items:
                result = await self._validate_as_writer(
                    template_id,
                    data,
                    namespace,
                    template_version=template_version,
                    doc_ref_cache=doc_ref_cache,
                )
                responses.append(self._result_to_validation_response(result))
        return responses

    @staticmethod
    async def _latest_versions_query(
        base: dict[str, Any], status: DocumentStatus | None
    ) -> dict[str, Any]:
        """A filter matching each document's latest version among ``base``.

        With ``status``, only documents whose latest version has that status.
        Grouping every row and sorting the groups made a large namespace's
        listing take seconds. Nearly every document has the shape a write
        leaves — one active row on top of its superseded inactive rows — and
        for those the latest version IS the active row. One lean group (no
        row bodies, no sort) finds the documents that differ, and the result
        is a plain filter:

        - several active rows (a merge restore with ``older_versions: keep``
          leaves them on purpose): only the highest is the latest;
        - no active row (soft-deleted): the highest inactive row is;
        - an active row under a higher inactive one: the inactive one is.
        """
        active = DocumentStatus.ACTIVE.value
        # A document is (namespace, document_id): a listing may span several
        # namespaces, and one namespace's document must not stand in for
        # another's.
        exceptions = await Document.get_motor_collection().aggregate([
            {"$match": base},
            {"$group": {
                "_id": {"ns": "$namespace", "doc": "$document_id"},
                "top": {"$max": "$version"},
                "n_active": {"$sum": {"$cond": [{"$eq": ["$status", active]}, 1, 0]}},
                "top_active": {"$max": {"$cond": [
                    {"$eq": ["$status", active]}, "$version", None]}},
            }},
            {"$match": {"$or": [
                {"n_active": {"$gt": 1}},
                {"n_active": 0},
                {"$expr": {"$lt": ["$top_active", "$top"]}},
            ]}},
            {"$limit": MAX_LATEST_EXCEPTIONS + 1},
        ], allowDiskUse=True).to_list(None)
        if len(exceptions) > MAX_LATEST_EXCEPTIONS:
            # The filter below names these documents' rows by id; past this
            # many it would outgrow a query document. Refused by name instead
            # of failing inside MongoDB.
            raise ValueError(
                f"More than {MAX_LATEST_EXCEPTIONS} of the documents in reach have "
                "a latest version that is not their only active one (most often: "
                "soft-deleted). List them with status=active, or narrow by "
                "namespace or template."
            )

        hidden_active, latest_inactive = await _exception_rows(base, exceptions)

        live: dict[str, Any] = {"status": active}
        if hidden_active:
            live["_id"] = {"$nin": hidden_active}
        retired: dict[str, Any] = {"_id": {"$in": latest_inactive}}

        if status == DocumentStatus.ACTIVE:
            return {**base, **live}
        if status is not None:
            return {**base, **retired, "status": status.value}
        if not latest_inactive:
            return {**base, **live}
        return {**base, "$or": [live, retired]}

    async def _batch_to_responses(self, documents: list[Document]) -> list[DocumentResponse]:
        """Convert a batch of Documents to DocumentResponses with a single aggregation.

        Instead of querying the latest version per-document (N+1 problem),
        does one aggregation for all document_ids in the batch.
        """
        if not documents:
            return []

        # Get unique document_ids in this batch
        doc_ids = list({d.document_id for d in documents})

        # Single aggregation: for each document_id, find the max version
        pipeline = [
            {"$match": {"document_id": {"$in": doc_ids}}},
            {"$group": {
                "_id": "$document_id",
                "latest_version": {"$max": "$version"},
            }},
        ]
        agg_results = await Document.aggregate(pipeline).to_list()
        latest_map = {r["_id"]: r["latest_version"] for r in agg_results}

        # Build responses using the pre-fetched latest version info
        responses = []
        for doc in documents:
            latest_version = latest_map.get(doc.document_id, doc.version)
            is_latest = doc.version == latest_version

            responses.append(DocumentResponse(
                document_id=doc.document_id,
                namespace=doc.namespace,
                template_id=doc.template_id,
                template_version=doc.template_version,
                template_value=doc.template_value,
                identity_hash=doc.identity_hash,
                version=doc.version,
                revision=current_revision(doc),
                data=doc.data,
                term_references=doc.term_references,
                references=doc.references,
                file_references=doc.file_references,
                status=doc.status,
                created_at=doc.created_at,
                created_by=doc.created_by,
                updated_at=doc.updated_at,
                updated_by=doc.updated_by,
                metadata=doc.metadata,
                is_latest_version=is_latest,
                latest_version=latest_version,
            ))
        return responses

    async def _to_response(self, document: Document) -> DocumentResponse:
        """Convert Document to DocumentResponse with latest version info."""
        # Find the latest version for this document_id (stable)
        latest = await Document.find(
            {"document_id": document.document_id}
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()

        if latest:
            is_latest = document.version == latest[0].version
            latest_version = latest[0].version
        else:
            is_latest = True
            latest_version = document.version

        return DocumentResponse(
            document_id=document.document_id,
            namespace=document.namespace,
            template_id=document.template_id,
            template_version=document.template_version,
            template_value=document.template_value,
            identity_hash=document.identity_hash,
            version=document.version,
            revision=current_revision(document),
            data=document.data,
            term_references=document.term_references,
            references=document.references,
            file_references=document.file_references,
            status=document.status,
            created_at=document.created_at,
            created_by=document.created_by,
            updated_at=document.updated_at,
            updated_by=document.updated_by,
            metadata=document.metadata,
            is_latest_version=is_latest,
            latest_version=latest_version,
        )

    async def _update_file_reference_counts(
        self,
        file_references: list[dict[str, Any]],
        delta: int
    ):
        """
        Update reference counts for files.

        Args:
            file_references: List of file reference dicts with file_id
            delta: Change in reference count (+1 for add, -1 for remove)
        """
        if not file_references or not is_file_storage_enabled():
            return

        from .file_service import get_file_service

        file_service = get_file_service()
        for ref in file_references:
            file_id = ref.get("file_id")
            if file_id:
                try:
                    await file_service.update_reference_count(file_id, delta)
                except Exception as e:
                    # Log but don't fail - reference count is best-effort
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Failed to update reference count for file {file_id}: {e}"
                    )


    # ========================================================================
    # POST /documents/migrate  (template-version re-pin — CASE-491)
    # ========================================================================

    async def migrate_document_version(
        self,
        template_id: str,
        from_version: int,
        to_version: int,
        namespace: str,
        dry_run: bool,
    ) -> tuple[DocumentMigrateResponse | None, str | None, str | None]:
        """Migrate every active document pinned to ``from_version`` to ``to_version``.

        Validated, identity-preserving, bulk re-pin (CASE-491). Each document's
        existing data is re-validated against the TARGET version (which must be
        active; the source may be inactive/frozen). On apply a new document
        version is created (or the single version overwritten in place for
        ``versioned: false`` templates), pinned to ``to_version``, with the same
        ``document_id`` and ``identity_hash``. No data transformation happens here.

        Op-level failures return ``(None, error_code, message)`` for the route to
        map to a 4xx; per-document outcomes ride the bulk-first 200 envelope.

        Returns:
            (response, error_code, error_message)
        """
        start = time.perf_counter()

        # ---- Op-level guards (fail the whole migration) ----
        if from_version == to_version:
            return None, "invalid_migration", "from_version and to_version must differ"

        client = get_template_store_client()
        target = await client.get_template_resolved(template_id, version=to_version)
        if target is None:
            return None, "template_not_found", (
                f"Template '{template_id}' version {to_version} not found"
            )
        if target.get("status") != "active":
            return None, "target_inactive", (
                f"Target version {to_version} is not active; migration must target an "
                "active version. Reactivate it first or pick an active target."
            )

        source = await client.get_template_resolved(template_id, version=from_version)
        if source is None:
            return None, "template_not_found", (
                f"Template '{template_id}' version {from_version} not found"
            )

        # Identity-preserving only: a change in identity_fields would re-key every
        # document (the CASE-36/316 collision class). That is a fork, not a migrate.
        source_idf = list(source.get("identity_fields") or [])
        target_idf = list(target.get("identity_fields") or [])
        if source_idf != target_idf:
            return None, "identity_fields_changed", (
                f"identity_fields differ between v{from_version} ({source_idf}) and "
                f"v{to_version} ({target_idf}); this is a fork (create new documents), "
                "not a migrate."
            )

        # Declared renames on the target version: the one sanctioned data
        # transformation in a migrate. A rename declaration ("new_field
        # renames old_field") means mechanically-same data under a new key —
        # without it a rename is indistinguishable from drop+add and every
        # renamed doc would fail target validation with unknown_field.
        # Anything beyond key-mapping stays app territory.
        renames = dict(target.get("renames") or {})

        # ---- Per-document fan-out (cohort = active docs pinned to from_version) ----
        cohort = await Document.find({
            "namespace": namespace,
            "template_id": template_id,
            "template_version": from_version,
            "status": DocumentStatus.ACTIVE.value,
        }).to_list()

        results: list[BulkResultItem] = []
        for index, doc in enumerate(cohort):
            try:
                results.append(
                    await self._migrate_one(
                        index, doc, template_id, to_version, namespace, dry_run,
                        renames=renames,
                    )
                )
            except DependencyUnavailableError as exc:
                # A service the validation needed did not answer: an outage
                # on this item, never an internal error.
                results.append(BulkResultItem(
                    index=index, status="error", document_id=doc.document_id,
                    **error_fields(exc),
                ))
            except Exception as exc:
                logger.exception(
                    "Unexpected error migrating document %s", doc.document_id,
                )
                results.append(BulkResultItem(
                    index=index, status="error", document_id=doc.document_id,
                    error=f"Internal error: {exc}", error_code="internal_error",
                ))

        succeeded = sum(1 for r in results if r.status != "error")
        failed = sum(1 for r in results if r.status == "error")
        timing = {"total": (time.perf_counter() - start) * 1000}
        return DocumentMigrateResponse(
            results=results, total=len(results), succeeded=succeeded, failed=failed,
            timing=timing, dry_run=dry_run, template_id=template_id,
            from_version=from_version, to_version=to_version,
        ), None, None

    async def get_template_impact_stats(
        self,
        template_id: str,
        namespace: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Live-document counts for template version-change impact analysis.

        Returns per-template_version active-document counts plus, for each
        requested field name, the count of active documents carrying a
        non-empty value at ``data.<field>`` (across all versions). "Non-empty"
        means the key exists and is neither null nor the empty string — an
        empty array or object counts as non-empty, which errs toward NOT
        offering an automatic migration (the safe direction).

        The counts are of ACTIVE ROWS. A write retires the version it
        replaces, so a document has one active row and is counted once — except
        a document a restore kept with several active versions (older_versions
        'keep'), counted once per active row, each under its template_version.

        Read-only and advisory: consumed by template-store's create-as-upsert
        to attach consequence data to a version event.
        """
        match = {
            "namespace": namespace,
            "template_id": template_id,
            "status": DocumentStatus.ACTIVE.value,
        }
        rows = await Document.aggregate([
            {"$match": match},
            {"$group": {"_id": "$template_version", "count": {"$sum": 1}}},
        ]).to_list()
        docs_per_version = {str(r["_id"]): r["count"] for r in rows}

        field_nonempty_counts: dict[str, int] = {}
        # Bounded fan-out: one indexed count per field, capped so a
        # pathological field list cannot turn advisory stats into a scan storm.
        for field in (fields or [])[:50]:
            field_nonempty_counts[field] = await Document.find({
                **match,
                f"data.{field}": {"$exists": True, "$nin": [None, ""]},
            }).count()

        return {
            "template_id": template_id,
            "namespace": namespace,
            "total_live_docs": sum(docs_per_version.values()),
            "docs_per_version": docs_per_version,
            "field_nonempty_counts": field_nonempty_counts,
        }

    async def get_template_facets(
        self,
        namespace: str,
        status: str = "active",
    ) -> dict[str, Any]:
        """Distinct templates one namespace's documents are instances of.

        Grouped from the documents, NOT from template ownership: a document's
        namespace is independent of its template's namespace, so an
        owner-based template listing misses shared and foreign templates the
        namespace's documents actually use.

        Counts are logical documents — version rows collapse on document_id
        before counting, so a heavily-versioned document still counts once.
        The `$match` runs inside the (namespace, template_id, status) index;
        the intermediate group is bounded by the namespace's live documents.

        Each facet carries the template's own namespace so consumers can
        label cross-namespace templates honestly. That lookup is one
        template-cache fetch per DISTINCT template; a fetch failure degrades
        that facet's template_namespace to None instead of failing the call.
        """
        match: dict[str, Any] = {"namespace": namespace}
        if status != "all":
            match["status"] = status
        rows = await Document.aggregate([
            {"$match": match},
            {"$group": {
                "_id": {"template_id": "$template_id", "document_id": "$document_id"},
                "template_value": {"$first": "$template_value"},
            }},
            {"$group": {
                "_id": "$_id.template_id",
                "template_value": {"$first": "$template_value"},
                "document_count": {"$sum": 1},
            }},
            {"$sort": {"document_count": -1, "_id": 1}},
        ]).to_list()

        template_client = get_template_store_client()
        facets: list[dict[str, Any]] = []
        for row in rows:
            template_namespace = None
            try:
                tpl = await template_client.get_template(template_id=row["_id"])
                if tpl:
                    template_namespace = tpl.get("namespace")
            except Exception:
                logger.warning(
                    "template-facets: get_template(%s) failed; leaving "
                    "template_namespace unset", row["_id"],
                )
            facets.append({
                "template_id": row["_id"],
                "template_value": row.get("template_value"),
                "template_namespace": template_namespace,
                "document_count": row["document_count"],
            })
        return {"namespace": namespace, "facets": facets}

    async def _migrate_one(
        self,
        index: int,
        doc: Document,
        template_id: str,
        to_version: int,
        namespace: str,
        dry_run: bool,
        renames: dict[str, str] | None = None,
    ) -> BulkResultItem:
        """Validate one document against the target version; apply the re-pin
        unless ``dry_run``. Validating the document's existing data against the
        target IS the readiness check — a now-removed field still present surfaces
        as ``unknown_field``; a newly-mandatory field missing surfaces as ``required``.

        ``renames`` maps new_field -> old_field (the target version's declared
        renames): matching old keys are re-keyed before validation and the
        re-keyed data is what the applied new document version stores. Identity
        fields can never appear in a rename declaration (template-store rejects
        that at declare time), so the identity hash is unaffected.
        """
        data = dict(doc.data)
        for new_key, old_key in (renames or {}).items():
            if old_key in data and new_key not in data:
                data[new_key] = data.pop(old_key)

        vr = await self._validate_as_writer(
            template_id, data, namespace, template_version=to_version, previous=doc,
        )
        if not vr.valid:
            return BulkResultItem(
                index=index, status="error", document_id=doc.document_id,
                error=self._format_validation_errors(vr.errors),
                error_code="validation_failed", details={"errors": vr.errors},
            )

        # The relationship constraints, as on every other write: a re-pinned
        # edge whose source lies in another namespace is refused.
        rel_error = await self._relationship_refusal(
            template_id, to_version, vr, namespace,
        )
        if rel_error:
            code, message = rel_error
            return BulkResultItem(
                index=index, status="error", document_id=doc.document_id,
                error=message, error_code=code,
            )

        # The reference gates, as on every other write. A reference the
        # document already holds carries over; one the target version makes
        # (a rename moving a value into a reference field) is checked.
        try:
            await get_reference_validator().validate_document_references(
                document_namespace=namespace,
                template_namespace=vr.template_namespace or namespace,
                term_references=vr.term_references,
                file_references=vr.file_references,
                document_references=vr.references,
                previous=doc,
                placements=vr.referent_namespaces or None,
            )
        except ReferenceValidationError as exc:
            return BulkResultItem(
                index=index, status="error", document_id=doc.document_id,
                error=f"Cross-namespace reference violation: {exc.violations}",
                error_code="reference_violation",
                details={"rule": "cross_namespace", "violations": exc.violations},
            )
        except RegistryUnavailableError as exc:
            # Isolation cannot be checked, so the re-pin is refused rather
            # than let through unchecked.
            return BulkResultItem(
                index=index, status="error", document_id=doc.document_id,
                **error_fields(exc),
            )

        # The re-pin carries the stored identity hash forward, so it must be the
        # hash the Registry would compute for this document under the target
        # version: the resolved identity feed validation just built. A hash
        # written under older rules (before identity fed resolved referent ids),
        # or one the target's retyped identity field no longer produces, would
        # otherwise be migrated silently and fork the entity on its next write.
        expected = _identity_hash_of(vr.identity_values)
        if expected != (doc.identity_hash or ""):
            return BulkResultItem(
                index=index, status="error", document_id=doc.document_id,
                error=(
                    "the stored identity hash is not the one this document's identity "
                    f"produces under v{to_version}; re-hash the namespace "
                    "(POST /api/document-store/maintenance/rehash-identities) if it "
                    "predates the current identity rules, or create new documents if "
                    "the target changes what identifies them"
                ),
                error_code="identity_hash_stale",
                details={"stored": doc.identity_hash or "", "expected": expected},
            )

        if dry_run:
            # `version` is the DOCUMENT version, as on the apply and every
            # other bulk result: the one the apply would write. A versioned:
            # false template re-pins in place, keeping the version.
            projected = (
                doc.version + 1
                if await self._is_template_versioned(template_id)
                else doc.version
            )
            return BulkResultItem(
                index=index, status="updated", id=doc.document_id,
                document_id=doc.document_id, identity_hash=doc.identity_hash,
                version=projected, is_new=False,
                template_version=to_version,
            )

        # Apply: reuse the create-new-version machinery, forcing a write even when
        # the data is byte-identical (the re-pin is the change). Reuse the existing
        # document_id + identity_hash — the entity is already registered; migration
        # changes only template_version, so no new Registry resolve is needed.
        migrate_request = DocumentCreateRequest(
            template_id=template_id,
            template_version=to_version,
            namespace=namespace,
            data=data,
            metadata=(doc.metadata.custom if doc.metadata else None),
        )
        vr.identity_hash = doc.identity_hash
        response, error = await self._create_new_version(
            migrate_request, doc, vr,
            document_id=doc.document_id, namespace=namespace,
            force_new_version=True,
        )
        if error:
            return BulkResultItem(
                index=index, status="error", document_id=doc.document_id, **error,
            )
        assert response is not None
        return BulkResultItem(
            index=index, status="updated", id=response.document_id,
            document_id=response.document_id, identity_hash=response.identity_hash,
            version=response.version, template_version=response.template_version,
            is_new=False, warnings=response.warnings,
        )

    # ========================================================================
    # PATCH /documents
    # ========================================================================

    async def bulk_patch(self, items: list[PatchDocumentItem]) -> BulkResponse:
        """Apply JSON Merge Patches to a batch of documents.

        Each item is processed independently. Errors are returned per-item
        in the BulkResponse rather than aborting the batch (per the bulk-first
        contract in docs/api-conventions.md).
        """
        results: list[BulkResultItem] = []
        for index, item in enumerate(items):
            try:
                results.append(await self._patch_one(index, item))
            except PatchError as exc:
                results.append(BulkResultItem(
                    index=index,
                    status="error",
                    document_id=item.document_id,
                    error=exc.message,
                    error_code=exc.code,
                ))
            except DependencyUnavailableError as exc:
                # A service the validation needed did not answer: an outage
                # on this item, never an internal error.
                results.append(BulkResultItem(
                    index=index, status="error", document_id=item.document_id,
                    **error_fields(exc),
                ))
            except Exception as exc:
                logger.exception(
                    "Unexpected error patching document %s", item.document_id,
                )
                results.append(BulkResultItem(
                    index=index,
                    status="error",
                    document_id=item.document_id,
                    error=f"Internal error: {exc}",
                    error_code="internal_error",
                ))

        succeeded = sum(1 for r in results if r.status != "error")
        failed = sum(1 for r in results if r.status == "error")
        return BulkResponse(
            results=results,
            total=len(items),
            succeeded=succeeded,
            failed=failed,
        )

    async def _patch_one(
        self,
        index: int,
        item: PatchDocumentItem,
        max_retries: int = 3,
    ) -> BulkResultItem:
        """Apply a single PATCH item.

        Implements the read-merge-validate-write loop with optimistic
        concurrency. The unique index on (namespace, document_id, version)
        guarantees version uniqueness; on a race, _insert_with_retry handles
        the retry transparently.

        Raises PatchError on per-item failures (caller wraps into BulkResultItem).
        """
        for attempt in range(max_retries):
            # 1. Read latest version; 2. permission on its namespace.
            current = await _read_patch_target(item.document_id)
            await _check_patch_permission(current.namespace)

            # 3. Optimistic concurrency control (per-item if_match), against
            #    the row's write token: its version, or on a row overwritten
            #    in place (versioned:false) the in-place write counter. The
            #    in-place write below re-checks it atomically.
            read_revision = current.revision
            if item.if_match is not None and current_revision(current) != item.if_match:
                raise PatchError(
                    "concurrency_conflict",
                    f"Expected revision {item.if_match}, current is "
                    f"{current_revision(current)}",
                )

            # 4. Apply RFC 7396 merge to current data, and (when supplied) to
            #    the document's custom metadata. metadata_patch=None means the
            #    caller did not address metadata — it carries forward as-is.
            #    Platform-owned metadata (warnings, source_system) is never
            #    patchable; only the custom bag is.
            merged_data = json_merge_patch(current.data, item.patch)
            merged_custom = (
                json_merge_patch(dict(current.metadata.custom), item.metadata_patch)
                if item.metadata_patch is not None
                else dict(current.metadata.custom)
            )

            # 5-6. Validate against the document's own template version, and
            #      keep its identity.
            validation_result = await self._validate_patch(current, merged_data)

            # 7. Relationship constraints and the reference gates, before the
            #    no-op check: an edge whose source lies in another namespace
            #    is refused even when the PATCH changes nothing, rather than
            #    reported unchanged as if it were acceptable. References the
            #    current version holds carry over, so the gates check nothing
            #    for an unchanged PATCH.
            await self._check_patch_references(current, validation_result)

            # 8. No-op detection — if data, all 3 reference arrays, and the
            #    merged custom metadata are byte-equal to the current version,
            #    return without bumping. A metadata-only delta is a real
            #    change and falls through to the write like any other.
            if not self._document_has_changed(
                current,
                merged_data,
                validation_result.term_references,
                validation_result.references,
                validation_result.file_references,
                new_metadata_custom=merged_custom,
            ):
                return _patch_result(index, current, "unchanged", validation_result.warnings)

            # 9. Write. Phase-3 branch: templates with versioned=False
            #    overwrite the existing document in place.
            actor = get_identity_string()
            now = datetime.now(UTC)
            if not await self._is_template_versioned(current.template_id):
                written = await self._patch_in_place(
                    index, item, current, read_revision, merged_data, merged_custom,
                    validation_result, actor, now,
                )
            else:
                written = await self._patch_new_version(
                    index, current, merged_data, merged_custom, validation_result,
                    actor, now, last_attempt=attempt >= max_retries - 1,
                    max_retries=max_retries,
                )
            if written is not None:
                return written
            # Lost a race: re-read and re-merge onto the winner.

        # All retries exhausted — should be unreachable because the loop body
        # either returns or raises, but defensive in case the loop falls through.
        raise PatchError(
            "concurrency_conflict",
            f"Failed after {max_retries} retries due to concurrent writers",
        )

    async def _validate_patch(self, current: Document, merged_data: dict[str, Any]) -> Any:
        """Steps 5-6: the merged document must validate, its template must
        declare an identity, and that identity must not change."""
        # 5. Re-validate the merged document against the SAME template
        #    version the existing document was created with (design §13).
        validation_result = await self._validate_as_writer(
            current.template_id,
            merged_data,
            current.namespace,
            template_version=current.template_version,
            previous=current,
        )
        if not validation_result.valid:
            # CASE-490: a PATCH whose pinned template version is inactive
            # (the version was deactivated after the doc was created) gets a
            # distinct, branchable error_code instead of being buried in the
            # generic validation_failed — so a caller (and the kb gateway,
            # which should map it to a 4xx, not a 502) can detect a "frozen
            # template" cleanly. Remediation: reactivate the version
            # (reactivate_template), or migrate the doc to an active version.
            frozen = any(e.get("code") == "template_inactive" for e in validation_result.errors)
            raise PatchError(
                "template_inactive" if frozen else "validation_failed",
                self._format_validation_errors(validation_result.errors),
            )

        # 5b. Append-only guard (CASE-478). A template with no
        #     identity_fields declares append-only: every document is a
        #     standalone record addressed only by a surrogate document_id,
        #     not a logical identity. PATCH operates on logical entities, so
        #     there is nothing to update here — fail loud with remediation
        #     rather than silently mutating a surrogate-keyed row (which
        #     would let any holder of the document_id repurpose it). The
        #     guard reads identity_fields off the template (via the
        #     validation result), not the per-doc identity_hash. It sits
        #     after the validity check so identity_fields is populated, and
        #     in the shared apply loop so REST, MCP update_document, and
        #     bulk_patch all enforce it identically.
        if not validation_result.identity_fields:
            raise PatchError(
                "append_only",
                "This template declares no identity_fields (append-only): "
                "each document is a standalone record addressed only by "
                "document_id, not a logical identity, so it cannot be "
                "PATCHed. To change the data, create a new document; to "
                "make this template updatable, declare identity_fields on "
                "the template.",
            )
        self._check_identity_unchanged(current, merged_data, validation_result)
        return validation_result

    def _check_identity_unchanged(
        self, current: Document, merged_data: dict[str, Any], validation_result: Any,
    ) -> None:
        """6. Identity-field invariant (design §3). An identity field may
        change its spelling but not what it names: a term or reference
        identity field is compared by the referent it resolves to (another
        alias of the same term is the same identity), every other field by
        value. Dot-path lookup keeps nested identity fields (e.g.
        "address.city") in reach."""
        stored_feed = self._stored_identity_feed(current, validation_result)
        for field_path in validation_result.identity_fields or []:
            old_val = IdentityService._get_nested_value(current.data, field_path)
            new_val = IdentityService._get_nested_value(merged_data, field_path)
            if old_val == new_val or _same_referent(field_path, stored_feed, validation_result):
                continue
            if stored_feed is None and field_path in validation_result.referent_identity_fields:
                # The stored version holds no resolution to compare by
                # referent, so only the spelling was compared: say that, not
                # that the change names another entity.
                raise PatchError(
                    "identity_field_change",
                    f"Cannot patch identity field '{field_path}': its spelling "
                    "changed and the stored version holds no resolution of it "
                    "(namespace validation reports this as "
                    "identity_resolution_missing), so the change cannot be "
                    "shown to name the same entity. A document's identity "
                    "cannot change.",
                )
            raise PatchError(
                "identity_field_change",
                f"Cannot patch identity field '{field_path}': the change "
                "names a different entity, and a document's identity "
                "cannot change. POST the new values to create a separate "
                "document (this one stays as it is).",
            )

    async def _check_patch_references(self, current: Document, validation_result: Any) -> None:
        """Relationship constraints, as on create and before the gates: an
        edge's source lives in the edge's namespace, whatever the namespace
        settings. A PATCH re-pointing source_ref (an edge type whose identity
        does not include it) is refused.

        Then cross-namespace reference validation, the same gates as the POST
        flow. A PATCH is a new write, so a reference it adds is checked;
        references the current version already holds carry over unchecked,
        so a document whose references a namespace setting or a revoked grant
        would now refuse stays patchable."""
        rel_error = await self._relationship_refusal(
            current.template_id, current.template_version,
            validation_result, current.namespace,
        )
        if rel_error:
            raise PatchError(*rel_error)
        try:
            validator = get_reference_validator()
            await validator.validate_document_references(
                document_namespace=current.namespace,
                template_namespace=validation_result.template_namespace
                or current.namespace,
                term_references=validation_result.term_references,
                file_references=validation_result.file_references,
                document_references=validation_result.references,
                previous=current,
                placements=validation_result.referent_namespaces or None,
            )
        except ReferenceValidationError as exc:
            raise PatchError(
                "reference_violation",
                f"Cross-namespace reference violation: {exc.violations}",
            ) from exc
        except RegistryUnavailableError as exc:
            raise PatchError("registry_error", str(exc)) from exc

    async def _patch_in_place(
        self,
        index: int,
        item: PatchDocumentItem,
        current: Document,
        read_revision: Any,
        merged_data: dict[str, Any],
        merged_custom: dict[str, Any],
        validation_result: Any,
        actor: str,
        now: datetime,
    ) -> BulkResultItem | None:
        """A versioned:false template overwrites the existing document in
        place: same document_id, same version, fresh data + updated_at, and
        the row's revision moves on. The write is conditional on the revision
        read at step 1, so a concurrent writer is detected at the write
        itself, not only at step 3. None when it lost that race without a
        token (re-read and re-merge onto the winner)."""
        previous_file_refs = list(current.file_references)
        read_status = current.status
        current.data = merged_data
        current.term_references = validation_result.term_references
        current.references = validation_result.references
        current.file_references = validation_result.file_references
        current.metadata = DocumentMetadata(
            source_system=current.metadata.source_system,
            warnings=validation_result.warnings,
            custom=merged_custom,
        )
        current.updated_at = now
        current.updated_by = actor
        if not await self._write_in_place(current, read_revision, read_status):
            if item.if_match is not None:
                # The caller's token was current at step 3 and is not
                # now: another write landed in between.
                raise PatchError(
                    "concurrency_conflict",
                    f"Expected revision {item.if_match}; the document "
                    "was written concurrently",
                )
            return None

        await publish_document_event(
            EventType.DOCUMENT_UPDATED,
            await self._document_to_event_payload(current),
            changed_by=actor,
        )
        await self._update_file_reference_counts(previous_file_refs, delta=-1)
        await self._update_file_reference_counts(validation_result.file_references, delta=1)
        return _patch_result(index, current, "updated", validation_result.warnings)

    async def _patch_new_version(
        self,
        index: int,
        current: Document,
        merged_data: dict[str, Any],
        merged_custom: dict[str, Any],
        validation_result: Any,
        actor: str,
        now: datetime,
        *,
        last_attempt: bool,
        max_retries: int,
    ) -> BulkResultItem | None:
        """versioned=True: deactivate current, insert v+1. None when it lost
        the version race and another attempt is left."""
        from pymongo.errors import DuplicateKeyError

        prior = _retired_stamps(current)
        current.status = DocumentStatus.INACTIVE
        current.updated_at = now
        current.updated_by = actor
        await current.save()

        # Carry the merged custom metadata (identical to the current
        # version's when no metadata_patch was supplied). Warnings come
        # from the new validation (they describe the merged state).
        new_metadata = DocumentMetadata(
            source_system=current.metadata.source_system,
            warnings=validation_result.warnings,
            custom=merged_custom,
        )

        try:
            new_doc = await self._insert_or_restore(
                prior,
                namespace=current.namespace,
                document_id=current.document_id,
                template_id=current.template_id,
                template_version=current.template_version,
                template_value=cast(str, current.template_value),
                identity_hash=current.identity_hash,  # invariant under PATCH
                version=current.version + 1,
                data=merged_data,
                term_references=validation_result.term_references,
                references=validation_result.references,
                file_references=validation_result.file_references,
                metadata=new_metadata,
                actor=actor,
                now=now,
                # PATCH writes a successor version — the entity was created
                # when v1 was written, not now (CASE-802).
                entity_created_at=current.created_at,
            )
        except DuplicateKeyError:
            # Lost the version race even after _insert_with_retry's internal
            # retries. Try the whole read-merge-write cycle again.
            if last_attempt:
                raise PatchError(
                    "concurrency_conflict",
                    f"Failed after {max_retries} retries due to concurrent writers",
                ) from None
            return None

        # 10. NATS event — reuse DOCUMENT_UPDATED so reporting-sync needs
        #     no changes (a PATCH is just a new version, semantically).
        await publish_document_event(
            EventType.DOCUMENT_UPDATED,
            await self._document_to_event_payload(new_doc),
            changed_by=actor,
        )

        # 11. File reference counts: decrement the old version's refs,
        #     increment the new ones. Same as _create_new_version.
        await self._update_file_reference_counts(
            current.file_references, delta=-1
        )
        await self._update_file_reference_counts(
            validation_result.file_references, delta=1
        )
        return _patch_result(index, new_doc, "updated", validation_result.warnings, revision=False)


# Singleton instance
_service: DocumentService | None = None


def get_document_service() -> DocumentService:
    """Get the singleton document service instance."""
    global _service
    if _service is None:
        _service = DocumentService()
    return _service
