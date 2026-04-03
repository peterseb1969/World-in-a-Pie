"""Client for communicating with the WIP Registry service."""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RegistryClient:
    """
    Client for the WIP Registry service.

    Handles ID registration and composite key management for
    terminologies and terms.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
        transport: Any | None = None,
    ):
        """
        Initialize the Registry client.

        Args:
            base_url: Registry API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
            transport: Optional httpx transport (for in-process testing)
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
        self._transport = transport

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def _make_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        """Create an httpx client, injecting transport when set."""
        kwargs: dict[str, Any] = {"timeout": timeout or self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
            kwargs["base_url"] = self.base_url
        return httpx.AsyncClient(**kwargs)

    async def register_terminology(
        self,
        value: str,
        label: str,
        namespace: str,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> str:
        """
        Register a new terminology in the Registry.

        Args:
            value: Terminology value (e.g., 'DOC_STATUS')
            label: Terminology label
            created_by: User or system creating this
            namespace: Namespace for the terminology (default: wip)
            entry_id: Pre-assigned ID (for restore/migration)

        Returns:
            Generated or pre-assigned terminology ID

        Raises:
            RegistryError: If registration fails
        """
        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": "terminologies",
            "composite_key": {
                "value": value,
                "label": label
            },
            "created_by": created_by,
            "metadata": {"type": "terminology"}
        }
        if entry_id:
            item["entry_id"] = entry_id

        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[item]
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
        value: str,
        namespace: str,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> str:
        """
        Register a new term in the Registry.

        Args:
            terminology_id: Parent terminology ID
            value: Term value
            created_by: User or system creating this
            namespace: Namespace for the term (default: wip)
            entry_id: Pre-assigned ID (for restore/migration)

        Returns:
            Generated or pre-assigned term ID

        Raises:
            RegistryError: If registration fails
        """
        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": "terms",
            "composite_key": {
                "terminology_id": terminology_id,
                "value": value
            },
            "created_by": created_by,
            "metadata": {"type": "term"}
        }
        if entry_id:
            item["entry_id"] = entry_id

        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[item]
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
        namespace: str,
        created_by: str | None = None,
        timeout: float | None = None,
        registry_batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Register multiple terms in the Registry.

        Uses sub-batching to avoid timeout issues with large batches.
        Each sub-batch is sent as a separate HTTP request.

        Args:
            terminology_id: Parent terminology ID
            terms: List of term dicts with 'value'
            created_by: User or system creating these
            timeout: Request timeout in seconds per sub-batch (default 120)
            registry_batch_size: Number of terms per registry HTTP call (default 100)
            namespace: Namespace for the terms (default: wip)

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
        async with self._make_client(timeout=bulk_timeout) as client:
            for batch_start in range(0, len(terms), registry_batch_size):
                batch_end = min(batch_start + registry_batch_size, len(terms))
                batch_terms = terms[batch_start:batch_end]

                items = []
                for term in batch_terms:
                    entry: dict[str, Any] = {
                        "namespace": namespace,
                        "entity_type": "terms",
                        "composite_key": {
                            "terminology_id": terminology_id,
                            "value": term["value"]
                        },
                        "created_by": created_by,
                        "metadata": {"type": "term"}
                    }
                    if term.get("entry_id"):
                        entry["entry_id"] = term["entry_id"]
                    items.append(entry)

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
        entity_type: str,
        target_id: str,
        new_value: str,
        additional_fields: dict[str, Any] | None = None
    ) -> bool:
        """
        Add a synonym when a value changes.

        This allows lookups by both old and new values.

        Args:
            namespace: Namespace (e.g., 'wip')
            entity_type: Entity type (e.g., 'terminologies', 'terms')
            target_id: The existing registry ID
            new_value: The new value to add as synonym
            additional_fields: Additional composite key fields

        Returns:
            True if synonym was added

        Raises:
            RegistryError: If operation fails
        """
        composite_key = {"value": new_value}
        if additional_fields:
            composite_key.update(additional_fields)

        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=[{
                    "target_id": target_id,
                    "synonym_namespace": namespace,
                    "synonym_entity_type": entity_type,
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
        entity_type: str,
        composite_key: dict[str, Any],
        created_by: str | None = None,
    ) -> None:
        """
        Register an auto-synonym for an entity.

        Auto-synonyms enable human-readable resolution (e.g., "STATUS" → terminology ID).
        Failure raises and prevents entity creation.

        Args:
            target_id: The entity's canonical ID
            namespace: Entity namespace
            entity_type: Registry entity type (e.g., 'terminologies', 'terms')
            composite_key: Auto-synonym composite key
            created_by: Creator identifier

        Raises:
            RegistryError: If auto-synonym registration fails
        """
        try:
            async with self._make_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/registry/synonyms/add",
                    headers=self._get_headers(),
                    json=[{
                        "target_id": target_id,
                        "synonym_namespace": namespace,
                        "synonym_entity_type": entity_type,
                        "synonym_composite_key": composite_key,
                        "created_by": created_by,
                    }]
                )
                if response.status_code != 200:
                    raise RegistryError(
                        f"Failed to register auto-synonym for {target_id}: {response.text}"
                    )
        except RegistryError:
            raise
        except Exception as e:
            raise RegistryError(
                f"Failed to register auto-synonym for {target_id}: {e}"
            ) from e

    async def register_auto_synonyms_bulk(
        self,
        items: list[dict[str, Any]],
        batch_size: int = 100,
    ) -> None:
        """
        Register auto-synonyms in bulk.

        Each item should have: target_id, namespace, entity_type, composite_key, created_by.
        Failure raises and prevents entity creation.

        Args:
            items: List of synonym registration dicts
            batch_size: Number of synonyms per HTTP call

        Raises:
            RegistryError: If auto-synonym registration fails
        """
        if not items:
            return

        try:
            async with self._make_client(timeout=30.0) as client:
                for batch_start in range(0, len(items), batch_size):
                    batch = items[batch_start:batch_start + batch_size]
                    payload = [
                        {
                            "target_id": item["target_id"],
                            "synonym_namespace": item["namespace"],
                            "synonym_entity_type": item["entity_type"],
                            "synonym_composite_key": item["composite_key"],
                            "created_by": item.get("created_by"),
                        }
                        for item in batch
                    ]
                    response = await client.post(
                        f"{self.base_url}/api/registry/synonyms/add",
                        headers=self._get_headers(),
                        json=payload,
                    )
                    if response.status_code != 200:
                        raise RegistryError(
                            f"Failed to register auto-synonyms batch {batch_start}-"
                            f"{batch_start + len(batch)}: {response.text}"
                        )
                    if batch_start + batch_size < len(items):
                        await asyncio.sleep(0.05)
        except RegistryError:
            raise
        except Exception as e:
            raise RegistryError(
                f"Failed to register auto-synonyms bulk: {e}"
            ) from e

    async def lookup_by_value(
        self,
        namespace: str,
        entity_type: str,
        value: str,
        additional_fields: dict[str, Any] | None = None
    ) -> str | None:
        """
        Look up a registry ID by value.

        Args:
            namespace: Namespace to search
            entity_type: Entity type to search
            value: Value to look up
            additional_fields: Additional composite key fields

        Returns:
            Registry ID if found, None otherwise
        """
        composite_key = {"value": value}
        if additional_fields:
            composite_key.update(additional_fields)

        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/lookup/by-key",
                headers=self._get_headers(),
                json=[{
                    "namespace": namespace,
                    "entity_type": entity_type,
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

    async def hard_delete_entry(self, entry_id: str, updated_by: str | None = None) -> bool:
        """Hard-delete a Registry entry. Returns True if deleted."""
        async with self._make_client() as client:
            response = await client.request(
                "DELETE",
                f"{self.base_url}/api/registry/entries",
                headers=self._get_headers(),
                json=[{
                    "entry_id": entry_id,
                    "hard_delete": True,
                    "updated_by": updated_by,
                }],
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to hard-delete entry {entry_id}: {response.status_code} - {response.text}"
                )
            data = response.json()
            return data.get("succeeded", 0) > 0

    async def get_namespace_deletion_mode(self, namespace: str) -> str:
        """Fetch namespace deletion_mode from Registry. Returns 'retain' or 'full'."""
        async with self._make_client() as client:
            response = await client.get(
                f"{self.base_url}/api/registry/namespaces/{namespace}",
                headers=self._get_headers(),
            )
            if response.status_code != 200:
                logger.warning(f"Failed to fetch namespace {namespace}: {response.status_code}")
                return "retain"
            return response.json().get("deletion_mode", "retain")

    async def health_check(self) -> bool:
        """Check if the Registry service is healthy."""
        try:
            async with self._make_client(timeout=5.0) as client:
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
    api_key: str | None = None,
    transport: Any | None = None,
) -> RegistryClient:
    """Configure and return the Registry client."""
    global _client
    _client = RegistryClient(base_url=base_url, api_key=api_key, transport=transport)
    return _client
