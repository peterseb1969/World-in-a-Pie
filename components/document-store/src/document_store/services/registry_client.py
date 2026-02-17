"""Client for communicating with the WIP Registry service."""

import os
from typing import Any, Optional

import httpx


class RegistryClient:
    """
    Client for the WIP Registry service.

    Handles UUID7 ID generation for documents.
    Documents use UUID7 IDs for time-based ordering.

    Stable ID semantics:
    - With identity_fields: composite key = {identity_hash, template_id}
      → Registry returns existing ID on match (is_new=False)
    - Without identity_fields: empty composite key {}
      → Registry always generates fresh ID (is_new=True)
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
        template_id: str,
        identity_values: Optional[dict[str, Any]] = None,
        has_identity_fields: bool = True,
        created_by: Optional[str] = None,
        namespace: str = "wip",
        entry_id: Optional[str] = None,
    ) -> tuple[str, bool, Optional[str]]:
        """
        Generate or retrieve a document ID from the Registry.

        With identity_fields: sends identity_values to Registry which computes
        identity_hash, injects it into composite_key for dedup, and creates a
        synonym with the raw values.

        Without identity_fields: uses empty composite key — Registry always
        generates a fresh ID (unless entry_id is provided).

        Args:
            template_id: Template ID the document conforms to
            identity_values: Raw identity field values (Registry computes hash)
            has_identity_fields: Whether the template has identity fields
            created_by: User or system creating this
            namespace: Namespace for the document (default: wip)
            entry_id: Pre-assigned ID (for restore/migration)

        Returns:
            Tuple of (document_id, is_new, identity_hash)

        Raises:
            RegistryError: If registration fails
        """
        if has_identity_fields:
            composite_key = {
                "namespace": namespace,
                "template_id": template_id,
            }
        else:
            composite_key = {}

        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": "documents",
            "composite_key": composite_key,
            "identity_values": identity_values if has_identity_fields else None,
            "created_by": created_by,
            "metadata": {"type": "document"},
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
                    f"Failed to generate document ID: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            is_new = result["status"] == "created"
            identity_hash = result.get("identity_hash")
            return result["registry_id"], is_new, identity_hash

    async def generate_document_ids_bulk(
        self,
        items: list[dict[str, Any]],
        created_by: Optional[str] = None,
        namespace: str = "wip"
    ) -> list[dict[str, Any]]:
        """
        Generate multiple document IDs from the Registry in a single call.

        Items with has_identity_fields=True send identity_values for the Registry
        to compute identity_hash, inject into composite_key, and create synonyms.
        Items without use empty composite key.

        Args:
            items: List of dicts with identity_values, template_id, has_identity_fields
            created_by: User or system creating these
            namespace: Namespace for the documents (default: wip)

        Returns:
            List of registration results with IDs, status, and identity_hash

        Raises:
            RegistryError: If registration fails
        """
        registry_items = []
        for item in items:
            has_identity = item.get("has_identity_fields", True)
            if has_identity:
                composite_key = {
                    "namespace": namespace,
                    "template_id": item["template_id"],
                }
                identity_values = item.get("identity_values")
            else:
                composite_key = {}
                identity_values = None

            registry_items.append({
                "namespace": namespace,
                "entity_type": "documents",
                "composite_key": composite_key,
                "identity_values": identity_values,
                "created_by": created_by,
                "metadata": {"type": "document"},
            })

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

    async def add_synonyms(
        self,
        entry_id: str,
        namespace: str,
        entity_type: str,
        synonyms: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Register synonyms for a registry entry via POST /api/registry/synonyms/add.

        Args:
            entry_id: The entry ID to add synonyms to
            namespace: Namespace of the entry
            entity_type: Entity type (e.g., 'documents')
            synonyms: List of synonym composite key dicts

        Returns:
            List of synonym registration results

        Raises:
            RegistryError: If registration fails
        """
        items = [
            {
                "target_namespace": namespace,
                "target_entity_type": entity_type,
                "target_id": entry_id,
                "synonym_namespace": namespace,
                "synonym_entity_type": entity_type,
                "synonym_composite_key": syn,
            }
            for syn in synonyms
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=items
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to add synonyms: {response.status_code} - {response.text}"
                )

            return response.json()

    async def resolve_identifier(
        self,
        namespace: Optional[str],
        entity_type: Optional[str],
        value: str
    ) -> Optional[str]:
        """
        Resolve any identifier to a canonical entry_id via POST /api/registry/entries/lookup/by-id.

        Uses the extended lookup which searches entry_id, additional_ids,
        and composite key values.

        Args:
            namespace: Namespace to search in (None = search all)
            entity_type: Entity type to search in (None = search all)
            value: The identifier value to resolve

        Returns:
            The resolved entry_id, or None if not found
        """
        lookup_item = {"entry_id": value}
        if namespace:
            lookup_item["namespace"] = namespace
        if entity_type:
            lookup_item["entity_type"] = entity_type

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/lookup/by-id",
                headers=self._get_headers(),
                json=[lookup_item]
            )

            if response.status_code != 200:
                return None

            data = response.json()
            results = data.get("results", [])
            if results and results[0].get("status") == "found":
                return results[0].get("preferred_id")

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
