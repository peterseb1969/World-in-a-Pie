"""Client for communicating with the WIP Registry service."""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RegistryClient:
    """
    Client for the WIP Registry service.

    Handles ID registration and composite key management for templates.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
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

    async def register_template(
        self,
        created_by: str | None = None,
        namespace: str = "wip",
        entry_id: str | None = None,
    ) -> str:
        """
        Register a new template in the Registry.

        Uses empty composite key — templates don't support upsert via Registry.
        Each call always generates a fresh template ID unless entry_id is provided.

        Args:
            created_by: User or system creating this
            namespace: Namespace for the template (default: wip)
            entry_id: Pre-assigned ID (for restore/migration)

        Returns:
            Generated or pre-assigned template ID

        Raises:
            RegistryError: If registration fails
        """
        item = {
            "namespace": namespace,
            "entity_type": "templates",
            "composite_key": {},
            "created_by": created_by,
            "metadata": {"type": "template"},
        }
        if entry_id:
            item["entry_id"] = entry_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[item]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register template: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            return result["registry_id"]

    async def register_templates_bulk(
        self,
        count: int,
        created_by: str | None = None,
        namespace: str = "wip"
    ) -> list[dict[str, Any]]:
        """
        Register multiple templates in the Registry.

        Uses empty composite keys — each template gets a fresh ID.

        Args:
            count: Number of template IDs to generate
            created_by: User or system creating these
            namespace: Namespace for the templates (default: wip)

        Returns:
            List of registration results with IDs

        Raises:
            RegistryError: If registration fails
        """
        items = [
            {
                "namespace": namespace,
                "entity_type": "templates",
                "composite_key": {},
                "created_by": created_by,
                "metadata": {"type": "template"}
            }
            for _ in range(count)
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=items
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register templates: {response.status_code} - {response.text}"
                )

            data = response.json()
            return data["results"]

    async def add_synonym(
        self,
        target_id: str,
        new_value: str,
        namespace: str = "wip",
        additional_fields: dict[str, Any] | None = None
    ) -> bool:
        """
        Add a synonym when a value changes.

        This allows lookups by both old and new values.

        Args:
            target_id: The existing registry ID
            new_value: The new value to add as synonym
            namespace: Namespace for the synonym
            additional_fields: Additional composite key fields

        Returns:
            True if synonym was added

        Raises:
            RegistryError: If operation fails
        """
        composite_key = {"value": new_value}
        if additional_fields:
            composite_key.update(additional_fields)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=[{
                    "target_id": target_id,
                    "synonym_namespace": namespace,
                    "synonym_entity_type": "templates",
                    "synonym_composite_key": composite_key
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to add synonym: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data.get("results", data) if isinstance(data, dict) else data
            if isinstance(result, list):
                return result[0].get("status") == "added"
            return result.get("results", [{}])[0].get("status") == "added"

    async def register_auto_synonym(
        self,
        target_id: str,
        namespace: str,
        composite_key: dict[str, Any],
        created_by: str | None = None,
    ) -> None:
        """
        Register an auto-synonym for a template (best-effort).

        Auto-synonyms enable human-readable resolution (e.g., "PATIENT" → template ID).
        Failure is logged but does not prevent template creation.

        Args:
            target_id: The template's canonical ID
            namespace: Template namespace
            composite_key: Auto-synonym composite key
            created_by: Creator identifier
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/registry/synonyms/add",
                    headers=self._get_headers(),
                    json=[{
                        "target_id": target_id,
                        "synonym_namespace": namespace,
                        "synonym_entity_type": "templates",
                        "synonym_composite_key": composite_key,
                        "created_by": created_by,
                    }]
                )
                if response.status_code != 200:
                    logger.warning(
                        "Failed to register auto-synonym for template %s: %s",
                        target_id, response.text
                    )
        except Exception as e:
            logger.warning("Failed to register auto-synonym for template %s: %s", target_id, e)

    async def lookup_by_value(
        self,
        value: str,
        namespace: str = "wip",
        additional_fields: dict[str, Any] | None = None
    ) -> str | None:
        """
        Look up a registry ID by value.

        Args:
            value: Value to look up
            namespace: Namespace to search in
            additional_fields: Additional composite key fields

        Returns:
            Registry ID if found, None otherwise
        """
        composite_key = {"value": value}
        if additional_fields:
            composite_key.update(additional_fields)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/lookup/by-key",
                headers=self._get_headers(),
                json=[{
                    "namespace": namespace,
                    "entity_type": "templates",
                    "composite_key": composite_key,
                    "search_synonyms": True
                }]
            )

            if response.status_code != 200:
                return None

            data = response.json()
            if data["found"] > 0:
                return data["results"][0].get("entry_id")

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
_client: RegistryClient | None = None


def get_registry_client() -> RegistryClient:
    """Get the singleton Registry client instance."""
    global _client
    if _client is None:
        _client = RegistryClient()
    return _client


def configure_registry_client(
    base_url: str | None = None,
    api_key: str | None = None
) -> RegistryClient:
    """Configure and return the Registry client."""
    global _client
    _client = RegistryClient(base_url=base_url, api_key=api_key)
    return _client
