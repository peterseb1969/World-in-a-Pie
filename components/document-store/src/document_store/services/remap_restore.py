"""Fresh / remap restore — persist an archive's content as new entities.

Modes 1 and 2 preserve canonical IDs. This one keeps nothing: every
terminology, term, template, document and file is registered afresh, and every
reference between them is rewritten to the new IDs. That is what lets a
namespace be restored *beside* the one it came from — two live copies cannot
share an ID, because a canonical ID names one entity.

Two things make it more than a rewrite pass.

**IDs come from the Registry, never from here.** The Registry is the identity
authority; minting a UUID locally would be a service inventing identity for
itself. Provisioning also gives the mode its crash behaviour: provisioned
entries are *reserved*, and reserved entries do not resolve. A job that dies
halfway leaves an invisible, reconcilable namespace rather than a
half-resolvable one, and the whole set becomes visible at the end in one
activation step.

**Composite keys have to match what the owning service would have registered.**
The key is what the Registry deduplicates on, so a key of the wrong shape does
not fail — it silently creates an identity nothing else will ever match. The
shapes below are copied from each service's own registry client and cite it;
they are the single most breakable thing in this module.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("document_store.remap_restore")

# Provisioned in this order because a key may embed its parent's new ID: a
# term's key carries terminology_id, a document's carries template_id.
REMAP_ENTITY_ORDER = ("terminologies", "terms", "templates", "files", "documents")

# The canonical id field each type carries.
ID_FIELDS: dict[str, str] = {
    "terminologies": "terminology_id",
    "terms": "term_id",
    "templates": "template_id",
    "documents": "document_id",
    "files": "file_id",
}

# Provisions IDs: (entity_type, composite_keys) -> new ids, in order.
Provisioner = Callable[[str, list[dict[str, Any]]], Awaitable[list[str]]]


@dataclass
class RemapPlan:
    """Old→new ids per entity type, and the rewritten rows to write."""

    id_map: dict[str, dict[str, str]] = field(default_factory=dict)
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Documents whose identity hash had to be recomputed because an identity
    # field held a canonical ID that has just changed.
    rehashed_documents: int = 0

    def summary(self) -> dict[str, int]:
        counts = {
            entity_type: len(rows) for entity_type, rows in self.rows.items()
        }
        counts["rehashed_documents"] = self.rehashed_documents
        return counts


def composite_key_for(
    entity_type: str,
    entity: dict[str, Any],
    namespace: str,
    id_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """The Registry key the owning service would have registered.

    Each shape is taken from that service's registry client, because the key
    is the dedup identity and a mismatch is silent:

    - terminologies ``{ns, value, label}`` — def-store registry_client
    - terms ``{ns, terminology_id, value}`` — def-store registry_client
    - templates ``{ns, type: "template", value}`` — template-store registry_client
    - files ``{checksum}`` — document-store file_service; deliberately without
      ``ns``, since the entry's own namespace scopes it
    - documents ``{ns, template_id}`` plus ``identity_hash`` — document-store
      registry_client passes ``identity_values`` and the Registry injects the
      hash of them into the key. Provision takes the key directly, so the hash
      is injected here instead. The two hashers agree on identity values: the
      Registry's composite-key hash and wip_auth's document identity hash are
      the same canonical JSON and the same digest. A document whose template
      declares no identity fields registers an EMPTY key — it opts out of
      dedup, and inventing a key for it would give an append-only row an
      identity the platform says it does not have.

    Parent ids are taken from ``id_map``, which is why the types are
    provisioned in dependency order.
    """
    if entity_type == "terminologies":
        return {
            "ns": namespace,
            "value": entity.get("value"),
            "label": entity.get("label"),
        }
    if entity_type == "terms":
        old_parent = entity.get("terminology_id")
        return {
            "ns": namespace,
            "terminology_id": id_map["terminologies"].get(old_parent, old_parent),
            "value": entity.get("value"),
        }
    if entity_type == "templates":
        return {"ns": namespace, "type": "template", "value": entity.get("value")}
    if entity_type == "files":
        return {"checksum": entity.get("checksum")}
    if entity_type == "documents":
        if not entity.get("identity_hash"):
            return {}
        old_template = entity.get("template_id")
        return {
            "ns": namespace,
            "template_id": id_map["templates"].get(old_template, old_template),
            "identity_hash": entity["identity_hash"],
        }
    raise ValueError(f"No composite key shape for entity type '{entity_type}'")


class RemapRestore:
    """Builds the new-identity plan for one namespace.

    The provisioner is injected rather than called directly so the
    orchestration — key construction, dependency order, reference rewriting —
    is testable without a live Registry, and so the HTTP concern stays in the
    engine.
    """

    def __init__(
        self,
        provision: Provisioner,
        remapper: Any,
        *,
        identity_fields_by_template: dict[str, list[str]] | None = None,
    ):
        self._provision = provision
        self._remapper = remapper
        # template value -> identity_fields, used to decide which documents
        # need their identity hash recomputed after references move.
        self._identity_fields = identity_fields_by_template or {}

    async def plan(
        self,
        entities_by_type: dict[str, list[dict[str, Any]]],
        namespace: str,
    ) -> RemapPlan:
        """Provision new ids for every entity and rewrite what points at them."""
        plan = RemapPlan(id_map={t: {} for t in REMAP_ENTITY_ORDER})

        for entity_type in REMAP_ENTITY_ORDER:
            entities = entities_by_type.get(entity_type) or []
            if not entities:
                plan.rows[entity_type] = []
                continue

            keys = [
                composite_key_for(entity_type, entity, namespace, plan.id_map)
                for entity in entities
            ]
            new_ids = await self._provision(entity_type, keys)
            if len(new_ids) != len(entities):
                raise ValueError(
                    f"Registry provisioned {len(new_ids)} id(s) for "
                    f"{len(entities)} {entity_type} — refusing to guess which "
                    "entity got which id"
                )

            id_field = ID_FIELDS[entity_type]
            for entity, new_id in zip(entities, new_ids, strict=True):
                old_id = entity.get(id_field)
                if old_id:
                    plan.id_map[entity_type][str(old_id)] = new_id
            self._feed_remapper(entity_type, plan.id_map[entity_type])

            plan.rows[entity_type] = [
                self._rewrite(entity_type, entity, namespace, new_id)
                for entity, new_id in zip(entities, new_ids, strict=True)
            ]

        # Term relations carry no id of their own — they ARE their endpoints,
        # so they only need those rewritten.
        plan.rows["term_relations"] = [
            {**self._remapper.remap_term_relation(relation), "namespace": namespace}
            for relation in entities_by_type.get("term_relations") or []
        ]

        plan.rehashed_documents = self._recompute_identity_hashes(plan, namespace)
        return plan

    def _feed_remapper(self, entity_type: str, mapping: dict[str, str]) -> None:
        add = {
            "terminologies": self._remapper.add_terminology_mapping,
            "terms": self._remapper.add_term_mapping,
            "templates": self._remapper.add_template_mapping,
            "documents": self._remapper.add_document_mapping,
            "files": self._remapper.add_file_mapping,
        }[entity_type]
        for old_id, new_id in mapping.items():
            add(old_id, new_id)

    def _rewrite(
        self,
        entity_type: str,
        entity: dict[str, Any],
        namespace: str,
        new_id: str,
    ) -> dict[str, Any]:
        """Give one entity its new identity and repoint its references."""
        if entity_type == "templates":
            row = self._remapper.remap_template(entity)
        elif entity_type == "documents":
            row = self._remapper.remap_document(entity)
        elif entity_type == "terms":
            row = self._remapper.remap_term(entity)
        else:
            row = dict(entity)

        row["namespace"] = namespace
        row[ID_FIELDS[entity_type]] = new_id
        return row

    def _recompute_identity_hashes(self, plan: RemapPlan, namespace: str) -> int:
        """Rehash documents whose identity values were themselves ids.

        A document's identity hash is namespace-free and value-based, so it
        survives re-minting untouched — unless one of its identity fields
        holds a canonical ID, which has just changed. Left alone, such a
        document would carry a hash that no longer describes its own data:
        wrong silently, and the next write on that identity would land
        somewhere else.
        """
        from wip_auth.document_identity import compute_hash, extract_identity_values

        rehashed = 0
        for row in plan.rows.get("documents", []):
            identity_fields = self._identity_fields.get(row.get("template_value") or "")
            if not identity_fields:
                continue
            try:
                values = extract_identity_values(row.get("data") or {}, identity_fields)
            except ValueError:
                logger.warning(
                    "Document %s is missing an identity field; leaving its hash "
                    "as archived", row.get("document_id"),
                )
                continue
            recomputed = compute_hash(values)
            if recomputed != row.get("identity_hash"):
                row["identity_hash"] = recomputed
                rehashed += 1
        return rehashed
