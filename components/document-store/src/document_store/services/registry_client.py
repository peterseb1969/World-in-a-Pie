"""document-store-side Registry client.

Per-domain thin wrapper around the canonical client in
libs/wip-auth/src/wip_auth/registry_client.py. Adds document-specific
ID-generation methods (generate_document_id, generate_document_ids_bulk)
and an identifier-resolution helper that uses /api/registry/entries/lookup/by-id
(distinct from the by-key endpoint def-store / template-store use).

CASE-398 consolidated the universal infrastructure into the canonical
base; what's left here is document-store-specific.
"""

import logging
import uuid
from typing import Any, cast

from wip_auth.registry_client import (
    RegistryClientBase,
    RegistryError,
    clear_registry_transport,
    set_registry_transport,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RegistryClient",
    "RegistryError",
    "clear_registry_transport",
    "configure_registry_client",
    "get_registry_client",
    "set_registry_transport",
]


class RegistryClient(RegistryClientBase):
    """document-store-facing Registry client.

    Inherits universal infrastructure from RegistryClientBase. Adds
    document-ID-generation wrappers below.
    """

    async def generate_document_id(
        self,
        template_id: str,
        namespace: str,
        identity_values: dict[str, Any] | None = None,
        has_identity_fields: bool = True,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> tuple[str, bool, str | None]:
        """Generate or retrieve a document ID. Returns (id, is_new, identity_hash)."""
        composite_key: dict[str, Any] = (
            {"ns": namespace, "template_id": template_id} if has_identity_fields else {}
        )
        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": "documents",
            "composite_key": composite_key,
            "identity_values": identity_values if has_identity_fields else None,
            "created_by": created_by,
            "source_info": {"system_id": "document-store"},
        }
        if entry_id:
            item["entry_id"] = entry_id

        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=[item],
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
        namespace: str,
        created_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate multiple document IDs in one request."""
        registry_items: list[dict[str, Any]] = []
        for item in items:
            has_identity = item.get("has_identity_fields", True)
            if has_identity:
                composite_key: dict[str, Any] = {
                    "ns": namespace,
                    "template_id": item["template_id"],
                }
                identity_values = item.get("identity_values")
            else:
                composite_key = {}
                identity_values = None

            entry: dict[str, Any] = {
                "namespace": namespace,
                "entity_type": "documents",
                "composite_key": composite_key,
                "identity_values": identity_values,
                "created_by": created_by,
                "source_info": {"system_id": "document-store"},
            }
            if item.get("entry_id"):
                entry["entry_id"] = item["entry_id"]
            registry_items.append(entry)

        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=registry_items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to generate document IDs: {response.status_code} - {response.text}"
                )
            data = response.json()
            return cast(list[dict[str, Any]], data["results"])

    async def add_synonyms(
        self,
        entry_id: str,
        namespace: str,
        entity_type: str,
        synonyms: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Register synonyms for a registry entry."""
        items = [
            {
                "target_id": entry_id,
                "synonym_namespace": namespace,
                "synonym_entity_type": entity_type,
                "synonym_composite_key": syn,
            }
            for syn in synonyms
        ]
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to add synonyms: {response.status_code} - {response.text}"
                )
            return cast(list[dict[str, Any]], response.json())

    async def register_auto_synonym(
        self,
        document_id: str,
        namespace: str,
        template_value: str,
        identity_hash: str | None,
        has_identity_fields: bool,
        created_by: str | None = None,
    ) -> None:
        """Register a document auto-synonym.

        For documents with identity fields: composite key includes template +
        identity_hash. Without: includes a portable UUID4. Failure raises and
        prevents document creation.
        """
        if has_identity_fields and identity_hash:
            composite_key: dict[str, Any] = {
                "ns": namespace,
                "type": "document",
                "template": template_value,
                "identity_hash": identity_hash,
            }
        else:
            composite_key = {
                "ns": namespace,
                "type": "document",
                "portable_id": str(uuid.uuid4()),
            }
        await self._register_auto_synonym(
            target_id=document_id,
            namespace=namespace,
            entity_type="documents",
            composite_key=composite_key,
            created_by=created_by,
        )

    async def resolve_identifier(
        self,
        namespace: str | None,
        entity_type: str | None,
        value: str,
    ) -> str | None:
        """Resolve any identifier to a canonical entry_id via
        POST /api/registry/entries/lookup/by-id. Distinct endpoint
        from def-store/template-store's by-key lookup."""
        lookup_item: dict[str, Any] = {"entry_id": value}
        if namespace:
            lookup_item["namespace"] = namespace
        if entity_type:
            lookup_item["entity_type"] = entity_type
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/lookup/by-id",
                headers=self._get_headers(),
                json=[lookup_item],
            )
            if response.status_code != 200:
                return None
            data = response.json()
            results = data.get("results", [])
            if results and results[0].get("status") == "found":
                return cast("str | None", results[0].get("entry_id"))
            return None


# ── Singleton ───────────────────────────────────────────────────────────────


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
) -> RegistryClient:
    """Configure the Registry client singleton.

    Tests inject transport via set_registry_transport(...) at module scope
    rather than per-instance transport= kwarg. CASE-398.
    """
    global _client
    _client = RegistryClient(base_url=base_url, api_key=api_key)
    return _client
