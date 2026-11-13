"""Client for communicating with the WIP Def-Store service.

Term values are resolved by def-store, never locally — through the shared
wip_auth.term_resolution entry point, the one way any service asks which
term a submitted value names. This client adds document-store's
terminology-metadata lookups on top.
"""

import os
from typing import Any, cast

import httpx

from wip_auth import DEF_STORE_KEY, service_api_key
from wip_auth.term_resolution import (
    TermResolutionError,
    get_def_store_transport,
    reset_resolution_stats,
    resolution_stats,
    resolve_term_values,
    set_def_store_transport,  # noqa: F401  (re-exported: the in-process test seam)
)


class DefStoreClient:
    """
    Client for the WIP Def-Store service.

    Term values resolve through wip_auth.term_resolution (one def-store
    request per document covers all its term fields; inside a
    term_resolution_batch identical values resolve once). Terminology
    metadata is fetched here, never cached: its one reader is the document
    write path, whose resolved terminology_id is persisted, and a write
    must not answer from state def-store may already have retired.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
    ):
        """
        Initialize the Def-Store client.

        Args:
            base_url: Def-Store API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.getenv(
            "DEF_STORE_URL",
            "http://localhost:8002"
        )
        self.api_key = api_key or service_api_key(*DEF_STORE_KEY)
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def get_cache_stats(self) -> dict[str, Any]:
        """Term-resolution statistics (this client caches nothing)."""
        return {f"term_resolution_{k}": v for k, v in resolution_stats().items()}

    def clear_cache(self):
        """Reset the term-resolution counters."""
        reset_resolution_stats()

    async def get_terminology(
        self, terminology_ref: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        """A terminology's metadata (no terms), or None when def-store has none.

        terminology_ref is a canonical id, a bare value (read in namespace,
        the caller's own) or 'ns:VALUE' (the named namespace); def-store
        resolves all three through the Registry. Every call asks def-store:
        a bare value names a different terminology in each namespace, so no
        answer for it may be reused across callers.
        """
        url = f"{self.base_url}/api/def-store/terminologies/{terminology_ref}"
        params = {"namespace": namespace} if namespace else None
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=get_def_store_transport()) as client:
                response = await client.get(url, headers=self._get_headers(), params=params)
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}", unavailable=True) from e
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DefStoreError(
                f"Failed to get terminology: {response.status_code} - {response.text}",
                unavailable=True,
            )
        return cast(dict[str, Any], response.json())

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
            raise DefStoreError(str(e), unavailable=True) from e

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
                        f"Failed to get term: {response.status_code} - {response.text}",
                        unavailable=True,
                    )

                return cast(dict[str, Any] | None, response.json())
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}", unavailable=True) from e

    async def health_check(self) -> bool:
        """Check if the Def-Store service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=get_def_store_transport()) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class DefStoreError(Exception):
    """Error communicating with the Def-Store service.

    ``unavailable`` is True when def-store could not be asked or answered with
    an error status, so callers can tell an outage from an answer: one that
    gates a write refuses it (DependencyUnavailableError) instead of reading
    it as "not found".
    """

    def __init__(self, message: str, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable


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
) -> DefStoreClient:
    """Configure and return the Def-Store client."""
    global _client
    _client = DefStoreClient(base_url=base_url, api_key=api_key)
    return _client
