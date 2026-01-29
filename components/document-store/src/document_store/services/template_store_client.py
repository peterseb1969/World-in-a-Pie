"""Client for communicating with the WIP Template Store service."""

import os
from typing import Any, Optional

import httpx


class TemplateStoreClient:
    """
    Client for the WIP Template Store service.

    Used to fetch templates for document validation. Templates include
    field definitions, validation rules, and identity field configuration.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0
    ):
        """
        Initialize the Template Store client.

        Args:
            base_url: Template Store API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.getenv(
            "TEMPLATE_STORE_URL",
            "http://localhost:8003"
        )
        self.api_key = api_key or os.getenv(
            "TEMPLATE_STORE_API_KEY",
            "dev_master_key_for_testing"
        )
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    async def get_template(
        self,
        template_id: Optional[str] = None,
        template_code: Optional[str] = None,
        resolve_inheritance: bool = True
    ) -> Optional[dict[str, Any]]:
        """
        Get a template by ID or code.

        Args:
            template_id: Template ID (e.g., 'TPL-000001')
            template_code: Template code (e.g., 'PERSON')
            resolve_inheritance: If True, returns fully resolved template

        Returns:
            Template data if found, None otherwise
        """
        if template_id:
            url = f"{self.base_url}/api/template-store/templates/{template_id}"
        elif template_code:
            url = f"{self.base_url}/api/template-store/templates/by-code/{template_code}"
        else:
            return None

        params = {}
        if resolve_inheritance:
            params["resolve"] = "true"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers(),
                    params=params
                )

                if response.status_code == 404:
                    return None

                if response.status_code != 200:
                    raise TemplateStoreError(
                        f"Failed to get template: {response.status_code} - {response.text}"
                    )

                return response.json()
        except httpx.RequestError as e:
            raise TemplateStoreError(f"Request failed: {str(e)}")

    async def get_template_resolved(
        self,
        template_id: str
    ) -> Optional[dict[str, Any]]:
        """
        Get a template with inheritance fully resolved.

        This returns the template with all parent fields and rules merged.

        Args:
            template_id: Template ID

        Returns:
            Resolved template data if found, None otherwise
        """
        return await self.get_template(
            template_id=template_id,
            resolve_inheritance=True
        )

    async def template_exists(
        self,
        template_ref: str
    ) -> bool:
        """
        Check if a template exists by ID or code.

        Args:
            template_ref: Template ID or code

        Returns:
            True if template exists and is active
        """
        # Try as ID first (if it looks like an ID)
        if template_ref.startswith("TPL-"):
            template = await self.get_template(template_id=template_ref)
        else:
            template = await self.get_template(template_code=template_ref)

        if template is None:
            return False

        # Check if active
        return template.get("status") == "active"

    async def validate_template_references(
        self,
        template_ids: list[str]
    ) -> dict[str, bool]:
        """
        Check if multiple templates exist.

        Args:
            template_ids: List of template IDs to check

        Returns:
            Dict mapping template_id to existence status
        """
        results = {}
        for template_id in template_ids:
            results[template_id] = await self.template_exists(template_id)
        return results

    async def health_check(self) -> bool:
        """Check if the Template Store service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class TemplateStoreError(Exception):
    """Error communicating with the Template Store service."""
    pass


# Singleton instance for convenience
_client: Optional[TemplateStoreClient] = None


def get_template_store_client() -> TemplateStoreClient:
    """Get the singleton Template Store client instance."""
    global _client
    if _client is None:
        _client = TemplateStoreClient()
    return _client


def configure_template_store_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None
) -> TemplateStoreClient:
    """Configure and return the Template Store client."""
    global _client
    _client = TemplateStoreClient(base_url=base_url, api_key=api_key)
    return _client
