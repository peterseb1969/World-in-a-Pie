"""template-store-side Registry client.

Per-domain thin wrapper around the canonical client in
libs/wip-auth/src/wip_auth/registry_client.py. Adds template-specific
methods (register_template, add_synonym, register_auto_synonym).

CASE-398 consolidated the universal infrastructure into the canonical
base.
"""

import logging
from typing import Any

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
        value: str,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> tuple[str, str]:
        """Register a template's identity with the Registry.

        The composite key {ns, type, value} IS the template's identity —
        the same key shape the auto-synonym has always carried, now
        registered as the entry's primary key. The Registry's dedup treats
        primary keys and synonym keys uniformly, so a value already claimed
        by a pre-existing template (whose primary key predates this and is
        empty) resolves to that template's entry via its auto-synonym —
        upsert works against old data with no backfill.

        Returns (template_id, status) where status is "created" for a
        fresh identity or "already_exists" when the key resolved to an
        existing entry. Callers branch: create v1, or version the existing
        template (create-as-upsert), or fail a restore onto a dirty target.
        """
        result = await self._register_entry(
            namespace=namespace,
            entity_type="templates",
            composite_key={"ns": namespace, "type": "template", "value": value},
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
        return result.registry_id, result.status

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
