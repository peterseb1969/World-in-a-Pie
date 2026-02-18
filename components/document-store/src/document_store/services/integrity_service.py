"""
Referential Integrity Service for Document Store.

Checks for orphaned references:
- Template references (template_id)
- Term references (term_references dictionary values)

Uses batched cursor iteration to avoid loading all documents into memory.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..models.document import Document, DocumentStatus
from .template_store_client import get_template_store_client, TemplateStoreError
from .def_store_client import get_def_store_client, DefStoreError

# Batch size for cursor iteration — bounds memory usage
BATCH_SIZE = 500


class IntegrityIssue(BaseModel):
    """A single referential integrity issue."""

    type: str = Field(
        ...,
        description="Issue type: orphaned_template_ref, orphaned_term_ref, inactive_ref"
    )
    severity: str = Field(
        default="warning",
        description="Severity: error, warning, info"
    )
    document_id: str = Field(..., description="Document with the issue")
    template_id: str = Field(..., description="Template ID of the document")
    version: int = Field(..., description="Document version")
    field_path: Optional[str] = Field(
        None,
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


class IntegrityCheckResult(BaseModel):
    """Result of an integrity check."""

    status: str = Field(
        ...,
        description="Overall status: healthy, warning, error"
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    summary: IntegritySummary = Field(default_factory=IntegritySummary)
    issues: list[IntegrityIssue] = Field(default_factory=list)


# Cache for checked references to avoid repeated lookups
_template_check_cache: dict[str, tuple[bool, str]] = {}  # template_id -> (exists, status)
_term_check_cache: dict[str, bool] = {}  # term_id -> exists


def clear_integrity_cache():
    """Clear the integrity check cache (call between checks if needed)."""
    global _template_check_cache, _term_check_cache
    _template_check_cache.clear()
    _term_check_cache.clear()


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
        term_id: Term ID (e.g., 'T-000001')
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

    New array format: [{"field_path": "gender", "term_id": "T-001", ...}, ...]

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
    status_filter: Optional[str] = None,
    template_id_filter: Optional[str] = None,
    limit: int = 0,
    check_term_refs: bool = True,
    recent_first: bool = False
) -> IntegrityCheckResult:
    """
    Check referential integrity for documents.

    Uses batched cursor iteration to keep memory bounded — never loads
    more than BATCH_SIZE documents at a time.

    Args:
        status_filter: Optional filter by document status ('active', 'inactive', 'archived')
        template_id_filter: Optional filter by template_id
        limit: Maximum number of documents to check (0 = all)
        check_term_refs: Whether to check term references (can be slow for many documents)
        recent_first: Sort by created_at descending so the most recent documents are checked first

    Returns:
        IntegrityCheckResult with summary and issues
    """
    # Clear cache for fresh check
    clear_integrity_cache()

    # Build query
    filters = []
    if status_filter:
        filters.append(Document.status == status_filter)
    if template_id_filter:
        filters.append(Document.template_id == template_id_filter)

    # Combine filters
    if filters:
        query = filters[0]
        for f in filters[1:]:
            query = query & f
    else:
        query = {}

    # Get total count (cheap — uses index)
    total_count = await Document.find(query).count()

    # limit=0 means check all (still batched for memory safety)
    effective_limit = limit if limit > 0 else total_count

    # Process in batches to bound memory usage
    all_issues: list[IntegrityIssue] = []
    documents_with_issues: set[str] = set()
    documents_checked = 0
    skip = 0

    while documents_checked < effective_limit:
        batch_size = min(BATCH_SIZE, effective_limit - documents_checked)
        find_q = Document.find(query).skip(skip).limit(batch_size)
        if recent_first:
            find_q = find_q.sort([("created_at", -1)])
        batch = await find_q.to_list()

        if not batch:
            break

        for document in batch:
            issues = []

            # Always check template reference
            await check_template_reference(document.template_id, document, issues)

            # Optionally check term references
            if check_term_refs and document.term_references:
                term_refs = extract_term_ids(document.term_references)
                for field_path, term_id in term_refs:
                    await check_term_reference(term_id, document, field_path, issues)

            if issues:
                documents_with_issues.add(document.document_id)
                all_issues.extend(issues)

        documents_checked += len(batch)
        skip += len(batch)

        # Yield to event loop between batches so other requests aren't starved
        await asyncio.sleep(0)

    # Build summary
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
    )

    # Determine overall status
    if summary.orphaned_template_refs > 0 or summary.orphaned_term_refs > 0:
        status = "error"
    elif summary.inactive_template_refs > 0:
        status = "warning"
    else:
        status = "healthy"

    return IntegrityCheckResult(
        status=status,
        summary=summary,
        issues=all_issues
    )
