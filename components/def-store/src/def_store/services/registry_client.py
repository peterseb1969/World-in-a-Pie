"""Client for communicating with the WIP Registry service."""

import asyncio
import os
from typing import Any, Optional

import httpx


class RegistryClient:
    """
    Client for the WIP Registry service.

    Handles ID generation and composite key registration for
    terminologies and terms.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0
    ):
        """
        Initialize the Registry client.

        Args:
            base_url: Registry API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.getenv(
            "REGISTRY_URL",
            "http://localhost:8001"
        )
        self.api_key = api_key or os.getenv(
            "REGISTRY_API_KEY",
            "dev_master_key_for_testing"
        )
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    async def register_terminology(
        self,
        code: str,
        name: str,
        created_by: Optional[str] = None,
        namespace: str = "wip-terminologies"
    ) -> str:
        """
        Register a new terminology in the Registry.

        Args:
            code: Terminology code (e.g., 'DOC_STATUS')
            name: Terminology name
            created_by: User or system creating this
            namespace: Namespace for the terminology (default: wip-terminologies)

        Returns:
            Generated terminology ID (e.g., 'TERM-000001')

        Raises:
            RegistryError: If registration fails
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[{
                    "pool_id": namespace,
                    "composite_key": {
                        "code": code,
                        "name": name
                    },
                    "created_by": created_by,
                    "metadata": {"type": "terminology"}
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register terminology: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            if result["status"] == "already_exists":
                # Return existing ID
                return result["registry_id"]

            return result["registry_id"]

    async def register_term(
        self,
        terminology_id: str,
        code: str,
        value: str,
        created_by: Optional[str] = None,
        namespace: str = "wip-terms"
    ) -> str:
        """
        Register a new term in the Registry.

        Args:
            terminology_id: Parent terminology ID
            code: Term code (e.g., 'APPROVED')
            value: Term value
            created_by: User or system creating this
            namespace: Namespace for the term (default: wip-terms)

        Returns:
            Generated term ID (e.g., 'T-000042')

        Raises:
            RegistryError: If registration fails
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[{
                    "pool_id": namespace,
                    "composite_key": {
                        "terminology_id": terminology_id,
                        "code": code,
                        "value": value
                    },
                    "created_by": created_by,
                    "metadata": {"type": "term"}
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register term: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            if result["status"] == "already_exists":
                return result["registry_id"]

            return result["registry_id"]

    async def register_terms_bulk(
        self,
        terminology_id: str,
        terms: list[dict[str, Any]],
        created_by: Optional[str] = None,
        timeout: Optional[float] = None,
        registry_batch_size: int = 100,
        namespace: str = "wip-terms"
    ) -> list[dict[str, Any]]:
        """
        Register multiple terms in the Registry.

        Uses sub-batching to avoid timeout issues with large batches.
        Each sub-batch is sent as a separate HTTP request.

        Args:
            terminology_id: Parent terminology ID
            terms: List of term dicts with 'code' and 'value'
            created_by: User or system creating these
            timeout: Request timeout in seconds per sub-batch (default 120)
            registry_batch_size: Number of terms per registry HTTP call (default 100)
            namespace: Namespace for the terms (default: wip-terms)

        Returns:
            List of registration results with IDs

        Raises:
            RegistryError: If registration fails
        """
        if not terms:
            return []

        # Use longer timeout for bulk operations (120s default per sub-batch)
        bulk_timeout = timeout if timeout is not None else 120.0

        all_results: list[dict[str, Any]] = []

        # Process in sub-batches to avoid registry timeout
        async with httpx.AsyncClient(timeout=bulk_timeout) as client:
            for batch_start in range(0, len(terms), registry_batch_size):
                batch_end = min(batch_start + registry_batch_size, len(terms))
                batch_terms = terms[batch_start:batch_end]

                items = [
                    {
                        "pool_id": namespace,
                        "composite_key": {
                            "terminology_id": terminology_id,
                            "code": term["code"],
                            "value": term["value"]
                        },
                        "created_by": created_by,
                        "metadata": {"type": "term"}
                    }
                    for term in batch_terms
                ]

                response = await client.post(
                    f"{self.base_url}/api/registry/entries/register",
                    headers=self._get_headers(),
                    json=items
                )

                if response.status_code != 200:
                    raise RegistryError(
                        f"Failed to register terms (batch {batch_start}-{batch_end}): "
                        f"{response.status_code} - {response.text}"
                    )

                data = response.json()
                all_results.extend(data["results"])

                # Small pause between sub-batches to prevent overwhelming the system
                # This is especially important on resource-constrained environments
                if batch_end < len(terms):
                    await asyncio.sleep(0.05)  # 50ms pause

        return all_results

    async def add_synonym(
        self,
        namespace: str,
        target_id: str,
        new_code: str,
        additional_fields: Optional[dict[str, Any]] = None
    ) -> bool:
        """
        Add a synonym when a code changes.

        This allows lookups by both old and new codes.

        Args:
            namespace: Target namespace ('wip-terminologies' or 'wip-terms')
            target_id: The existing registry ID
            new_code: The new code to add as synonym
            additional_fields: Additional composite key fields

        Returns:
            True if synonym was added

        Raises:
            RegistryError: If operation fails
        """
        composite_key = {"code": new_code}
        if additional_fields:
            composite_key.update(additional_fields)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=[{
                    "target_pool_id": namespace,
                    "target_id": target_id,
                    "synonym_pool_id": namespace,
                    "synonym_composite_key": composite_key
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to add synonym: {response.status_code} - {response.text}"
                )

            data = response.json()
            return data[0].get("status") == "added"

    async def lookup_by_code(
        self,
        namespace: str,
        code: str,
        additional_fields: Optional[dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Look up a registry ID by code.

        Args:
            namespace: Namespace to search
            code: Code to look up
            additional_fields: Additional composite key fields

        Returns:
            Registry ID if found, None otherwise
        """
        composite_key = {"code": code}
        if additional_fields:
            composite_key.update(additional_fields)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/lookup/by-key",
                headers=self._get_headers(),
                json=[{
                    "pool_id": namespace,
                    "composite_key": composite_key,
                    "search_synonyms": True
                }]
            )

            if response.status_code != 200:
                return None

            data = response.json()
            if data["found"] > 0:
                return data["results"][0].get("preferred_id")

            return None

    async def health_check(self) -> bool:
        """Check if the Registry service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class RegistryError(Exception):
    """Error communicating with the Registry service."""
    pass


# Singleton instance for convenience
_client: Optional[RegistryClient] = None


def get_registry_client() -> RegistryClient:
    """Get the singleton Registry client instance."""
    global _client
    if _client is None:
        _client = RegistryClient()
    return _client


def configure_registry_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None
) -> RegistryClient:
    """Configure and return the Registry client."""
    global _client
    _client = RegistryClient(base_url=base_url, api_key=api_key)
    return _client
