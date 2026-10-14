"""
Dependency Service for Template Store.

Checks what depends on a template before allowing deactivation.
"""

import os
from typing import cast

import httpx
from beanie.odm.enums import SortDirection
from pydantic import BaseModel, Field

from ..models.template import Template
from .inheritance_service import InheritanceService


class TemplateDependencies(BaseModel):
    """Dependencies of a template."""

    template_id: str
    template_value: str

    # Templates that extend this one
    child_template_count: int = 0
    child_templates: list[dict] = Field(default_factory=list)

    # Documents that use this template
    document_count: int = 0

    # Summary
    has_dependencies: bool = False
    can_deactivate: bool = True
    warning_message: str | None = None


class DependencyService:
    """Service to check dependencies before deactivation."""

    # Document Store configuration (from environment)
    DOCUMENT_STORE_URL = os.getenv("DOCUMENT_STORE_URL", "http://localhost:8004")
    DOCUMENT_STORE_API_KEY = os.getenv("DOCUMENT_STORE_API_KEY", "dev_master_key_for_testing")

    @classmethod
    async def check_template_dependencies(
        cls,
        template_id: str
    ) -> TemplateDependencies:
        """
        Check all dependencies of a template.

        Returns:
            TemplateDependencies with counts and details
        """
        # Get template info
        # Get latest version (template_id is stable across versions)
        results = await Template.find(Template.template_id == template_id).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        template = results[0] if results else None
        if not template:
            raise ValueError(f"Template {template_id} not found")

        result = TemplateDependencies(
            template_id=template_id,
            template_value=template.value
        )

        # Check child templates (extends this template)
        children = await InheritanceService.get_children(template_id)
        result.child_template_count = len(children)
        result.child_templates = [
            {"template_id": c.template_id, "value": c.value, "label": c.label}
            for c in children[:10]  # Limit to first 10
        ]

        # Check documents from Document Store
        try:
            document_count = await cls._get_document_count(template_id)
            result.document_count = document_count
        except Exception:
            # If Document Store is unavailable, we can't check documents
            # Set to -1 to indicate unknown
            result.document_count = -1

        # Calculate summary
        result.has_dependencies = (
            result.child_template_count > 0 or
            result.document_count > 0
        )

        # Build warning message
        warnings = []
        if result.child_template_count > 0:
            warnings.append(f"{result.child_template_count} template(s) extend this template")
        if result.document_count > 0:
            warnings.append(f"{result.document_count} document(s) use this template")
        elif result.document_count == -1:
            warnings.append("Could not check document count (Document Store unavailable)")

        if warnings:
            result.warning_message = "This template has dependencies: " + ", ".join(warnings)

        # Can only deactivate if no active children (documents can remain)
        result.can_deactivate = result.child_template_count == 0

        return result

    @classmethod
    async def hard_delete_document_rows(cls, template_id: str) -> int:
        """Document rows of ANY status that reference this template.

        The count a hard-delete is judged by: archived and inactive rows carry
        the template_id too, and are orphaned just the same when the template
        disappears. Raises when the count cannot be established — the caller
        must refuse an irreversible delete on "unknown", never treat it as 0.
        """
        return await cls._get_document_count(template_id, all_statuses=True)

    @classmethod
    async def _get_document_count(cls, template_id: str, *, all_statuses: bool = False) -> int:
        """Count document rows using this template, from Document Store.

        Active rows only, unless `all_statuses`. Raises on a transport failure
        AND on any non-200 answer: an auth, routing or server error is an
        unknown count, and reporting it as 0 would make every guard built on
        this count fail open.
        """
        params: dict[str, str | int] = {"template_id": template_id, "page_size": 1}
        if not all_statuses:
            params["status"] = "active"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{cls.DOCUMENT_STORE_URL}/api/document-store/documents",
                params=params,
                headers={"X-API-Key": cls.DOCUMENT_STORE_API_KEY},
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"Document Store answered {response.status_code}: {response.text[:200]}"
            )
        return cast(int, response.json().get("total", 0))
