"""Client for communicating with the WIP Def-Store service."""

import os
from typing import Any

import httpx


class DefStoreClient:
    """
    Client for the WIP Def-Store service.

    Used to validate terminology references in templates.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0
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
        self.api_key = api_key or os.getenv(
            "DEF_STORE_API_KEY",
            "dev_master_key_for_testing"
        )
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    async def get_terminology(
        self,
        terminology_id: str | None = None,
        terminology_value: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Get a terminology by ID or value.

        Args:
            terminology_id: Terminology ID
            terminology_value: Terminology value (e.g., 'DOC_STATUS')
            namespace: Namespace for scoped lookups

        Returns:
            Terminology data if found, None otherwise
        """
        if terminology_id:
            url = f"{self.base_url}/api/def-store/terminologies/{terminology_id}"
        elif terminology_value:
            url = f"{self.base_url}/api/def-store/terminologies/by-value/{terminology_value}"
        else:
            return None

        params = {"namespace": namespace} if namespace else None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._get_headers(), params=params)

                if response.status_code == 404:
                    return None

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Failed to get terminology: {response.status_code} - {response.text}"
                    )

                return response.json()
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}")

    async def terminology_exists(
        self,
        terminology_ref: str,
        namespace: str | None = None,
    ) -> bool:
        """
        Check if a terminology exists by ID or value.

        Args:
            terminology_ref: Terminology ID or value
            namespace: Namespace for scoped lookups

        Returns:
            True if terminology exists and is active
        """
        # Try as ID first, then as value
        terminology = await self.get_terminology(
            terminology_id=terminology_ref, namespace=namespace
        )
        if terminology is None:
            terminology = await self.get_terminology(
                terminology_value=terminology_ref, namespace=namespace
            )

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
            terminology_ref: Terminology ID or value
            value: Value to validate

        Returns:
            Validation result with valid, matched_term, suggestion
        """
        payload = {"terminology_id": terminology_ref, "value": value}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/def-store/validation/validate",
                    headers=self._get_headers(),
                    json=payload
                )

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Validation failed: {response.status_code} - {response.text}"
                    )

                return response.json()
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}")

    async def validate_values_bulk(
        self,
        items: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """
        Validate multiple values against terminologies.

        Args:
            items: List of dicts with terminology_ref and value

        Returns:
            List of validation results
        """
        api_items = [
            {
                "terminology_id": item["terminology_ref"],
                "value": item["value"]
            }
            for item in items
        ]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/def-store/validation/validate/bulk",
                    headers=self._get_headers(),
                    json={"items": api_items}
                )

                if response.status_code != 200:
                    raise DefStoreError(
                        f"Bulk validation failed: {response.status_code} - {response.text}"
                    )

                data = response.json()
                return data.get("results", [])
        except httpx.RequestError as e:
            raise DefStoreError(f"Request failed: {e!s}")

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
_client: DefStoreClient | None = None


def get_def_store_client() -> DefStoreClient:
    """Get the singleton Def-Store client instance."""
    global _client
    if _client is None:
        _client = DefStoreClient()
    return _client


def configure_def_store_client(
    base_url: str | None = None,
    api_key: str | None = None
) -> DefStoreClient:
    """Configure and return the Def-Store client."""
    global _client
    _client = DefStoreClient(base_url=base_url, api_key=api_key)
    return _client
