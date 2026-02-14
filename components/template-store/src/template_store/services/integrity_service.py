"""
Referential Integrity Service for Template Store.

Checks for orphaned references:
- Terminology references (terminology_ref, array_terminology_ref)
- Template references (extends, template_ref, array_template_ref)
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from ..models.template import Template
from ..models.field import FieldType
from .def_store_client import get_def_store_client, DefStoreError


class IntegrityIssue(BaseModel):
    """A single referential integrity issue."""

    type: str = Field(
        ...,
        description="Issue type: orphaned_terminology_ref, orphaned_template_ref, inactive_ref"
    )
    severity: str = Field(
        default="warning",
        description="Severity: error, warning, info"
    )
    template_id: str = Field(..., description="Template with the issue")
    template_value: str = Field(..., description="Template value")
    template_version: int = Field(..., description="Template version")
    field_path: Optional[str] = Field(
        None,
        description="Field path (e.g., 'gender', 'addresses[].country')"
    )
    reference: str = Field(..., description="The reference value")
    message: str = Field(..., description="Human-readable description")


class IntegritySummary(BaseModel):
    """Summary of integrity check results."""

    total_templates: int = 0
    templates_with_issues: int = 0
    orphaned_terminology_refs: int = 0
    orphaned_template_refs: int = 0
    inactive_terminology_refs: int = 0
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


async def check_terminology_reference(
    ref: str,
    template: Template,
    field_path: str,
    issues: list[IntegrityIssue]
) -> None:
    """
    Check if a terminology reference is valid.

    Args:
        ref: Terminology ID or code
        template: Template containing the reference
        field_path: Path to the field (e.g., 'gender', 'addresses[].country')
        issues: List to append issues to
    """
    def_store = get_def_store_client()

    try:
        # Try to get the terminology (try as ID first, then as value)
        terminology = await def_store.get_terminology(terminology_id=ref)
        if terminology is None:
            terminology = await def_store.get_terminology(terminology_value=ref)

        if terminology is None:
            issues.append(IntegrityIssue(
                type="orphaned_terminology_ref",
                severity="error",
                template_id=template.template_id,
                template_value=template.value,
                template_version=template.version,
                field_path=field_path,
                reference=ref,
                message=f"Terminology '{ref}' not found"
            ))
        elif terminology.get("status") != "active":
            issues.append(IntegrityIssue(
                type="inactive_terminology_ref",
                severity="warning",
                template_id=template.template_id,
                template_value=template.value,
                template_version=template.version,
                field_path=field_path,
                reference=ref,
                message=f"Terminology '{ref}' is {terminology.get('status', 'unknown')}"
            ))
    except DefStoreError as e:
        # Can't reach Def-Store, skip check but don't report as issue
        pass


async def check_template_reference(
    ref: str,
    template: Template,
    field_path: Optional[str],
    issues: list[IntegrityIssue],
) -> None:
    """
    Check if a template reference is valid.

    Args:
        ref: Template ID (canonical, after normalization)
        template: Template containing the reference
        field_path: Path to the field (None for 'extends')
        issues: List to append issues to
    """
    # Look up the referenced template — both extends and template_ref store canonical template_id
    referenced = await Template.find_one(Template.template_id == ref)

    if referenced is None:
        issues.append(IntegrityIssue(
            type="orphaned_template_ref",
            severity="error",
            template_id=template.template_id,
            template_value=template.value,
            template_version=template.version,
            field_path=field_path,
            reference=ref,
            message=f"Template '{ref}' not found"
        ))
    elif referenced.status != "active":
        issues.append(IntegrityIssue(
            type="inactive_template_ref",
            severity="warning",
            template_id=template.template_id,
            template_value=template.value,
            template_version=template.version,
            field_path=field_path,
            reference=ref,
            message=f"Template '{ref}' is {referenced.status}"
        ))


async def check_template_integrity(template: Template) -> list[IntegrityIssue]:
    """
    Check all references in a single template.

    Returns:
        List of integrity issues found
    """
    issues: list[IntegrityIssue] = []

    # Check extends reference (extends stores template_id, not code)
    if template.extends:
        await check_template_reference(
            template.extends,
            template,
            field_path=None,  # 'extends' is not a field
            issues=issues,
        )

    # Check field references
    for field in template.fields:
        # Check terminology_ref for term fields
        if field.type == FieldType.TERM and field.terminology_ref:
            await check_terminology_reference(
                field.terminology_ref,
                template,
                field_path=field.name,
                issues=issues
            )

        # Check template_ref for object fields
        if field.type == FieldType.OBJECT and field.template_ref:
            await check_template_reference(
                field.template_ref,
                template,
                field_path=field.name,
                issues=issues
            )

        # Check array item references
        if field.type == FieldType.ARRAY:
            if field.array_item_type == FieldType.TERM and field.array_terminology_ref:
                await check_terminology_reference(
                    field.array_terminology_ref,
                    template,
                    field_path=f"{field.name}[]",
                    issues=issues
                )

            if field.array_item_type == FieldType.OBJECT and field.array_template_ref:
                await check_template_reference(
                    field.array_template_ref,
                    template,
                    field_path=f"{field.name}[]",
                    issues=issues
                )

    return issues


async def check_all_templates(
    status_filter: Optional[str] = None,
    limit: int = 1000
) -> IntegrityCheckResult:
    """
    Check referential integrity for all templates.

    Args:
        status_filter: Optional filter by template status ('draft', 'active', 'inactive')
        limit: Maximum number of templates to check

    Returns:
        IntegrityCheckResult with summary and issues
    """
    # Build query
    query = {}
    if status_filter:
        query = Template.status == status_filter

    # Get templates
    templates = await Template.find(query).limit(limit).to_list()

    all_issues: list[IntegrityIssue] = []
    templates_with_issues: set[str] = set()

    for template in templates:
        issues = await check_template_integrity(template)
        if issues:
            templates_with_issues.add(template.template_id)
            all_issues.extend(issues)

    # Build summary
    summary = IntegritySummary(
        total_templates=len(templates),
        templates_with_issues=len(templates_with_issues),
        orphaned_terminology_refs=sum(
            1 for i in all_issues if i.type == "orphaned_terminology_ref"
        ),
        orphaned_template_refs=sum(
            1 for i in all_issues if i.type == "orphaned_template_ref"
        ),
        inactive_terminology_refs=sum(
            1 for i in all_issues if i.type == "inactive_terminology_ref"
        ),
        inactive_template_refs=sum(
            1 for i in all_issues if i.type == "inactive_template_ref"
        ),
    )

    # Determine overall status
    if summary.orphaned_terminology_refs > 0 or summary.orphaned_template_refs > 0:
        status = "error"
    elif summary.inactive_terminology_refs > 0 or summary.inactive_template_refs > 0:
        status = "warning"
    else:
        status = "healthy"

    return IntegrityCheckResult(
        status=status,
        summary=summary,
        issues=all_issues
    )
