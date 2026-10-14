"""
Dependency Service for Def-Store.

Checks what depends on a terminology before allowing deactivation.
"""

import logging
import os

import httpx
from pydantic import BaseModel, Field

from ..models.terminology import Terminology

logger = logging.getLogger(__name__)


class TerminologyDependencies(BaseModel):
    """Dependencies of a terminology.

    `template_count == -1` means the check could not be completed (Template
    Store unreachable or answering non-200); `has_dependencies` is then None
    ("unknown"), never False — callers must not read a failed check as "no
    dependencies".
    """

    terminology_id: str
    terminology_value: str

    # Templates that reference this terminology (-1 = could not check)
    template_count: int = 0
    templates: list[dict] = Field(default_factory=list)

    # Summary. None = unknown (the check did not complete).
    has_dependencies: bool | None = False
    # Deactivation is always possible (force=true overrides the guard);
    # the delete endpoint decides what a non-forced delete may do.
    can_deactivate: bool = True
    warning_message: str | None = None


class DependencyService:
    """Service to check dependencies before deactivation."""

    @classmethod
    async def check_terminology_dependencies(
        cls,
        terminology_id: str,
        *,
        all_statuses: bool = False,
    ) -> TerminologyDependencies:
        """
        Check all dependencies of a terminology.

        all_statuses=True counts referencing templates of every status, not
        just active ones — the set a hard-delete must be judged by, since an
        inactive template still validates the documents pinned to it.

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
            terminology_value=terminology.value
        )

        # Check templates that reference this terminology
        try:
            templates = await cls._get_referencing_templates(
                terminology_id,
                terminology.value,
                all_statuses=all_statuses,
            )
            result.template_count = len(templates)
            result.templates = templates[:10]  # Limit to first 10
        except Exception:
            # Template Store unavailable or refusing — the count is UNKNOWN,
            # which must never read as "no dependencies" (the delete guard
            # fails closed on it).
            result.template_count = -1

        # Calculate summary; None = unknown, not False
        result.has_dependencies = (
            None if result.template_count == -1 else result.template_count > 0
        )

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
        terminology_value: str,
        *,
        all_statuses: bool = False,
    ) -> list[dict]:
        """
        Get templates that reference this terminology.

        This is a simplified check - we fetch all templates and filter locally.
        For production, this should be a dedicated API endpoint.

        Raises on any failure, a non-200 answer included: an unauthorized or
        erroring Template Store means the reference set is UNKNOWN, and an
        empty list here would read as "nothing references this terminology".
        """
        # Read URL at call time to ensure we have the latest env var
        template_store_url = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
        template_store_api_key = os.getenv("TEMPLATE_STORE_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"

        try:
            # Fetch all templates with pagination (max page_size is 100)
            all_templates = []
            page = 1
            page_size = 100

            params: dict[str, str | int] = {"page_size": page_size}
            if not all_statuses:
                params["status"] = "active"

            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    response = await client.get(
                        f"{template_store_url}/api/template-store/templates",
                        params={**params, "page": page},
                        headers={"X-API-Key": template_store_api_key}
                    )

                    if response.status_code != 200:
                        logger.warning(f"Template Store returned {response.status_code}")
                        raise RuntimeError(
                            f"Template Store returned {response.status_code}"
                        )

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
                    if term_ref in (terminology_id, terminology_value):
                        referencing.append({
                            "template_id": template.get("template_id"),
                            "value": template.get("value"),
                            "label": template.get("label"),
                            "field": field.get("name")
                        })
                        break  # Only count template once
                    elif array_term_ref in (terminology_id, terminology_value):
                        referencing.append({
                            "template_id": template.get("template_id"),
                            "value": template.get("value"),
                            "label": template.get("label"),
                            "field": field.get("name")
                        })
                        break

            return referencing
        except Exception:
            raise
