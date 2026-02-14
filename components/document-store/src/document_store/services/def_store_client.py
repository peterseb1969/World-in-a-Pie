"""Client for communicating with the WIP Def-Store service."""

import os
import time
from typing import Any, Optional

import httpx


class TerminologyCache:
    """
    Cache for complete terminologies with TTL-based refresh.

    Caches entire terminologies (with all terms) for fast local validation.
    Terminologies are essentially immutable, so a 5-minute TTL is just a
    safety net to pick up any rare changes.
    """

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        # terminology_ref -> (terminology_data, timestamp)
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, terminology_ref: str) -> Optional[dict[str, Any]]:
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

    Used to validate term field values against terminologies.

    Caches complete terminologies for fast local validation instead of
    making HTTP calls for each term. This is much more efficient since
    terminologies are typically small (< 1000 terms) and change rarely.
    """

    DEFAULT_CACHE_TTL = 300  # 5 minutes

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ):
        """
        Initialize the Def-Store client.

        Args:
            base_url: Def-Store API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
            cache_ttl: Terminology cache TTL in seconds (default 5 minutes)
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

        # Cache for complete terminologies (refreshed every TTL seconds)
        self._terminology_cache = TerminologyCache(ttl=cache_ttl)

        # Track validation stats
        self._validations_total = 0
        self._validations_local = 0

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
            "validations_total": self._validations_total,
            "validations_local": self._validations_local,
            "validations_local_percent": round(
                self._validations_local / self._validations_total * 100, 1
            ) if self._validations_total > 0 else 0,
        }

    def clear_cache(self):
        """Clear the terminology cache."""
        self._terminology_cache.clear()
        self._validations_total = 0
        self._validations_local = 0

    async def _fetch_terminology_with_terms(
        self,
        terminology_ref: str
    ) -> Optional[dict[str, Any]]:
        """
        Fetch a terminology with ALL its terms from Def-Store.

        Makes exactly 1 HTTP connection: fetches the terminology metadata,
        then paginates through all terms using the same client.

        Args:
            terminology_ref: Terminology ID or value

        Returns:
            Terminology data with terms and _lookup index, or None if not found
        """
        url = f"{self.base_url}/api/def-store/terminologies/{terminology_ref}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = self._get_headers()

                # Fetch terminology (try ID, fall back to value)
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    value_url = f"{self.base_url}/api/def-store/terminologies/by-value/{terminology_ref}"
                    response = await client.get(value_url, headers=headers)
                    if response.status_code == 404:
                        return None

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Failed to get terminology: {response.status_code} - {response.text}"
                    )

                terminology = response.json()
                terminology_id = terminology.get("terminology_id")

                # Fetch ALL terms (paginate if >500)
                all_terms = []
                terms_url = f"{self.base_url}/api/def-store/terminologies/{terminology_id}/terms"
                page = 1
                while True:
                    terms_response = await client.get(
                        terms_url,
                        headers=headers,
                        params={"page_size": 500, "page": page},
                    )
                    if terms_response.status_code != 200:
                        break
                    terms_data = terms_response.json()
                    items = terms_data.get("items", [])
                    all_terms.extend(items)
                    # Stop when we got fewer than page_size (last page)
                    if len(items) < 500:
                        break
                    page += 1

                terminology["terms"] = all_terms
                terminology["_lookup"] = self._build_term_lookup(all_terms)

                return terminology

        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {str(e)}")

    def _build_term_lookup(self, terms: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """
        Build lookup indexes for fast term validation.

        Returns a dict mapping lowercase values to term data:
        - value (lowercase) -> term
        - each alias (lowercase) -> term
        """
        lookup = {}
        for term in terms:
            term_id = term.get("term_id")
            value = term.get("value", "").lower()
            aliases = term.get("aliases", [])

            # Create term reference data
            term_ref = {
                "term_id": term_id,
                "value": term.get("value"),
            }

            # Index by value
            if value:
                lookup[value] = {"term": term_ref, "matched_via": "value"}

            # Index by aliases
            for alias in aliases:
                if alias:
                    lookup[alias.lower()] = {"term": term_ref, "matched_via": "alias"}

        return lookup

    async def _get_terminology_cached(self, terminology_ref: str) -> Optional[dict[str, Any]]:
        """Get a terminology, using cache if available."""
        # Check cache first
        cached = self._terminology_cache.get(terminology_ref)
        if cached is not None:
            return cached

        # Fetch and cache
        terminology = await self._fetch_terminology_with_terms(terminology_ref)
        if terminology:
            self._terminology_cache.set(terminology_ref, terminology)
            # Also cache by the other ref (value or id) for convenience
            term_id = terminology.get("terminology_id")
            value = terminology.get("value")
            if term_id and term_id != terminology_ref:
                self._terminology_cache.set(term_id, terminology)
            if value and value != terminology_ref:
                self._terminology_cache.set(value, terminology)

        return terminology

    def _validate_term_locally(
        self,
        terminology: dict[str, Any],
        value: str
    ) -> dict[str, Any]:
        """
        Validate a term value locally using cached terminology.

        Args:
            terminology: Cached terminology with _lookup index
            value: Value to validate

        Returns:
            Validation result dict
        """
        lookup = terminology.get("_lookup", {})
        value_lower = value.lower()

        if value_lower in lookup:
            match = lookup[value_lower]
            return {
                "valid": True,
                "matched_term": match["term"],
                "matched_via": match["matched_via"],
            }

        return {
            "valid": False,
            "matched_term": None,
            "matched_via": None,
            "suggestion": None,  # Could implement fuzzy matching here
        }

    async def get_terminology(
        self,
        terminology_id: Optional[str] = None,
        terminology_value: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
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
        value: str
    ) -> dict[str, Any]:
        """
        Validate a value against a terminology.

        Uses cached terminology for fast local validation.

        Args:
            terminology_ref: Terminology ID or code
            value: Value to validate

        Returns:
            Validation result with valid, matched_term, matched_via
        """
        self._validations_total += 1

        # Get terminology (from cache or fetch)
        terminology = await self._get_terminology_cached(terminology_ref)

        if terminology is None:
            return {
                "valid": False,
                "error": f"Terminology '{terminology_ref}' not found",
            }

        # Validate locally
        self._validations_local += 1
        return self._validate_term_locally(terminology, value)

    async def validate_values_bulk(
        self,
        items: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """
        Validate multiple values against terminologies.

        Uses cached terminologies for fast local validation.
        Fetches any missing terminologies first, then validates all locally.

        Args:
            items: List of dicts with terminology_ref and value

        Returns:
            List of validation results (in same order as input)
        """
        if not items:
            return []

        # Collect unique terminology refs
        terminology_refs = set(item["terminology_ref"] for item in items)

        # Ensure all terminologies are cached
        for ref in terminology_refs:
            await self._get_terminology_cached(ref)

        # Validate all items locally
        results = []
        for item in items:
            result = await self.validate_value(
                item["terminology_ref"],
                item["value"]
            )
            results.append(result)

        return results

    async def get_term(self, term_id: str) -> Optional[dict[str, Any]]:
        """
        Get a term by ID.

        Args:
            term_id: Term ID (e.g., 'T-000001')

        Returns:
            Term data if found, None otherwise
        """
        url = f"{self.base_url}/api/def-store/terms/{term_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._get_headers())

                if response.status_code == 404:
                    return None

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Failed to get term: {response.status_code} - {response.text}"
                    )

                return response.json()
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
    cache_ttl: Optional[int] = None,
) -> DefStoreClient:
    """Configure and return the Def-Store client."""
    global _client
    _client = DefStoreClient(
        base_url=base_url,
        api_key=api_key,
        cache_ttl=cache_ttl or DefStoreClient.DEFAULT_CACHE_TTL,
    )
    return _client
