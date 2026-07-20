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

from wip_auth.document_identity import compute_hash, extract_identity_values

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
            "orphaned_term_ref, orphaned_document_ref, orphaned_file_ref, "
            "identity_hash_mismatch, identity_field_missing"
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
    orphaned_file_refs: int = 0
    identity_hash_mismatches: int = 0


class IntegrityCheckResult(BaseModel):
    """Result of an integrity check."""

    status: str = Field(
        ...,
        description="Overall status: healthy, warning, error"
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    summary: IntegritySummary = Field(default_factory=IntegritySummary)
    issues: list[IntegrityIssue] = Field(default_factory=list)


# Cache for checked references to avoid repeated lookups
_template_check_cache: dict[str, tuple[bool, str]] = {}  # template_id -> (exists, status)
_term_check_cache: dict[str, bool] = {}  # term_id -> exists
# (template_id, version) -> identity_fields, or None when the template could
# not be read. Identity verification needs the field list, and fetching it per
# document would be one HTTP call per row.
_identity_fields_cache: dict[tuple[str, int], list[str] | None] = {}


def clear_integrity_cache():
    """Clear the integrity check cache (call between checks if needed)."""
    global _template_check_cache, _term_check_cache, _identity_fields_cache
    _template_check_cache.clear()
    _term_check_cache.clear()
    _identity_fields_cache.clear()


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

    # Check cache first
    if template_id in _template_check_cache:
        exists, status = _template_check_cache[template_id]
    else:
        # Fetch from Template Store
        template_store = get_template_store_client()
        try:
            template = await template_store.get_template(template_id=template_id)
            if template is None:
                exists, status = False, "not_found"
            else:
                exists, status = True, template.get("status", "unknown")
            _template_check_cache[template_id] = (exists, status)
        except TemplateStoreError:
            # Can't reach Template Store, skip check
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
    elif status != "active":
        issues.append(IntegrityIssue(
            type="inactive_template_ref",
            severity="warning",
            document_id=document.document_id,
            template_id=document.template_id,
            version=document.version,
            field_path=None,
            reference=template_id,
            message=f"Template '{template_id}' is {status}"
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
        term_id: Term ID (UUID or value code, e.g., '019abc42-...' or 'GENDER:Male')
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
            # Can't reach Def-Store, skip check
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


def _extract_document_refs(document: Document) -> list[tuple[str, str]]:
    """(field_path, document_id) for every resolved document reference."""
    refs: list[tuple[str, str]] = []
    for ref in document.references or []:
        resolved = ref.get("resolved") or {}
        target = resolved.get("document_id")
        if target:
            refs.append((ref.get("field_path", ""), target))
    return refs


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
    """
    wanted_docs: dict[str, list[tuple[Document, str]]] = {}
    wanted_files: dict[str, list[tuple[Document, str]]] = {}
    for document in documents:
        for field_path, target in _extract_document_refs(document):
            wanted_docs.setdefault(target, []).append((document, field_path))
        for field_path, file_id in _extract_file_refs(document):
            wanted_files.setdefault(file_id, []).append((document, field_path))

    if wanted_docs:
        found = set(
            await Document.get_motor_collection().distinct(
                "document_id", {"document_id": {"$in": list(wanted_docs)}}
            )
        )
        for target, holders in wanted_docs.items():
            if target in found:
                continue
            for document, field_path in holders:
                issues.append(IntegrityIssue(
                    type="orphaned_document_ref",
                    severity="error",
                    document_id=document.document_id,
                    template_id=document.template_id,
                    version=document.version,
                    field_path=field_path,
                    reference=target,
                    message=f"Referenced document '{target}' not found",
                ))

    if wanted_files:
        found = set(
            await File.get_motor_collection().distinct(
                "file_id", {"file_id": {"$in": list(wanted_files)}}
            )
        )
        for file_id, holders in wanted_files.items():
            if file_id in found:
                continue
            for document, field_path in holders:
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


async def _identity_fields_for(template_id: str, version: int) -> list[str] | None:
    """The template version's identity_fields, cached. None = unreadable."""
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
    fields = None if template is None else list(template.get("identity_fields") or [])
    _identity_fields_cache[key] = fields
    return fields


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
    identity_fields = await _identity_fields_for(
        document.template_id, document.template_version
    )
    if identity_fields is None:
        return  # template unreadable — the orphaned-template check reports it

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

    query: dict[str, Any] = {}
    if namespace:
        query["namespace"] = namespace
    if status_filter:
        query["status"] = status_filter
    if template_id_filter:
        query["template_id"] = template_id_filter

    total_count = await Document.find(query).count()
    effective_limit = limit if limit > 0 else total_count

    all_issues: list[IntegrityIssue] = []
    documents_with_issues: set[str] = set()
    documents_checked = 0
    batch: list[Document] = []

    async def _drain(batch: list[Document]) -> None:
        """Run every check over one batch and fold in its issues."""
        issues: list[IntegrityIssue] = []
        for document in batch:
            await check_template_reference(document.template_id, document, issues)
            if check_term_refs and document.term_references:
                for field_path, term_id in extract_term_ids(document.term_references):
                    await check_term_reference(term_id, document, field_path, issues)
            if check_identity:
                await check_identity_hash(document, issues)
        await check_batch_references(batch, issues)

        for issue in issues:
            documents_with_issues.add(issue.document_id)
        all_issues.extend(issues)

    find_q = Document.find(query)
    if recent_first:
        find_q = find_q.sort([("created_at", SortDirection.DESCENDING)])

    async for document in find_q:
        batch.append(document)
        if len(batch) < BATCH_SIZE:
            continue

        await _drain(batch)
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
        await _drain(batch)
        documents_checked += len(batch)
        if progress is not None:
            progress(documents_checked, effective_limit)

    summary = IntegritySummary(
        total_documents=total_count,
        documents_checked=documents_checked,
        documents_with_issues=len(documents_with_issues),
        orphaned_template_refs=sum(
            1 for i in all_issues if i.type == "orphaned_template_ref"
        ),
        orphaned_term_refs=sum(
            1 for i in all_issues if i.type == "orphaned_term_ref"
        ),
        inactive_template_refs=sum(
            1 for i in all_issues if i.type == "inactive_template_ref"
        ),
        orphaned_document_refs=sum(
            1 for i in all_issues if i.type == "orphaned_document_ref"
        ),
        orphaned_file_refs=sum(
            1 for i in all_issues if i.type == "orphaned_file_ref"
        ),
        identity_hash_mismatches=sum(
            1 for i in all_issues
            if i.type in ("identity_hash_mismatch", "identity_field_missing")
        ),
    )

    # Anything that breaks resolution or identity is an error; a reference to
    # a retired-but-present template is a warning (PoNIF #1 — inactive means
    # retired, and existing data keeps resolving).
    if any(i.severity == "error" for i in all_issues):
        status = "error"
    elif all_issues:
        status = "warning"
    else:
        status = "healthy"

    return IntegrityCheckResult(
        status=status,
        summary=summary,
        issues=all_issues,
    )
