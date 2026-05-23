"""def-store-side Registry client.

Per-domain thin wrapper around the canonical client in
libs/wip-auth/src/wip_auth/registry_client.py. Adds the
terminology/term-specific methods that aren't general-purpose enough
to live in the canonical base.

CASE-398 consolidated ~700 LOC of shared infrastructure (auth headers,
httpx client construction, health checks, hard-delete, namespace
metadata, synonym add/lookup, bulk-register envelope) into the
canonical base. What's left here is the def-store-specific
register_terminology / register_term / register_terms_bulk surface.
"""

import asyncio
import logging
from typing import Any, cast

from wip_auth.registry_client import (
    RegistryClientBase,
    RegistryError,
    clear_registry_transport,
    set_registry_transport,
)

logger = logging.getLogger(__name__)

# Re-export RegistryError so existing callers keep working without
# importing from wip_auth directly. The `bulk-result-item-canonical`
# /  `registry-client-singleton` audit rule allows re-exports.
__all__ = [
    "RegistryClient",
    "RegistryError",
    "clear_registry_transport",
    "configure_registry_client",
    "get_registry_client",
    "set_registry_transport",
]


class RegistryClient(RegistryClientBase):
    """def-store-facing Registry client.

    Inherits universal infrastructure (auth, transport, health,
    hard-delete, namespace metadata, synonym helpers) from
    RegistryClientBase. Adds terminology/term-specific wrappers below.
    """

    async def register_terminology(
        self,
        value: str,
        label: str,
        namespace: str,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> str:
        """Register a new terminology. Returns generated/existing terminology_id."""
        result = await self._register_entry(
            namespace=namespace,
            entity_type="terminologies",
            composite_key={"ns": namespace, "value": value, "label": label},
            created_by=created_by,
            entry_id=entry_id,
            metadata={"type": "terminology"},
        )
        if result.status == "error":
            raise RegistryError(f"Registration error: {result.error}")
        if result.registry_id is None:
            raise RegistryError(
                f"Registry returned status={result.status} without registry_id"
            )
        return result.registry_id

    async def register_term(
        self,
        terminology_id: str,
        value: str,
        namespace: str,
        created_by: str | None = None,
        entry_id: str | None = None,
    ) -> str:
        """Register a new term. Returns generated/existing term_id."""
        result = await self._register_entry(
            namespace=namespace,
            entity_type="terms",
            composite_key={
                "ns": namespace,
                "terminology_id": terminology_id,
                "value": value,
            },
            created_by=created_by,
            entry_id=entry_id,
            metadata={"type": "term"},
        )
        if result.status == "error":
            raise RegistryError(f"Registration error: {result.error}")
        if result.registry_id is None:
            raise RegistryError(
                f"Registry returned status={result.status} without registry_id"
            )
        return result.registry_id

    async def register_terms_bulk(
        self,
        terminology_id: str,
        terms: list[dict[str, Any]],
        namespace: str,
        created_by: str | None = None,
        timeout: float | None = None,
        registry_batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Register multiple terms in sub-batches. Returns the registry's
        per-item result dicts (with `status`, `registry_id`, etc.)."""
        if not terms:
            return []

        bulk_timeout = timeout if timeout is not None else 120.0
        all_results: list[dict[str, Any]] = []

        async with self._make_client(timeout=bulk_timeout) as client:
            for batch_start in range(0, len(terms), registry_batch_size):
                batch_end = min(batch_start + registry_batch_size, len(terms))
                batch_terms = terms[batch_start:batch_end]
                items: list[dict[str, Any]] = []
                for term in batch_terms:
                    entry: dict[str, Any] = {
                        "namespace": namespace,
                        "entity_type": "terms",
                        "composite_key": {
                            "ns": namespace,
                            "terminology_id": terminology_id,
                            "value": term["value"],
                        },
                        "created_by": created_by,
                        "metadata": {"type": "term"},
                    }
                    if term.get("entry_id"):
                        entry["entry_id"] = term["entry_id"]
                    items.append(entry)
                response = await client.post(
                    f"{self.base_url}/api/registry/entries/register",
                    headers=self._get_headers(),
                    json=items,
                )
                if response.status_code != 200:
                    raise RegistryError(
                        f"Failed to register terms (batch {batch_start}-{batch_end}): "
                        f"{response.status_code} - {response.text}"
                    )
                data = response.json()
                all_results.extend(data["results"])
                if batch_end < len(terms):
                    await asyncio.sleep(0.05)

        return all_results

    async def add_synonym(
        self,
        namespace: str,
        entity_type: str,
        target_id: str,
        new_value: str,
        additional_fields: dict[str, Any] | None = None,
    ) -> bool:
        """Add a synonym (e.g., for value changes)."""
        composite_key: dict[str, Any] = {"ns": namespace, "value": new_value}
        if additional_fields:
            composite_key.update(additional_fields)
        return await self._add_synonym(
            target_id=target_id,
            synonym_namespace=namespace,
            synonym_entity_type=entity_type,
            synonym_composite_key=composite_key,
        )

    async def register_auto_synonym(
        self,
        target_id: str,
        namespace: str,
        entity_type: str,
        composite_key: dict[str, Any],
        created_by: str | None = None,
    ) -> None:
        """Register an auto-synonym for human-readable resolution."""
        await self._register_auto_synonym(
            target_id=target_id,
            namespace=namespace,
            entity_type=entity_type,
            composite_key=composite_key,
            created_by=created_by,
        )

    async def register_auto_synonyms_bulk(
        self,
        items: list[dict[str, Any]],
        batch_size: int = 100,
    ) -> None:
        """Register auto-synonyms in batches; raise on any HTTP failure."""
        if not items:
            return
        try:
            for batch_start in range(0, len(items), batch_size):
                batch = items[batch_start:batch_start + batch_size]
                await self._register_auto_synonyms_bulk(batch)
                if batch_start + batch_size < len(items):
                    await asyncio.sleep(0.05)
        except RegistryError:
            raise
        except Exception as e:
            raise RegistryError(f"Failed to register auto-synonyms bulk: {e}") from e

    async def lookup_by_value(
        self,
        namespace: str,
        entity_type: str,
        value: str,
        additional_fields: dict[str, Any] | None = None,
    ) -> str | None:
        """Look up a registry ID via /api/registry/entries/lookup/by-key.

        Domain-specific endpoint; not in the canonical base because
        document-store uses /by-id with a different shape.
        """
        composite_key: dict[str, Any] = {"ns": namespace, "value": value}
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
    """Configure the Registry client singleton (called once at app startup).

    Note: this no longer takes a `transport=` argument. Tests inject the
    transport via `set_registry_transport(...)` (module-level), matching
    the pattern in `wip_auth.resolve` (`set_resolve_transport`).
    CASE-398.
    """
    global _client
    _client = RegistryClient(base_url=base_url, api_key=api_key)
    return _client
