"""Canonical HTTP client for the WIP Registry service.

CASE-398 consolidates three drifting clients across def-store,
document-store, and template-store into this shared module. The
duplication was ~700 LOC; the per-domain methods that genuinely differ
(register_terminology, register_template, generate_document_id) stay on
per-component subclasses, but the universal infrastructure
(authentication headers, client construction, health checks, hard-delete,
namespace deletion-mode lookup, synonym add/lookup, bulk-register
envelope, retry, RegistryError) lives here.

Transport injection follows the resolve.py precedent — module-level
set_registry_transport()/clear_registry_transport(), not per-instance
transport= constructor argument. Test fixtures call set_registry_transport
during setup and clear during teardown, mirroring how set_resolve_transport
is already used today.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Module-level transport injection ────────────────────────────────────────
#
# Matches the wip_auth.resolve precedent. Tests call set_registry_transport
# in fixture setup; teardown clears.

_registry_transport: httpx.AsyncBaseTransport | None = None


def set_registry_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Inject an httpx transport for Registry calls (in-process testing).

    Pass None to clear. Mirrors set_resolve_transport in this module's
    sibling resolve.py.
    """
    global _registry_transport
    _registry_transport = transport


def clear_registry_transport() -> None:
    """Clear the injected Registry transport."""
    global _registry_transport
    _registry_transport = None


# ── Errors ──────────────────────────────────────────────────────────────────


class RegistryError(Exception):
    """Error communicating with the Registry service.

    Canonical across all backend services — components import this class
    rather than defining their own. The drift CASE-398 closes had each
    component carrying its own RegistryError that was structurally
    identical but couldn't be caught by `except wip_auth.registry_client.RegistryError`
    from another component.
    """


# ── Pydantic response models ────────────────────────────────────────────────
#
# Typed Registry-endpoint responses. Replace the cast() calls CASE-337
# Angle A's tactical sweep added in three registry_client.py files.

class RegisterEntryResult(BaseModel):
    """One result item from POST /api/registry/entries/register."""

    status: Literal["created", "already_exists", "error"]
    registry_id: str | None = None
    error: str | None = None
    error_code: str | None = None


class RegisterEntryResponse(BaseModel):
    """Top-level response from POST /api/registry/entries/register."""

    results: list[RegisterEntryResult]


class AddSynonymResult(BaseModel):
    """One result item from POST /api/registry/synonyms/add."""

    status: str  # "added", "already_exists", "error"
    error: str | None = None


class AddSynonymResponse(BaseModel):
    """Top-level response from POST /api/registry/synonyms/add."""

    results: list[AddSynonymResult] | None = None


# ── Canonical client ────────────────────────────────────────────────────────


class RegistryClientBase:
    """Canonical Registry HTTP client.

    Per-component subclasses add domain-specific wrapper methods
    (register_terminology, register_template, generate_document_id) that
    delegate to the protected helpers here. The shared infrastructure —
    auth headers, httpx.AsyncClient construction with optional injected
    transport, health checks, hard-delete, namespace metadata fetches —
    lives on this class, not duplicated per service.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
    ):
        """Initialize the Registry client.

        Args:
            base_url: Registry API base URL (default from REGISTRY_URL env).
            api_key: API key (default from REGISTRY_API_KEY env, then
                API_KEY, then a dev-default for local testing).
            timeout: Default request timeout in seconds.
        """
        # base_url has a `os.getenv(key, default)` fallback so it's always str
        # at runtime; mypy needs cast() to see that (the `or` chain combined
        # with str|None inputs doesn't narrow on its own).
        self.base_url: str = cast(
            str, base_url or os.getenv("REGISTRY_URL", "http://localhost:8001")
        )
        # api_key falls back to a dev key string literal at the end of the
        # chain — mypy narrows that one without cast.
        self.api_key: str = (
            api_key
            or os.getenv("REGISTRY_API_KEY")
            or os.getenv("API_KEY")
            or "dev_master_key_for_testing"
        )
        self.timeout: float = timeout

    def _get_headers(self) -> dict[str, str]:
        """Standard auth + content-type headers for Registry requests."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _make_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        """Create an httpx.AsyncClient, honouring the module-level transport
        when one is injected (test fixtures use this for in-process Registry
        mounting)."""
        kwargs: dict[str, Any] = {"timeout": timeout or self.timeout}
        if _registry_transport is not None:
            kwargs["transport"] = _registry_transport
            kwargs["base_url"] = self.base_url
        return httpx.AsyncClient(**kwargs)

    # ── Generic bulk-register helper ────────────────────────────────────

    async def _register_entries_bulk(
        self,
        items: list[dict[str, Any]],
    ) -> list[RegisterEntryResult]:
        """POST a list of items to /api/registry/entries/register.

        Each item is a dict with keys: namespace, entity_type, composite_key,
        created_by (optional), entry_id (optional), metadata (optional).
        Returns the parsed result list — caller branches on per-item
        status / error_code.
        """
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/register",
                headers=self._get_headers(),
                json=items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register entries: {response.status_code} - {response.text}"
                )
            parsed = RegisterEntryResponse.model_validate(response.json())
            return parsed.results

    async def _register_entry(
        self,
        namespace: str,
        entity_type: str,
        composite_key: dict[str, Any],
        created_by: str | None = None,
        entry_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RegisterEntryResult:
        """Single-item convenience wrapper around _register_entries_bulk."""
        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key": composite_key,
            "created_by": created_by,
        }
        if metadata is not None:
            item["metadata"] = metadata
        if entry_id:
            item["entry_id"] = entry_id
        results = await self._register_entries_bulk([item])
        return results[0]

    # ── Generic synonym helpers ─────────────────────────────────────────

    async def _add_synonym(
        self,
        target_id: str,
        synonym_namespace: str,
        synonym_entity_type: str,
        synonym_composite_key: dict[str, Any],
        created_by: str | None = None,
    ) -> bool:
        """POST one synonym to /api/registry/synonyms/add. Returns True
        if status=='added'. Raises on HTTP failure."""
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=[{
                    "target_id": target_id,
                    "synonym_namespace": synonym_namespace,
                    "synonym_entity_type": synonym_entity_type,
                    "synonym_composite_key": synonym_composite_key,
                    "created_by": created_by,
                }],
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to add synonym: {response.status_code} - {response.text}"
                )
            data = response.json()
            # /synonyms/add response can be wrapped {"results": [...]} or
            # bare [...] — handle both for compatibility.
            results = data.get("results", data) if isinstance(data, dict) else data
            if isinstance(results, list) and results:
                return cast(bool, results[0].get("status") == "added")
            return False

    async def _register_auto_synonym(
        self,
        target_id: str,
        namespace: str,
        entity_type: str,
        composite_key: dict[str, Any],
        created_by: str | None = None,
    ) -> None:
        """Register an auto-synonym; raise on failure.

        Used by services as part of entity creation — auto-synonyms enable
        human-readable resolution (e.g., "PATIENT" -> template ID). Failure
        prevents entity creation, so we raise rather than return a status.
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
                    }],
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

    async def _register_auto_synonyms_bulk(
        self,
        items: list[dict[str, Any]],
    ) -> list[AddSynonymResult]:
        """Bulk auto-synonym registration. items: list of dicts with keys
        target_id, namespace (or synonym_namespace), entity_type (or
        synonym_entity_type), composite_key (or synonym_composite_key),
        created_by."""
        normalised: list[dict[str, Any]] = []
        for it in items:
            normalised.append({
                "target_id": it["target_id"],
                "synonym_namespace": it.get("synonym_namespace") or it["namespace"],
                "synonym_entity_type": it.get("synonym_entity_type") or it["entity_type"],
                "synonym_composite_key": it.get("synonym_composite_key") or it["composite_key"],
                "created_by": it.get("created_by"),
            })
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=normalised,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to register auto-synonyms: {response.status_code} - {response.text}"
                )
            data = response.json()
            results_data = data.get("results", data) if isinstance(data, dict) else data
            if not isinstance(results_data, list):
                return []
            return [AddSynonymResult.model_validate(r) for r in results_data]

    # Lookup endpoints (/api/registry/entries/lookup/by-key vs /by-id) are
    # called per-domain — def-store/template-store use by-key with composite
    # keys; document-store uses by-id for identifier resolution. Components
    # keep their own lookup wrappers using self._make_client() + self._get_headers().

    # ── Universal public methods ────────────────────────────────────────

    async def hard_delete_entry(
        self,
        entry_id: str,
        updated_by: str | None = None,
        rollback_uncommitted: bool = False,
    ) -> bool:
        """Hard-delete a Registry entry. Returns True if deleted.

        rollback_uncommitted (CASE-436): set when aborting a just-allocated
        entry whose backing object was never committed (e.g. a document create
        that failed on synonym registration). It bypasses the namespace
        deletion_mode='full' gate, honored only for privileged (service/admin)
        callers — see DeleteItem.rollback_uncommitted.
        """
        async with self._make_client() as client:
            response = await client.request(
                "DELETE",
                f"{self.base_url}/api/registry/entries",
                headers=self._get_headers(),
                json=[{
                    "entry_id": entry_id,
                    "hard_delete": True,
                    "rollback_uncommitted": rollback_uncommitted,
                    "updated_by": updated_by,
                }],
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to hard-delete entry {entry_id}: "
                    f"{response.status_code} - {response.text}"
                )
            data = response.json()
            return cast(bool, data.get("succeeded", 0) > 0)

    async def get_namespace_deletion_mode(self, namespace: str) -> str:
        """Fetch namespace deletion_mode from Registry. Returns 'retain' or 'full'."""
        async with self._make_client() as client:
            response = await client.get(
                f"{self.base_url}/api/registry/namespaces/{namespace}",
                headers=self._get_headers(),
            )
            if response.status_code != 200:
                logger.warning(
                    f"Failed to fetch namespace {namespace}: {response.status_code}"
                )
                return "retain"
            return cast(str, response.json().get("deletion_mode", "retain"))

    async def health_check(self) -> bool:
        """Check if the Registry service is healthy."""
        try:
            async with self._make_client(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


__all__ = [
    "AddSynonymResponse",
    "AddSynonymResult",
    "RegisterEntryResponse",
    "RegisterEntryResult",
    "RegistryClientBase",
    "RegistryError",
    "clear_registry_transport",
    "set_registry_transport",
]
