"""Client for communicating with the WIP Template Store service."""

import os
from typing import Any, Optional

import httpx


class TemplateStoreClient:
    """
    Client for the WIP Template Store service.

    Used to fetch templates for document validation. Templates include
    field definitions, validation rules, and identity field configuration.

    Includes permanent caching for templates since template_id is immutable -
    each template version gets a unique ID that never changes.
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

        # Permanent cache for resolved templates (template_id -> template data)
        # Template IDs are immutable - each version gets a new ID
        self._template_cache: dict[str, Optional[dict[str, Any]]] = {}

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def get_cache_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            "template_cache_size": len(self._template_cache),
            "template_cache_hits": getattr(self, '_cache_hits', 0),
            "template_cache_misses": getattr(self, '_cache_misses', 0),
        }

    def clear_cache(self):
        """Clear the template cache (mainly for testing)."""
        self._template_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    async def get_template(
        self,
        template_id: Optional[str] = None,
        template_value: Optional[str] = None,
        resolve_inheritance: bool = True,
        version: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """
        Get a template by ID or value.

        Args:
            template_id: Template ID
            template_value: Template value (e.g., 'PERSON')
            resolve_inheritance: If True, returns fully resolved template
            version: Specific version (None = latest)

        Returns:
            Template data if found, None otherwise
        """
        if template_id:
            url = f"{self.base_url}/api/template-store/templates/{template_id}"
        elif template_value:
            url = f"{self.base_url}/api/template-store/templates/by-value/{template_value}"
        else:
            return None

        # The /{id} endpoint always resolves inheritance; /{id}/raw does not.
        if not resolve_inheritance and template_id:
            url = f"{self.base_url}/api/template-store/templates/{template_id}/raw"

        params = {}
        if version is not None:
            params["version"] = version

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers(),
                    params=params if params else None,
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
        template_id: str,
        version: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """
        Get a template with inheritance fully resolved.

        This returns the template with all parent fields and rules merged.
        Results are cached by (template_id, version).

        Args:
            template_id: Template ID

        Returns:
            Resolved template data if found, None otherwise
        """
        # Check cache first (keyed by template_id + version)
        cache_key = f"{template_id}:v{version}" if version else template_id
        if cache_key in self._template_cache:
            self._cache_hits = getattr(self, '_cache_hits', 0) + 1
            return self._template_cache[cache_key]

        # Cache miss - fetch from service
        self._cache_misses = getattr(self, '_cache_misses', 0) + 1
        template = await self.get_template(
            template_id=template_id,
            resolve_inheritance=True,
            version=version
        )

        # Cache the result (including None for not found)
        self._template_cache[cache_key] = template
        return template

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
        # Try as ID first, then as value
        template = await self.get_template(template_id=template_ref)
        if template is None:
            template = await self.get_template(template_value=template_ref)

        if template is None:
            return False

        # Check if active
        return template.get("status") == "active"

    async def get_template_descendants(
        self,
        template_id: str
    ) -> list[dict[str, Any]]:
        """
        Get all templates that inherit from this template (directly or indirectly).

        Args:
            template_id: Template ID to get descendants for

        Returns:
            List of descendant template dicts
        """
        url = f"{self.base_url}/api/template-store/templates/{template_id}/descendants"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers()
                )

                if response.status_code == 404:
                    return []

                if response.status_code != 200:
                    raise TemplateStoreError(
                        f"Failed to get descendants: {response.status_code} - {response.text}"
                    )

                data = response.json()
                return data.get("items", [])
        except httpx.RequestError as e:
            raise TemplateStoreError(f"Request failed: {str(e)}")

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
