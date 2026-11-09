"""
Referential Integrity Service for Template Store.

Checks for orphaned references:
- Terminology references (terminology_ref, array_terminology_ref,
  a reference field's target_terminologies)
- Template references (extends, template_ref, array_template_ref, a
  reference field's target_templates), with pinned versions
"""

from datetime import UTC, datetime
from typing import Any

from beanie.odm.enums import SortDirection
from pydantic import BaseModel, Field

from ..models.field import FieldType
from ..models.template import Template
from .def_store_client import DefStoreError, get_def_store_client


class IntegrityIssue(BaseModel):
    """A single referential integrity issue."""

    type: str = Field(
        ...,
        description=(
            "Issue type: orphaned_terminology_ref, orphaned_template_ref, "
            "inactive_terminology_ref or inactive_template_ref"
        ),
    )
    severity: str = Field(
        default="warning",
        description="Severity: error, warning, info"
    )
    template_id: str = Field(..., description="Template with the issue")
    template_value: str = Field(..., description="Template value")
    template_version: int = Field(..., description="Template version")
    field_path: str | None = Field(
        default=None,
        description="Field path (e.g., 'gender', 'addresses[].country')"
    )
    reference: str = Field(..., description="The reference value")
    message: str = Field(..., description="Human-readable description")


class SkippedCheck(BaseModel):
    """A reference that could not be checked (Def-Store did not answer)."""

    template_id: str
    template_version: int
    field_path: str | None = None
    reference: str
    reason: str


class IntegritySummary(BaseModel):
    """Summary of integrity check results."""

    total_templates: int = 0
    templates_with_issues: int = 0
    orphaned_terminology_refs: int = 0
    orphaned_template_refs: int = 0
    inactive_terminology_refs: int = 0
    inactive_template_refs: int = 0
    checks_skipped: int = Field(
        default=0, description="References that could not be checked (see checks_skipped)"
    )
    truncated: bool = Field(
        default=False,
        description="True when more templates matched than the limit: only the first `limit` (by template_id, version) were checked",
    )


class IntegrityCheckResult(BaseModel):
    """Result of an integrity check."""

    status: str = Field(
        ...,
        description=(
            "Overall status: healthy, warning, error, or unknown (a reference "
            "could not be checked; nothing wrong was found among the rest)"
        ),
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    summary: IntegritySummary = Field(default_factory=IntegritySummary)
    issues: list[IntegrityIssue] = Field(default_factory=list)
    checks_skipped: list[SkippedCheck] = Field(default_factory=list)


class _Scan:
    """What one scan found: issues, and references it could not check."""

    def __init__(self) -> None:
        self.issues: list[IntegrityIssue] = []
        self.skipped: list[SkippedCheck] = []

    def issue(self, template: Template, kind: str, severity: str,
              field_path: str | None, ref: str, message: str) -> None:
        self.issues.append(IntegrityIssue(
            type=kind, severity=severity, template_id=template.template_id,
            template_value=template.value, template_version=template.version,
            field_path=field_path, reference=ref, message=message,
        ))


async def check_terminology_reference(
    ref: str, template: Template, field_path: str, scan: _Scan,
) -> None:
    """Check a terminology reference (an id or a value). A Def-Store that
    cannot answer is a skipped check, never a pass."""
    def_store = get_def_store_client()
    try:
        terminology = await def_store.get_terminology(terminology_id=ref)
        if terminology is None:
            terminology = await def_store.get_terminology(terminology_value=ref)
    except DefStoreError as e:
        scan.skipped.append(SkippedCheck(
            template_id=template.template_id, template_version=template.version,
            field_path=field_path, reference=ref, reason=f"Def-Store did not answer: {e}",
        ))
        return
    if terminology is None:
        scan.issue(template, "orphaned_terminology_ref", "error", field_path, ref,
                   f"Terminology '{ref}' not found")
    elif terminology.get("status") != "active":
        scan.issue(template, "inactive_terminology_ref", "warning", field_path, ref,
                   f"Terminology '{ref}' is {terminology.get('status', 'unknown')}")


async def _referenced_template(ref: str, template: Template) -> Template | None:
    """Any row of the template ``ref`` names: by id, or (a draft stores what
    it was given) by value in the template's namespace or as ns:VALUE."""
    row = await Template.find_one(Template.template_id == ref)
    if row is not None:
        return row
    ns, sep, value = ref.partition(":")
    if not sep:
        ns, value = template.namespace, ref
    return await Template.find_one({"namespace": ns, "value": value})


async def check_template_reference(
    ref: str, template: Template, field_path: str | None, scan: _Scan,
    pinned_version: int | None = None,
) -> None:
    """Check a template reference: the template exists, is in use (has an
    active version, whichever), and a pinned version exists."""
    referenced = await _referenced_template(ref, template)
    if referenced is None:
        scan.issue(template, "orphaned_template_ref", "error", field_path, ref,
                   f"Template '{ref}' not found")
        return
    if pinned_version is not None and await Template.find_one(
        {"template_id": referenced.template_id, "version": pinned_version}
    ) is None:
        scan.issue(template, "orphaned_template_ref", "error", field_path, ref,
                   f"Template '{ref}' has no version {pinned_version}")
        return
    if await Template.find_one(
        {"template_id": referenced.template_id, "status": "active"}
    ) is None:
        scan.issue(template, "inactive_template_ref", "warning", field_path, ref,
                   f"Template '{ref}' has no active version")


async def _check_field(field: Any, template: Template, scan: _Scan) -> None:
    """Every reference one field holds."""
    name = field.name
    if field.type == FieldType.TERM and field.terminology_ref:
        await check_terminology_reference(field.terminology_ref, template, name, scan)
    if field.type == FieldType.OBJECT and field.template_ref:
        await check_template_reference(field.template_ref, template, name, scan,
                                       field.template_ref_version)
    if field.type == FieldType.ARRAY:
        if field.array_item_type == FieldType.TERM and field.array_terminology_ref:
            await check_terminology_reference(field.array_terminology_ref, template, f"{name}[]", scan)
        if field.array_item_type == FieldType.OBJECT and field.array_template_ref:
            await check_template_reference(field.array_template_ref, template, f"{name}[]",
                                           scan, field.array_template_ref_version)
    # A reference field's targets (single or array of references).
    path = f"{name}[]" if field.type == FieldType.ARRAY else name
    for ref in field.target_templates or []:
        await check_template_reference(ref, template, path, scan)
    for ref in field.target_terminologies or []:
        await check_terminology_reference(ref, template, path, scan)


async def check_template_integrity(template: Template, scan: _Scan | None = None) -> list[IntegrityIssue]:
    """Check every reference in one template version; returns its issues."""
    scan = scan if scan is not None else _Scan()
    before = len(scan.issues)
    if template.extends:
        await check_template_reference(template.extends, template, None, scan,
                                       template.extends_version)
    for field in template.fields:
        await _check_field(field, template, scan)
    return scan.issues[before:]


def _overall_status(summary: IntegritySummary) -> str:
    if summary.orphaned_terminology_refs or summary.orphaned_template_refs:
        return "error"
    if summary.checks_skipped:
        return "unknown"
    if summary.inactive_terminology_refs or summary.inactive_template_refs:
        return "warning"
    return "healthy"


async def check_all_templates(
    status_filter: str | None = None,
    limit: int = 1000
) -> IntegrityCheckResult:
    """
    Check referential integrity for all templates.

    Args:
        status_filter: Optional filter by template status ('draft', 'active', 'inactive')
        limit: Maximum number of templates to check (0 = all). Templates are
            taken in (template_id, version) order, and the result says when
            more matched than were checked.

    Returns:
        IntegrityCheckResult with summary and issues
    """
    query: Any = {}
    if status_filter:
        query = Template.status == status_filter

    templates = await Template.find(query).sort(
        [("template_id", SortDirection.ASCENDING), ("version", SortDirection.ASCENDING)]
    ).limit(limit).to_list()
    truncated = bool(limit) and await Template.find(query).count() > len(templates)

    scan = _Scan()
    templates_with_issues: set[str] = set()
    for template in templates:
        if await check_template_integrity(template, scan):
            templates_with_issues.add(template.template_id)

    def count(kind: str) -> int:
        return sum(1 for i in scan.issues if i.type == kind)

    summary = IntegritySummary(
        total_templates=len(templates),
        templates_with_issues=len(templates_with_issues),
        orphaned_terminology_refs=count("orphaned_terminology_ref"),
        orphaned_template_refs=count("orphaned_template_ref"),
        inactive_terminology_refs=count("inactive_terminology_ref"),
        inactive_template_refs=count("inactive_template_ref"),
        checks_skipped=len(scan.skipped),
        truncated=truncated,
    )
    return IntegrityCheckResult(
        status=_overall_status(summary), summary=summary,
        issues=scan.issues, checks_skipped=scan.skipped,
    )
