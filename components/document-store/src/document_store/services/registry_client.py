"""document-store-side Registry client.

Per-domain thin wrapper around the canonical client in
libs/wip-auth/src/wip_auth/registry_client.py. Adds document-specific
ID-generation methods (generate_document_id, generate_document_ids_bulk).
Identifier resolution (resolve_identifier) lives on the base (CASE-433).

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
    "SYSTEM_QUALIFIER_KEY",
    "RegistryClient",
    "RegistryError",
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

    async def lookup_document_by_primary_key(
        self, namespace: str, template_id: str, identity_hash: str,
    ) -> str | None:
        """The document_id whose primary key is (namespace, template_id,
        identity_hash), or None.

        The key a document write registers (generate_document_id): synonyms
        are not searched, so only the document whose identity this IS can
        match, never one that merely carries the values as a synonym.
        """
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
        if response.status_code != 200:
            raise RegistryError(
                f"Failed to look up document key: {response.status_code} - {response.text}"
            )
        result = response.json()["results"][0]
        return cast(str, result["entry_id"]) if result.get("status") == "found" else None

    async def get_entry_synonyms(
        self, entry_ids: list[str], namespace: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Each found entry's synonyms, as ``{entry_id: [synonym, ...]}``.

        Entries the Registry does not return as found are left out."""
        found: dict[str, list[dict[str, Any]]] = {}
        async with self._make_client() as client:
            for start in range(0, len(entry_ids), 500):
                chunk = entry_ids[start:start + 500]
                response = await client.post(
                    f"{self.base_url}/api/registry/entries/lookup/by-id",
                    headers=self._get_headers(),
                    json=[
                        {"entry_id": eid, "namespace": namespace, "entity_type": "documents"}
                        for eid in chunk
                    ],
                )
                if response.status_code != 200:
                    raise RegistryError(
                        f"Failed to look up entries: {response.status_code} - {response.text}"
                    )
                for eid, result in zip(chunk, response.json()["results"], strict=True):
                    if result.get("status") == "found" and result.get("entry_id") == eid:
                        found[eid] = list(result.get("synonyms") or [])
        return found

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
        async with self._make_client() as client:
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
            data = response.json()
            return cast(list[dict[str, Any]], data.get("results", []))

    async def add_synonyms_bulk(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add synonyms to MANY entries in one request.

        Each item is {target_id, synonym_namespace, synonym_entity_type,
        synonym_composite_key}. Returns the per-item results in input order.
        The caller chunks (500 per request, like the other restore steps).
        """
        return await self._post_synonym_items("add", items)

    async def remove_synonyms_bulk(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove synonyms from MANY entries in one request; the counterpart
        of `add_synonyms_bulk`. A result is `removed`, or `not_found` when the
        synonym was already gone."""
        return await self._post_synonym_items("remove", items)

    async def _post_synonym_items(
        self, verb: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not items:
            return []
        async with self._make_client() as client:
            response = await client.post(
                f"{self.base_url}/api/registry/synonyms/{verb}",
                headers=self._get_headers(),
                json=items,
            )
            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to {verb} synonyms: {response.status_code} - {response.text}"
                )
            results = cast(list[dict[str, Any]], response.json().get("results", []))
        by_index = {r.get("index"): r for r in results}
        return [
            by_index.get(i, {
                "index": i, "status": "error", "error_code": "registry_error",
                "error": "no result returned",
            })
            for i in range(len(items))
        ]

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
        async with self._make_client() as client:
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

    async def get_namespace_status(self, namespace: str) -> str | None:
        """The namespace's lifecycle status as the Registry serves it, or None
        when the Registry does not serve it: deleted, or locked for deletion
        (the Registry hides a locked namespace from every caller, so the two
        are indistinguishable here).

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
