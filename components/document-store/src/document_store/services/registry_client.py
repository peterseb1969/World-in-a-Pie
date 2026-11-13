"""document-store-side Registry client.

Per-domain thin wrapper around the canonical client in
libs/wip-auth/src/wip_auth/registry_client.py. Adds document-specific
ID-generation methods (generate_document_id, generate_document_ids_bulk).
Identifier resolution (resolve_identifier) lives on the base (CASE-433).

CASE-398 consolidated the universal infrastructure into the canonical
base; what's left here is document-store-specific.
"""

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from wip_auth.registry_client import (
    RegistryClientBase,
    RegistryError,
    RegistryUnavailableError,
    clear_registry_transport,
    registry_unavailable_from,
    set_registry_transport,
)

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _transport_errors(operation: str) -> AsyncIterator[None]:
    """A call the Registry could not be reached for raises
    RegistryUnreachableError, a RegistryError, as an error status does, so
    every caller reports it per item as registry_error. A bare httpx error
    escaped them as a 500."""
    try:
        yield
    except httpx.HTTPError as e:
        raise RegistryUnreachableError(
            f"Registry unreachable ({operation}): {type(e).__name__}: {e}"
        ) from e


class RegistryUnreachableError(RegistryError):
    """A Registry write that could not reach the Registry. A RegistryError,
    so every write path's handler reports it per item; its item carries
    ``details.rule: registry_unavailable``, as every other "could not be
    reached" does, so a caller can tell the outage from a refusal."""

__all__ = [
    "SYSTEM_QUALIFIER_KEY",
    "RegistryClient",
    "RegistryError",
    "RegistryUnreachableError",
    "clear_registry_transport",
    "configure_registry_client",
    "get_registry_client",
    "qualified_synonym_values",
    "set_registry_transport",
]

# The key that qualifies a system-generated synonym. Every synonym is a
# qualified key-value pair: a document's raw-values synonym carries its
# template's value under this key, so the same spelling under two templates
# is two synonyms ("@template": "CHAPTER", "title": "X" vs "@template":
# "SCENE", "title": "X"). Keys starting with "@" are reserved for the
# system: the Registry refuses them in caller-supplied keys and synonyms, and
# template-store refuses a field with this name.
SYSTEM_QUALIFIER_KEY = "@template"


def qualified_synonym_values(
    template_value: str | None, submitted_values: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The raw-values synonym for a document: its submitted identity
    spellings, qualified by its template's value. None when there is
    nothing to register, or no template value to qualify it with (an
    unqualified synonym would claim the spelling for every template)."""
    if not submitted_values:
        return None
    if not template_value:
        logger.warning(
            "No template value to qualify the identity-values synonym %r; not registering it",
            submitted_values,
        )
        return None
    return {SYSTEM_QUALIFIER_KEY: template_value, **submitted_values}


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
        skip_identity_value_synonym: bool = False,
        synonym_values: dict[str, Any] | None = None,
    ) -> tuple[str, bool, str | None]:
        """Generate or retrieve a document ID. Returns (id, is_new, identity_hash).

        skip_identity_value_synonym (CASE-430): for relationship/edge types,
        still compute identity_hash for the primary key but suppress the bare
        identity-values synonym ({source_ref, target_ref}) that would collide
        across edge types between the same pair.

        synonym_values: the submitted identity spellings, minted as the
        raw-values synonym in place of identity_values. The two diverge under
        resolved-referent identity: identity_values carries resolved referent
        IDs (the hash feed) while the human-readable spellings stay the
        business-key lookup surface.
        """
        composite_key: dict[str, Any] = (
            {"ns": namespace, "template_id": template_id} if has_identity_fields else {}
        )
        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": "documents",
            "composite_key": composite_key,
            "identity_values": identity_values if has_identity_fields else None,
            "synonym_values": synonym_values if has_identity_fields else None,
            "skip_identity_value_synonym": skip_identity_value_synonym,
            "created_by": created_by,
            "source_info": {"system_id": "document-store"},
        }
        if entry_id:
            item["entry_id"] = entry_id

        async with _transport_errors("document ID registration"), self._make_client() as client:
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
                synonym_values = item.get("synonym_values")
            else:
                composite_key = {}
                identity_values = None
                synonym_values = None

            entry: dict[str, Any] = {
                "namespace": namespace,
                "entity_type": "documents",
                "composite_key": composite_key,
                "identity_values": identity_values,
                "synonym_values": synonym_values,
                # CASE-430: relationship/edge types suppress the bare
                # identity-values synonym (set per item by bulk_create).
                "skip_identity_value_synonym": bool(item.get("skip_identity_value_synonym")),
                "created_by": created_by,
                "source_info": {"system_id": "document-store"},
            }
            if item.get("entry_id"):
                entry["entry_id"] = item["entry_id"]
            registry_items.append(entry)

        async with _transport_errors("document ID registration"), self._make_client() as client:
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

    async def lookup_document_by_primary_key(
        self, namespace: str, template_id: str, identity_hash: str,
    ) -> str | None:
        """The document_id whose primary key is (namespace, template_id,
        identity_hash), or None.

        The key a document write registers (generate_document_id): synonyms
        are not searched, so only the document whose identity this IS can
        match, never one that merely carries the values as a synonym.

        A Registry that cannot answer (unreachable, timed out, or an error
        status) raises RegistryUnavailableError: an outage is not "no such
        document".
        """
        try:
            async with self._make_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/registry/entries/lookup/by-key",
                    headers=self._get_headers(),
                    json=[{
                        "namespace": namespace,
                        "entity_type": "documents",
                        "composite_key": {
                            "ns": namespace,
                            "template_id": template_id,
                            "identity_hash": identity_hash,
                        },
                        "search_synonyms": False,
                    }],
                )
        except httpx.HTTPError as e:
            raise registry_unavailable_from("document key lookup", error=e) from e
        if response.status_code != 200:
            raise registry_unavailable_from("document key lookup", response)
        result = response.json()["results"][0]
        if result.get("status") == "error":
            # The Registry answered for the request but not for this item:
            # it could not look the key up, which is never "no such document".
            raise RegistryUnavailableError(
                f"Registry could not look up a document key: {result.get('error')}",
                "document key lookup",
            )
        return cast(str, result["entry_id"]) if result.get("status") == "found" else None

    async def rekey_identity(
        self,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Re-key document entries' composite keys to new identity hashes.

        The identity migration's Registry leg: each item is
        ``{entry_id, identity_hash}``, and the Registry rewrites the entry's
        primary composite key (a representation change on the same entity —
        its own sanctioned path, distinct from identity-changing moves).
        Returns the per-item result list.
        """
        async with _transport_errors("identity re-key"), self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/entries/rekey-identity",
                headers=self._get_headers(),
                json=items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to re-key identities: {response.status_code} - {response.text}"
                )
            return cast(list[dict[str, Any]], response.json()["results"])

    async def add_synonyms(
        self,
        entry_id: str,
        namespace: str,
        entity_type: str,
        synonyms: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Register synonyms for a registry entry.

        Returns the per-item result list (each {index, status,
        registry_id, error}); status is one of added / already_exists /
        target_not_found / error. The bulk envelope is unwrapped here so callers
        can inspect per-synonym outcomes (CASE-434/436) rather than discarding
        them.
        """
        items = [
            {
                "target_id": entry_id,
                "synonym_namespace": namespace,
                "synonym_entity_type": entity_type,
                "synonym_composite_key": syn,
            }
            for syn in synonyms
        ]
        async with _transport_errors("synonym registration"), self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/add",
                headers=self._get_headers(),
                json=items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to add synonyms: {response.status_code} - {response.text}"
                )
            data = response.json()
            return cast(list[dict[str, Any]], data.get("results", []))

    async def remove_synonyms(
        self,
        entry_id: str,
        namespace: str,
        entity_type: str,
        synonyms: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove synonyms from a registry entry (rollback of a partial
        add). Returns the per-item result list. Not gated by deletion_mode —
        it is a normal synonym edit that also releases the claim."""
        items = [
            {
                "target_id": entry_id,
                "synonym_namespace": namespace,
                "synonym_entity_type": entity_type,
                "synonym_composite_key": syn,
            }
            for syn in synonyms
        ]
        async with _transport_errors("synonym removal"), self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/remove",
                headers=self._get_headers(),
                json=items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to remove synonyms: {response.status_code} - {response.text}"
                )
            data = response.json()
            return cast(list[dict[str, Any]], data.get("results", []))

    @staticmethod
    def _auto_synonym_key(
        namespace: str,
        template_value: str,
        identity_hash: str | None,
        has_identity_fields: bool,
    ) -> dict[str, Any]:
        """The composite key of a document's auto-synonym.

        With identity fields: the template plus the identity hash. Without:
        a portable UUID4, the document's only address apart from its id.
        """
        if has_identity_fields and identity_hash:
            return {
                "ns": namespace,
                "type": "document",
                "template": template_value,
                "identity_hash": identity_hash,
            }
        return {
            "ns": namespace,
            "type": "document",
            "portable_id": str(uuid.uuid4()),
        }

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
        await self._register_auto_synonym(
            target_id=document_id,
            namespace=namespace,
            entity_type="documents",
            composite_key=self._auto_synonym_key(
                namespace, template_value, identity_hash, has_identity_fields,
            ),
            created_by=created_by,
        )

    async def register_auto_synonyms_per_item(
        self,
        documents: list[dict[str, Any]],
        namespace: str,
        created_by: str | None = None,
    ) -> list[str | None]:
        """Register the auto-synonyms of several new documents in one call.

        ``documents`` holds dicts with document_id, template_value,
        identity_hash and has_identity_fields. Returns one entry per
        document, in order: None when its synonym is registered (or already
        was), else the reason it was refused — so a bulk create can fail
        exactly the documents whose synonym the Registry refused. A transport
        or HTTP failure refuses every document with the same reason.
        """
        if not documents:
            return []
        payload = [
            {
                "target_id": d["document_id"],
                "synonym_namespace": namespace,
                "synonym_entity_type": "documents",
                "synonym_composite_key": self._auto_synonym_key(
                    namespace, d.get("template_value") or "",
                    d.get("identity_hash"), bool(d.get("has_identity_fields")),
                ),
                "created_by": created_by,
            }
            for d in documents
        ]
        try:
            async with self._make_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/registry/synonyms/add",
                    headers=self._get_headers(),
                    json=payload,
                )
        except Exception as e:
            return [f"Failed to register auto-synonym: {e}"] * len(documents)
        if response.status_code != 200:
            reason = (
                f"Failed to register auto-synonym: {response.status_code} - {response.text}"
            )
            return [reason] * len(documents)
        data = response.json()
        rows = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(rows, list) or len(rows) != len(documents):
            return ["Failed to register auto-synonym: unreadable Registry response"] * len(
                documents
            )
        out: list[str | None] = [
            "Failed to register auto-synonym: no result from the Registry"
        ] * len(documents)
        for pos, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            index = row.get("index", pos)
            if not isinstance(index, int) or not 0 <= index < len(documents):
                continue
            status = row.get("status")
            if status in ("added", "already_exists"):
                out[index] = None
            else:
                error = row.get("error")
                out[index] = (
                    f"Failed to register auto-synonym: status={status}"
                    + (f": {error}" if error else "")
                )
        return out

    async def get_namespace_status(self, namespace: str) -> str | None:
        """The namespace's lifecycle status as the Registry serves it, or None
        when the Registry does not serve it to this service's key: deleted,
        no grant, or locked for deletion when the key is not a platform admin
        (a platform admin sees "locked"). None never says which.

        Raises RegistryError (or the transport's error) when the Registry
        cannot answer — callers gating a destructive action must fail closed.
        """
        async with self._make_client() as client:
            response = await client.get(
                f"{self.base_url}/api/registry/namespaces/{namespace}",
                headers=self._get_headers(),
            )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RegistryError(
                f"Failed to fetch namespace {namespace}: "
                f"{response.status_code} - {response.text}"
            )
        return cast(str, response.json().get("status"))

    async def get_namespace_deletion_journal(self, namespace: str) -> dict[str, Any] | None:
        """The Registry's most recent deletion journal for the namespace, or
        None when it has none (404 — also what a key that is not a platform
        admin gets). Raises RegistryError when the Registry cannot answer."""
        async with _transport_errors("deletion journal"), self._make_client() as client:
            response = await client.get(
                f"{self.base_url}/api/registry/namespaces/{namespace}/deletion-status",
                headers=self._get_headers(),
            )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RegistryError(
                f"Failed to fetch the deletion journal of {namespace}: "
                f"{response.status_code} - {response.text}"
            )
        return cast(dict[str, Any], response.json())


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
