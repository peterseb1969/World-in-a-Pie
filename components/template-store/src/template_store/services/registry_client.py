"""template-store-side Registry client.

Per-domain thin wrapper around the canonical client in
libs/wip-auth/src/wip_auth/registry_client.py. Adds template-specific
methods (register_template, register_templates_bulk, add_synonym,
register_auto_synonym, lookup_by_value).

CASE-398 consolidated the universal infrastructure into the canonical
base.
"""

import logging
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
    """template-store-facing Registry client."""

    async def register_template(
        self,
        namespace: str,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> str:
        """Register a new template. Empty composite key — Registry always
        generates a fresh ID (unless entry_id is provided)."""
        result = await self._register_entry(
            namespace=namespace,
            entity_type="templates",
            composite_key={},
            created_by=created_by,
            entry_id=entry_id,
            metadata={"type": "template"},
        )
        if result.status == "error":
            raise RegistryError(f"Registration error: {result.error}")
        if result.registry_id is None:
            raise RegistryError(
                f"Registry returned status={result.status} without registry_id"
            )
        return result.registry_id

    async def register_templates_bulk(
        self,
        count: int,
        namespace: str,
        created_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Register N templates (empty composite keys, fresh IDs)."""
        items: list[dict[str, Any]] = [
            {
                "namespace": namespace,
                "entity_type": "templates",
                "composite_key": {},
                "created_by": created_by,
                "metadata": {"type": "template"},
            }
            for _ in range(count)
        ]
        results = await self._register_entries_bulk(items)
        # Return as dicts for back-compat with existing callers (which expected
        # the raw Registry response shape).
        return [r.model_dump() for r in results]

    async def add_synonym(
        self,
        target_id: str,
        new_value: str,
        namespace: str,
        additional_fields: dict[str, Any] | None = None,
    ) -> bool:
        """Add a synonym for a template (e.g., when its value changes)."""
        composite_key: dict[str, Any] = {"ns": namespace, "value": new_value}
        if additional_fields:
            composite_key.update(additional_fields)
        return await self._add_synonym(
            target_id=target_id,
            synonym_namespace=namespace,
            synonym_entity_type="templates",
            synonym_composite_key=composite_key,
        )

    async def register_auto_synonym(
        self,
        target_id: str,
        namespace: str,
        composite_key: dict[str, Any],
        created_by: str | None = None,
    ) -> None:
        """Register a template auto-synonym."""
        await self._register_auto_synonym(
            target_id=target_id,
            namespace=namespace,
            entity_type="templates",
            composite_key=composite_key,
            created_by=created_by,
        )

    async def lookup_by_value(
        self,
        value: str,
        namespace: str,
        additional_fields: dict[str, Any] | None = None,
    ) -> str | None:
        """Look up a registry ID via /api/registry/entries/lookup/by-key."""
        composite_key: dict[str, Any] = {"ns": namespace, "value": value}
        if additional_fields:
            composite_key.update(additional_fields)
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/lookup/by-key",
                headers=self._get_headers(),
                json=[{
                    "namespace": namespace,
                    "entity_type": "templates",
                    "composite_key": composite_key,
                    "search_synonyms": True,
                }],
            )
            if response.status_code != 200:
                return None
            data = response.json()
            if data["found"] > 0:
                return cast("str | None", data["results"][0].get("entry_id"))
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
