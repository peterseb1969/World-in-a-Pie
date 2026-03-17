"""Client for communicating with the WIP Template Store service."""

import os
import time
from typing import Any, Optional

import httpx

# How long to cache "latest version" resolution (seconds).
# Pinned (template_id, version) pairs are cached forever — they're immutable.
LATEST_TTL_SECONDS = 5.0


class TemplateStoreClient:
    """
    Client for the WIP Template Store service.

    Used to fetch templates for document validation. Templates include
    field definitions, validation rules, and identity field configuration.

    Caching strategy (version-aware):
    - (template_id, version) lookups: cached permanently — immutable pair
    - "latest" lookups (version=None): cached with short TTL (5s) — covers
      bulk batches without going stale across template updates
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

        # Permanent cache: keyed by "template_id:v{version}" — immutable
        self._template_cache: dict[str, Optional[dict[str, Any]]] = {}

        # TTL cache for "latest" resolution: keyed by template_id
        # Value: (template_data, timestamp)
        self._latest_cache: dict[str, tuple[Optional[dict[str, Any]], float]] = {}

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
            "latest_cache_size": len(self._latest_cache),
            "template_cache_hits": getattr(self, '_cache_hits', 0),
            "template_cache_misses": getattr(self, '_cache_misses', 0),
        }

    def clear_cache(self):
        """Clear all template caches (mainly for testing)."""
        self._template_cache.clear()
        self._latest_cache.clear()
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

        Uses the same cache as get_template_resolved() when looking up by
        template_id with resolve_inheritance=True (the common case in
        reference validation).

        Args:
            template_id: Template ID
            template_value: Template value (e.g., 'PERSON')
            resolve_inheritance: If True, returns fully resolved template
            version: Specific version (None = latest)

        Returns:
            Template data if found, None otherwise
        """
        # Check cache for the common case: by template_id with inheritance
        use_cache = template_id and resolve_inheritance
        if use_cache:
            now = time.monotonic()
            if version is not None:
                cache_key = f"{template_id}:v{version}"
                if cache_key in self._template_cache:
                    self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                    return self._template_cache[cache_key]
            else:
                if template_id in self._latest_cache:
                    cached_template, cached_at = self._latest_cache[template_id]
                    if (now - cached_at) < LATEST_TTL_SECONDS:
                        self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                        return cached_template

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

        if use_cache:
            self._cache_misses = getattr(self, '_cache_misses', 0) + 1

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

                result = response.json()

                # Populate cache
                if use_cache and result is not None:
                    if version is not None:
                        self._template_cache[f"{template_id}:v{version}"] = result
                    else:
                        self._latest_cache[template_id] = (result, time.monotonic())
                        actual_version = result.get("version")
                        if actual_version is not None:
                            self._template_cache[f"{template_id}:v{actual_version}"] = result

                return result
        except httpx.RequestError as e:
            raise TemplateStoreError(f"Request failed: {str(e)}")

    async def get_template_resolved(
        self,
        template_id: str,
        version: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """
        Get a template with inheritance fully resolved.

        Caching strategy:
        - Pinned version (version != None): cached permanently — immutable pair
        - Latest (version == None): cached with short TTL (5s) — fast enough
          for bulk batches, fresh enough for template updates

        Args:
            template_id: Template ID
            version: Specific version (None = latest)

        Returns:
            Resolved template data if found, None otherwise
        """
        now = time.monotonic()

        # --- Pinned version: permanent cache ---
        if version is not None:
            cache_key = f"{template_id}:v{version}"
            if cache_key in self._template_cache:
                self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                return self._template_cache[cache_key]
        else:
            # --- Latest: TTL cache ---
            if template_id in self._latest_cache:
                cached_template, cached_at = self._latest_cache[template_id]
                if (now - cached_at) < LATEST_TTL_SECONDS:
                    self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                    return cached_template

        # Fetch from service
        self._cache_misses = getattr(self, '_cache_misses', 0) + 1
        template = await self.get_template(
            template_id=template_id,
            resolve_inheritance=True,
            version=version
        )

        # Cache the result
        if version is not None:
            # Pinned version: permanent
            self._template_cache[f"{template_id}:v{version}"] = template
        else:
            # Latest: TTL cache
            self._latest_cache[template_id] = (template, now)
            # Also populate the permanent cache for the resolved version
            if template is not None:
                actual_version = template.get("version")
                if actual_version is not None:
                    self._template_cache[f"{template_id}:v{actual_version}"] = template

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
