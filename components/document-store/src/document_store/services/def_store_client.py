"""Client for communicating with the WIP Def-Store service."""

import os
import time
from typing import Any, Optional

import httpx


class DefStoreClient:
    """
    Client for the WIP Def-Store service.

    Used to validate term field values against terminologies.

    Includes TTL-based caching for term validations to improve performance
    when processing batches of documents with repeated term values.
    """

    # Default cache TTL: 5 minutes
    DEFAULT_CACHE_TTL = 300

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        cache_ttl: int = DEFAULT_CACHE_TTL
    ):
        """
        Initialize the Def-Store client.

        Args:
            base_url: Def-Store API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
            cache_ttl: Cache time-to-live in seconds (default 5 minutes)
        """
        self.base_url = base_url or os.getenv(
            "DEF_STORE_URL",
            "http://localhost:8002"
        )
        self.api_key = api_key or os.getenv(
            "DEF_STORE_API_KEY",
            "dev_master_key_for_testing"
        )
        self.timeout = timeout
        self.cache_ttl = cache_ttl

        # TTL-based cache for term validations
        # Key: (terminology_ref, value) -> (result, timestamp)
        self._validation_cache: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def _cache_key(self, terminology_ref: str, value: str) -> tuple[str, str]:
        """Create a cache key for a term validation."""
        return (terminology_ref, value)

    def _get_cached(self, terminology_ref: str, value: str) -> Optional[dict[str, Any]]:
        """Get a cached validation result if not expired."""
        key = self._cache_key(terminology_ref, value)
        if key in self._validation_cache:
            result, timestamp = self._validation_cache[key]
            if time.time() - timestamp < self.cache_ttl:
                self._cache_hits += 1
                return result
            else:
                # Expired - remove from cache
                del self._validation_cache[key]
        return None

    def _set_cached(self, terminology_ref: str, value: str, result: dict[str, Any]):
        """Cache a validation result."""
        key = self._cache_key(terminology_ref, value)
        self._validation_cache[key] = (result, time.time())

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "validation_cache_size": len(self._validation_cache),
            "validation_cache_hits": self._cache_hits,
            "validation_cache_misses": self._cache_misses,
            "cache_ttl_seconds": self.cache_ttl,
        }

    def clear_cache(self):
        """Clear the validation cache."""
        self._validation_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    async def get_terminology(
        self,
        terminology_id: Optional[str] = None,
        terminology_code: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """
        Get a terminology by ID or code.

        Args:
            terminology_id: Terminology ID (e.g., 'TERM-000001')
            terminology_code: Terminology code (e.g., 'DOC_STATUS')

        Returns:
            Terminology data if found, None otherwise
        """
        if terminology_id:
            url = f"{self.base_url}/api/def-store/terminologies/{terminology_id}"
        elif terminology_code:
            url = f"{self.base_url}/api/def-store/terminologies/by-code/{terminology_code}"
        else:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._get_headers())

                if response.status_code == 404:
                    return None

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Failed to get terminology: {response.status_code} - {response.text}"
                    )

                return response.json()
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {str(e)}")

    async def terminology_exists(
        self,
        terminology_ref: str
    ) -> bool:
        """
        Check if a terminology exists by ID or code.

        Args:
            terminology_ref: Terminology ID or code

        Returns:
            True if terminology exists and is active
        """
        # Try as ID first (if it looks like an ID)
        if terminology_ref.startswith("TERM-"):
            terminology = await self.get_terminology(terminology_id=terminology_ref)
        else:
            terminology = await self.get_terminology(terminology_code=terminology_ref)

        if terminology is None:
            return False

        # Check if active
        return terminology.get("status") == "active"

    async def validate_value(
        self,
        terminology_ref: str,
        value: str
    ) -> dict[str, Any]:
        """
        Validate a value against a terminology.

        Args:
            terminology_ref: Terminology ID or code
            value: Value to validate

        Returns:
            Validation result with valid, matched_term, suggestion
        """
        # Check cache first
        cached = self._get_cached(terminology_ref, value)
        if cached is not None:
            return cached

        self._cache_misses += 1

        # Determine if ID or code
        if terminology_ref.startswith("TERM-"):
            payload = {"terminology_id": terminology_ref, "value": value}
        else:
            payload = {"terminology_code": terminology_ref, "value": value}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/def-store/validate",
                    headers=self._get_headers(),
                    json=payload
                )

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Validation failed: {response.status_code} - {response.text}"
                    )

                result = response.json()
                # Cache the result
                self._set_cached(terminology_ref, value, result)
                return result
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {str(e)}")

    async def validate_values_bulk(
        self,
        items: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """
        Validate multiple values against terminologies.

        Uses caching to avoid re-validating the same (terminology, value) pairs.
        Only uncached items are sent to the Def-Store service.

        Args:
            items: List of dicts with terminology_ref and value

        Returns:
            List of validation results (in same order as input)
        """
        if not items:
            return []

        # Check cache for each item and collect uncached ones
        results: list[Optional[dict[str, Any]]] = [None] * len(items)
        uncached_items: list[tuple[int, dict[str, str]]] = []

        for i, item in enumerate(items):
            terminology_ref = item["terminology_ref"]
            value = item["value"]
            cached = self._get_cached(terminology_ref, value)
            if cached is not None:
                results[i] = cached
            else:
                uncached_items.append((i, item))
                self._cache_misses += 1

        # If all items were cached, return early
        if not uncached_items:
            return results

        # Transform uncached items to API format
        api_items = []
        for _, item in uncached_items:
            terminology_ref = item["terminology_ref"]
            if terminology_ref.startswith("TERM-"):
                api_items.append({
                    "terminology_id": terminology_ref,
                    "value": item["value"]
                })
            else:
                api_items.append({
                    "terminology_code": terminology_ref,
                    "value": item["value"]
                })

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/def-store/validate/bulk",
                    headers=self._get_headers(),
                    json={"items": api_items}
                )

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Bulk validation failed: {response.status_code} - {response.text}"
                    )

                data = response.json()
                api_results = data.get("results", [])

                # Map results back to original indices and cache them
                for j, (original_index, item) in enumerate(uncached_items):
                    if j < len(api_results):
                        result = api_results[j]
                        results[original_index] = result
                        # Cache the result
                        self._set_cached(item["terminology_ref"], item["value"], result)

                return results
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {str(e)}")

    async def health_check(self) -> bool:
        """Check if the Def-Store service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class DefStoreError(Exception):
    """Error communicating with the Def-Store service."""
    pass


# Singleton instance for convenience
_client: Optional[DefStoreClient] = None


def get_def_store_client() -> DefStoreClient:
    """Get the singleton Def-Store client instance."""
    global _client
    if _client is None:
        _client = DefStoreClient()
    return _client


def configure_def_store_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    cache_ttl: Optional[int] = None
) -> DefStoreClient:
    """Configure and return the Def-Store client."""
    global _client
    _client = DefStoreClient(
        base_url=base_url,
        api_key=api_key,
        cache_ttl=cache_ttl or DefStoreClient.DEFAULT_CACHE_TTL
    )
    return _client
