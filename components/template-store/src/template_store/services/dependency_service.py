"""
Dependency Service for Template Store.

Checks what depends on a template before allowing deactivation.
"""

from typing import Optional
import httpx
import os

from pydantic import BaseModel, Field

from ..models.template import Template
from .inheritance_service import InheritanceService


class TemplateDependencies(BaseModel):
    """Dependencies of a template."""

    template_id: str
    template_code: str

    # Templates that extend this one
    child_template_count: int = 0
    child_templates: list[dict] = Field(default_factory=list)

    # Documents that use this template
    document_count: int = 0

    # Summary
    has_dependencies: bool = False
    can_deactivate: bool = True
    warning_message: Optional[str] = None


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
        template = await Template.find_one(Template.template_id == template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")

        result = TemplateDependencies(
            template_id=template_id,
            template_code=template.code
        )

        # Check child templates (extends this template)
        children = await InheritanceService.get_children(template_id)
        result.child_template_count = len(children)
        result.child_templates = [
            {"template_id": c.template_id, "code": c.code, "name": c.name}
            for c in children[:10]  # Limit to first 10
        ]

        # Check documents from Document Store
        try:
            document_count = await cls._get_document_count(template_id)
            result.document_count = document_count
        except Exception as e:
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
    async def _get_document_count(cls, template_id: str) -> int:
        """Get count of documents using this template from Document Store."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{cls.DOCUMENT_STORE_URL}/api/document-store/documents",
                    params={
                        "template_id": template_id,
                        "status": "active",
                        "page_size": 1  # We only need the count
                    },
                    headers={"X-API-Key": cls.DOCUMENT_STORE_API_KEY}
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("total", 0)
                return 0
        except Exception:
            raise
