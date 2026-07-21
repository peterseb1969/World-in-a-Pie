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

# Types whose archive rows are VERSIONS of one entity rather than distinct
# entities. All versions of a document share (template_id, identity_hash), and
# all versions of a template share its value — so each is one identity to the
# Registry, and provisioning per row makes version 2 collide with version 1.
# Found live: 65,923 document rows produced a 409 on the first chunk.
VERSIONED_TYPES = frozenset({"documents", "templates"})

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


class RemapCollisionError(ValueError):
    """Two sources collapsing into one target claim the same Registry key.

    Provisioning would not fail on this — the Registry's composite key is an
    upsert, so the second claim silently RESOLVES to the first entity's id and
    two definitions that may disagree become one identity. Merging same-keyed
    content is merge-mode's job; a fresh restore refuses instead.
    """

    def __init__(self, target: str, collisions: list[tuple[str, str, dict[str, Any]]]):
        self.target = target
        self.collisions = collisions
        lines = "; ".join(
            f"{entity_type} key {key} claimed by sources {srcs}"
            for entity_type, srcs, key in collisions[:10]
        )
        more = f" (+{len(collisions) - 10} more)" if len(collisions) > 10 else ""
        super().__init__(
            f"Namespaces mapped into '{target}' collide on "
            f"{len(collisions)} Registry key(s): {lines}{more}. Same-keyed "
            "content cannot be freshly restored into one namespace — use "
            "mode=merge for the overlapping namespace instead, or map it to "
            "its own target."
        )


@dataclass
class RemapSource:
    """One archive namespace and where it is going."""

    source: str
    target: str
    entities_by_type: dict[str, list[dict[str, Any]]]
    identity_fields: dict[str, list[str]] = field(default_factory=dict)


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
            groups, keys = self._stage(plan, entity_type, entities_by_type, namespace)
            if groups is None:
                continue
            new_ids = await self._provision_and_map(
                plan, entity_type, groups, keys, self._provision
            )
            self._rewrite_type(plan, entity_type, groups, new_ids, namespace)

        self._finish(plan, entities_by_type, namespace)
        return plan

    def _stage(
        self,
        plan: RemapPlan,
        entity_type: str,
        entities_by_type: dict[str, list[dict[str, Any]]],
        namespace: str,
        id_map: dict[str, dict[str, str]] | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]] | None, list[dict[str, Any]]]:
        """Group one type's rows into entities and build their Registry keys.

        One identity per ENTITY, not per row: a versioned type's rows are
        versions of one thing — they share a composite key, so asking the
        Registry for an id per row collides on the second version.

        A multi-namespace plan passes the GLOBAL old→new map as ``id_map``: a
        document may be pinned to another archived namespace's template
        (legal — verified against the live create path), so its Registry key
        must embed that template's NEW id, which this source's own plan never
        learns.
        """
        entities = entities_by_type.get(entity_type) or []
        if not entities:
            plan.rows[entity_type] = []
            return None, []

        id_field = ID_FIELDS[entity_type]
        groups: dict[str, list[dict[str, Any]]] = {}
        for entity in entities:
            groups.setdefault(str(entity.get(id_field)), []).append(entity)

        keys = [
            composite_key_for(
                entity_type, rows[0], namespace,
                id_map if id_map is not None else plan.id_map,
            )
            for rows in groups.values()
        ]
        return groups, keys

    async def _provision_and_map(
        self,
        plan: RemapPlan,
        entity_type: str,
        groups: dict[str, list[dict[str, Any]]],
        keys: list[dict[str, Any]],
        provision: Provisioner,
    ) -> list[str]:
        """Provision one staged type and record the old→new mapping.

        Rewriting is a separate step (`_rewrite_type`): in a multi-namespace
        plan, a type's rows may reference same-type entities from another
        source, so no source's rows may be rewritten until every source's
        mapping for the type has been fed to the shared remapper.
        """
        new_ids = await provision(entity_type, keys)
        if len(new_ids) != len(groups):
            raise ValueError(
                f"Registry provisioned {len(new_ids)} id(s) for "
                f"{len(groups)} {entity_type} — refusing to guess "
                "which entity got which id"
            )

        for old_id, new_id in zip(groups, new_ids, strict=True):
            if old_id and old_id != "None":
                plan.id_map[entity_type][old_id] = new_id
        self._feed_remapper(entity_type, plan.id_map[entity_type])
        return new_ids

    def _rewrite_type(
        self,
        plan: RemapPlan,
        entity_type: str,
        groups: dict[str, list[dict[str, Any]]],
        new_ids: list[str],
        namespace: str,
    ) -> None:
        plan.rows[entity_type] = [
            self._rewrite(entity_type, row, namespace, new_id)
            for (rows, new_id) in zip(groups.values(), new_ids, strict=True)
            for row in rows
        ]

    def _finish(
        self,
        plan: RemapPlan,
        entities_by_type: dict[str, list[dict[str, Any]]],
        namespace: str,
    ) -> None:
        """Term relations + identity-hash recompute — the post-mapping tail.

        Term relations carry no id of their own — they ARE their endpoints,
        so they only need those rewritten.
        """
        plan.rows["term_relations"] = [
            {**self._remapper.remap_term_relation(relation), "namespace": namespace}
            for relation in entities_by_type.get("term_relations") or []
        ]
        plan.rehashed_documents = self._recompute_identity_hashes(plan, namespace)

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


async def plan_multi(
    sources: list[RemapSource],
    remapper: Any,
    provision_for_target: Callable[[str], Provisioner],
) -> dict[str, RemapPlan]:
    """Plan a fresh restore of several namespaces at once, keyed by source.

    Type-major on purpose: for each entity type, EVERY source is provisioned
    and fed into the one shared remapper before ANY source's rows of that
    type are rewritten. Source-major ordering would rewrite namespace A's
    documents while namespace B's terms and documents still map to nothing,
    leaving every cross-namespace reference on its old id. One remapper
    suffices because canonical ids are globally unique.

    N:1 mappings (several sources into one target) are checked for Registry
    key collisions per type BEFORE provisioning — the Registry key is an
    upsert, so a collision would silently merge two entities rather than
    fail (RemapCollisionError explains the refusal). Empty document keys are
    exempt: an identity-less document opts out of dedup by design.
    """
    planners = {
        s.source: RemapRestore(
            provision_for_target(s.target),
            remapper,
            identity_fields_by_template=s.identity_fields,
        )
        for s in sources
    }
    plans = {
        s.source: RemapPlan(id_map={t: {} for t in REMAP_ENTITY_ORDER})
        for s in sources
    }

    global_id_map: dict[str, dict[str, str]] = {
        t: {} for t in REMAP_ENTITY_ORDER
    }
    for entity_type in REMAP_ENTITY_ORDER:
        staged: list[tuple[RemapSource, dict, list]] = []
        for s in sources:
            groups, keys = planners[s.source]._stage(
                plans[s.source], entity_type, s.entities_by_type, s.target,
                id_map=global_id_map,
            )
            if groups is not None:
                staged.append((s, groups, keys))

        _check_target_collisions(entity_type, staged)

        rewrites: list[tuple[RemapSource, dict, list[str]]] = []
        for s, groups, keys in staged:
            new_ids = await planners[s.source]._provision_and_map(
                plans[s.source], entity_type, groups, keys,
                provision_for_target(s.target),
            )
            global_id_map[entity_type].update(plans[s.source].id_map[entity_type])
            rewrites.append((s, groups, new_ids))

        # Only now — with every source's mapping for this type fed — rewrite.
        for s, groups, new_ids in rewrites:
            planners[s.source]._rewrite_type(
                plans[s.source], entity_type, groups, new_ids, s.target
            )

    for s in sources:
        planners[s.source]._finish(plans[s.source], s.entities_by_type, s.target)
    return plans


def _check_target_collisions(
    entity_type: str,
    staged: list[tuple[RemapSource, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]],
) -> None:
    """Refuse duplicate non-empty Registry keys within one target namespace."""
    import json as _json

    claimed: dict[tuple[str, str], list[str]] = {}
    for s, _groups, keys in staged:
        for key in keys:
            if not key:
                continue  # identity-less document — no dedup key to collide on
            canon = _json.dumps(key, sort_keys=True, default=str)
            claimed.setdefault((s.target, canon), []).append(s.source)

    collisions = [
        (entity_type, ", ".join(sorted(set(srcs))), _json.loads(canon))
        for (target, canon), srcs in claimed.items()
        if len(set(srcs)) > 1
    ]
    if collisions:
        target = next(
            t for (t, canon), srcs in claimed.items() if len(set(srcs)) > 1
        )
        raise RemapCollisionError(target, collisions)
