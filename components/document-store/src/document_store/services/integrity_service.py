"""
Referential Integrity Service for Document Store.

Answers "is this namespace's data internally consistent?" — every reference
resolves, and every document's stored identity hash still matches its own
data. It is the referential twin of reporting-sync's parity check: that one
compares MongoDB against PostgreSQL, this one compares MongoDB against itself.

It carries more weight than a health endpoint because **restore performs no
content validation**. Archives are written to MongoDB with `insert_many` —
deliberately, since per-record validation while writing would reintroduce the
read-interleaved-with-write profile the direct-Mongo redesign removed. A
restore is therefore fast and unverified, and this is where the verifying
happens: afterwards, on demand, over a whole namespace.

Checks, by what owns the data:

- **template and term references** go through the service clients, cached per
  ID — they belong to template-store and def-store.
- **document and file references** are read directly, batched: those are
  document-store's own collections, so there is no service boundary to cross
  and a per-reference HTTP call would be pure overhead.
- **identity hashes** are recomputed from the document's own data and its
  template's `identity_fields`. Nothing else in the platform would ever notice
  a hash that stopped matching its content — which is exactly what a botched
  ID re-mint produces.

Iteration is cursor-based and bounded: never more than BATCH_SIZE documents in
memory, and no `skip`, which degrades quadratically over a large collection.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from beanie.odm.enums import SortDirection
from pydantic import BaseModel, Field

from wip_auth.document_identity import (
    build_referent_resolutions,
    compute_hash,
    extract_identity_values,
    referent_identity_fields,
    substitute_resolved_referents,
)
from wip_auth.resolve import EntityNotFoundError, resolve_entity_id

from ..models.document import Document
from ..models.file import File
from .def_store_client import DefStoreError, get_def_store_client
from .template_store_client import TemplateStoreError, get_template_store_client

# Batch size for cursor iteration — bounds memory usage
BATCH_SIZE = 500


class IntegrityIssue(BaseModel):
    """A single referential integrity issue."""

    type: str = Field(
        ...,
        description=(
            "Issue type: orphaned_template_ref, inactive_template_ref, "
            "orphaned_term_ref, orphaned_document_ref, "
            "misplaced_document_ref, orphaned_file_ref, "
            "identity_hash_mismatch, identity_field_missing, "
            "identity_resolution_missing, template_version_not_found"
        )
    )
    severity: str = Field(
        default="warning",
        description="Severity: error, warning, info"
    )
    document_id: str = Field(..., description="Document with the issue")
    template_id: str = Field(..., description="Template ID of the document")
    version: int = Field(..., description="Document version")
    field_path: str | None = Field(
        default=None,
        description="Field path (e.g., 'gender', 'addresses[0].country')"
    )
    reference: str = Field(..., description="The reference value")
    message: str = Field(..., description="Human-readable description")


class IntegritySummary(BaseModel):
    """Summary of integrity check results."""

    total_documents: int = 0
    documents_checked: int = 0
    documents_with_issues: int = 0
    orphaned_template_refs: int = 0
    orphaned_term_refs: int = 0
    inactive_template_refs: int = 0
    orphaned_document_refs: int = 0
    misplaced_document_refs: int = 0
    orphaned_file_refs: int = 0
    identity_hash_mismatches: int = Field(
        default=0,
        description="Documents whose stored identity hash differs from the one recomputed",
    )
    identity_unverified: int = Field(
        default=0,
        description=(
            "Documents whose identity could not be compared: an identity value "
            "or resolution missing, or the template version they are pinned "
            "to not found"
        ),
    )
    checks_not_run: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Checks skipped because a service could not answer, counted by "
            "'<check>:<service>' (e.g. 'term_reference:def-store'). Any count "
            "makes the status incomplete: an unchecked reference is not a "
            "healthy one."
        ),
    )


class IntegrityCheckResult(BaseModel):
    """Result of an integrity check."""

    status: str = Field(
        ...,
        description=(
            "Overall status: error or warning by the worst issue; incomplete "
            "when some checks could not run (summary.checks_not_run) and "
            "nothing was found; healthy only when every check ran"
        )
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    summary: IntegritySummary = Field(default_factory=IntegritySummary)
    issues: list[IntegrityIssue] = Field(default_factory=list)


# Cache for checked references to avoid repeated lookups
# (template_id, version) -> (exists, status). A document is pinned to one
# template version, and versions differ in status (an old version may be
# deactivated while the latest stays active), so the check reads, and caches,
# the version each document is pinned to. exists is False only when the
# template is missing whole; a missing version of an existing template has
# status "version_missing" and is reported by the identity check
# (template_version_not_found).
_template_check_cache: dict[tuple[str, int], tuple[bool, str]] = {}
_term_check_cache: dict[str, bool] = {}  # term_id -> exists
# (template_id, version) -> its identity shape, or _MISSING when the template
# store has no such version. Identity verification needs the field list, and
# fetching it per document would be one HTTP call per row.
_identity_fields_cache: dict[tuple[str, int], Any] = {}

# Checks skipped in this run because the service they ask could not answer,
# by "<check>:<service>". Reset with the caches at the start of each run.
_checks_not_run: dict[str, int] = {}


def _not_run(check: str, service: str) -> None:
    key = f"{check}:{service}"
    _checks_not_run[key] = _checks_not_run.get(key, 0) + 1


def clear_integrity_cache():
    """Clear the integrity check cache (call between checks if needed)."""
    global _template_check_cache, _term_check_cache, _identity_fields_cache
    _template_check_cache.clear()
    _term_check_cache.clear()
    _identity_fields_cache.clear()
    _checks_not_run.clear()


async def check_template_reference(
    template_id: str,
    document: Document,
    issues: list[IntegrityIssue]
) -> None:
    """
    Check if a template reference is valid.

    Args:
        template_id: Template ID
        document: Document containing the reference
        issues: List to append issues to
    """
    global _template_check_cache

    key = (template_id, document.template_version)
    if key in _template_check_cache:
        exists, status = _template_check_cache[key]
    else:
        template_store = get_template_store_client()
        try:
            template = await template_store.get_template(
                template_id=template_id, version=document.template_version,
            )
            if template is not None:
                exists, status = True, template.get("status", "unknown")
            elif await template_store.get_template(template_id=template_id) is None:
                exists, status = False, "not_found"
            else:
                exists, status = True, "version_missing"
            _template_check_cache[key] = (exists, status)
        except TemplateStoreError:
            # The template store could not answer: the check did not run,
            # which makes the result incomplete — never healthy.
            _not_run("template_reference", "template-store")
            return

    if not exists:
        issues.append(IntegrityIssue(
            type="orphaned_template_ref",
            severity="error",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=None,
            reference=template_id,
            message=f"Template '{template_id}' not found"
        ))
    elif status not in ("active", "version_missing"):
        issues.append(IntegrityIssue(
            type="inactive_template_ref",
            severity="warning",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=None,
            reference=template_id,
            message=f"Template '{template_id}' version {document.template_version} is {status}"
        ))


async def check_term_reference(
    term_id: str,
    document: Document,
    field_path: str,
    issues: list[IntegrityIssue]
) -> None:
    """
    Check if a term reference is valid.

    Args:
        term_id: Term ID — canonical UUID as stored on the document (the
            2-part 'GENDER:Male' shorthand is rejected by def-store)
        document: Document containing the reference
        field_path: Field path in the document
        issues: List to append issues to
    """
    global _term_check_cache

    # Check cache first
    if term_id in _term_check_cache:
        exists = _term_check_cache[term_id]
    else:
        # Fetch from Def-Store
        def_store = get_def_store_client()
        try:
            term = await def_store.get_term(term_id)
            exists = term is not None
            _term_check_cache[term_id] = exists
        except DefStoreError:
            # def-store could not answer: the check did not run, which makes
            # the result incomplete — never healthy.
            _not_run("term_reference", "def-store")
            return

    if not exists:
        issues.append(IntegrityIssue(
            type="orphaned_term_ref",
            severity="error",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=field_path,
            reference=term_id,
            message=f"Term '{term_id}' not found"
        ))


def extract_term_ids(term_references: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """
    Extract all term IDs from a term_references array.

    New array format: [{"field_path": "gender", "term_id": "019abc42-...", ...}, ...]

    Returns:
        List of (field_path, term_id) tuples
    """
    results = []

    for ref in term_references:
        field_path = ref.get("field_path", "")
        term_id = ref.get("term_id", "")
        if term_id:
            results.append((field_path, term_id))

    return results


class _DocumentRef(BaseModel):
    """One resolved document reference, as a document holds it."""

    field_path: str
    target: str
    claimed_namespace: str | None
    lookup_value: str | None


def _extract_document_refs(document: Document) -> list[_DocumentRef]:
    """Every resolved document reference, with the namespace it claims and
    the lookup half it was resolved from."""
    refs: list[_DocumentRef] = []
    for ref in document.references or []:
        resolved = ref.get("resolved") or {}
        target = resolved.get("document_id")
        if target:
            lookup = ref.get("lookup_value")
            refs.append(_DocumentRef(
                field_path=ref.get("field_path", ""),
                target=target,
                claimed_namespace=resolved.get("namespace"),
                lookup_value=lookup if isinstance(lookup, str) else None,
            ))
    return refs


async def _lookup_hint(
    ref: _DocumentRef,
    namespace: str,
    hints: dict[tuple[str, str], str],
) -> str:
    """What the reference's lookup half resolves to on this install, as a
    message suffix.

    A reference carries two halves: the identifier it was written with and
    the document id that identifier resolved to. A restore that re-mints ids
    carries the second half over as it was, so for a target the archive did
    not hold, the id can name another install's document, or this install's
    document under the wrong namespace. The lookup half is what says which
    document was meant. Naming its resolution here points the reader at the
    repair. A lookup that is the held id itself adds nothing and gets no
    hint. Read path: the resolver's cache is fine. The lookup resolves in
    the referencing document's namespace, as it did when it was written.
    """
    lookup = ref.lookup_value
    if not lookup or lookup == ref.target:
        return ""
    key = (lookup, namespace)
    if key not in hints:
        try:
            resolved = await resolve_entity_id(lookup, "document", namespace)
            hints[key] = f"; its lookup value '{lookup}' resolves to '{resolved}' here"
        except EntityNotFoundError:
            # Not found and ambiguous land here; the wording claims no more
            # than that. A Registry that does not answer is not caught: it
            # fails the check (503) rather than reading as unresolvable.
            hints[key] = f"; its lookup value '{lookup}' could not be resolved here"
    return hints[key]


def _extract_file_refs(document: Document) -> list[tuple[str, str]]:
    """(field_path, file_id) for every file reference."""
    return [
        (ref.get("field_path", ""), ref["file_id"])
        for ref in document.file_references or []
        if ref.get("file_id")
    ]


async def check_batch_references(
    documents: list[Document], issues: list[IntegrityIssue]
) -> None:
    """Check document and file references for a whole batch at once.

    Both targets live in document-store's own collections, so existence is a
    batched ``$in`` query rather than a per-reference service call. References
    may point across namespaces — a document referencing one in the shared
    ``wip`` namespace is legitimate — so the probe is not namespace-scoped.

    Existence alone is not enough, though. A reference also records the
    namespace its target lives in, and the held id must be a document of
    that namespace. A fresh restore maps the recorded namespace to the new
    one even when the id itself was not re-minted (its target was not in the
    archive), producing a reference that names the new namespace and an old
    id. On the source install that id still exists, so an existence check
    passes it while the reference silently points across namespaces.
    """
    wanted_docs: dict[str, list[tuple[Document, _DocumentRef]]] = {}
    wanted_files: dict[str, list[tuple[Document, str]]] = {}
    for document in documents:
        for ref in _extract_document_refs(document):
            wanted_docs.setdefault(ref.target, []).append((document, ref))
        for field_path, file_id in _extract_file_refs(document):
            wanted_files.setdefault(file_id, []).append((document, field_path))

    if wanted_docs:
        # Every version of a document shares its namespace, so one row per
        # document_id is the whole answer.
        homes: dict[str, str] = {
            row["_id"]: row["namespace"]
            async for row in Document.get_motor_collection().aggregate([
                {"$match": {"document_id": {"$in": list(wanted_docs)}}},
                {"$group": {"_id": "$document_id", "namespace": {"$first": "$namespace"}}},
            ])
        }
        hints: dict[tuple[str, str], str] = {}
        for target, holders in wanted_docs.items():
            home = homes.get(target)
            for document, ref in holders:
                if home is None:
                    issue_type = "orphaned_document_ref"
                    message = f"Referenced document '{target}' not found"
                elif ref.claimed_namespace and ref.claimed_namespace != home:
                    issue_type = "misplaced_document_ref"
                    message = (
                        f"Reference names namespace '{ref.claimed_namespace}' "
                        f"but document '{target}' lives in '{home}'"
                    )
                else:
                    continue
                message += await _lookup_hint(ref, document.namespace, hints)
                issues.append(IntegrityIssue(
                    type=issue_type,
                    severity="error",
                    document_id=document.document_id,
                    template_id=document.template_id,
                    version=document.version,
                    field_path=ref.field_path,
                    reference=target,
                    message=message,
                ))

    if wanted_files:
        found = set(
            await File.get_motor_collection().distinct(
                "file_id", {"file_id": {"$in": list(wanted_files)}}
            )
        )
        for file_id, file_holders in wanted_files.items():
            if file_id in found:
                continue
            for document, field_path in file_holders:
                issues.append(IntegrityIssue(
                    type="orphaned_file_ref",
                    severity="error",
                    document_id=document.document_id,
                    template_id=document.template_id,
                    version=document.version,
                    field_path=field_path,
                    reference=file_id,
                    message=f"Referenced file '{file_id}' not found",
                ))


# _identity_shape_for's answer for a template version the store does not have.
_MISSING = object()


async def _identity_shape_for(
    template_id: str, version: int
) -> Any:
    """The template version's (identity_fields, fields, referent_identity_paths),
    cached. ``_MISSING`` = the store has no such version (cached); None = the
    store could not be asked (not cached).

    Both halves are needed to recompute a hash: identity_fields names the
    feed, and the field definitions decide which identity fields are
    referent-typed (resolved-referent rule).
    """
    key = (template_id, version)
    if key in _identity_fields_cache:
        return _identity_fields_cache[key]

    template_store = get_template_store_client()
    try:
        template = await template_store.get_template(
            template_id=template_id, version=version
        )
    except TemplateStoreError:
        return None  # not cached: a transient failure should not stick
    shape: Any = (
        _MISSING
        if template is None
        else (
            list(template.get("identity_fields") or []),
            list(template.get("fields") or []),
            template.get("referent_identity_paths"),
        )
    )
    _identity_fields_cache[key] = shape
    return shape


async def check_identity_hash(
    document: Document, issues: list[IntegrityIssue]
) -> None:
    """Recompute the document's identity hash and compare it to the stored one.

    This is the check nothing else in the platform performs. The hash decides
    whether a write is a new version or a new document (PoNIF #3), and it is
    written once at create time — so a hash that has drifted from its own data
    stays wrong silently, and the next write on that identity lands in the
    wrong place. A restore that rewrote identity-bearing reference values
    without recomputing is exactly how that happens.

    A template with no identity_fields is append-only: its documents carry an
    empty hash by contract, and anything else is a defect.
    """
    shape = await _identity_shape_for(
        document.template_id, document.template_version
    )
    if shape is _MISSING:
        # The template store has no such version. A template missing whole is
        # the orphaned-template check's to report; a missing VERSION of an
        # existing template is reported by nothing else.
        known = _template_check_cache.get(
            (document.template_id, document.template_version)
        )
        if known is None or known[0]:
            issues.append(IntegrityIssue(
                type="template_version_not_found",
                severity="error",
                document_id=document.document_id,
                template_id=document.template_id,
                version=document.version,
                field_path=None,
                reference=f"{document.template_id}@v{document.template_version}",
                message=(
                    f"Template version {document.template_version} not found; "
                    "the document's identity cannot be verified"
                ),
            ))
        return
    if shape is None:
        # The template store could not answer: not a finding, and not cached,
        # but the check did not run (the result is incomplete).
        _not_run("identity_hash", "template-store")
        return
    identity_fields, template_fields, referent_paths = shape

    if not identity_fields:
        if document.identity_hash:
            issues.append(IntegrityIssue(
                type="identity_hash_mismatch",
                severity="error",
                document_id=document.document_id,
                template_id=document.template_id,
                version=document.version,
                field_path=None,
                reference=document.identity_hash,
                message=(
                    "Template declares no identity_fields (append-only) but "
                    "the document carries an identity hash"
                ),
            ))
        return

    try:
        values = extract_identity_values(document.data, identity_fields)
    except ValueError as exc:
        issues.append(IntegrityIssue(
            type="identity_field_missing",
            severity="error",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=None,
            reference=",".join(identity_fields),
            message=f"Identity cannot be computed: {exc}",
        ))
        return

    # Resolved-referent rule: a term/reference identity field feeds its
    # stored resolution's canonical ID, not the data spelling. A referent
    # identity field with no stored resolution (a legacy doc written during
    # a resolution outage) cannot be verified — that is its own defect, and
    # exactly what the identity migration re-resolves.
    try:
        values = substitute_resolved_referents(
            values,
            referent_identity_fields(identity_fields, template_fields, referent_paths),
            build_referent_resolutions(
                document.term_references, document.references
            ),
        )
    except ValueError as exc:
        issues.append(IntegrityIssue(
            type="identity_resolution_missing",
            severity="error",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=None,
            reference=",".join(identity_fields),
            message=(
                f"Identity cannot be verified from stored resolutions: {exc}"
            ),
        ))
        return

    expected = compute_hash(values)
    if expected != document.identity_hash:
        issues.append(IntegrityIssue(
            type="identity_hash_mismatch",
            severity="error",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=None,
            reference=document.identity_hash,
            message=(
                "Stored identity hash does not match the document's own data "
                f"(expected {expected})"
            ),
        ))


async def check_document_integrity(document: Document) -> list[IntegrityIssue]:
    """
    Check all references in a single document.

    Returns:
        List of integrity issues found
    """
    issues: list[IntegrityIssue] = []

    # Check template reference
    await check_template_reference(
        document.template_id,
        document,
        issues
    )

    # Check term references
    if document.term_references:
        term_refs = extract_term_ids(document.term_references)
        for field_path, term_id in term_refs:
            await check_term_reference(term_id, document, field_path, issues)

    return issues


async def check_all_documents(
    status_filter: str | None = None,
    template_id_filter: str | None = None,
    limit: int = 0,
    check_term_refs: bool = True,
    recent_first: bool = False,
    namespace: str | None = None,
    check_identity: bool = True,
    progress: Any = None,
) -> IntegrityCheckResult:
    """Check referential and identity integrity across a document set.

    Args:
        status_filter: Only documents with this status.
        template_id_filter: Only documents of this template.
        limit: Stop after this many documents (0 = all).
        check_term_refs: Check term references (one cached service call per
            distinct term).
        recent_first: Check the most recently created documents first.
        namespace: Restrict to one namespace — the usual way to run this, and
            what makes it a post-restore verification rather than a
            whole-instance audit.
        check_identity: Recompute and compare identity hashes.
        progress: Optional ``callable(checked, total)`` invoked per batch, so a
            long run can report against a job record.

    Iteration is a plain cursor. The previous implementation paged with
    ``skip``, which makes MongoDB walk and discard every preceding document on
    each batch — quadratic, and unusable on the namespace sizes this is meant
    to verify.
    """
    clear_integrity_cache()

    query = _document_query(namespace, status_filter, template_id_filter)
    total_count = await Document.find(query).count()
    effective_limit = limit if limit > 0 else total_count

    all_issues: list[IntegrityIssue] = []
    documents_checked = 0
    batch: list[Document] = []

    async for document in _scan_cursor(query, recent_first=recent_first, limit=limit):
        batch.append(document)
        if len(batch) < BATCH_SIZE:
            continue

        all_issues += await _check_document_batch(batch, check_term_refs, check_identity)
        documents_checked += len(batch)
        batch = []
        if progress is not None:
            progress(documents_checked, effective_limit)
        # Yield to the event loop between batches so other requests aren't
        # starved by a long-running check.
        await asyncio.sleep(0)
        if documents_checked >= effective_limit:
            break

    if batch and documents_checked < effective_limit:
        del batch[max(0, effective_limit - documents_checked):]
        all_issues += await _check_document_batch(batch, check_term_refs, check_identity)
        documents_checked += len(batch)
        if progress is not None:
            progress(documents_checked, effective_limit)

    summary = _summarize(all_issues, total_count, documents_checked)
    return IntegrityCheckResult(
        status=_overall_status(all_issues, summary),
        summary=summary,
        issues=all_issues,
    )


def _scan_cursor(query: dict[str, Any], *, recent_first: bool, limit: int) -> Any:
    find_q = Document.find(query)
    if recent_first:
        find_q = find_q.sort([("created_at", SortDirection.DESCENDING)])
    if limit > 0:
        # The cursor stops at the limit, so nothing past it is read or checked.
        find_q = find_q.limit(limit)
    return find_q


def _document_query(
    namespace: str | None, status_filter: str | None, template_id_filter: str | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if namespace:
        query["namespace"] = namespace
    if status_filter:
        query["status"] = status_filter
    if template_id_filter:
        query["template_id"] = template_id_filter
    return query


async def _check_document_batch(
    batch: list[Document], check_term_refs: bool, check_identity: bool,
) -> list[IntegrityIssue]:
    """Run every check over one batch; returns its issues."""
    issues: list[IntegrityIssue] = []
    for document in batch:
        await check_template_reference(document.template_id, document, issues)
        if check_term_refs and document.term_references:
            for field_path, term_id in extract_term_ids(document.term_references):
                await check_term_reference(term_id, document, field_path, issues)
        if check_identity:
            await check_identity_hash(document, issues)
    await check_batch_references(batch, issues)
    return issues


# The summary counter of each issue type; identity_unverified gathers three.
_SUMMARY_COUNTERS = {
    "orphaned_template_ref": "orphaned_template_refs",
    "orphaned_term_ref": "orphaned_term_refs",
    "inactive_template_ref": "inactive_template_refs",
    "orphaned_document_ref": "orphaned_document_refs",
    "misplaced_document_ref": "misplaced_document_refs",
    "orphaned_file_ref": "orphaned_file_refs",
    "identity_hash_mismatch": "identity_hash_mismatches",
    "identity_field_missing": "identity_unverified",
    "identity_resolution_missing": "identity_unverified",
    "template_version_not_found": "identity_unverified",
}


def _summarize(
    issues: list[IntegrityIssue], total_count: int, documents_checked: int,
) -> IntegritySummary:
    counts = dict.fromkeys(set(_SUMMARY_COUNTERS.values()), 0)
    for issue in issues:
        counter = _SUMMARY_COUNTERS.get(issue.type)
        if counter:
            counts[counter] += 1
    return IntegritySummary(
        total_documents=total_count,
        documents_checked=documents_checked,
        documents_with_issues=len({issue.document_id for issue in issues}),
        **counts,
        checks_not_run=dict(_checks_not_run),
    )


def _overall_status(issues: list[IntegrityIssue], summary: IntegritySummary) -> str:
    """Anything that breaks resolution or identity is an error; a reference to
    a retired-but-present template is a warning (PoNIF #1 — inactive means
    retired, and existing data keeps resolving). With no finding, a check that
    could not run makes it incomplete: healthy only when every check ran."""
    if any(i.severity == "error" for i in issues):
        return "error"
    if issues:
        return "warning"
    return "incomplete" if summary.checks_not_run else "healthy"
