"""Client for communicating with the WIP Registry service."""

import os
from typing import Any, Optional

import httpx


class RegistryClient:
    """
    Client for the WIP Registry service.

    Handles ID generation and composite key registration for templates.
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

    async def register_template(
        self,
        code: str,
        name: str,
        version: int = 1,
        created_by: Optional[str] = None,
        namespace: str = "wip-templates"
    ) -> str:
        """
        Register a new template in the Registry.

        Args:
            code: Template code (e.g., 'PERSON')
            name: Template name
            version: Template version (included in composite key for versioning)
            created_by: User or system creating this
            namespace: Namespace for the template (default: wip-templates)

        Returns:
            Generated template ID (e.g., 'TPL-000001')

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
                        "name": name,
                        "version": version
                    },
                    "created_by": created_by,
                    "metadata": {"type": "template", "version": version}
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register template: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            if result["status"] == "already_exists":
                # Return existing ID
                return result["registry_id"]

            return result["registry_id"]

    async def register_templates_bulk(
        self,
        templates: list[dict[str, Any]],
        created_by: Optional[str] = None,
        namespace: str = "wip-templates"
    ) -> list[dict[str, Any]]:
        """
        Register multiple templates in the Registry.

        Args:
            templates: List of template dicts with 'code', 'name', and optional 'version'
            created_by: User or system creating these
            namespace: Namespace for the templates (default: wip-templates)

        Returns:
            List of registration results with IDs

        Raises:
            RegistryError: If registration fails
        """
        items = [
            {
                "pool_id": namespace,
                "composite_key": {
                    "code": template["code"],
                    "name": template["name"],
                    "version": template.get("version", 1)
                },
                "created_by": created_by,
                "metadata": {"type": "template", "version": template.get("version", 1)}
            }
            for template in templates
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
        new_code: str,
        additional_fields: Optional[dict[str, Any]] = None
    ) -> bool:
        """
        Add a synonym when a code changes.

        This allows lookups by both old and new codes.

        Args:
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
                    "target_pool_id": "wip-templates",
                    "target_id": target_id,
                    "synonym_pool_id": "wip-templates",
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
        code: str,
        additional_fields: Optional[dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Look up a registry ID by code.

        Args:
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
                    "pool_id": "wip-templates",
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
