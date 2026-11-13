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

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator
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



def remap_file(entity: dict[str, Any], remapper: Any) -> dict[str, Any]:
    """A file row with its ``allowed_templates`` — canonical template ids —
    pointed at the ids those templates have on the target. An id the archive
    does not map (a template outside it) is kept as it is."""
    allowed = entity.get("allowed_templates")
    if not allowed:
        return entity
    return {**entity, "allowed_templates": [remapper.template_map.get(t, t) for t in allowed]}


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
    # The identity definition of every template the archive's documents are
    # pinned to, whichever namespace holds it (see TemplateDefinitions).
    template_definitions: TemplateDefinitions | None = None
    # Streamed documents: when set, ``entities_by_type["documents"]`` holds
    # ONE compact row per document (what its Registry key needs) and this
    # re-reads the archive's version chains, so the plan never holds the
    # documents' content.
    document_chains: Callable[[], Iterator[list[dict[str, Any]]]] | None = None


@dataclass
class RemapPlan:
    """Old→new ids per entity type, and the rewritten rows to write."""

    id_map: dict[str, dict[str, str]] = field(default_factory=dict)
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Documents whose identity hash had to be recomputed because an identity
    # field held a canonical ID that has just changed.
    rehashed_documents: int = 0
    # Keyed documents on a template whose definition neither the archive nor
    # this install could give: their identity hash is kept as archived.
    unrehashable_documents: int = 0
    # Streamed documents (RemapSource.document_chains): the rewritten rows
    # are produced on demand instead of held in ``rows["documents"]``, each
    # paired with whether its identity hash was recomputed.
    document_stream: Callable[[], Iterator[tuple[dict[str, Any], bool | None]]] | None = None
    document_rows: int = 0
    # Streamed documents' FINAL Registry keys (after rewrite and rehash), by
    # new document_id — what activation and the synonym restore need, kept
    # so neither has to stream the documents again.
    document_keys: dict[str, dict[str, Any]] = field(default_factory=dict)

    def final_document_keys(self, namespace: str) -> dict[str, dict[str, Any]]:
        """New document_id -> final Registry key, for every keyed document."""
        if self.document_stream is not None:
            return self.document_keys
        keys: dict[str, dict[str, Any]] = {}
        for row in self.rows.get("documents", []):
            key = final_document_key(row, namespace)
            if key:
                keys[row["document_id"]] = key
        return keys

    def iter_documents(self) -> Iterator[dict[str, Any]]:
        """The rewritten document rows, held or streamed."""
        if self.document_stream is not None:
            for row, _rehashed in self.document_stream():
                yield row
        else:
            yield from self.rows.get("documents", [])

    def summary(self) -> dict[str, int]:
        counts = {
            entity_type: len(rows) for entity_type, rows in self.rows.items()
        }
        if self.document_stream is not None:
            counts["documents"] = self.document_rows
        counts["rehashed_documents"] = self.rehashed_documents
        if self.unrehashable_documents:
            counts["unrehashable_documents"] = self.unrehashable_documents
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
        old_parent: str | None = entity.get("terminology_id")
        return {
            "ns": namespace,
            "terminology_id": (
                id_map["terminologies"].get(old_parent, old_parent)
                if old_parent is not None else None
            ),
            "value": entity.get("value"),
        }
    if entity_type == "templates":
        return {"ns": namespace, "type": "template", "value": entity.get("value")}
    if entity_type == "files":
        return {"checksum": entity.get("checksum")}
    if entity_type == "documents":
        if not entity.get("identity_hash"):
            return {}
        old_template: str | None = entity.get("template_id")
        return {
            "ns": namespace,
            "template_id": (
                id_map["templates"].get(old_template, old_template)
                if old_template is not None else None
            ),
            "identity_hash": entity["identity_hash"],
        }
    raise ValueError(f"No composite key shape for entity type '{entity_type}'")


def final_document_key(row: dict[str, Any], namespace: str) -> dict[str, Any]:
    """The Registry key of a document row the restore has already rewritten.

    ``composite_key_for`` maps an ARCHIVED row's template id through the id
    map. A rewritten row already holds its new template id; mapping it again
    is a no-op only while new ids never equal old ones. With prefixed ids on a
    pre-created target a new id can be some other template's old id, and the
    key would silently name that template.
    """
    if not row.get("identity_hash"):
        return {}
    return {
        "ns": namespace,
        "template_id": row.get("template_id"),
        "identity_hash": row["identity_hash"],
    }


def template_definition(template: dict[str, Any]) -> dict[str, Any]:
    """What a rehash needs of one template version. ``extends`` /
    ``extends_version`` are kept so an archived row, stored without what it
    inherits, can be composed along its pinned parents."""
    return {
        "identity_fields": list(template.get("identity_fields") or []),
        "fields": list(template.get("fields") or []),
        "referent_paths": template.get("referent_identity_paths"),
        "extends": template.get("extends"),
        "extends_version": template.get("extends_version"),
    }


DefinitionKey = tuple[str, int]


@dataclass
class TemplateDefinitions:
    """The identity definition each document's rehash reads.

    A document is rehashed against the template version it is pinned to:
    its archived (template_id, template_version), which may belong to any
    namespace of the archive — a document's namespace is independent of its
    template's. Keying by template VALUE within the document's own namespace
    missed every cross-namespace template and, where a template of the same
    value sat in the document's namespace, read the wrong definition.

    ``archived`` holds the archive's template rows as stored: a template that
    extends another stores neither its inherited fields nor, unless it
    declares its own, its identity_fields. A lookup composes them along the
    pinned parents (``extends_version``), as template-store does: the
    nearest declared identity_fields, the parents' fields before its own.
    ``external`` holds templates the archive references but does not carry,
    read from this install per version, already composed.

    When the pinned version is in neither, another version of the same
    template stands in (the highest held): identity_fields cannot change
    between versions (the template store refuses it), so the same fields
    feed the hash. Only a template known in neither, or a parent chain that
    cannot be followed, leaves a document without a definition.
    """

    archived: dict[DefinitionKey, dict[str, Any]] = field(default_factory=dict)
    external: dict[DefinitionKey, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def of_templates(cls, templates: Iterable[dict[str, Any]]) -> TemplateDefinitions:
        archived: dict[DefinitionKey, dict[str, Any]] = {}
        for template in templates:
            template_id = template.get("template_id")
            if template_id:
                archived[(str(template_id), int(template.get("version") or 1))] = (
                    template_definition(template)
                )
        return cls(archived=archived)

    def template_ids(self) -> set[str]:
        return {template_id for template_id, _version in self.archived}

    def lookup(self, document: dict[str, Any]) -> dict[str, Any] | None:
        """The definition for an ARCHIVED document row, or None."""
        return self._resolved(
            str(document.get("template_id")), int(document.get("template_version") or 1),
        )

    def _held(self, template_id: str, version: int | None) -> tuple[dict[str, Any], bool] | None:
        """(definition, composed) at the version, else at the highest version
        held of the template; ``composed`` is False for an archived row."""
        for source, composed in ((self.archived, False), (self.external, True)):
            if version is not None and (template_id, version) in source:
                return source[(template_id, version)], composed
        for source, composed in ((self.archived, False), (self.external, True)):
            versions = [v for tid, v in source if tid == template_id]
            if versions:
                return source[(template_id, max(versions))], composed
        return None

    def _resolved(self, template_id: str, version: int | None) -> dict[str, Any] | None:
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        current: tuple[str, int | None] | None = (template_id, version)
        while current is not None:
            if current[0] in seen:
                return None  # a cycle cannot be judged
            seen.add(current[0])
            held = self._held(*current)
            if held is None:
                return None  # a template or parent known nowhere
            definition, composed = held
            chain.append(definition)
            parent = definition.get("extends")
            current = None if composed or not parent else (
                str(parent), definition.get("extends_version"),
            )
        return _compose(chain)


def _compose(chain: list[dict[str, Any]]) -> dict[str, Any]:
    """A definition from its row and its ancestors (child first): the
    nearest declared identity_fields, the ancestors' fields before the
    child's, and the child's own referent paths (template-store stores them
    computed over the composed template)."""
    identity_fields = next((d["identity_fields"] for d in chain if d["identity_fields"]), [])
    fields = [f for d in reversed(chain) for f in d["fields"]]
    return {
        "identity_fields": list(identity_fields),
        "fields": fields,
        "referent_paths": chain[0]["referent_paths"],
    }


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
        template_definitions: TemplateDefinitions | None = None,
        document_chains: Callable[[], Iterator[list[dict[str, Any]]]] | None = None,
    ):
        self._provision = provision
        self._remapper = remapper
        self._document_chains = document_chains
        # Which documents need their identity hash recomputed after their
        # references move, and how: by the template each is pinned to.
        self._templates = template_definitions or TemplateDefinitions()

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
        if entity_type == "documents" and self._document_chains is not None:
            self._defer_documents(plan, groups, new_ids, namespace)
            return
        rewritten: list[dict[str, Any]] = []
        for rows, new_id in zip(groups.values(), new_ids, strict=True):
            for archived in rows:
                row = self._rewrite(entity_type, archived, namespace, new_id)
                if entity_type == "documents":
                    outcome = self._rehash(row, archived)
                    plan.rehashed_documents += outcome is True
                    plan.unrehashable_documents += outcome is None
                rewritten.append(row)
        plan.rows[entity_type] = rewritten

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
        if plan.document_stream is not None:
            # One counting pass: the plan's summary (and a dry run's report)
            # states how many rows and rehashes the write will produce.
            rows = rehashed = unrehashable = 0
            for row, outcome in plan.document_stream():
                rows += 1
                rehashed += outcome is True
                unrehashable += outcome is None
                key = final_document_key(row, namespace)
                if key:
                    plan.document_keys[row["document_id"]] = key
            plan.document_rows, plan.rehashed_documents = rows, rehashed
            plan.unrehashable_documents = unrehashable
        # Held documents were rehashed as they were rewritten (_rewrite_type),
        # with the archived row at hand: that is what names the template.

    def _defer_documents(
        self,
        plan: RemapPlan,
        groups: dict[str, list[dict[str, Any]]],
        new_ids: list[str],
        namespace: str,
    ) -> None:
        """The document rewrite, as a stream over the archive's chains.

        The in-memory path rewrites every document row here and holds them.
        This produces the same rows on demand: the remapper is snapshotted at
        this point — the state the in-memory rewrite sees — and each row is
        rewritten and rehashed as it is read.
        """
        chains = self._document_chains
        assert chains is not None
        new_id_by_old = dict(zip(groups, new_ids, strict=True))
        remapper = copy.deepcopy(self._remapper)

        def stream() -> Iterator[tuple[dict[str, Any], bool | None]]:
            for chain in chains():
                new_id = new_id_by_old[str(chain[0].get("document_id"))]
                for entity in chain:
                    row = remapper.remap_document(entity)
                    row["namespace"] = namespace
                    row[ID_FIELDS["documents"]] = new_id
                    yield row, self._rehash(row, entity)

        plan.document_stream = stream
        plan.rows["documents"] = []

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
        row: dict[str, Any]
        if entity_type == "templates":
            row = self._remapper.remap_template(entity)
        elif entity_type == "documents":
            row = self._remapper.remap_document(entity)
        elif entity_type == "terms":
            row = self._remapper.remap_term(entity)
        elif entity_type == "files":
            row = remap_file(entity, self._remapper)
        else:
            row = dict(entity)

        row["namespace"] = namespace
        row[ID_FIELDS[entity_type]] = new_id
        return row

    def _rehash(self, row: dict[str, Any], archived: dict[str, Any]) -> bool | None:
        """Recompute one remapped row's identity hash: True if it changed,
        False if not, None for a keyed row no definition covers.

        A document's identity hash is namespace-free, so it survives
        re-minting untouched — unless its feed holds canonical IDs that have
        just changed: an identity data value that is itself an id, or a
        term/reference identity field, whose feed is the resolution's
        canonical ID. The remapper has rewritten both, so recomputing from
        the remapped row gives the hash the new instance's writes produce.
        The definition is the template ``archived`` is pinned to (composed
        along its parents); a keyed row whose template is known nowhere, or
        whose identity values or resolutions cannot be read, keeps its hash,
        and says so.
        """
        definition = self._templates.lookup(archived)
        if definition is None:
            return None if row.get("identity_hash") else False
        return rehash_document_row(
            row,
            definition["identity_fields"],
            definition["fields"],
            definition["referent_paths"],
        )


def rehash_document_row(
    row: dict[str, Any],
    identity_fields: list[str],
    template_fields: list[dict[str, Any]],
    referent_paths: list[str] | None = None,
) -> bool | None:
    """Recompute a rewritten document row's identity hash in place; True if
    it changed, None if its identity values or resolutions cannot be read
    (it keeps its hash, and its caller counts it unrehashable).

    The feed is the one a write produces: the identity values, with a term-
    or reference-typed identity field contributing the canonical id its
    stored resolution names (term_references / references). A restore that
    rewrote those resolutions must recompute, or the row carries a hash that
    no longer describes its own feed and the next write of that identity
    lands somewhere else.
    """
    from wip_auth.document_identity import (
        build_referent_resolutions,
        compute_hash,
        extract_identity_values,
        referent_identity_fields,
        substitute_resolved_referents,
    )

    if not identity_fields:
        return False
    try:
        values = extract_identity_values(row.get("data") or {}, identity_fields)
        values = substitute_resolved_referents(
            values,
            referent_identity_fields(identity_fields, template_fields, referent_paths),
            build_referent_resolutions(
                row.get("term_references"), row.get("references")
            ),
        )
    except ValueError as exc:
        logger.warning(
            "Document %s cannot be rehashed (%s); leaving its hash "
            "as archived", row.get("document_id"), exc,
        )
        return None
    recomputed = compute_hash(values)
    if recomputed != row.get("identity_hash"):
        row["identity_hash"] = recomputed
        return True
    return False


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
            template_definitions=s.template_definitions,
            document_chains=s.document_chains,
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
            # _stage, _rewrite_type and _finish are synchronous passes over
            # every row of the type (all documents, for that type). On a
            # worker thread the service keeps answering requests meanwhile;
            # the awaits keep them strictly one at a time, so the shared plans
            # and id map are never touched by two threads at once.
            groups, keys = await asyncio.to_thread(
                planners[s.source]._stage,
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
            await asyncio.to_thread(
                planners[s.source]._rewrite_type,
                plans[s.source], entity_type, groups, new_ids, s.target,
            )

    for s in sources:
        await asyncio.to_thread(
            planners[s.source]._finish, plans[s.source], s.entities_by_type, s.target
        )
    return plans


def check_collisions_before_writing(sources: list[RemapSource]) -> None:
    """Refuse an N:1 fresh restore's key collisions before anything is written.

    plan_multi checks each type's keys as it reaches the type, after the
    earlier types are provisioned in the Registry and the targets exist, so a
    collision found at documents left a created, half-reserved target that a
    retry then refused as not empty. This runs the same check for every type
    first, writing nothing.

    A type's keys embed the NEW ids of earlier types (a term's terminology),
    which do not exist yet. Each old id stands in for its new id instead,
    tagged so it cannot equal a real one: a fresh restore mints a distinct new
    id for every old id, so two keys are equal under these stand-ins exactly
    when they would be equal under the minted ids. Only the keys are built —
    no rewrite, no provisioning — and only targets that several sources fold
    into can collide, so a 1:1 restore skips it.
    """
    per_target: dict[str, set[str]] = {}
    for s in sources:
        per_target.setdefault(s.target, set()).add(s.source)
    if all(len(srcs) < 2 for srcs in per_target.values()):
        return
    stand_ins: dict[str, dict[str, str]] = {t: {} for t in REMAP_ENTITY_ORDER}
    for entity_type in REMAP_ENTITY_ORDER:
        id_field = ID_FIELDS[entity_type]
        staged: list[tuple[RemapSource, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]] = []
        for s in sources:
            entities = s.entities_by_type.get(entity_type) or []
            if not entities:
                continue
            groups: dict[str, list[dict[str, Any]]] = {}
            for entity in entities:
                groups.setdefault(str(entity.get(id_field)), []).append(entity)
            keys = [
                composite_key_for(entity_type, rows[0], s.target, stand_ins)
                for rows in groups.values()
            ]
            staged.append((s, groups, keys))
        _check_target_collisions(entity_type, staged)
        for _s, groups, _keys in staged:
            for old_id in groups:
                stand_ins[entity_type][old_id] = f"\x00planned:{entity_type}:{old_id}"


# Per-type projection of a staged Registry key down to the fields the target
# STORE is actually unique on. The staged key's job is Registry registration,
# so its shape must keep matching each service's registration shape — but the
# collision check must compare at store-uniqueness granularity, which for
# terminologies is (ns, value): def-store's ns_value_unique_idx, with label
# being mutable display metadata. Comparing the full {ns, value, label} key
# lets a same-valued, differently-labeled pair through the plan-time refusal,
# and the apply then dies mid-write on the Mongo unique index with a
# half-restored target. Types absent here are unique on their full key.
_UNIQUENESS_FIELDS: dict[str, tuple[str, ...]] = {
    "terminologies": ("ns", "value"),
}


def _check_target_collisions(
    entity_type: str,
    staged: list[tuple[RemapSource, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]],
) -> None:
    """Refuse duplicate non-empty uniqueness keys within one target namespace."""
    import json as _json

    unique_fields = _UNIQUENESS_FIELDS.get(entity_type)
    claimed: dict[tuple[str, str], list[str]] = {}
    for s, _groups, keys in staged:
        for key in keys:
            if not key:
                continue  # identity-less document — no dedup key to collide on
            if unique_fields is not None:
                key = {f: key.get(f) for f in unique_fields}
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
