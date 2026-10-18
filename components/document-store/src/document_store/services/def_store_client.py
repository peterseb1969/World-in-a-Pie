"""Client for communicating with the WIP Def-Store service.

Term values are resolved by def-store, never locally — through the shared
wip_auth.term_resolution entry point, the one way any service asks which
term a submitted value names. This client adds document-store's
terminology-metadata lookups on top.
"""

import os
import time
from typing import Any, cast

import httpx

from wip_auth.term_resolution import (
    TermResolutionError,
    get_def_store_transport,
    reset_resolution_stats,
    resolution_stats,
    resolve_term_values,
    set_def_store_transport,  # noqa: F401  (re-exported: the in-process test seam)
)


class TerminologyCache:
    """
    TTL cache for terminology METADATA (status, value, case rules) — never
    terms. Used by terminology-reference fields and template warm-up; term
    values always resolve through def-store.
    """

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        # terminology_ref -> (terminology_data, timestamp)
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, terminology_ref: str) -> dict[str, Any] | None:
        """Get a cached terminology if not expired."""
        if terminology_ref in self._cache:
            data, timestamp = self._cache[terminology_ref]
            if time.time() - timestamp < self.ttl:
                self._hits += 1
                return data
            else:
                del self._cache[terminology_ref]
        self._misses += 1
        return None

    def set(self, terminology_ref: str, data: dict[str, Any]):
        """Cache a terminology."""
        self._cache[terminology_ref] = (data, time.time())

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "ttl_seconds": self.ttl,
        }

    def clear(self):
        """Clear the cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0


class DefStoreClient:
    """
    Client for the WIP Def-Store service.

    Term values resolve through wip_auth.term_resolution (one def-store
    request per document covers all its term fields; inside a
    term_resolution_batch identical values resolve once). Terminology
    metadata is fetched here and cached.
    """

    DEFAULT_CACHE_TTL = 300  # 5 minutes

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ):
        """
        Initialize the Def-Store client.

        Args:
            base_url: Def-Store API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
            cache_ttl: Terminology metadata cache TTL in seconds (default 5 minutes)
        """
        self.base_url = base_url or os.getenv(
            "DEF_STORE_URL",
            "http://localhost:8002"
        )
        self.api_key = cast(str, api_key or os.getenv(
            "DEF_STORE_API_KEY",
            "dev_master_key_for_testing"
        ))
        self.timeout = timeout

        # Terminology metadata (no terms), refreshed every TTL seconds
        self._terminology_cache = TerminologyCache(ttl=cache_ttl)

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        cache_stats = self._terminology_cache.get_stats()
        return {
            "terminology_cache_size": cache_stats["size"],
            "terminology_cache_hits": cache_stats["hits"],
            "terminology_cache_misses": cache_stats["misses"],
            "cache_ttl_seconds": cache_stats["ttl_seconds"],
            **{f"term_resolution_{k}": v for k, v in resolution_stats().items()},
        }

    def clear_cache(self):
        """Clear the terminology metadata cache and the resolution counters."""
        self._terminology_cache.clear()
        reset_resolution_stats()

    async def _fetch_terminology(self, terminology_id: str) -> dict[str, Any] | None:
        """Fetch a terminology's metadata (no terms) from def-store."""
        url = f"{self.base_url}/api/def-store/terminologies/{terminology_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=get_def_store_transport()) as client:
                response = await client.get(url, headers=self._get_headers())
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}") from e
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DefStoreError(
                f"Failed to get terminology: {response.status_code} - {response.text}"
            )
        return cast(dict[str, Any], response.json())

    async def _get_terminology_cached(
        self, terminology_id: str, force_refresh: bool = False
    ) -> dict[str, Any] | None:
        """Get terminology metadata by canonical UUID, using the cache.

        Cache keys are canonical terminology UUIDs only. Value-form keys
        would let a cache hit cross namespaces: two namespaces sharing a
        terminology value must never serve each other's entries.
        """
        if not force_refresh:
            cached = self._terminology_cache.get(terminology_id)
            if cached is not None:
                return cached
        terminology = await self._fetch_terminology(terminology_id)
        if terminology:
            self._terminology_cache.set(terminology_id, terminology)
        return terminology

    async def get_terminology(
        self,
        terminology_id: str | None = None,
        terminology_value: str | None = None
    ) -> dict[str, Any] | None:
        """
        Get a terminology by ID or value.

        Args:
            terminology_id: Terminology ID
            terminology_value: Terminology value (e.g., 'DOC_STATUS')

        Returns:
            Terminology data if found, None otherwise
        """
        if terminology_id:
            return await self._get_terminology_cached(terminology_id)
        elif terminology_value:
            return await self._get_terminology_cached(terminology_value)
        return None

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
        terminology = await self._get_terminology_cached(terminology_ref)
        if terminology is None:
            return False
        return terminology.get("status") == "active"

    async def validate_value(
        self,
        terminology_ref: str,
        value: str,
        namespace: str | None = None
    ) -> dict[str, Any]:
        """
        Validate one value against a terminology (see validate_values_bulk).

        Args:
            terminology_ref: Terminology ID or value form (bare value =
                own namespace, 'ns:VALUE' = cross-namespace)
            value: Value to validate
            namespace: The validating document's namespace — resolution
                context for value-form refs. Without it a value-form ref
                cannot resolve and validation reports not-found.

        Returns:
            Validation result with valid, matched_term, matched_via
        """
        (result,) = await self.validate_values_bulk(
            [{"terminology_ref": terminology_ref, "value": value}], namespace=namespace
        )
        return result

    async def validate_values_bulk(
        self,
        items: list[dict[str, str]],
        namespace: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Validate several values against terminologies — one def-store call
        (wip_auth.term_resolution.resolve_term_values).

        Each result has `valid`, `matched_term` ({term_id, value}),
        `matched_via` ('value' | 'alias'), `ambiguous` + `candidates` when
        several terms bear the matched name, `suggestion` on a miss, or
        `error` when the terminology itself does not resolve.

        Args:
            items: List of dicts with terminology_ref and value
            namespace: The validating document's namespace — resolution
                context for value-form refs (see validate_value)

        Returns:
            List of validation results (in same order as input)

        Raises:
            DefStoreError: def-store unreachable or answering an error —
                the caller decides (identity fields fail closed).
        """
        try:
            return await resolve_term_values(
                items, namespace, base_url=self.base_url, api_key=self.api_key, timeout=self.timeout,
            )
        except TermResolutionError as e:
            raise DefStoreError(str(e)) from e

    async def get_term(self, term_id: str) -> dict[str, Any] | None:
        """
        Get a term by ID.

        Args:
            term_id: Term ID — canonical UUID or fully qualified
                'ns:terminology:value' (the 2-part 'GENDER:Male' shorthand
                is rejected by def-store)

        Returns:
            Term data if found, None otherwise
        """
        url = f"{self.base_url}/api/def-store/terms/{term_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=get_def_store_transport()) as client:
                response = await client.get(url, headers=self._get_headers())

                if response.status_code == 404:
                    return None

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Failed to get term: {response.status_code} - {response.text}"
                    )

                return cast(dict[str, Any] | None, response.json())
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}") from e

    async def health_check(self) -> bool:
        """Check if the Def-Store service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=get_def_store_transport()) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class DefStoreError(Exception):
    """Error communicating with the Def-Store service."""
    pass


# Singleton instance for convenience
_client: DefStoreClient | None = None


def get_def_store_client() -> DefStoreClient:
    """Get the singleton Def-Store client instance."""
    global _client
    if _client is None:
        _client = DefStoreClient()
    return _client


def configure_def_store_client(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl: int | None = None,
) -> DefStoreClient:
    """Configure and return the Def-Store client."""
    global _client
    _client = DefStoreClient(
        base_url=base_url,
        api_key=api_key,
        cache_ttl=cache_ttl or DefStoreClient.DEFAULT_CACHE_TTL,
    )
    return _client
