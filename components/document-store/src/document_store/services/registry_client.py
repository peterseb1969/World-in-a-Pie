"""Client for communicating with the WIP Registry service."""

import os
from typing import Any, Optional

import httpx


class RegistryClient:
    """
    Client for the WIP Registry service.

    Handles UUID7 ID generation for documents.
    Documents use the wip-documents namespace which generates UUID7 IDs
    for time-based ordering.
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

    async def generate_document_id(
        self,
        identity_hash: str,
        template_id: str,
        created_by: Optional[str] = None
    ) -> str:
        """
        Generate a new document ID from the Registry.

        Uses the wip-documents namespace which generates UUID7 IDs
        for time-based ordering.

        Args:
            identity_hash: Document identity hash
            template_id: Template ID the document conforms to
            created_by: User or system creating this

        Returns:
            Generated document ID (UUID7)

        Raises:
            RegistryError: If registration fails
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[{
                    "namespace": "wip-documents",
                    "composite_key": {
                        "identity_hash": identity_hash,
                        "template_id": template_id
                    },
                    "created_by": created_by,
                    "metadata": {"type": "document"}
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to generate document ID: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            # For documents, we always want a new ID (even if same identity_hash)
            # because each version is a separate registry entry
            return result["registry_id"]

    async def generate_document_ids_bulk(
        self,
        items: list[dict[str, Any]],
        created_by: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Generate multiple document IDs from the Registry.

        Args:
            items: List of dicts with identity_hash and template_id
            created_by: User or system creating these

        Returns:
            List of registration results with IDs

        Raises:
            RegistryError: If registration fails
        """
        registry_items = [
            {
                "namespace": "wip-documents",
                "composite_key": {
                    "identity_hash": item["identity_hash"],
                    "template_id": item["template_id"],
                    # Add a unique suffix to ensure new ID each time
                    "_version_marker": item.get("version", 1)
                },
                "created_by": created_by,
                "metadata": {"type": "document"}
            }
            for item in items
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=registry_items
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to generate document IDs: {response.status_code} - {response.text}"
                )

            data = response.json()
            return data["results"]

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
