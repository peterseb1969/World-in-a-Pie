"""
Dependency Service for Def-Store.

Checks what depends on a terminology before allowing deactivation.
"""

from typing import Optional
import httpx
import os
import logging

from pydantic import BaseModel, Field

from ..models.terminology import Terminology

logger = logging.getLogger(__name__)


class TerminologyDependencies(BaseModel):
    """Dependencies of a terminology."""

    terminology_id: str
    terminology_code: str

    # Templates that reference this terminology
    template_count: int = 0
    templates: list[dict] = Field(default_factory=list)

    # Summary
    has_dependencies: bool = False
    can_deactivate: bool = True
    warning_message: Optional[str] = None


class DependencyService:
    """Service to check dependencies before deactivation."""

    # Template Store configuration (from environment)
    TEMPLATE_STORE_URL = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
    TEMPLATE_STORE_API_KEY = os.getenv("TEMPLATE_STORE_API_KEY", "dev_master_key_for_testing")

    @classmethod
    async def check_terminology_dependencies(
        cls,
        terminology_id: str
    ) -> TerminologyDependencies:
        """
        Check all dependencies of a terminology.

        Returns:
            TerminologyDependencies with counts and details
        """
        # Get terminology info
        terminology = await Terminology.find_one(
            Terminology.terminology_id == terminology_id
        )
        if not terminology:
            raise ValueError(f"Terminology {terminology_id} not found")

        result = TerminologyDependencies(
            terminology_id=terminology_id,
            terminology_code=terminology.code
        )

        # Check templates that reference this terminology
        try:
            templates = await cls._get_referencing_templates(
                terminology_id,
                terminology.code
            )
            result.template_count = len(templates)
            result.templates = templates[:10]  # Limit to first 10
        except Exception as e:
            # If Template Store is unavailable, we can't check
            result.template_count = -1

        # Calculate summary
        result.has_dependencies = result.template_count > 0

        # Build warning message
        if result.template_count > 0:
            result.warning_message = f"This terminology is used by {result.template_count} template(s)"
        elif result.template_count == -1:
            result.warning_message = "Could not check template dependencies (Template Store unavailable)"

        # Can deactivate but with warning
        result.can_deactivate = True

        return result

    @classmethod
    async def _get_referencing_templates(
        cls,
        terminology_id: str,
        terminology_code: str
    ) -> list[dict]:
        """
        Get templates that reference this terminology.

        This is a simplified check - we fetch all templates and filter locally.
        For production, this should be a dedicated API endpoint.
        """
        # Read URL at call time to ensure we have the latest env var
        template_store_url = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
        template_store_api_key = os.getenv("TEMPLATE_STORE_API_KEY", "dev_master_key_for_testing")

        try:
            # Fetch all templates with pagination (max page_size is 100)
            all_templates = []
            page = 1
            page_size = 100

            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    response = await client.get(
                        f"{template_store_url}/api/template-store/templates",
                        params={
                            "status": "active",
                            "page": page,
                            "page_size": page_size
                        },
                        headers={"X-API-Key": template_store_api_key}
                    )

                    if response.status_code != 200:
                        logger.warning(f"Template Store returned {response.status_code}")
                        return []

                    data = response.json()
                    templates = data.get("items", [])
                    all_templates.extend(templates)

                    # Check if we've fetched all pages
                    total = data.get("total", 0)
                    if len(all_templates) >= total or len(templates) == 0:
                        break
                    page += 1

            templates = all_templates

            # Filter templates that reference this terminology
            referencing = []
            for template in templates:
                fields = template.get("fields", [])
                for field in fields:
                    term_ref = field.get("terminology_ref")
                    array_term_ref = field.get("array_terminology_ref")

                    # Check if this field references our terminology
                    if term_ref in (terminology_id, terminology_code):
                        referencing.append({
                            "template_id": template.get("template_id"),
                            "code": template.get("code"),
                            "name": template.get("name"),
                            "field": field.get("name")
                        })
                        break  # Only count template once
                    elif array_term_ref in (terminology_id, terminology_code):
                        referencing.append({
                            "template_id": template.get("template_id"),
                            "code": template.get("code"),
                            "name": template.get("name"),
                            "field": field.get("name")
                        })
                        break

            return referencing
        except Exception:
            raise
