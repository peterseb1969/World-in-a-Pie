"""Direct MongoDB backup/restore engine (CASE-23 redesign).

Replaces the toolkit-based HTTP-fan-out engine with direct motor cursor
reads for backup and bulk inserts for restore. Runs inside document-store,
which shares the MongoDB instance with all other services.

The engine emits :class:`~wip_archive.models.ProgressEvent` via a callback,
making it compatible with the existing ``start_async_job`` / SSE machinery
in :mod:`backup_service`.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import functools
import hashlib
import logging
import os
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TypeVar, cast

from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.models import (
    EntityCounts,
    Manifest,
    NamespaceConfig,
    NamespaceEntry,
    ProgressEvent,
)
from wip_archive.referents import ReferentCollector
from wip_archive.remap import IDRemapper

from wip_auth import REGISTRY_KEY, service_api_key
from wip_auth.build_info import build_metadata
from wip_auth.composite_key import compute_composite_key_hash
from wip_auth.template_identity import TemplateIdentityRow, effective_identity_fields

from .archived_status import retire_archived_row
from .document_chains import (
    UnsortedArchive,
    compact_heads,
    head_of,
    iter_chains,
    transformed_chains,
)
from .file_storage_client import (
    FileStorageClient,
    FileStorageError,
    FileStorageUnavailableError,
)
from .merge_definitions import (
    DEFINITION_TYPES,
    DefinitionsPlan,
    DefinitionsPlanner,
    TemplateAlignment,
)
from .merge_plan import MERGE_ENTITY_SPECS, Clash, EntityPlan, MergePlanner
from .referent_resolution import resolve_referents
from .remap_restore import (
    REMAP_ENTITY_ORDER,
    RemapPlan,
    RemapSource,
    TemplateDefinitions,
    check_collisions_before_writing,
    plan_multi,
    rehash_document_row,
    remap_file,
    template_definition,
)
from .reporting_client import ReportingSyncClient
from .template_store_client import get_template_store_client

logger = logging.getLogger("document_store.backup_engine")

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# Collection map: (archive entity type) → (database env var, database default, collection name)
#
# Archive entity type and MongoDB collection name match: "term_relations".
# ---------------------------------------------------------------------------

_DB_REGISTRY = os.getenv("REGISTRY_DATABASE_NAME", "wip_registry")
_DB_DEF_STORE = os.getenv("DEF_STORE_DATABASE_NAME", "wip_def_store")
_DB_TEMPLATE_STORE = os.getenv("TEMPLATE_STORE_DATABASE_NAME", "wip_template_store")
_DB_DOCUMENT_STORE = os.getenv("DOCUMENT_STORE_DATABASE_NAME", "wip_document_store")

# Composite-key claims are the Registry's uniqueness gate: one row per
# (namespace, entity_type, composite_key_hash) naming the entry that owns it.
# They are NOT backed up — every claim is derivable from the registry entries
# themselves — but they MUST be recreated after a restore. Without them a
# restored namespace has entries whose keys nothing claims, so the next write
# reusing one of those keys passes the gate and mints a duplicate identity.
CLAIMS_COLLECTION: tuple[str, str] = (_DB_REGISTRY, "composite_key_claims")

# One row per restore into a namespace (models/restore_provenance.py). Not an
# archive entity type: the rows travel inside the manifest's namespace entry,
# because they describe the namespace's history, not its content.
PROVENANCE_COLLECTION: tuple[str, str] = (_DB_DOCUMENT_STORE, "restore_provenance")


def _derived_chain(raw: Any) -> list[dict[str, Any]]:
    """The manifest's ``derived_from`` as a list of hops, oldest first.

    Producers write a list; a single object is accepted as a one-hop chain.
    Anything else — absent, or a shape no producer writes — is no chain.
    """
    if isinstance(raw, list):
        return [hop for hop in raw if isinstance(hop, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _file_sha256(path: Any) -> str | None:
    """SHA-256 of the whole archive file, or None when there is no file."""
    if not isinstance(path, (str, Path)):
        return None
    file = Path(path)
    if not file.is_file():
        return None
    digest = hashlib.sha256()
    with file.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

# (archive_entity_type) → (database_name, collection_name)
COLLECTION_MAP: dict[str, tuple[str, str]] = {
    "terminologies": (_DB_DEF_STORE, "terminologies"),
    "terms": (_DB_DEF_STORE, "terms"),
    "term_relations": (_DB_DEF_STORE, "term_relations"),
    "templates": (_DB_TEMPLATE_STORE, "templates"),
    "documents": (_DB_DOCUMENT_STORE, "documents"),
    "files": (_DB_DOCUMENT_STORE, "files"),
    "registry_entries": (_DB_REGISTRY, "registry_entries"),
}

# Backup reads these entity types in this order.
BACKUP_ENTITY_ORDER = [
    "terminologies",
    "terms",
    "term_relations",
    "templates",
    "documents",
    "files",
    "registry_entries",
]

# Every entity stream is exported in natural-key order, so an archive
# member's bytes are a pure function of corpus content — not of whichever
# index the query planner happened to walk. Unordered exports produced row
# permutations across otherwise-identical backups, which broke byte-level
# parity checks and cost up to ~32% archive size: near-identical versions of
# the same document only deflate well when they sit inside the same 32 KB
# compression window. allow_disk_use on the cursor covers namespaces where
# the sort cannot ride an index.
EXPORT_SORT_ORDER: dict[str, list[tuple[str, int]]] = {
    "terminologies": [("terminology_id", 1)],
    "terms": [("terminology_id", 1), ("value", 1)],
    "term_relations": [("source_term_id", 1), ("relation_type", 1), ("target_term_id", 1)],
    "templates": [("template_id", 1), ("version", 1)],
    "documents": [("document_id", 1), ("version", 1)],
    "files": [("file_id", 1)],
    "registry_entries": [("entry_id", 1)],
}

# What a fresh restore's Registry key for a document needs
# (`composite_key_for("documents", ...)`): the planner keeps one such row per
# document and streams the rest (CASE-959).
_DOCUMENT_KEY_FIELDS = ("document_id", "template_id", "identity_hash", "version")

# Ids per $in when reading the out-of-archive referents' Registry entries.
_REFERENT_LOOKUP_BATCH = 1000
# Ids per $in in the merge's file reference recount.
_RECOUNT_BATCH = 500

# Unresolved referents named individually on a restore job; the rest are
# counted in one more warning.
_REFERENT_WARNING_CAP = 20
# Referencing (document, field) pairs kept per unresolved referent.
_REFERENCE_SAMPLE_CAP = 20
# Files named in one job warning about files without their bytes; the rest
# are counted. A namespace-wide problem is one warning, not one per file.
_FILE_WARNING_SAMPLE = 20
_EDGE_ENDPOINT_FIELDS = ("source_ref", "target_ref")


class _UnresolvedReferent(NamedTuple):
    """A referent the target install could not identify."""

    entity_type: str
    description: str


def _unresolved_hits(
    row: dict[str, Any], unresolved: dict[str, _UnresolvedReferent]
) -> Iterator[tuple[str, str]]:
    """(field_path, referent id) for every reference of one archived document
    row that points at an unresolved referent."""
    for ref in row.get("references") or []:
        resolved = ref.get("resolved") or {}
        for key in ("document_id", "term_id", "template_id", "terminology_id"):
            target = resolved.get(key)
            if target and target in unresolved:
                yield str(ref.get("field_path") or ""), str(target)
    for kind, key in (("term_references", "term_id"), ("file_references", "file_id")):
        for ref in row.get(kind) or []:
            target = ref.get(key)
            if target and target in unresolved:
                yield str(ref.get("field_path") or ""), str(target)


def _drop_merge_rows(
    archive_entities: dict[str, list[dict[str, Any]]],
    doc_groups: dict[str, list[dict[str, Any]]] | None,
    ids: set[str],
) -> dict[str, list[dict[str, Any]]] | None:
    """Leave the given documents out of a merge: their rows and registry
    entries are removed from ``archive_entities`` in place, and the held
    document groups returned without them."""
    for entity_type in ("documents", "registry_entries"):
        if entity_type in archive_entities:
            archive_entities[entity_type] = [
                row for row in archive_entities[entity_type]
                if not _dropped_row(entity_type, row, ids)
            ]
    if doc_groups is not None:
        doc_groups = {
            doc_id: rows for doc_id, rows in doc_groups.items()
            if doc_id not in ids
        }
    return doc_groups


# The entity kinds a merge's documents can point at, with each kind's id field.
_MERGE_REFERENCE_ID_FIELD = {
    "templates": "template_id", "terms": "term_id", "terminologies": "terminology_id",
    "documents": "document_id", "files": "file_id",
}
_MERGE_REFERENCE_NOUN = {
    "templates": "template", "terms": "term", "terminologies": "terminology",
    "documents": "document", "files": "file",
}
# The kinds a reference field's resolved half names (a file field is in
# file_references; a term field also writes term_references, both count once).
_RESOLVED_KINDS = (
    ("document_id", "documents"), ("template_id", "templates"),
    ("term_id", "terms"), ("terminology_id", "terminologies"),
)


def _collect_resolved_ids(resolved: dict[str, Any], wanted: dict[str, set[str]]) -> None:
    """Add every id a reference's resolved half names to `wanted`, by kind."""
    for field_name, kind in _RESOLVED_KINDS:
        if resolved.get(field_name):
            wanted[kind].add(resolved[field_name])


def _merge_arriving(definitions: Any, built: dict[str, Any]) -> dict[str, set[str]]:
    """What a merge is about to bring with it — a reference is satisfied by
    the target OR by something arriving alongside."""
    empty_documents = EntityPlan(entity_type="documents")
    empty_files = EntityPlan(entity_type="files")
    arriving = {
        kind: {r[_MERGE_REFERENCE_ID_FIELD[kind]] for r in definitions.to_add[kind]
               if r.get(_MERGE_REFERENCE_ID_FIELD[kind])}
        for kind in ("templates", "terms", "terminologies")
    }
    arriving["documents"] = {
        d["document_id"] for d in built.get("documents", empty_documents).to_insert
        if d.get("document_id")
    }
    arriving["files"] = {
        f["file_id"] for f in built.get("files", empty_files).to_insert if f.get("file_id")
    }
    return arriving


def _dropped_row(entity_type: str, row: dict[str, Any], dropped: set[str]) -> bool:
    """Whether this archived row belongs to a dropped edge: one of its
    document versions, or its registry entry."""
    if entity_type == "documents":
        return row.get("document_id") in dropped
    if entity_type == "registry_entries":
        return row.get("entry_id") in dropped
    return False


def _drop_from_remap_source(source: RemapSource, dropped: set[str]) -> None:
    """Take dropped edges out of a fresh restore's plan inputs: their key
    rows (or rows), and the chains streamed when documents are written."""
    rows = source.entities_by_type.get("documents")
    if rows is not None:
        source.entities_by_type["documents"] = [
            row for row in rows if row.get("document_id") not in dropped
        ]
    chains = source.document_chains
    if chains is not None:
        def kept() -> Iterator[list[dict[str, Any]]]:
            for chain in chains():
                if chain[0].get("document_id") not in dropped:
                    yield chain
        source.document_chains = kept


def _referents_for(referents: dict[str, Any], source: str) -> dict[str, Any]:
    """The referent outcome a restore records for one archived namespace:
    the run's counts, and the references and dropped edges of that
    namespace's documents."""
    out = {k: v for k, v in referents.items()
           if k not in ("unresolved_references", "dropped_edges")}
    if "unresolved_references" in referents:
        out["unresolved_references"] = [
            r for r in referents["unresolved_references"] if r["namespace"] == source
        ]
    if "dropped_edges" in referents:
        out["dropped_edges"] = [
            e for e in referents["dropped_edges"] if e["namespace"] == source
        ]
    return out


def _scan_unresolved_references(
    reader: ArchiveReader,
    sources: list[str],
    unresolved: dict[str, _UnresolvedReferent],
    edge_templates: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """One pass over the archived documents of ``sources``: who references
    each unresolved referent, and which edges to drop.

    An edge is dropped as a whole document, every version: its versions
    share the identity its endpoints form, and half an edge history would be
    a fork nobody asked for. ``edge_templates`` is empty when edges are kept.
    References from a dropped edge are reported as the drop, not again as
    unresolved references.
    """
    # (namespace, referent) -> {document_id: field paths}
    referrers: dict[tuple[str, str], dict[str, set[str]]] = {}
    dropped: dict[str, dict[str, Any]] = {}
    for src in sources:
        for row in reader.read_entities("documents", namespace=src):
            hits = list(_unresolved_hits(row, unresolved))
            if not hits:
                continue
            doc_id = str(row.get("document_id") or "")
            endpoints = _unresolved_document_endpoints(hits, unresolved)
            if endpoints and row.get("template_id") in edge_templates:
                _record_dropped_edge(dropped, src, doc_id, row, endpoints)
                continue
            for field_path, target in hits:
                referrers.setdefault((src, target), {}).setdefault(doc_id, set()).add(field_path)
    return _unresolved_reference_report(referrers, dropped, unresolved), dropped


def _unresolved_document_endpoints(
    hits: list[tuple[str, str]], unresolved: dict[str, _UnresolvedReferent],
) -> list[str]:
    """The unresolved referents among a row's hits that are edge endpoints
    pointing at a document — the only kind of hit that drops an edge."""
    return [
        target for field_path, target in hits
        if field_path in _EDGE_ENDPOINT_FIELDS
        and unresolved[target].entity_type == "documents"
    ]


def _record_dropped_edge(
    dropped: dict[str, dict[str, Any]],
    src: str,
    doc_id: str,
    row: dict[str, Any],
    endpoints: list[str],
) -> None:
    """Record one version of an edge to drop: the edge entry (created by its
    first such version) takes each endpoint from the first version that
    names it, and gathers every unresolved endpoint any version has."""
    entry = dropped.setdefault(doc_id, {
        "namespace": src,
        "document_id": doc_id,
        "template_id": row.get("template_id"),
        "source_ref": None,
        "target_ref": None,
        "unresolved_endpoints": [],
    })
    for ref in row.get("references") or []:
        field_path = ref.get("field_path")
        if field_path in _EDGE_ENDPOINT_FIELDS and entry[field_path] is None:
            entry[field_path] = (ref.get("resolved") or {}).get("document_id")
    entry["unresolved_endpoints"] = sorted(set(entry["unresolved_endpoints"]) | set(endpoints))


def _unresolved_reference_report(
    referrers: dict[tuple[str, str], dict[str, set[str]]],
    dropped: dict[str, dict[str, Any]],
    unresolved: dict[str, _UnresolvedReferent],
) -> list[dict[str, Any]]:
    """One entry per (namespace, referent) still referenced once the dropped
    edges are left out: how many documents, and sorted, capped samples."""
    references: list[dict[str, Any]] = []
    for (src, target), by_document in sorted(referrers.items()):
        by_document = {d: f for d, f in by_document.items() if d not in dropped}
        if not by_document:
            continue
        samples = [
            {"document_id": doc_id, "field_path": field_path}
            for doc_id in sorted(by_document)
            for field_path in sorted(by_document[doc_id])
        ][:_REFERENCE_SAMPLE_CAP]
        references.append({
            "referent": target,
            "entity_type": unresolved[target].entity_type,
            "description": unresolved[target].description,
            "namespace": src,
            "documents": len(by_document),
            "samples": samples,
        })
    return references


@dataclass
class _OverwriteTally:
    """What a merge's clash overwrite did, for its summary."""

    appended: int = 0
    replaced: int = 0
    kept: int = 0  # the target's copy is newer
    ties: int = 0  # both copies updated at the same time
    identical: int = 0
    undecidable: int = 0
    retired: int = 0
    # A versioned:false row written on the target between the merge's read
    # and its replace: the target's copy is kept.
    changed_meanwhile: int = 0


def _clash_heads(
    clashes: list[Clash], doc_groups: dict[str, list[dict[str, Any]]],
) -> Iterator[tuple[Clash, dict[str, Any]]]:
    """Each clash with the archive's head of its document, from the
    in-memory version chains (the clash's own entity when it has none)."""
    for clash in clashes:
        yield clash, head_of(doc_groups.get(clash.entity.get("document_id", ""), [clash.entity]))


def _is_unversioned(versioned: dict[str, bool], clash: Clash) -> bool:
    """Whether the clash's template is versioned:false (unknown counts as
    versioned)."""
    return versioned.get(clash.entity.get("template_id") or "", True) is False


async def _retire_superseded(
    collection: Any, namespace: str, rows: list[dict[str, Any]], older_versions: str,
) -> int:
    """Under ``older_versions='retire'``, soft-delete each row's document's
    active versions below the row's own; returns how many were retired."""
    if older_versions != "retire":
        return 0
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        result = await collection.update_many(
            {
                "namespace": namespace,
                "document_id": row["document_id"],
                "version": {"$lt": row["version"]},
                "status": "active",
            },
            {"$set": {
                "status": "inactive",
                "updated_at": now,
                "updated_by": "restore-merge",
            }},
        )
        count += result.modified_count
    return count


class _StreamedDocuments:
    """A merge's documents, read from the archive chain by chain.

    Planning ran on compact heads; this carries the plan's per-document
    decisions (insert the chain, or take the archive's head over a clash) and
    streams the chains again for each step that needs their content — the
    reference check and the writes — so no step holds more than one chain.
    Every chain passes through ``transform`` (the namespace and reference
    rewrite) exactly as the in-memory path's chains do.
    """

    def __init__(
        self,
        reader: ArchiveReader,
        source: str,
        transform: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        plan: EntityPlan,
        *,
        overwrite: bool,
    ) -> None:
        self._reader = reader
        self._source = source
        self._transform = transform
        self._insert_ids = {str(h.get("document_id", "")) for h in plan.to_insert}
        # Keyed by the ARCHIVE's document_id (the clash entity is the archive
        # head); the write adopts the target's id from clash.targets.
        self._clashes: dict[str, Clash] = (
            {str(c.entity.get("document_id", "")): c for c in plan.differing_clashes}
            if overwrite else {}
        )

    def _chains(self) -> Iterator[list[dict[str, Any]]]:
        if not self._insert_ids and not self._clashes:
            return
        yield from transformed_chains(self._reader, self._source, self._transform)

    def referenced_ids(
        self, collect: Callable[[list[dict[str, Any]]], dict[str, set[str]]]
    ) -> dict[str, set[str]]:
        """What the rows this merge writes point at: every row of an inserted
        chain, the head of an overwritten clash."""
        wanted: dict[str, set[str]] = {}
        for chain in self._chains():
            doc_id = str(chain[0].get("document_id", ""))
            if doc_id in self._insert_ids:
                rows = chain
            elif doc_id in self._clashes:
                rows = [head_of(chain)]
            else:
                continue
            for kind, ids in collect(rows).items():
                wanted.setdefault(kind, set()).update(ids)
        return wanted

    def inserted_chains(self) -> Iterator[list[dict[str, Any]]]:
        for chain in self._chains():
            if str(chain[0].get("document_id", "")) in self._insert_ids:
                yield chain

    def clash_sources(self) -> Iterator[tuple[Clash, dict[str, Any]]]:
        """(clash, the archive's latest row) for every overwritten clash."""
        if not self._clashes:
            return
        for chain in self._chains():
            clash = self._clashes.get(str(chain[0].get("document_id", "")))
            if clash is not None:
                yield clash, head_of(chain)


# Merge writes in the same order for the same reason a restore does: it is
# dependency order. Terms need their terminology, documents need their
# template, and registry entries come last so nothing claims an identity
# before the entity holding it exists.
MERGE_ENTITY_ORDER = list(BACKUP_ENTITY_ORDER)

# The order a merge PLANS its data in, which is not the order it writes.
# Files come before documents: a file's natural key (its checksum) needs no
# rewritten reference, and a document referencing a file the target holds under
# another id must be rewritten with that mapping before it is planned.
_MERGE_PLAN_ORDER = [
    "terminologies", "terms", "term_relations", "templates",
    "files", "documents", "registry_entries",
]
assert sorted(_MERGE_PLAN_ORDER) == sorted(MERGE_ENTITY_ORDER)

# The Registry's provision endpoint caps `count` per call (ProvisionRequest
# bounds it at 1000), so a remap of a namespace-sized entity type asks in
# chunks. Found live: 1099 terms in one request came back 422.
PROVISION_BATCH = 1000


class BackupEngineError(Exception):
    """Raised when the backup engine encounters a fatal error."""


def _named_files(file_ids: list[str]) -> str:
    """The first _FILE_WARNING_SAMPLE file ids, and how many more there are."""
    shown = ", ".join(file_ids[:_FILE_WARNING_SAMPLE])
    more = len(file_ids) - _FILE_WARNING_SAMPLE
    return shown + (f" and {more} more" if more > 0 else "")


def _file_shortfall_warnings(prefix: str, by_reason: dict[str, list[str]]) -> list[str]:
    """One warning per reason files are without their bytes, naming a
    sample of them: ``by_reason`` maps the reason's wording to the file ids."""
    return [
        f"{prefix}{len(ids)} file(s) {reason}: {_named_files(sorted(ids))}"
        for reason, ids in by_reason.items() if ids
    ]


def _names_foreign_namespace(key: Any, namespace: str) -> bool:
    """Whether a composite key names a namespace other than `namespace` — the
    Registry's key-scope rule: a key carrying `ns` must carry its entry's,
    unless a member starting with "@" makes it system-qualified."""
    if not isinstance(key, dict) or any(isinstance(k, str) and k.startswith("@") for k in key):
        return False
    return "ns" in key and key["ns"] != namespace


class RestoreEngineError(Exception):
    """Raised when the restore engine encounters a fatal error.

    ``details`` is a structured account of the failure for the job record
    (``result`` on a failed job), when the message alone cannot carry it —
    a merge refused for 62 definitions names only a few of them in its
    message, and the operator needs all of them to act.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details


# The incompatibilities a refused merge records on its job, at most. A
# namespace's definitions are vocabularies and schemas, so a refusal is
# normally a few dozen; the cap only keeps a pathological archive from
# writing an unbounded record.
MERGE_REFUSAL_INCOMPATIBILITY_CAP = 1000


class RestoreRequester(NamedTuple):
    """Who started a restore, as the namespace upsert presents them to the
    Registry.

    The Registry decides who may create a namespace and with which settings:
    a platform admin freely, a reservation's holder only as reserved, nobody
    else. It can decide that only when it sees the requester, so every
    namespace create or configure a restore makes goes out with the
    requester's own credential — never the service's key.

    - ``credential``: the headers that authenticated the requester, passed
      on as they arrived (an API key, or an OIDC login's bearer token, which
      the Registry validates itself); ``{}`` when auth is off. Held in
      memory for the run, never written to the job record. None when the
      requester's credential cannot be passed on (a login a gateway
      forwarded, whose request carries the gateway's key): such a restore
      cannot create or configure a namespace.
    - ``platform_admin``: whether the requester is in the platform admin
      groups — the restore then applies the archive's namespace settings.
    - ``actor``: who the requester is, recorded by the Registry as the
      namespace's creator or last updater.
    - ``service``: the engine acting as the service itself (tests, tooling
      inside the service); the only requester presented by the service's
      key. The API never builds one.
    """

    credential: dict[str, str] | None
    platform_admin: bool
    actor: str | None = None
    service: bool = False


# An engine run with no requester (tests, tooling inside the service) acts as
# the service itself. The API's restore runner always passes the requester.
SERVICE_REQUESTER = RestoreRequester(credential=None, platform_admin=True, service=True)

# Namespace settings a restore carries in its manifest. Only a platform admin
# changes them; for anyone else a differing value is reported, not applied.
_NAMESPACE_SETTINGS = (
    "isolation_mode", "allowed_external_refs", "inbound_references",
    "deletion_mode", "id_config",
)


# ---------------------------------------------------------------------------
# DirectBackupEngine
# ---------------------------------------------------------------------------


def _engine_tool_version() -> str:
    """What the manifest records as having written an engine archive: this
    service's version plus the image and build it runs, which are what an
    operator can match to a release ("dev" on an unstamped local build)."""
    from .. import __version__

    build = build_metadata(__version__)
    return f"document-store {build['version']} (image {build['image_tag']}, build {build['sha']})"


# A template version's coordinates, and a requirement on another template:
# a specific version, or (None) every version of it.
_TemplateKey = tuple[str, int]
_TemplateLink = tuple[str, int | None]

# The field slots that pin a nested template version.
_NESTED_PIN_SLOTS = (
    ("template_ref", "template_ref_version"),
    ("array_template_ref", "array_template_ref_version"),
)


def _template_index(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[int]], dict[tuple[str, str], str]]:
    """Each template's versions, and its template_id by (namespace, value)."""
    versions: dict[str, list[int]] = {}
    by_value: dict[tuple[str, str], str] = {}
    for r in rows:
        versions.setdefault(r["template_id"], []).append(r["version"])
        if r.get("value"):
            by_value[(r["namespace"], r["value"])] = r["template_id"]
    return versions, by_value


def _resolve_template_ref(
    ref: str, namespace: str,
    versions: dict[str, list[int]], by_value: dict[tuple[str, str], str],
) -> str | None:
    """The template_id a nested ref names among the archive's templates. An
    active template stores the template_id; a draft may still hold the
    submitted value or NS:VALUE. None for a template outside the archive."""
    if ref in versions:
        return ref
    ns, sep, value = ref.partition(":")
    if sep and (ns, value) in by_value:
        return by_value[(ns, value)]
    return by_value.get((namespace, ref))


def _version_requirements(
    row: dict[str, Any],
    versions: dict[str, list[int]], by_value: dict[tuple[str, str], str],
) -> list[_TemplateLink]:
    """What one template version needs of others: the version it extends,
    and each nested template version a field pins (version None: every
    version of it). A ref outside the archive is skipped: it cannot be
    carried."""
    links: list[_TemplateLink] = []
    if row.get("extends") and row.get("extends_version") is not None:
        links.append((row["extends"], row["extends_version"]))
    for field in row.get("fields") or []:
        for ref_key, version_key in _NESTED_PIN_SLOTS:
            ref = field.get(ref_key)
            tid = _resolve_template_ref(ref, row["namespace"], versions, by_value) if ref else None
            if tid is not None:
                links.append((tid, field.get(version_key)))
    return links


def _close_over_requirements(
    needed: set[_TemplateKey],
    requires: dict[_TemplateKey, list[_TemplateLink]],
    versions: dict[str, list[int]],
) -> None:
    """Add to ``needed`` every existing version a needed version requires,
    transitively."""
    frontier = list(needed)
    while frontier:
        for tid, version in requires.get(frontier.pop(), []):
            for v in versions.get(tid, []) if version is None else [version]:
                target = (tid, v)
                if target not in needed and v in versions.get(tid, []):
                    needed.add(target)
                    frontier.append(target)


class DirectBackupEngine:
    """Backup a namespace via direct MongoDB cursor reads.

    Reads from all service databases (registry, def-store, template-store,
    document-store) using the shared motor client. Writes to a ZIP archive
    via :class:`~wip_archive.archive.ArchiveWriter`.
    """

    def __init__(
        self,
        mongo_client: AsyncIOMotorClient,
        storage_client: FileStorageClient | None,
        progress: Callable[[ProgressEvent], None],
    ) -> None:
        self._mongo = mongo_client
        self._storage = storage_client
        self._progress = progress

    async def run_backup(
        self,
        namespaces: str | list[str],
        archive_path: Path,
        *,
        include_files: bool = False,
        skip_documents: bool = False,
        include_inactive_templates: bool = False,
        tmp_dir: Path | None = None,
    ) -> None:
        """Run the full backup pipeline over one or more namespaces (CASE-542).

        Inactive template versions nothing in the archive needs are left out
        unless ``include_inactive_templates`` (see
        ``_template_versions_to_leave_out``); every other entity is carried in
        every status.

        Each namespace is read independently and written to its own
        ``namespaces/<prefix>/`` subtree of the v3 archive; the manifest carries
        a per-namespace entry plus the aggregate counts. A single-element list
        (or a bare string, accepted for ergonomics) produces a 1-namespace v3
        archive.
        """
        if isinstance(namespaces, str):
            namespaces = [namespaces]
        if not namespaces:
            raise BackupEngineError("run_backup requires at least one namespace")

        self._emit(
            "start",
            f"Starting backup of {len(namespaces)} namespace(s): {', '.join(namespaces)}",
            percent=0,
        )

        self._warn_if_files_unavailable(include_files)

        left_out: dict[str, list[tuple[str, int]]] = {}
        if not include_inactive_templates:
            left_out = await self._announce_left_out_templates(namespaces, skip_documents)

        # Pre-count entities across all namespaces for percent calculation
        per_ns_counts: dict[str, dict[str, int]] = {}
        for ns in namespaces:
            per_ns_counts[ns] = await self._pre_count(ns, skip_documents, left_out.get(ns, []))
        total_entities = sum(sum(c.values()) for c in per_ns_counts.values())

        writer = ArchiveWriter(archive_path, tmp_dir=tmp_dir)
        referents = ReferentCollector()
        processed = 0
        without_bytes: dict[str, list[str]] = {}

        try:
            for ns in namespaces:
                processed = await self._backup_namespace(
                    writer, referents, ns, archive_path, left_out.get(ns, []),
                    per_ns_counts[ns], processed, total_entities,
                    include_files=include_files, skip_documents=skip_documents,
                    without_bytes=without_bytes,
                )

            await self._warn_if_changed_while_read(
                namespaces, per_ns_counts, writer, skip_documents, left_out,
            )

            self._emit(
                "phase_referents",
                "Recording referenced entities the archive does not carry",
                percent=90,
            )
            referent_rows = await self._read_referents(referents.external_ids())
            writer.write_referents_file(referent_rows)

            manifest = await self._backup_manifest(
                writer, namespaces,
                include_files=bool(include_files and self._storage),
                referent_count=len(referent_rows),
                without_bytes=without_bytes,
            )

            self._emit("phase_finalize", "Writing archive", percent=95)
            # Offloaded to a thread: this one call closes every staged temp
            # file and DEFLATE-compresses the whole archive — entity JSONL and
            # blobs alike — with no await point inside it, so its duration
            # scales with total archive bytes. Left on the event loop it
            # starves everything else in the process, including /health: an
            # instance-wide backup produced an 853 MB archive (~1.5 GB of
            # input) and the service failed its readiness probes for ~2.5
            # minutes, dropping out of the ingress while every caller got 503.
            # Safe to offload wholesale — writer.write touches no async state,
            # only temp files the engine has already finished writing. Same
            # reasoning as the executor-wrapped read in archive_store's
            # download path.
            await asyncio.to_thread(writer.write, manifest)

            self._emit("complete", "Backup complete", percent=100)

        except Exception:
            # ArchiveWriter cleans up temp files in __del__, but let's be explicit
            with contextlib.suppress(Exception):
                writer._cleanup()
            raise

    async def _warn_if_changed_while_read(
        self,
        namespaces: list[str],
        before: dict[str, dict[str, int]],
        writer: ArchiveWriter,
        skip_documents: bool,
        left_out: dict[str, list[tuple[str, int]]],
    ) -> None:
        """Warn when a namespace was written to while the backup read it.

        A backup is not a point-in-time copy: each entity type is read by its
        own cursor, one after another, and MongoDB without a replica set has
        no snapshot read to make them one. A write in between can leave the
        archive with a document whose term or template it does not carry, or
        a Registry entry whose entity it does not. Creating or hard-deleting
        an entity, the writes that do that, change a count, so the counts
        taken before reading, the rows read, and the counts taken after are
        compared. Matching counts do not prove the archive consistent (an
        update, or a create and a delete of the same type, leaves them
        equal); a mismatch proves something changed.
        """
        changed: list[str] = []
        for ns in namespaces:
            after = await self._pre_count(ns, skip_documents, left_out.get(ns, []))
            for entity_type in BACKUP_ENTITY_ORDER:
                if skip_documents and entity_type == "documents":
                    continue
                pre, post = before[ns].get(entity_type, 0), after.get(entity_type, 0)
                read = writer.entity_count(entity_type, namespace=ns)
                if not pre == read == post:
                    changed.append(
                        f"{ns} {entity_type}: {pre} before, {read} read, {post} after"
                    )
        if changed:
            self._emit(
                "warning",
                "Entities were created or deleted while the backup read them ("
                + "; ".join(changed)
                + "), so the archive may hold references to entities it does "
                "not carry. Back up again while nothing writes to the "
                "namespace(s).",
            )

    def _warn_if_files_unavailable(self, include_files: bool) -> None:
        """Files asked for with no storage to read them from: the archive
        carries file records only, and its manifest says so."""
        if include_files and not self._storage:
            self._emit(
                "warning",
                "File storage is not enabled, so the archive carries no file "
                "contents: file records only, and its manifest says so.",
            )

    async def _announce_left_out_templates(
        self, namespaces: list[str], skip_documents: bool,
    ) -> dict[str, list[tuple[str, int]]]:
        """The inactive template versions this backup leaves out, per
        namespace (see ``_template_versions_to_leave_out``), announced in a
        progress event when there are any."""
        left_out = await self._template_versions_to_leave_out(namespaces, skip_documents)
        dropped = sum(len(v) for v in left_out.values())
        if dropped:
            self._emit(
                "phase_templates",
                f"Leaving out {dropped} inactive template version(s) that no "
                "document or template in the archive needs "
                "(include_inactive_templates keeps them)",
                percent=0,
            )
        return left_out

    async def _backup_namespace(
        self,
        writer: ArchiveWriter,
        referents: ReferentCollector,
        ns: str,
        archive_path: Path,
        left_out: list[tuple[str, int]],
        counts: dict[str, int],
        processed: int,
        total_entities: int,
        *,
        include_files: bool,
        skip_documents: bool,
        without_bytes: dict[str, list[str]] | None = None,
    ) -> int:
        """Write one namespace's entities, in BACKUP_ENTITY_ORDER, and its file
        blobs when asked. Returns the running count of entities processed;
        the files the archive carries without their bytes go into
        ``without_bytes`` under the namespace."""
        for entity_type in BACKUP_ENTITY_ORDER:
            if skip_documents and entity_type == "documents":
                continue
            processed = await self._backup_entity_type(
                writer, referents, ns, entity_type, left_out,
                counts.get(entity_type, 0), processed, total_entities,
                skip_documents=skip_documents,
            )

        # Blobs (per namespace; file_ids are globally unique so the
        # archive's flat blobs/ dir never collides across namespaces)
        if include_files and self._storage:
            dropped = await self._backup_blobs(writer, ns, archive_path, processed, total_entities)
            if dropped and without_bytes is not None:
                without_bytes[ns] = dropped
        return processed

    async def _backup_entity_type(
        self,
        writer: ArchiveWriter,
        referents: ReferentCollector,
        ns: str,
        entity_type: str,
        left_out: list[tuple[str, int]],
        expected: int,
        processed: int,
        total_entities: int,
        *,
        skip_documents: bool = False,
    ) -> int:
        """Stream one entity type of one namespace into the archive, in its
        export sort order, reporting progress every 5000 rows. Returns the
        running count of entities processed."""
        db_name, coll_name = COLLECTION_MAP[entity_type]
        collection = self._mongo[db_name][coll_name]
        query = self._entity_query(ns, entity_type, left_out, skip_documents)

        phase_name = f"phase_{entity_type}"
        self._emit(
            phase_name,
            f"[{ns}] Reading {entity_type} ({expected} expected)",
            percent=self._pct(processed, total_entities),
        )

        count = 0
        cursor = collection.find(query, allow_disk_use=True).sort(EXPORT_SORT_ORDER[entity_type])
        async for doc in cursor:
            doc.pop("_id", None)
            writer.add_entity(entity_type, doc, namespace=ns)
            referents.add(entity_type, doc)
            count += 1
            processed += 1
            if count % 5000 == 0:
                self._emit(
                    phase_name,
                    f"[{ns}] {entity_type}: {count}/{expected}",
                    percent=self._pct(processed, total_entities),
                    current=count,
                    total=expected,
                )

        logger.info("Backed up %d %s for namespace %s", count, entity_type, ns)
        return processed

    async def _backup_manifest(
        self,
        writer: ArchiveWriter,
        namespaces: list[str],
        *,
        include_files: bool,
        referent_count: int,
        without_bytes: dict[str, list[str]] | None = None,
    ) -> Manifest:
        """The archive's manifest: one entry per namespace (its config,
        counts, provenance and the files it carries without their bytes)
        plus the aggregate counts."""
        ns_entries: list[NamespaceEntry] = []
        agg: dict[str, int] = {et: 0 for et in BACKUP_ENTITY_ORDER}
        for ns in namespaces:
            counts = EntityCounts(
                **{et: writer.entity_count(et, namespace=ns) for et in BACKUP_ENTITY_ORDER}
            )
            ns_entries.append(
                NamespaceEntry(
                    prefix=ns,
                    namespace_config=await self._read_namespace_config(ns),
                    counts=counts,
                    provenance=await self._read_provenance(ns),
                    files_without_bytes=sorted((without_bytes or {}).get(ns, [])) or None,
                )
            )
            for et in BACKUP_ENTITY_ORDER:
                agg[et] += writer.entity_count(et, namespace=ns)

        single = len(namespaces) == 1
        return Manifest(
            format_version="3.1",
            exported_at=datetime.now(UTC),
            source_host=socket.gethostname(),
            tool_version=_engine_tool_version(),
            namespaces=ns_entries,
            namespace=namespaces[0] if single else "",
            namespace_config=ns_entries[0].namespace_config if single else None,
            source_install={
                "schema_version": "1.4",
                "hash_version": 1,
            },
            include_files=include_files,
            # Every version of every document is carried: the backup has no
            # latest-only export (the API refuses latest_only).
            include_all_versions=True,
            counts=EntityCounts(**agg),
            referents=referent_count,
        )

    async def _pre_count(
        self, namespace: str, skip_documents: bool,
        left_out: list[tuple[str, int]] | None = None,
    ) -> dict[str, int]:
        """Count documents per entity type for progress reporting."""
        counts: dict[str, int] = {}
        for entity_type in BACKUP_ENTITY_ORDER:
            if skip_documents and entity_type == "documents":
                counts[entity_type] = 0
                continue
            db_name, coll_name = COLLECTION_MAP[entity_type]
            counts[entity_type] = await self._mongo[db_name][coll_name].count_documents(
                self._entity_query(namespace, entity_type, left_out or [], skip_documents)
            )
        return counts

    def _entity_query(
        self, namespace: str, entity_type: str, left_out: list[tuple[str, int]],
        skip_documents: bool = False,
    ) -> dict[str, Any]:
        """The namespace's query for one entity type: every entity, minus the
        template versions this backup leaves out, and minus the Registry
        entries of documents a ``skip_documents`` backup leaves out — restored
        without their documents, they would claim the documents' identity keys
        for entries with nothing behind them."""
        query = self._build_query(namespace)
        if entity_type == "templates" and left_out:
            query["$nor"] = [{"template_id": tid, "version": v} for tid, v in left_out]
        if entity_type == "registry_entries" and skip_documents:
            query["entity_type"] = {"$ne": "documents"}
        return query

    async def _template_versions_to_leave_out(
        self, namespaces: list[str], skip_documents: bool,
    ) -> dict[str, list[tuple[str, int]]]:
        """Per namespace, the inactive template versions nothing in the
        archive needs.

        A version is carried when it is not inactive (drafts are carried), when
        an exported document row is pinned to it (any status — an inactive
        document, or a superseded version row of an active one, restores with
        its schema too), or when a carried version needs it: extends it, or
        pins it as a nested template (a field's template_ref /
        template_ref_version, or array_template_ref /
        array_template_ref_version). Nested validation reads that exact
        version, so a parent carried without it restores with its nested
        objects unvalidated. A nested pin with no version (a draft's) keeps
        every version of its template. A template whose versions would all be left out
        keeps its highest one: other definitions name templates by id and the
        template's Registry entry travels, so the template itself never
        vanishes from the archive. A document whose pin has no version keeps
        every version of its template. Pins and parents are read across every
        namespace of the archive, since a document in one may pin a template
        in another.
        """
        t_db, t_coll = COLLECTION_MAP["templates"]
        rows = [
            r async for r in self._mongo[t_db][t_coll].find(
                {"namespace": {"$in": namespaces}},
                {"_id": 0, "namespace": 1, "template_id": 1, "version": 1, "value": 1,
                 "status": 1, "extends": 1, "extends_version": 1,
                 "fields.template_ref": 1, "fields.template_ref_version": 1,
                 "fields.array_template_ref": 1, "fields.array_template_ref_version": 1},
            )
        ]
        versions, by_value = _template_index(rows)
        requires = {
            (r["template_id"], r["version"]): links
            for r in rows
            if (links := _version_requirements(r, versions, by_value))
        }
        needed = {(r["template_id"], r["version"]) for r in rows if r.get("status") != "inactive"}
        if not skip_documents:
            needed |= await self._document_template_pins(namespaces, versions)

        _close_over_requirements(needed, requires, versions)
        for tid, vs in versions.items():
            if not any((tid, v) in needed for v in vs):
                needed.add((tid, max(vs)))
        _close_over_requirements(needed, requires, versions)

        left_out: dict[str, list[tuple[str, int]]] = {}
        for r in rows:
            key = (r["template_id"], r["version"])
            if key not in needed:
                left_out.setdefault(r["namespace"], []).append(key)
        return left_out

    async def _document_template_pins(
        self, namespaces: list[str], versions: dict[str, list[int]],
    ) -> set[tuple[str, int]]:
        """The template versions the archive's document rows (any status) pin.
        A pin with no version stands for every version of its template."""
        d_db, d_coll = COLLECTION_MAP["documents"]
        pinned: set[tuple[str, int]] = set()
        async for pin in self._mongo[d_db][d_coll].aggregate([
            {"$match": {"namespace": {"$in": namespaces}}},
            {"$group": {"_id": {"t": "$template_id", "v": "$template_version"}}},
        ]):
            tid, version = pin["_id"].get("t"), pin["_id"].get("v")
            if version is None:
                pinned.update((tid, v) for v in versions.get(tid, []))
            else:
                pinned.add((tid, version))
        return pinned

    def _build_query(self, namespace: str) -> dict[str, Any]:
        # Every entity, every status: live data references inactive and
        # archived entities (documents pin inactive template versions, edges
        # point at archived documents), so an archive missing them would be a
        # restore trap. The one exception — inactive template versions nothing
        # in the archive needs — is applied per entity type by _entity_query.
        # The retired include_inactive flag's status filter matched a status
        # no persisted entity ever carried.
        return {"namespace": namespace}

    async def _backup_blobs(
        self,
        writer: ArchiveWriter,
        namespace: str,
        archive_path: Path,
        processed: int,
        total_entities: int,
    ) -> list[str]:
        """Stream file blobs from the object store into the archive.

        The bytes are hashed as they stream and checked against the file's
        record. A blob the store could not serve, or whose bytes are not the
        file's, is dropped from the archive: an archive without the bytes
        says so, one with partial bytes would restore them as the file. The
        backup still completes with every other file in it, and the dropped
        files are named on the job (one warning per reason) and returned,
        for the manifest. An object store that cannot be reached at all, or
        a scratch disk the archive cannot be written to, fails the backup
        instead: completed, it would be an archive of file records without
        their files.
        """
        assert self._storage is not None
        self._emit(
            "phase_blobs",
            "Downloading file blobs",
            percent=self._pct(processed, total_entities),
        )
        # Read file entries from what was already written
        files_db = self._mongo[_DB_DOCUMENT_STORE]["files"]
        query = {"namespace": namespace, "status": {"$ne": "deleted"}}
        count = 0
        unreadable: list[str] = []
        mismatched: list[str] = []
        projection = {"file_id": 1, "storage_key": 1, "checksum": 1}
        async for file_doc in files_db.find(query, projection):
            file_id = file_doc.get("file_id") or str(file_doc.get("_id"))
            try:
                digest = await self._stage_blob(
                    writer, namespace, file_id, file_doc.get("storage_key", file_id),
                )
            except FileStorageError:
                logger.warning("Failed to download blob for file %s", file_id, exc_info=True)
                writer.discard_blob(file_id)
                unreadable.append(file_id)
                continue
            expected = file_doc.get("checksum")
            if expected and digest != expected:
                writer.discard_blob(file_id)
                mismatched.append(file_id)
                continue
            count += 1

        for warning in _file_shortfall_warnings(f"[{namespace}] ", {
            "could not be read from the object store; the backup carries no bytes "
            "for them": unreadable,
            "have bytes in the object store that do not match their checksum; the "
            "backup carries no bytes for them": mismatched,
        }):
            self._emit("warning", warning)
        logger.info("Backed up %d blobs for namespace %s", count, namespace)
        return unreadable + mismatched

    async def _stage_blob(
        self, writer: ArchiveWriter, namespace: str, file_id: str, storage_key: str,
    ) -> str:
        """Stream one file's bytes into the archive; their SHA-256, hex.

        A FileStorageError (the object is missing, or the store refused it)
        is the caller's to record. An unreachable store and a failed write to
        the archive's scratch space stop the backup, each named as what it is.
        """
        assert self._storage is not None
        digest = hashlib.sha256()
        try:
            with writer.open_blob(file_id) as fh:
                async for chunk in self._storage.download_stream(storage_key):
                    fh.write(chunk)
                    digest.update(chunk)
        except FileStorageUnavailableError as exc:
            writer.discard_blob(file_id)
            raise BackupEngineError(
                f"[{namespace}] the object store could not be reached while reading "
                f"file {file_id} ({exc}); the backup stops rather than complete "
                "without its files. Retry once the store is back."
            ) from exc
        except OSError as exc:
            writer.discard_blob(file_id)
            raise BackupEngineError(
                f"[{namespace}] file {file_id}'s bytes could not be written to the "
                f"archive's scratch space ({exc}); the backup is stopped"
            ) from exc
        return digest.hexdigest()

    async def _read_referents(self, entry_ids: list[str]) -> list[dict[str, Any]]:
        """The Registry entries of referenced entities the archive does not
        carry, in entry_id order (see wip_archive.referents).

        A candidate with no entry is dropped: either it was a value rather
        than an id, or its target is already gone on this install, and in
        neither case is there an identity to record.
        """
        db_name, coll_name = COLLECTION_MAP["registry_entries"]
        collection = self._mongo[db_name][coll_name]
        rows: list[dict[str, Any]] = []
        for start in range(0, len(entry_ids), _REFERENT_LOOKUP_BATCH):
            chunk = entry_ids[start : start + _REFERENT_LOOKUP_BATCH]
            cursor = collection.find(
                {"entry_id": {"$in": chunk}}, {"_id": 0}
            ).sort(EXPORT_SORT_ORDER["registry_entries"])
            rows.extend([row async for row in cursor])
        return rows

    async def _read_provenance(self, namespace: str) -> list[dict[str, Any]] | None:
        """The namespace's restore history, oldest first, for the manifest.

        None when there is none, so a namespace that was never restored into
        archives exactly as before.
        """
        db_name, coll_name = PROVENANCE_COLLECTION
        cursor = self._mongo[db_name][coll_name].find(
            {"namespace": namespace}, {"_id": 0}
        ).sort([("at", 1), ("_id", 1)])
        rows = [row async for row in cursor]
        return rows or None

    async def _read_namespace_config(self, namespace: str) -> NamespaceConfig | None:
        """Read namespace config from the Registry database."""
        ns_doc = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": namespace}
        )
        if ns_doc is None:
            return None
        if not ns_doc.get("namespace_id"):
            # The Registry's startup gives every namespace its id; one without
            # cannot be archived, since its restore could not be told apart
            # from another namespace of the same name.
            raise RuntimeError(
                f"Namespace '{namespace}' has no namespace_id: restart the Registry "
                "(its startup gives every namespace one) and back up again"
            )
        return NamespaceConfig(
            prefix=ns_doc.get("prefix", namespace),
            namespace_id=ns_doc["namespace_id"],
            description=ns_doc.get("description", ""),
            isolation_mode=ns_doc.get("isolation_mode", "open"),
            id_config=ns_doc.get("id_config"),
            allowed_external_refs=ns_doc.get("allowed_external_refs"),
            deletion_mode=ns_doc.get("deletion_mode"),
            # Stored before the setting existed means closed, the default.
            inbound_references=ns_doc.get("inbound_references", "closed"),
        )

    def _emit(
        self,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        current: int | None = None,
        total: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._progress(
            ProgressEvent(
                phase=phase,
                message=message,
                percent=percent,
                current=current,
                total=total,
                details=details or {},
            )
        )

    @staticmethod
    def _pct(done: int, total: int) -> float:
        if total == 0:
            return 0.0
        # Entity reads map onto 0-90%. Blob uploads report the same figure
        # (they have no band of their own); recording referents emits 90,
        # finalize 95 and completion 100.
        return min(round(done / total * 90, 1), 90.0)


# ---------------------------------------------------------------------------
# DirectRestoreEngine
# ---------------------------------------------------------------------------


_CONTENT_FIELDS = ("data", "term_references", "references", "file_references")


def _same_document_content(archive_row: dict[str, Any], target_row: dict[str, Any]) -> bool:
    """The parts of a document that version, compared as document writes
    compare them (document_service._document_has_changed): data, the three
    resolved-reference lists and metadata.custom — and its status, since a
    copy that is deleted on one side and not on the other is a deletion (or
    a revival) for the merge's policy to carry, not an identical copy.
    Equal means a write would add a version that changes nothing."""
    import json

    def norm(value: Any) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    if (archive_row.get("status") or "active") != (target_row.get("status") or "active"):
        return False

    for name in _CONTENT_FIELDS:
        if norm(archive_row.get(name) or ([] if name != "data" else {})) != norm(
            target_row.get(name) or ([] if name != "data" else {})
        ):
            return False
    custom_a = (archive_row.get("metadata") or {}).get("custom") or {}
    custom_t = (target_row.get("metadata") or {}).get("custom") or {}
    return norm(custom_a) == norm(custom_t)


def _recounted_file(row: dict[str, Any], want: int) -> dict[str, Any]:
    """The fields to set on a file row whose recounted reference count is
    ``want``: the count, and the status that follows it as
    update_reference_count keeps it (an inactive file stays inactive)."""
    update: dict[str, Any] = {}
    if row.get("reference_count") != want:
        update["reference_count"] = want
    if row.get("status") != "inactive":
        status = "active" if want > 0 else "orphan"
        if row.get("status") != status:
            update["status"] = status
    return update


def _activation_failure_cause(failed: list[dict[str, Any]]) -> str:
    """What stopped an activation batch, by the Registry's error codes.

    Only a key collision (``already_exists``) is a duplicate identity; any
    other code is the Registry failing to activate or to confirm, which a
    retry may get past.
    """
    codes: dict[str, int] = {}
    for r in failed:
        code = r.get("error_code") or r.get("status") or "unknown"
        codes[code] = codes.get(code, 0) + 1
    by_code = ", ".join(f"{n} {code}" for code, n in sorted(codes.items()))
    duplicate = codes.get("already_exists", 0)
    note = (
        f"; {duplicate} could not claim their composite key (a genuine duplicate)"
        if duplicate else ""
    )
    return (
        f"{len(failed)} identit(ies) were not activated ({by_code}){note}; "
        f"first: {failed[:5]}"
    )


def _rehydrate_timestamps(row: dict[str, Any]) -> None:
    """Convert platform ``*_at`` ISO strings back to datetime, in place.

    Archive rows are JSON, so timestamp fields that were BSON dates when
    exported come back as ISO strings. Platform timestamp fields follow the
    ``*_at`` naming convention: at the row's top level, and on each of a
    registry entry's ``synonyms`` (their ``created_at``). Other nested
    payloads (``data``, ``metadata``) are caller content stored as
    submitted, and are deliberately left untouched. A string that does not
    parse is left as-is — a restore must not fail over one odd value that
    was already a string in the archive.
    """
    _rehydrate_top_level(row)
    synonyms = row.get("synonyms")
    if isinstance(synonyms, list):
        for synonym in synonyms:
            if isinstance(synonym, dict):
                _rehydrate_top_level(synonym)


def _release_outcome(results: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """From /entries/deactivate's per-item results: how many were released,
    and the items that were neither released nor already released."""
    released = sum(1 for r in results if r.get("status") == "deactivated")
    failed = [r for r in results if r.get("status") not in ("deactivated", "unchanged")]
    return released, failed


def _sample_ids(results: list[dict[str, Any]]) -> str:
    """The first five of /entries/deactivate's per-item results, for a message."""
    return ", ".join(
        f"{r.get('registry_id')} ({r.get('status')}: {r.get('error') or ''})"
        for r in results[:5]
    )


def _skipped_types(skip_documents: bool, skip_files: bool) -> set[str]:
    """The entity types a restore's skip flags leave out."""
    skipped = {"documents"} if skip_documents else set()
    if skip_files:
        skipped.add("files")
    return skipped


def _entry_of_skipped(entity_type: str, row: dict[str, Any], skipped: set[str]) -> bool:
    """Whether the row is the Registry entry of a skipped entity type. It is
    left out with its entities: restored, it would claim their keys with no
    entity behind them."""
    return entity_type == "registry_entries" and row.get("entity_type") in skipped


def _rows_left_out(entity_type: str, skipped: set[str], dropped: Any) -> bool:
    """Whether a plain restore leaves some rows of this type out: the
    Registry entries of skipped types, or the rows of dropped edges."""
    if dropped and entity_type in ("documents", "registry_entries"):
        return True
    return entity_type == "registry_entries" and bool(skipped)


def _left_out(entity_type: str, row: dict[str, Any], skipped: set[str], dropped: Any) -> bool:
    """Whether a plain restore leaves this row out (see _rows_to_restore)."""
    return bool(dropped and _dropped_row(entity_type, row, dropped)) or _entry_of_skipped(
        entity_type, row, skipped,
    )


def _entry_template_id(entry: dict[str, Any]) -> str | None:
    """The template a document's Registry entry names in its primary key."""
    template_id = (entry.get("primary_composite_key") or {}).get("template_id")
    return template_id if isinstance(template_id, str) else None


def _archived_template_rows(reader: Any, sources: list[str]) -> dict[str, TemplateIdentityRow]:
    """(identity_fields, extends) of every template the archive carries."""
    rows: dict[str, TemplateIdentityRow] = {}
    for src in sources:
        for template in reader.read_entities("templates", namespace=src):
            if template.get("template_id"):
                rows.setdefault(
                    template["template_id"],
                    (template.get("identity_fields") or [], template.get("extends")),
                )
    return rows


def _left_out_types(*, skip_documents: bool, skip_files: bool) -> set[str]:
    """The entity types a restore with these flags does not write."""
    return {t for t, skip in (("documents", skip_documents), ("files", skip_files)) if skip}


def _foreign_keys(entry: dict[str, Any], src: str) -> list[Any]:
    """An archived entry's live synonym keys that name a namespace other
    than its own (``src``)."""
    return [
        syn.get("composite_key") for syn in _live_synonyms(entry)
        if _names_foreign_namespace(syn.get("composite_key"), src)
    ]


def _live_synonyms(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """An archived entry's synonyms that are still keys: not inactive."""
    return [
        syn for syn in entry.get("synonyms") or []
        if isinstance(syn, dict) and syn.get("status") != "inactive"
    ]


def _bare_identity_synonyms(
    entries: list[tuple[str, dict[str, Any]]], identity: dict[str, set[str]],
) -> tuple[dict[str, int], list[str]]:
    """Per namespace, how many document entries carry a synonym whose keys
    are exactly their template's identity; and examples ("<entry> <key>").
    An inactive synonym (released when its entity retired) is not restored
    as a key, so it does not count, as in _refuse_foreign_scoped_keys."""
    counts: dict[str, int] = {}
    examples: list[str] = []
    for src, entry in entries:
        keys = identity.get(_entry_template_id(entry) or "")
        bare = [
            syn["composite_key"] for syn in _live_synonyms(entry)
            if keys and set(syn.get("composite_key") or {}) == keys
        ]
        if bare:
            counts[src] = counts.get(src, 0) + 1
            examples.append(f"{entry.get('entry_id')} {bare[0]}")
    return counts, examples


def _would_restore(
    reader: Any, entry: Any, src: str, entity_type: str, skipped: set[str],
    dropped: Any = None,
) -> int:
    """How many rows of one entity type a plain restore writes for ``src``:
    none of a skipped type, none of a dropped edge (its document rows and
    Registry entry), and only the Registry entries of the entities it does
    write. Counts come from the manifest entry when nothing is left out."""
    if entity_type in skipped:
        return 0
    if _rows_left_out(entity_type, skipped, dropped):
        return sum(
            1 for row in reader.read_entities(entity_type, namespace=src)
            if not _left_out(entity_type, row, skipped, dropped)
        )
    if entry is not None:
        return getattr(entry.counts, entity_type, 0)
    return reader.entity_count(entity_type, namespace=src)


def _recorded_export_time(manifest: Any) -> datetime | None:
    """When the archive was exported, as its manifest recorded it, or None.

    The manifest model fills a missing ``exported_at`` with the time it is
    read, which would report the restore's own time as the export's.
    """
    fields_set = getattr(manifest, "model_fields_set", None)
    if fields_set is not None and "exported_at" not in fields_set:
        return None
    exported = getattr(manifest, "exported_at", None)
    return exported if isinstance(exported, datetime) else None


def _rehydrate_top_level(row: dict[str, Any]) -> None:
    for key, value in row.items():
        if not key.endswith("_at") or not isinstance(value, str):
            continue
        try:
            row[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue


def _convert_legacy_term_names(row: dict[str, Any]) -> None:
    """Convert a legacy term row's aliases/translations to names, in place.

    Mirrors def-store's startup migration (its term_names module): plain
    aliases become untyped names, translations become language-bearing
    names; exact duplicates and names exactly equal to the term's own
    value are dropped (first occurrence wins). Case variants are kept —
    in a case-sensitive terminology they are genuine resolution.
    """
    seen: set[tuple[str, str | None]] = {(row.get("value", ""), None)}
    names: list[dict[str, Any]] = []
    for alias in row.pop("aliases", None) or []:
        key = (alias, None)
        if not alias or key in seen:
            continue
        seen.add(key)
        names.append({
            "value": alias, "type": None, "language": None,
            "description": None, "source": None, "preferred": False,
        })
    for tr in row.pop("translations", None) or []:
        label = tr.get("label")
        language = tr.get("language")
        key = (label or "", language)
        if not label or key in seen:
            continue
        seen.add(key)
        names.append({
            "value": label, "type": None, "language": language,
            "description": tr.get("description"), "source": None,
            "preferred": False,
        })
    row["names"] = names


# Reporting verdicts, best first. A run's overall verdict is its worst
# namespace's: one namespace that gave up makes the run "incomplete".
_VERDICT_RANK = {"verified": 0, "not_checked": 1, "unavailable": 2, "incomplete": 3}


def _parity_gaps(parity: dict[str, Any]) -> list[dict[str, Any]]:
    """What a reporting parity result says is missing, one entry per gap:
    ``kind`` (template, table, view), ``name``, ``issue``, and the expected
    and actual counts where the check has them."""
    gaps: list[dict[str, Any]] = []
    for t in parity.get("templates", []):
        if not t.get("table_present", True):
            issue = "table_missing"
        elif t.get("missing_columns"):
            issue = "columns_missing"
        elif t.get("counts_match") is False:
            issue = "count_mismatch"
        else:
            continue
        gaps.append({
            "kind": "template", "name": t.get("template_value"), "issue": issue,
            "expected": t.get("expected_documents"), "actual": t.get("actual_rows"),
        })
    for d in parity.get("derived_tables", []):
        if d.get("issue"):
            gaps.append({
                "kind": "table", "name": d.get("table"), "issue": d.get("issue"),
                "expected": d.get("expected_rows"), "actual": d.get("actual_rows"),
            })
    for v in parity.get("views", []):
        if not v.get("present"):
            gaps.append({
                "kind": "view", "name": v.get("view_name"), "issue": "view_missing",
                "expected": None, "actual": None,
            })
    return gaps


def _drop_unchanged_mappings(remapper: IDRemapper) -> None:
    """Forget referents that resolved to the id they already carry.

    On the install an archive came from, every referent outside it resolves
    to itself. Such a mapping changes nothing; dropping it lets a plain
    restore see that nothing needs rewriting and write the rows as archived.
    """
    for id_map in (
        remapper.terminology_map, remapper.term_map, remapper.template_map,
        remapper.document_map, remapper.file_map,
    ):
        for old_id in [k for k, v in id_map.items() if k == v]:
            del id_map[old_id]
            remapper.external_documents.pop(old_id, None)


@dataclass
class _StagedMerge:
    """One namespace of a merge, planned and waiting to be checked and written.

    A merge plans every namespace before it checks any, and checks every one
    before it writes any, so this holds what the write phase needs.
    """

    src: str
    tgt: str
    definitions: DefinitionsPlan
    remapper: IDRemapper
    built: dict[str, EntityPlan]
    doc_groups: dict[str, list[dict[str, Any]]] | None
    streamed: _StreamedDocuments | None
    arriving: dict[str, set[str]]
    wanted: dict[str, set[str]]


class _AligningRemapper(IDRemapper):
    """The merge's remapper, which also re-pins a document under
    ``align_templates``.

    Each archived row pins a version in the source install's numbering; the
    alignment says which target version takes it. The re-pin keys on the
    ARCHIVED row's (template_id, template_version), so it rides every rewrite
    of a document — heads, in-memory chains, streamed chains — while the
    archived row itself, which the identity rehash reads, is left alone.
    """

    def __init__(self) -> None:
        super().__init__()
        self.version_map: dict[tuple[str, int], int] = {}

    def remap_document(self, document: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = super().remap_document(document)
        if self.version_map:
            key = (str(document.get("template_id")), int(document.get("template_version") or 1))
            target_version = self.version_map.get(key)
            if target_version is not None:
                result["template_version"] = target_version
        return result


# Unplaceable documents an align_templates merge lists on the job, at most;
# the count is always complete.
ALIGNMENT_UNPLACEABLE_SAMPLE = 200


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def registry_search_values(entry: dict[str, Any]) -> list[str]:
    """A Registry entry row's search_values, derived as the Registry does
    (RegistryEntry.rebuild_search_values): the string values of the primary
    key and of every active synonym's key, sorted. A synonym without a status
    is active; an inactive one's values must not resolve."""
    values: set[str] = set()
    keys = [entry.get("primary_composite_key")]
    keys += [
        synonym.get("composite_key")
        for synonym in entry.get("synonyms") or []
        if isinstance(synonym, dict) and synonym.get("status") != "inactive"
    ]
    for key in keys:
        if isinstance(key, dict):
            values.update(v for v in key.values() if isinstance(v, str))
    return sorted(values)


def _resolved_in(ref: Any, source: str, target: str) -> Any:
    """A document reference whose snapshot names ``source``, naming ``target``."""
    if not isinstance(ref, dict):
        return ref
    resolved = ref.get("resolved")
    if isinstance(resolved, dict) and resolved.get("namespace") == source:
        return {**ref, "resolved": {**resolved, "namespace": target}}
    return ref


def _archived_template_ids(reader: Any, sources: list[str]) -> set[str]:
    """The template ids the archive carries in ``sources``."""
    return {
        str(template_id)
        for src in sources
        for template in reader.read_entities("templates", namespace=src)
        if (template_id := template.get("template_id"))
    }


def _document_map_state(remapper: IDRemapper) -> str:
    """A digest of the remapper's document mappings, to tell whether merge
    planning rounds have come back to mappings they already started from."""
    digest = hashlib.sha256()
    for old_id, new_id in sorted(remapper.document_map.items()):
        digest.update(f"{old_id}\0{new_id}\n".encode())
    return digest.hexdigest()


class _IdentityRehash:
    """Recompute the identity hash of document rows whose references a
    restore rewrote, and re-key their Registry rows to the new hash.

    A term- or reference-typed identity field hashes the canonical id its
    stored resolution names, so repointing a resolution changes the
    document's identity. Left as archived, the row and its Registry key would
    describe an identity no write computes: the next write of the same
    identity would create a second document, and a merge would not match the
    target's copy of it.

    The identity definition comes from the archived template the row was
    written against, keyed by its archived (template_id, template_version),
    referent_identity_paths included (a dotted identity path into a nested
    template cannot be classified without them). A template the archive
    references but does not carry is read from this install
    (``external``); a row on a template known nowhere keeps its hash.
    """

    def __init__(
        self,
        reader: Any,
        sources: list[str],
        external: dict[tuple[str, int], dict[str, Any]] | None = None,
    ):
        # Every archived namespace's templates: a document may use a template
        # of another namespace in the same archive.
        self._templates = TemplateDefinitions.of_templates(
            template
            for src in sources
            for template in reader.read_entities("templates", namespace=src)
        )
        self._templates.set_external(external or {})
        self.new_hash: dict[str, str] = {}
        # Keyed rows whose rewrite fed the hash but could not be rehashed:
        # no definition, or identity values or resolutions unreadable.
        self.unrehashable = 0

    def rehash(self, archived: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        """``row`` is ``archived`` after the reference rewrite; recompute its
        hash in place when the rewrite changed anything the hash is fed from:
        a resolution, or an id in the data itself (a file field's file_id,
        say, which no resolution list records)."""
        if all(
            row.get(k) == archived.get(k)
            for k in ("data", "references", "term_references")
        ):
            return row
        template = self._templates.lookup(archived)
        changed = rehash_document_row(
            row, template["identity_fields"], template["fields"], template["referent_paths"]
        ) if template else None
        if changed:
            self.new_hash[str(row.get("document_id"))] = row["identity_hash"]
        elif changed is None and row.get("identity_hash"):
            self.unrehashable += 1
        return row

    def rekey(self, entry: dict[str, Any]) -> dict[str, Any]:
        """A rehashed document's Registry row, keyed on its new hash."""
        new_hash = (
            self.new_hash.get(str(entry.get("entry_id")))
            if entry.get("entity_type") == "documents" else None
        )
        key = entry.get("primary_composite_key")
        if new_hash and isinstance(key, dict) and key.get("identity_hash") != new_hash:
            key = {**key, "identity_hash": new_hash}
            entry = {
                **entry,
                "primary_composite_key": key,
                "primary_composite_key_hash": compute_composite_key_hash(key),
            }
        return entry


class _PlainRestoreRewrite:
    """Repoint a plain restore's references to entities outside the archive.

    Only ids in the remapper's maps change, and those are all referents the
    archive does not carry; the archive's own ids pass through every rewrite.

    A rewritten reference can change a document's identity: a term- or
    reference-typed identity field hashes the canonical id it resolves to.
    Such a document is rehashed, and its Registry row (restored after the
    documents) is re-keyed to the new hash, so the key it claims is the one
    the next write of that identity computes.
    """

    def __init__(
        self,
        remapper: IDRemapper,
        reader: Any,
        sources: list[str],
        external: dict[tuple[str, int], dict[str, Any]] | None = None,
    ):
        self.remapper = remapper
        self._rehash = _IdentityRehash(reader, sources, external)

    @property
    def rehashed_documents(self) -> int:
        return len(self._rehash.new_hash)

    @property
    def unrehashable_documents(self) -> int:
        return self._rehash.unrehashable

    def rewrite(
        self, entity_type: str, entity: dict[str, Any], engine: DirectRestoreEngine
    ) -> dict[str, Any]:
        remap = {
            "terms": self.remapper.remap_term,
            "term_relations": self.remapper.remap_term_relation,
            "templates": self.remapper.remap_template,
        }.get(entity_type)
        if remap is not None:
            return cast(dict[str, Any], remap(entity))
        if entity_type == "documents":
            return self._rewrite_document(entity)
        if entity_type == "registry_entries":
            return self._rewrite_entry(entity, engine)
        return entity

    def _rewrite_document(self, entity: dict[str, Any]) -> dict[str, Any]:
        row = cast(dict[str, Any], self.remapper.remap_document(entity))
        return self._rehash.rehash(entity, row)

    def _rewrite_entry(
        self, entry: dict[str, Any], engine: DirectRestoreEngine
    ) -> dict[str, Any]:
        return self._rehash.rekey(engine._rewrite_registry_entry(entry, self.remapper))


def _drain(chains: Iterator[Any]) -> None:
    """Run a lazy pass for its side effects."""
    for _ in chains:
        pass


def _describe_gap(gap: dict[str, Any]) -> str:
    if gap["kind"] == "view":
        return f"view {gap['name']} missing"
    return f"{gap['name']} {gap['issue']} expected={gap['expected']} actual={gap['actual']}"


# The order a plain restore writes a namespace's entities in: definitions
# before what references them, registry entries last.
_PLAIN_RESTORE_ORDER = (
    "terminologies",
    "terms",
    "term_relations",
    "templates",
    "documents",
    "files",
    "registry_entries",
)

# The Registry entries of definitions. They are written with the definitions,
# ahead of the reporting structure gate and of any document, not with the
# rest of the entries at the end: every service resolves a template or
# terminology id through the Registry, so until its entry exists the
# definition is unknown to every read. Reporting-sync's batch sync lists a
# template's documents by its id, and document-store answers 404 for an id the
# Registry does not know; reporting-sync rightly reads that as "source did
# not answer", creates no table, and the gate times out.
_DEFINITION_ENTRY_TYPES = frozenset({"terminologies", "terms", "templates"})


def _is_definition_entry(row: dict[str, Any]) -> bool:
    return row.get("entity_type") in _DEFINITION_ENTRY_TYPES


# The advice after a halted reporting structure gate. A plain restore
# filled an empty namespace, so removing what it wrote is the way back; a
# merge added to a namespace that already held data, which must survive.
_RESTORE_STRUCTURE_HALT = (
    "NOTE: namespace '{namespace}' is now PARTIAL — definitions were "
    "restored but documents and their registry identities were NOT, so "
    "namespaced lookups against it will fail to resolve. Delete the "
    "namespace and re-restore after remediating."
)
_MERGE_STRUCTURE_HALT = (
    "NOTE: this merge added the archive's definitions to namespace "
    "'{namespace}', but none of its documents; the documents the namespace "
    "already held are untouched. Do not delete the namespace: remediate "
    "reporting-sync and run the merge again."
)


def _structure_failures(parity: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The parity rows of templates whose reporting table is missing or
    misshaped."""
    return [
        t for t in (parity or {}).get("templates", [])
        if not (
            t.get("table_present")
            and not t.get("missing_columns")
            and not t.get("legacy_table")
            and not t.get("error")
        )
    ]


def _structure_halt(
    namespace: str, timeout_s: float, issues: list[str], *, merge: bool
) -> str:
    """The message of a halted reporting structure gate: the first five
    issues, how many more, and the advice for the mode."""
    more = f" (and {len(issues) - 5} more)" if len(issues) > 5 else ""
    advice = _MERGE_STRUCTURE_HALT if merge else _RESTORE_STRUCTURE_HALT
    return (
        f"Reporting tables for namespace '{namespace}' did not verify "
        f"within {timeout_s}s — halting before "
        f"{'merging documents' if merge else 'document restore'}. "
        f"Issues: {'; '.join(issues[:5]) or 'unknown'}{more}. "
        + advice.format(namespace=namespace)
    )


def _parity_owner(namespace: str, template: dict[str, Any]) -> tuple[str, str]:
    """(owning namespace, value) of a parity row's template: its
    template_namespace when another namespace owns it, else the checked one."""
    return (
        str(template.get("template_namespace") or namespace),
        str(template.get("template_value")),
    )


def _structure_issue(template: dict[str, Any], sync_error: str | None) -> str:
    """One template's failed structure check, with reporting-sync's reason
    for it when its batch-sync job failed."""
    if template.get("error"):
        problem = str(template["error"])
    elif template.get("legacy_table"):
        problem = "legacy pre-split table shadows the entity view"
    elif not template.get("table_present"):
        problem = "table missing"
    else:
        problem = f"missing columns {template.get('missing_columns')}"
    if sync_error:
        problem += f" — its reporting sync failed: {sync_error}"
    return f"{template.get('template_value')}: {problem}"


class DirectRestoreEngine:
    """Restore an archive's namespaces via direct MongoDB bulk writes.

    Three modes, one entry point each: ``run_restore`` (ID-preserving, into
    empty namespaces), ``run_remap`` (``fresh``: every entity re-minted, so a
    copy can live beside its source) and ``run_merge`` (into a namespace that
    already holds data, with clash policies). Reads the archive with
    :class:`~wip_archive.archive.ArchiveReader` and writes every service's
    database directly.
    """

    def __init__(
        self,
        mongo_client: AsyncIOMotorClient,
        storage_client: FileStorageClient | None,
        progress: Callable[[ProgressEvent], None],
        *,
        registry_base_url: str | None = None,
        registry_api_key: str | None = None,
        reporting_client: ReportingSyncClient | None = None,
        job_id: str | None = None,
        actor: str | None = None,
        requester: RestoreRequester = SERVICE_REQUESTER,
    ) -> None:
        self._mongo = mongo_client
        self._storage = storage_client
        self._progress = progress
        self._requester = requester
        # The event loop the run is on, set by `_off_loop` before work leaves it.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Who and which job, for the provenance record each restore writes.
        self._job_id = job_id
        self._actor = actor
        # The archive file's digest, computed once per run on first use.
        self._archive_sha256: str | None = None
        # Templates the archive references but does not carry, read from
        # this install for the rehash: every version, by the id they map to
        # here. Kept for the run, so a merge into several targets asks once.
        self._external_template_cache: dict[str, dict[int, dict[str, Any]]] = {}
        # Set when this run restores an archive whose own verify failed
        # because the request accepted it; recorded on the provenance rows.
        self._accepted_failed_verify = False
        self._unresolved_referents: dict[str, _UnresolvedReferent] = {}
        # Structured outcome for a caller that was not watching the event
        # stream. Progress events overwrite each other on the job record, so a
        # dry run's per-type counts — the whole reason to ask — are gone by the
        # time it completes unless they are collected here.
        self.result: dict[str, Any] = {}
        # Per target of a fresh restore, how many restored identities are
        # active so far: what a failed activation reports as live.
        self._activation: dict[str, dict[str, int]] = {}
        # Namespaces whose reporting batch sync this run has already
        # triggered (the count phase). Reported on the complete event so the
        # job's completion does not trigger a second, full re-sync of them.
        self._reporting_synced: list[str] = []
        # Per namespace, the key claims of the definitions' registry entries
        # (claimed, owned by another), written in a pass ahead of the rest and
        # reported together with them (_restore_entity_type).
        self._held_claims: dict[str, tuple[int, int]] = {}
        # Per-namespace reporting verdicts (verified / incomplete /
        # unavailable / not_checked), put on the result at completion.
        self._reporting_verdicts: dict[str, dict[str, Any]] = {}
        self._registry_url = registry_base_url or os.getenv(
            "REGISTRY_URL", "http://localhost:8001"
        )
        self._registry_api_key = registry_api_key or service_api_key(*REGISTRY_KEY)
        # Reporting verification phases (restore-verification design): when a
        # client is injected AND reporting-sync answers, the restore verifies
        # the reporting layer at phase boundaries. When reporting-sync is
        # unreachable (core preset deploys without it), the phases degrade to
        # a logged warning — a restore never fails because the convenience
        # layer is absent, only on positive verification failures.
        self._reporting = reporting_client

    async def run_restore(
        self,
        archive_path: Path,
        target_namespace: str,
        *,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        drop_stale_reporting: bool = False,
        dry_run: bool = False,
        allow_missing_identity: bool = False,
        drop_unresolved_edges: bool = False,
        allow_failed_verify: bool = False,
    ) -> None:
        """Run the full restore pipeline over every namespace in the archive.

        Identity-only restore (CASE-542): each namespace restores to itself and
        its target must be empty. A ``target_namespace`` that differs from the
        archive's own namespace is rejected — ID-preserving restore cannot
        re-namespace data (entities, registry entries, and composite keys all
        embed the source namespace); re-namespacing is ``run_remap`` (mode
        ``fresh``), and writing into a live namespace is ``run_merge``.

        With ``dry_run`` the engine runs every precondition (archive format,
        empty targets, reporting schema) and reports what it *would* restore
        from the manifest, then completes without writing anything — no
        namespace upsert, no inserts, no blobs, no reporting sync.
        """
        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            # Before anything else: a derived archive whose own verify failed
            # is refused here, dry runs included.
            raw_manifest = reader.read_manifest_raw()
            self._check_derived_archive(
                raw_manifest, allow_failed_verify=allow_failed_verify
            )
            await self._refuse_bare_identity_synonyms(reader, manifest)
            targets = self._resolve_targets(reader, manifest, target_namespace)

            self._emit(
                "start",
                f"Starting restore of {len(targets)} namespace(s)",
                percent=0,
            )

            # Phase 1: validate ALL targets empty before writing anything.
            # The reporting precondition runs alongside: the namespace's
            # reporting schema must be absent/empty too, or stale tables
            # would shadow the restored data (fossil-schema incident class).
            self._emit("phase_validate", "Checking target namespaces are empty", percent=2)
            for _src, tgt in targets:
                await self._check_namespace_empty(tgt)
                await self._check_reporting_precondition(
                    tgt, drop_stale_reporting, dry_run=dry_run
                )

            self._check_identity_rows(reader, targets, allow_missing_identity)

            remapper, referents, dropped, repoint = await self._plain_restore_repoint(
                reader, manifest, targets, drop_unresolved_edges=drop_unresolved_edges,
            )

            entry_by_prefix = {e.prefix: e for e in manifest.namespaces}
            restore_order = _PLAIN_RESTORE_ORDER

            if dry_run:
                await self._restore_dry_run(
                    reader, manifest, targets, entry_by_prefix, referents, remapper,
                    skip_documents=skip_documents, skip_files=skip_files, dropped=dropped,
                )
                return

            total = sum(getattr(manifest.counts, et, 0) for et in restore_order)
            processed = 0
            skipped = _skipped_types(skip_documents, skip_files)

            # Every target namespace is created first: the Registry's
            # refusal of a namespace is the last one a restore can meet
            # before it writes, so a refused restore has written no bytes.
            for src, tgt in targets:
                entry = entry_by_prefix.get(src)
                self._emit(
                    "phase_namespace",
                    f"Creating/updating namespace '{tgt}'",
                    percent=self._pct(processed, total),
                )
                await self._upsert_namespace(
                    tgt, entry.namespace_config if entry else manifest.namespace_config,
                )

            # Bytes before the records that need them. Blobs are flat
            # (namespace-agnostic, globally unique file_ids), so the archive's
            # whole set goes up once, first: a restore that fails later leaves
            # at most bytes no record points at yet (left as they are by a
            # retry that brings the same bytes), never a file record whose
            # bytes are missing. No key that holds other bytes is written.
            if not skip_files and self._storage:
                await self._restore_blobs(reader, targets[0][1])

            for src, tgt in targets:
                processed = await self._restore_one_namespace(
                    reader, manifest, raw_manifest, archive_path, src, tgt,
                    entry_by_prefix.get(src),
                    referents=referents, dropped=dropped, repoint=repoint,
                    skipped=skipped, skip_documents=skip_documents,
                    batch_size=batch_size, processed=processed, total=total,
                )

        self._finish_plain_restore(targets, referents, remapper, repoint)

    def _report_unrehashable(self, where: str, count: int, result: dict[str, Any]) -> None:
        """Record and warn about rows a merge or plain restore rewrote but
        could not rehash, as a fresh restore does."""
        if not count:
            return
        result["unrehashable_documents"] = count
        self._emit(
            "warning",
            f"[{where}] {count} document(s) keep their archived identity hash: "
            "their template (or a parent it extends) is in neither the archive nor "
            "this install, or their identity values or resolutions cannot be read. "
            "If an identity field names a term or document this restore repointed, "
            "rehash the namespace (POST /api/document-store/maintenance/"
            "rehash-identities) once their template exists.",
        )

    def _finish_plain_restore(
        self, targets: list[tuple[str, str]], referents: Any, remapper: IDRemapper, repoint: Any,
    ) -> None:
        """Record what a plain restore repointed and synced, and report done."""
        self.result["referents"] = referents
        self.result["repointed_referents"] = remapper.total_mappings
        self.result["rehashed_documents"] = repoint.rehashed_documents if repoint else 0
        self._report_unrehashable(
            ", ".join(tgt for _, tgt in targets),
            repoint.unrehashable_documents if repoint else 0, self.result,
        )
        self.result["reporting_synced"] = list(self._reporting_synced)
        self._finish_reporting_verification(
            [tgt for _, tgt in targets], "reporting was not verified for this namespace"
        )
        self._emit("complete", "Restore complete", percent=100, details=self.result)

    async def _plain_restore_repoint(
        self,
        reader: ArchiveReader,
        manifest: Any,
        targets: list[tuple[str, str]],
        *,
        drop_unresolved_edges: bool,
    ) -> tuple[IDRemapper, Any, Any, Any]:
        """The referents a plain restore points at this install's entities.

        The archive's own ids are preserved — that is this mode's contract.
        References to entities it does NOT carry (the wip namespace's
        vocabularies, sibling namespaces) name the source install's ids,
        which on another install differ or do not exist; they are pointed at
        this install's entities. On the install the archive came from every
        referent resolves to itself, nothing is mapped, and rows are written
        as archived. Returns (remapper, referents, dropped rows, the row
        rewrite or None when nothing is mapped).
        """
        remapper = IDRemapper()
        label = ", ".join(tgt for _src, tgt in targets)
        referents = await self._map_external_referents(
            reader, manifest, remapper, label,
        )
        dropped = await self._report_unresolved_references(
            reader, [src for src, _tgt in targets], remapper, referents, label,
            drop_edges=drop_unresolved_edges,
            unresolved_hint=(
                " — if it lives in a namespace restored separately, restore "
                "that namespace first, then this one"
            ),
        )
        _drop_unchanged_mappings(remapper)
        repoint = (
            _PlainRestoreRewrite(
                remapper, reader, [src for src, _tgt in targets],
                await self._external_template_definitions(
                    reader, manifest, remapper,
                    _archived_template_ids(reader, [src for src, _tgt in targets]),
                ),
            )
            if remapper.total_mappings else None
        )
        return remapper, referents, dropped, repoint

    async def _restore_one_namespace(
        self,
        reader: ArchiveReader,
        manifest: Any,
        raw_manifest: Any,
        archive_path: Path,
        src: str,
        tgt: str,
        entry: Any,
        *,
        referents: Any,
        dropped: Any,
        repoint: Any,
        skipped: set[str],
        skip_documents: bool,
        batch_size: int,
        processed: int,
        total: int,
    ) -> int:
        """Write one archived namespace into its target, which the run
        created before any write; returns the run's processed count after it."""
        for entity_type in _PLAIN_RESTORE_ORDER:
            if entity_type in skipped:
                continue
            processed += await self._restore_entity_type(
                reader, entity_type, src, tgt, entry,
                dropped=dropped, repoint=repoint, batch_size=batch_size,
                processed=processed, total=total, skipped=skipped,
                definition_entries=False if entity_type == "registry_entries" else None,
            )
            if entity_type == "templates":
                # The definitions' Registry entries first: until they
                # exist no service can resolve a restored template or
                # terminology (_DEFINITION_ENTRY_TYPES).
                processed += await self._restore_entity_type(
                    reader, "registry_entries", src, tgt, entry,
                    dropped=dropped, repoint=repoint, batch_size=batch_size,
                    processed=processed, total=total, skipped=skipped,
                    definition_entries=True,
                )
                # Structural gate between templates and documents: the
                # reporting tables the restored templates imply must be
                # creatable and correctly shaped BEFORE any document
                # moves. Catches broken bookkeeping / mis-shaped tables
                # at the first template instead of after a full restore.
                await self._reporting_phase_structure(tgt)

        await self._release_retired_synonyms(tgt)

        # Count parity after the namespace's data is in: expected vs
        # actual rows, bounded wait. Mismatch completes WITH a
        # warning — never a hard fail (operator ruling).
        await self._reporting_phase_counts(tgt, skip_documents=skip_documents)
        await self._write_restore_provenance(
            archive_path, manifest, raw_manifest,
            source=src, target=tgt, mode="restore",
            referents=_referents_for(referents, src),
        )
        return processed

    def _check_identity_rows(
        self, reader: ArchiveReader, targets: list[tuple[str, str]], allow_missing_identity: bool,
    ) -> None:
        # Identity precondition: an archive that carries entities but no
        # registry rows restores into a namespace where nothing resolves
        # by id — every list/query surface looks healthy while point
        # reads fail, and post-restore integrity validation cannot see it
        # (it checks MongoDB against itself; identity lives in the
        # Registry). Refuse up front instead of minting that namespace.
        entity_types_with_identity = (
            "terminologies", "terms", "term_relations",
            "templates", "documents", "files",
        )
        for src, _tgt in targets:
            entity_rows = sum(
                reader.entity_count(et, namespace=src)
                for et in entity_types_with_identity
            )
            registry_rows = reader.entity_count(
                "registry_entries", namespace=src
            )
            if entity_rows > 0 and registry_rows == 0:
                if allow_missing_identity:
                    self._emit(
                        "warning",
                        f"[{src}] archive carries {entity_rows} entity "
                        "row(s) but NO registry identity rows "
                        "(allow_missing_identity=true) — id-based reads "
                        "in the restored namespace will fail to resolve.",
                    )
                else:
                    raise RestoreEngineError(
                        f"Archive namespace '{src}' carries "
                        f"{entity_rows} entity row(s) but no registry "
                        "identity rows — was it exported with "
                        "--skip-synonyms? Restoring it would produce a "
                        "namespace where nothing resolves by id. Pass "
                        "allow_missing_identity=true to restore anyway."
                    )

    async def _restore_dry_run(
        self,
        reader: ArchiveReader,
        manifest: Any,
        targets: list[tuple[str, str]],
        entry_by_prefix: dict[str, Any],
        referents: Any,
        remapper: IDRemapper,
        *,
        skip_documents: bool,
        skip_files: bool,
        dropped: Any = None,
    ) -> None:
        """What a plain restore would write, without writing it: the same rows
        the real run writes, so without skipped types and dropped edges."""
        restore_order = list(_PLAIN_RESTORE_ORDER)
        skipped = _skipped_types(skip_documents, skip_files)

        self.result = {
            "mode": "restore",
            "dry_run": True,
            "referents": referents,
            "repointed_referents": remapper.total_mappings,
            "namespaces": {
                tgt: {
                    entity_type: _would_restore(
                        reader, entry_by_prefix.get(src), src, entity_type, skipped, dropped,
                    )
                    for entity_type in restore_order
                }
                for src, tgt in targets
            },
        }
        # The one step a restore takes as its requester: asked with
        # the Registry's dry_run, so its refusals are the real ones.
        for src, tgt in targets:
            entry = entry_by_prefix.get(src)
            await self._upsert_namespace(
                tgt, entry.namespace_config if entry else manifest.namespace_config,
                dry_run=True,
            )
        if not skip_files and self._storage:
            await self._dry_run_blob_report(reader)
        self._dry_run_report(
            reader, targets, entry_by_prefix, restore_order,
            skip_documents=skip_documents, skip_files=skip_files, dropped=dropped,
        )

    def _rows_to_restore(
        self, reader: ArchiveReader, entity_type: str, src: str,
        dropped: Any, repoint: Any, skipped: set[str],
        definition_entries: bool | None = None,
    ) -> Iterator[dict[str, Any]]:
        """The archive's rows of one type and namespace as a plain restore
        writes them: without MongoDB's _id, without dropped rows and the
        Registry entries of skipped types, and repointed. For registry
        entries, ``definition_entries`` True keeps only the definitions'
        entries, False only the others, None all of them."""
        for entity in reader.read_entities(entity_type, namespace=src):
            entity.pop("_id", None)  # Strip MongoDB internal ID
            if (dropped and _dropped_row(entity_type, entity, dropped)) or _entry_of_skipped(
                entity_type, entity, skipped,
            ):
                continue
            if (
                definition_entries is not None and entity_type == "registry_entries"
                and _is_definition_entry(entity) != definition_entries
            ):
                continue
            yield repoint.rewrite(entity_type, entity, self) if repoint is not None else entity

    async def _restore_entity_type(
        self,
        reader: ArchiveReader,
        entity_type: str,
        src: str,
        tgt: str,
        entry: Any,
        *,
        dropped: Any,
        repoint: Any,
        batch_size: int,
        processed: int,
        total: int,
        skipped: set[str] | None = None,
        definition_entries: bool | None = None,
    ) -> int:
        """Write one entity type of one namespace in batches of batch_size;
        returns the rows written. ``processed`` is the run's count before this
        type, for the progress percentage. Registry entries of a skipped
        entity type (``skipped``) are left out with their entities: an entry
        claims its keys, and one without its entity would hold them for
        nothing. ``definition_entries`` splits the registry entries into the
        definitions' (True) and the rest (False); see _rows_to_restore."""
        db_name, coll_name = COLLECTION_MAP[entity_type]
        collection = self._mongo[db_name][coll_name]
        expected = getattr(entry.counts, entity_type, 0) if entry else 0
        phase_name = f"phase_{entity_type}"
        if definition_entries is None or entity_type != "registry_entries":
            what = f"{entity_type} ({expected} expected)"
        else:
            what = f"registry_entries of {'definitions' if definition_entries else 'documents and files'}"
        self._emit(
            phase_name,
            f"[{tgt}] Restoring {what}",
            percent=self._pct(processed, total),
        )

        batch: list[dict[str, Any]] = []
        count = claimed = taken_by_other = 0
        rows = self._rows_to_restore(
            reader, entity_type, src, dropped, repoint, skipped or set(), definition_entries,
        )
        for entity in rows:
            batch.append(entity)
            if len(batch) < batch_size:
                continue
            ok, theirs = await self._write_restore_batch(collection, batch, entity_type, tgt)
            claimed, taken_by_other = claimed + ok, taken_by_other + theirs
            count += len(batch)
            batch = []
            if count % 5000 == 0:
                self._emit(
                    phase_name,
                    f"[{tgt}] {entity_type}: {count}/{expected}",
                    percent=self._pct(processed + count, total),
                    current=count,
                    total=expected,
                )
        if batch:
            ok, theirs = await self._write_restore_batch(collection, batch, entity_type, tgt)
            claimed, taken_by_other = claimed + ok, taken_by_other + theirs
            count += len(batch)

        logger.info("Restored %d %s into namespace %s", count, entity_type, tgt)
        if entity_type == "registry_entries":
            # The definitions' entries are written in a pass of their own;
            # their claims are reported with the rest's, once per namespace.
            held = self._held_claims.pop(tgt, (0, 0))
            claimed, taken_by_other = claimed + held[0], taken_by_other + held[1]
            if definition_entries:
                self._held_claims[tgt] = (claimed, taken_by_other)
            else:
                self._report_claims(tgt, claimed, taken_by_other)
        return count

    async def _write_restore_batch(
        self, collection: Any, batch: list[dict[str, Any]], entity_type: str, namespace: str,
    ) -> tuple[int, int]:
        """Insert one batch; for registry entries, claim their keys as well.
        Returns (keys claimed, keys another entry already owns)."""
        await self._insert_batch(collection, batch, entity_type)
        if entity_type != "registry_entries":
            return 0, 0
        return await self._claim_inserted_entries(namespace, batch)

    # Documents are the only thing a merge resolves by policy. Definitions
    # (terminologies, terms, templates) are a precondition instead: a merge
    # that had to reconcile schemas while writing data would be validating one
    # side's documents against the other's contract.
    MERGE_CLASH_POLICIES = ("skip", "overwrite", "newer")
    # What happens to the target's older versions when a clash appends the
    # archive's version on top. 'retire' soft-deletes them, exactly as a normal
    # write retires the version it supersedes; 'keep' leaves them active, so
    # the document has several active versions (legal, like several active
    # template versions, but it is a choice the caller makes, never a default).
    MERGE_OLDER_VERSIONS = ("retire", "keep")

    async def run_remap(
        self,
        archive_path: Path,
        target_namespace: str = "",
        *,
        namespace_map: dict[str, str] | None = None,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        dry_run: bool = False,
        drop_unresolved_edges: bool = False,
        allow_failed_verify: bool = False,
        drop_stale_reporting: bool = False,
    ) -> None:
        """Restore an archive's content as brand-new entities.

        Nothing is kept: every entity is registered afresh with a
        Registry-minted id, and every reference between them is rewritten. It
        is what lets a namespace be restored beside the one it came from —
        two live copies cannot share a canonical id.

        A single-namespace archive takes ``target_namespace``. A
        multi-namespace archive takes ``namespace_map`` — an explicit
        ``{source: target}`` for EVERY namespace it carries; several sources
        may share one target (Registry-key collisions between them refuse at
        plan time), and cross-namespace references between archived
        namespaces follow their entities to the new names.

        The targets must be empty, as for a plain restore. What differs is
        that the archive's own registry entries are NOT written: provisioning
        creates the entries, so importing the archived ones would duplicate
        every identity under its old id.

        Ordering is deliberate. Entries are provisioned as *reserved*, which
        does not resolve, so a job that dies before activation leaves an
        invisible and reconcilable namespace. Activation at the end makes the
        set visible; it runs in batches, target by target, so a failure during
        it leaves the earlier batches live, and the error and the job record
        say exactly how many per target.

        No reporting sync runs here. A restore's job is to get the data in;
        PostgreSQL is a derived layer that can be rebuilt at any time, and
        syncing mid-restore only couples the write to a service that has
        nothing to say yet. Verify afterwards — `check_reporting_parity` and
        the namespace validation job both exist for that. The stale-schema
        precondition does apply: a target that already has a reporting schema
        is refused, unless ``drop_stale_reporting`` is set, which drops that
        schema before anything is written.
        """
        if not target_namespace and not namespace_map:
            raise RestoreEngineError(
                "A remap restore needs a target namespace — it mints new "
                "identities, so it must be told where to put them."
            )

        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            # Before anything else: a derived archive whose own verify failed
            # is refused here, dry runs included.
            raw_manifest = reader.read_manifest_raw()
            self._check_derived_archive(
                raw_manifest, allow_failed_verify=allow_failed_verify
            )
            await self._refuse_bare_identity_synonyms(reader, manifest)
            sources = self._archive_namespaces(reader, manifest)
            mapping = self._resolve_remap_mapping(
                sources, target_namespace, namespace_map
            )
            targets = list(dict.fromkeys(mapping.values()))

            self._emit(
                "start",
                "Starting remap restore: "
                + ", ".join(f"'{s}' → '{t}'" for s, t in mapping.items()),
                percent=0,
            )
            self._emit("phase_validate", "Checking the targets are empty", percent=2)
            for target in targets:
                await self._check_namespace_empty(target)
                await self._check_reporting_precondition(
                    target, drop_stale_reporting, dry_run=dry_run
                )

            entries = {e.prefix: e for e in manifest.namespaces}
            remap_sources = self._collect_remap_sources(
                reader, mapping, skip_documents=skip_documents, skip_files=skip_files,
            )

            definitions = self._share_template_definitions(remap_sources)

            # Several sources folding into one target may collide on a Registry
            # key. That is refused here, for every type at once, before any
            # target is created or anything reserved: found later, it left a
            # half-provisioned target that a retry refused as not empty.
            await self._off_loop(check_collisions_before_writing, remap_sources)
            # Off the event loop, as the collision pass above: it reads every
            # source's registry entries.
            await self._off_loop(
                self._refuse_foreign_scoped_keys, reader, mapping,
                _left_out_types(skip_documents=skip_documents, skip_files=skip_files),
            )

            # The namespaces have to exist before anything is provisioned: the
            # Registry mints ids per the TARGET namespace's id_config, and
            # refuses outright for a namespace it does not know. A dry run asks
            # the Registry the same question with its dry_run flag, so it meets
            # the same refusals and creates nothing. For several sources
            # collapsing into one target, the FIRST source's config wins
            # (archive order); allowed_external_refs naming other archive
            # sources are rewritten through the mapping, names outside the
            # archive are kept.
            await self._create_remap_targets(targets, mapping, entries, manifest, dry_run=dry_run)

            remapper, referents, plans = await self._plan_remap(
                reader, manifest, mapping, remap_sources, definitions,
                dry_run=dry_run, drop_unresolved_edges=drop_unresolved_edges,
            )

            self._report_remap_plans(mapping, plans, dry_run=dry_run)
            self.result = {
                "mode": "fresh",
                "dry_run": dry_run,
                "namespace_map": dict(mapping),
                # Kept for single-namespace callers; absent shape-change risk.
                "source_namespace": next(iter(mapping)),
                "target_namespace": mapping[next(iter(mapping))],
                "planned": {src: plans[src].summary() for src in mapping},
                "referents": referents,
            }

            if dry_run:
                self._emit(
                    "complete",
                    "Remap dry run complete — nothing provisioned, nothing "
                    "written",
                    percent=100,
                    details=self.result,
                )
                return

            # Door repair for edge-type endpoint declarations. The remapper
            # rewrote every resolved half the id map covers (in-archive
            # targets — every archived template was provisioned into the
            # map). What is left uncovered is out-of-archive by construction,
            # and for those the lookup half is the only mechanism: re-resolve
            # it here on the target, or clear the resolved half with a
            # warning. A stale source-install id must never persist into a
            # fresh namespace (the leak-sweep contract).
            await self._repair_endpoint_declarations(mapping, plans, remapper)

            if not skip_files:
                await self._upload_remapped_blobs(reader, plans)
            rewritten = await self._write_and_activate_remapped(
                reader, mapping, targets, plans, batch_size,
            )
            # CASE-792: provision/activate set only the primary composite key
            # for everything but documents. The value-form lookup synonyms the
            # create services auto-register (and any custom synonyms) live in
            # the archive's registry_entries; carry the remaining ones over now
            # that every entity is active.
            await self._restore_synonyms(mapping, rewritten)
            # A fresh restore registers synonyms as active; release the ones a
            # retired document must not keep.
            await self._record_remap_provenance(
                archive_path, manifest, raw_manifest, mapping, referents,
            )

        # A fresh restore does not wait on the reporting layer: its sync is
        # triggered when the job completes. Say so rather than imply a check.
        self._finish_reporting_verification(
            list(mapping.values()),
            "a fresh restore does not wait for reporting; its sync starts when "
            "the job completes — check GET /api/reporting-sync/parity",
        )
        self._emit("complete", "Remap restore complete", percent=100,
                   details=self.result)

    @staticmethod
    def _share_template_definitions(remap_sources: list[RemapSource]) -> TemplateDefinitions:
        """The rehash reads the template each document is pinned to, from
        EVERY archived namespace: a document may sit on another namespace's
        template. These are the definitions the documents were written
        against (the target has none yet); every source gets the same set."""
        definitions = TemplateDefinitions.of_templates(
            template
            for rs in remap_sources
            for template in rs.entities_by_type.get("templates") or []
        )
        for rs in remap_sources:
            rs.template_definitions = definitions
        return definitions

    async def _plan_remap(
        self,
        reader: ArchiveReader,
        manifest: Any,
        mapping: dict[str, str],
        remap_sources: list[RemapSource],
        definitions: TemplateDefinitions,
        *,
        dry_run: bool,
        drop_unresolved_edges: bool,
    ) -> tuple[IDRemapper, Any, dict[str, Any]]:
        """Map the external referents, drop what cannot resolve, and plan
        every source (provisioning reserved ids, or a dry run's stand-ins).

        The remapper gets the namespace mapping so reference snapshots
        (resolved.namespace) follow their entities — after a fresh restore
        no data may point at the original namespaces.
        """
        targets = ", ".join(dict.fromkeys(mapping.values()))
        remapper = IDRemapper(namespace_map=dict(mapping))
        referents = await self._map_external_referents(reader, manifest, remapper, targets)
        definitions.set_external(await self._external_template_definitions(
            reader, manifest, remapper, definitions.template_ids(),
        ))
        dropped = await self._report_unresolved_references(
            reader, list(mapping), remapper, referents, targets,
            drop_edges=drop_unresolved_edges,
        )
        if dropped:
            for rs in remap_sources:
                _drop_from_remap_source(rs, dropped)
        provision_for = (
            self._dry_run_provisioner() if dry_run
            else self._registry_provisioner
        )
        plans = await plan_multi(remap_sources, remapper, provision_for)
        return remapper, referents, plans

    async def _upload_remapped_blobs(self, reader: ArchiveReader, plans: dict[str, Any]) -> None:
        """Bytes before the records that need them, under their new ids: a
        write that fails afterwards leaves at most unreferenced bytes, never
        a file record whose bytes are missing."""
        if not self._storage:
            return
        merged_file_map: dict[str, str] = {}
        for plan in plans.values():
            merged_file_map.update(plan.id_map["files"])
        await self._remap_blobs(reader, merged_file_map)

    async def _write_and_activate_remapped(
        self,
        reader: ArchiveReader,
        mapping: dict[str, str],
        targets: list[str],
        plans: dict[str, Any],
        batch_size: int,
    ) -> dict[str, Any]:
        """Write every source's rows, then activate each target's entries.
        Returns the archived synonyms rewritten for their re-minted owners,
        less those that travelled with a document's activation item."""
        for src, target in mapping.items():
            await self._write_remapped(target, plans[src], batch_size)

        # The archived synonyms, rewritten for their re-minted owners. A
        # document's travel with its activation item, so its entry is
        # written once with its key and its synonyms; the rest are added
        # once every entity is active.
        rewritten = {
            src: self._rewritten_synonyms(reader, src, target, plans[src])
            for src, target in mapping.items()
        }
        for target in targets:
            # Documents were reserved with a deferred claim; their FINAL
            # composite key (built from the rewritten + rehashed row) is
            # claimed now, at activation (CASE-787), together with their
            # synonyms. An identity-less doc has an empty key — it stays
            # keyless (opts out of dedup). Other entity types were already
            # claimed at provision, so they activate keyless (a bare status
            # flip) and get their synonyms afterwards.
            items = self._remap_activation_items(target, mapping, plans, rewritten)
            await self._activate_entries(target, items)
        return rewritten

    def _collect_remap_sources(
        self,
        reader: ArchiveReader,
        mapping: dict[str, str],
        *,
        skip_documents: bool,
        skip_files: bool,
    ) -> list[RemapSource]:
        """Each archived namespace's rows, by type, for the remap plan."""
        skipped = _skipped_types(skip_documents, skip_files)
        remap_sources: list[RemapSource] = []
        for src, target in mapping.items():
            entities_by_type: dict[str, list[dict[str, Any]]] = {}
            document_chains = None
            for entity_type in (*REMAP_ENTITY_ORDER, "term_relations"):
                if entity_type in skipped:
                    continue
                rows, chains = self._remap_type_rows(reader, src, entity_type)
                entities_by_type[entity_type] = rows
                document_chains = chains or document_chains
            remap_sources.append(RemapSource(
                source=src, target=target,
                entities_by_type=entities_by_type,
                document_chains=document_chains,
            ))
        return remap_sources

    def _remap_type_rows(
        self, reader: ArchiveReader, src: str, entity_type: str,
    ) -> tuple[list[dict[str, Any]], Any]:
        """One type's rows of one archived namespace, and for documents the
        stream of their version chains (None when the rows are held whole)."""
        if entity_type == "documents":
            # Documents dominate the archive: plan from one key row
            # per document and stream the chains where they are
            # rewritten and written (CASE-959). An unsorted
            # (pre-sort) archive falls back to holding them.
            try:
                keys = [
                    {k: chain[0][k] for k in _DOCUMENT_KEY_FIELDS if k in chain[0]}
                    for chain in iter_chains(reader, src)
                ]
                return keys, functools.partial(iter_chains, reader, src)
            except UnsortedArchive as e:
                self._emit(
                    "warning",
                    f"[{src}] {e} — the restore holds every document "
                    "in memory; a backup taken with this release streams.",
                )
        rows = []
        for row in reader.read_entities(entity_type, namespace=src):
            row.pop("_id", None)
            rows.append(row)
        return rows, None

    async def _create_remap_targets(
        self,
        targets: list[str],
        mapping: dict[str, str],
        entries: dict[str, Any],
        manifest: Any,
        *,
        dry_run: bool,
    ) -> None:
        """Create (or, in a dry run, ask the Registry about) each target."""
        for target in targets:
            first_src = next(s for s, t in mapping.items() if t == target)
            entry = entries.get(first_src)
            ns_config = (
                entry.namespace_config if entry
                else manifest.namespace_config
            )
            ns_config = self._rewrite_ns_config_refs(ns_config, mapping)
            # Fresh means fresh: a remap re-mints every id, so the target
            # must not inherit the source's id_config. A prefixed source
            # scheme would otherwise re-mint the source's exact ids and
            # collide with the live original on the global entry_id index
            # (CASE-784). preserve_id_config=False defaults the target to
            # UUID7 (or keeps a pre-created target's own config).
            await self._upsert_namespace(
                target, ns_config, preserve_id_config=False, dry_run=dry_run,
            )

    def _report_remap_plans(
        self, mapping: dict[str, str], plans: dict[str, Any], *, dry_run: bool,
    ) -> None:
        """What each source's plan will write, and the documents whose
        identity hash it cannot recompute."""
        phase = "phase_dry_run" if dry_run else "phase_remap"
        for src, target in mapping.items():
            for entity_type, count in plans[src].summary().items():
                if count:
                    self._emit(phase, f"[{src} → {target}] {entity_type}: {count}")
            if unrehashable := plans[src].unrehashable_documents:
                self._emit(
                    "warning",
                    f"[{src} → {target}] {unrehashable} document(s) keep their "
                    "archived identity hash: their template (or a parent it "
                    "extends) is in neither the archive nor this install, or "
                    "their identity values or resolutions cannot be read from "
                    "the archived row. If an identity field names a "
                    "term or document this restore re-minted, the next write of "
                    "that identity creates a second document — rehash the "
                    "namespace (POST /api/document-store/maintenance/"
                    "rehash-identities) once their template exists.",
                )

    @staticmethod
    def _remap_activation_items(
        target: str,
        mapping: dict[str, str],
        plans: dict[str, Any],
        rewritten: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """The activation items of one target: every entry its sources'
        plans minted, a document's with its final key and carried synonyms
        (taken out of ``rewritten``)."""
        items: list[dict[str, Any]] = []
        for src, t in mapping.items():
            if t != target:
                continue
            plan = plans[src]
            doc_keys = plan.final_document_keys(target)
            for m in plan.id_map.values():
                for new_id in m.values():
                    item: dict[str, Any] = {"entry_id": new_id}
                    if new_id in doc_keys:
                        item["composite_key"] = doc_keys[new_id]
                        carried = rewritten[src].pop(new_id, None)
                        if carried:
                            item["synonyms"] = carried
                    items.append(item)
        return items

    async def _record_remap_provenance(
        self,
        archive_path: Path,
        manifest: Any,
        raw_manifest: Any,
        mapping: dict[str, str],
        referents: Any,
    ) -> None:
        """Release what retired documents must not keep, and record where
        each target came from."""
        for target in mapping.values():
            await self._release_retired_synonyms(target)
        for src, target in mapping.items():
            await self._record_provenance(target, src, manifest)
            await self._write_restore_provenance(
                archive_path, manifest, raw_manifest,
                source=src, target=target, mode="remap",
                referents=_referents_for(referents, src),
            )

    def _resolve_remap_mapping(
        self,
        sources: list[str],
        target_namespace: str,
        namespace_map: dict[str, str] | None,
    ) -> dict[str, str]:
        """Settle where every archived namespace goes.

        Without a map, only a single-namespace archive is unambiguous and
        target_namespace names its destination. With a map, EVERY archived
        namespace must be mapped — restoring an unmapped namespace to its old
        name by default would silently collide with the live original, which
        is the exact accident this mode exists to avoid. A target equal to
        the source name is legal precisely when that namespace is absent (the
        per-target emptiness check enforces it). Several sources may share
        one target; key collisions refuse at plan time.
        """
        if namespace_map:
            unknown = sorted(set(namespace_map) - set(sources))
            unmapped = sorted(set(sources) - set(namespace_map))
            if unknown or unmapped:
                problems = []
                if unmapped:
                    problems.append(f"unmapped archive namespace(s) {unmapped}")
                if unknown:
                    problems.append(f"mapped but not in the archive: {unknown}")
                raise RestoreEngineError(
                    "namespace_map must cover exactly the archive's "
                    f"namespaces {sorted(sources)} — " + "; ".join(problems)
                )
            invalid = {s: t for s, t in namespace_map.items() if not t}
            if invalid:
                raise RestoreEngineError(
                    f"namespace_map has empty target(s) for {sorted(invalid)}"
                )
            # Preserve archive order, not caller order — provisioning order
            # and the N:1 first-source-config rule both key off it.
            return {s: namespace_map[s] for s in sources}

        if not target_namespace:
            raise RestoreEngineError(
                "A remap restore needs a target namespace — it mints new "
                "identities, so it must be told where to put them."
            )
        if len(sources) > 1:
            raise RestoreEngineError(
                f"This archive carries {len(sources)} namespaces {sources}. "
                "A remap restore needs an explicit source-to-target mapping "
                "for each one: pass namespace_map."
            )
        return {sources[0]: target_namespace}

    @staticmethod
    def _rewrite_ns_config_refs(
        ns_config: Any, mapping: dict[str, str]
    ) -> Any:
        """allowed_external_refs that name other ARCHIVE namespaces must
        follow them to their new names; names outside the archive (e.g.
        'wip') are kept as-is."""
        if not ns_config:
            return ns_config
        config = dict(ns_config) if isinstance(ns_config, dict) else ns_config
        refs = (
            config.get("allowed_external_refs")
            if isinstance(config, dict)
            else getattr(config, "allowed_external_refs", None)
        )
        if not refs:
            return ns_config
        rewritten = [mapping.get(r, r) for r in refs]
        if isinstance(config, dict):
            config["allowed_external_refs"] = rewritten
            return config
        config.allowed_external_refs = rewritten
        return config

    def _registry_provisioner(self, namespace: str) -> Any:
        """Ask the Registry for ids. It is the identity authority; a service
        minting its own UUIDs would be inventing identity for itself."""
        async def provision(
            entity_type: str, keys: list[dict[str, Any]]
        ) -> list[str]:
            import httpx

            if not keys:
                return []
            url = f"{self._registry_url}/api/registry/entries/provision"
            headers = {
                "X-API-Key": self._registry_api_key,
                "Content-Type": "application/json",
            }
            minted: list[str] = []
            async with httpx.AsyncClient(timeout=120.0) as client:
                # The endpoint caps `count` at PROVISION_BATCH per call, so a
                # namespace-sized type has to be asked for in chunks. Ids come
                # back in request order and are appended in that order, which
                # is what lets the caller zip them against its entities.
                for start in range(0, len(keys), PROVISION_BATCH):
                    chunk = keys[start:start + PROVISION_BATCH]
                    resp = await client.post(
                        url,
                        json={
                            "namespace": namespace,
                            "entity_type": entity_type,
                            "count": len(chunk),
                            "composite_keys": chunk,
                            # Documents defer their claim to activation: an
                            # id-valued-identity document's final hash isn't
                            # known until every referenced id is minted, so the
                            # correct key is claimed at activation instead of
                            # here (CASE-787). Definitions/terms/templates/files
                            # have value-based identities that survive re-minting
                            # unchanged, so they claim at provision as before.
                            "defer_claim": entity_type == "documents",
                        },
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        raise RestoreEngineError(
                            f"Could not provision {len(chunk)} {entity_type} "
                            f"id(s) in '{namespace}' (chunk at offset {start} "
                            f"of {len(keys)}): {resp.status_code} — {resp.text}"
                        )
                    minted.extend(item["entry_id"] for item in resp.json()["ids"])
            return minted

        return provision

    @staticmethod
    def _dry_run_provisioner() -> Any:
        """Factory for stand-in ids, ONE counter per dry run.

        Provisioning writes reserved entries, so a dry run must not call the
        Registry — a preview that leaves rows behind is not a preview.

        Returns a per-target factory (the shape plan_multi expects) whose
        provisioners all share one counter: placeholder ids must be unique
        across every source and entity type in the run, exactly as
        Registry-minted ids are. A counter per source hands two sources'
        terminologies the same placeholder, their terms' Registry keys then
        embed identical parent ids, and an N:1 dry run reports a collision
        the real run would never hit — a preview that refuses what apply
        allows.
        """
        counter = {"n": 0}

        async def provision(
            entity_type: str, keys: list[dict[str, Any]]
        ) -> list[str]:
            ids = []
            for _ in keys:
                counter["n"] += 1
                ids.append(f"would-mint-{counter['n']}")
            return ids

        return lambda _target: provision

    async def _external_template_definitions(
        self,
        reader: ArchiveReader,
        manifest: Manifest,
        remapper: IDRemapper,
        carried: set[str],
    ) -> dict[tuple[str, int], dict[str, Any]]:
        """The identity definitions of templates the archive's documents are
        pinned to but the archive does not carry, read from this install at
        every version it holds (keyed by archived id and version), so each
        document is rehashed against the version it is pinned to.

        Run after ``_map_external_referents``: each such template is read
        under the id it maps to here (its own id when nothing maps it). One
        this install does not hold, or a template store that does not answer,
        leaves the template out — its documents keep their hashes, and the
        plan counts them. Reads are kept for the engine's run, so a merge
        into several targets asks once per template version.
        """
        if manifest.referents is None:
            return {}
        out: dict[tuple[str, int], dict[str, Any]] = {}
        for row in reader.read_referents():
            archived_id = row.get("entry_id")
            if row.get("entity_type") != "templates" or not archived_id or archived_id in carried:
                continue
            here = remapper.template_map.get(archived_id, archived_id)
            for version, template in (await self._external_template_versions(here)).items():
                out[(str(archived_id), version)] = template_definition(template)
        return out

    async def _external_template_versions(self, here: str) -> dict[int, dict[str, Any]]:
        """Every version of a template this install holds, composed; the
        latest alone when the versions cannot be listed. Kept per run."""
        cache = self._external_template_cache
        if here in cache:
            return cache[here]
        client = get_template_store_client()
        try:
            versions: list[int | None] = list(await client.get_template_version_numbers(here))
        except Exception as exc:  # fall back to the latest version below
            logger.warning("Versions of template %s not listable for the rehash: %s", here, exc)
            versions = []
        # Nothing listed (an error, or a template store without the versions
        # route answering 404): the latest version is read once before the
        # template is taken as unknown.
        versions = versions or [None]
        found: dict[int, dict[str, Any]] = {}
        for version in versions:
            try:
                template = await client.get_template_resolved(here, version=version)
            except Exception as exc:  # the documents keep their hashes; counted
                logger.warning("Template %s v%s not readable for the rehash: %s", here, version, exc)
                continue
            if template:
                found[int(template.get("version") or version or 1)] = template
        cache[here] = found
        return found

    async def _map_external_referents(
        self,
        reader: ArchiveReader,
        manifest: Manifest,
        remapper: IDRemapper,
        label: str,
        unresolved_hint: str = "",
    ) -> dict[str, Any]:
        """Point references to entities outside the archive at this install's
        entities (see referent_resolution).

        Feeds the remapper before any row is rewritten, so these references
        are rewritten by the same passes as references to archived entities.
        Every caller runs it first, on a remapper that maps nothing yet; the
        referents are the entities the archive does not carry, so no later
        mapping (an archived entity, a merge definition matched by content)
        names one of them. Returns the summary for the job result. The referents it could not identify are
        left in ``self._unresolved_referents`` for
        ``_report_unresolved_references``, which warns about them.
        """
        self._unresolved_referents = {}
        if manifest.referents is None:
            # A progress message, not a job warning: most restores of an
            # older archive need no repair (a same-install merge, a namespace
            # with no outside references), and a warning on every one of them
            # would teach operators to ignore restore warnings. The result
            # records it, and validation reports any reference that fails.
            self._emit(
                "phase_referents",
                f"[{label}] this archive predates the referent table: references "
                "to entities it does not carry keep their source ids. The "
                "namespace validation job reports any that do not hold here.",
            )
            return {"recorded": False}
        rows = list(reader.read_referents())
        if not rows:
            return {"recorded": True, "total": 0, "resolved": 0, "unresolved": 0}

        resolution = await resolve_referents(rows, self._resolve_on_registry)
        maps = {
            "terminologies": remapper.terminology_map,
            "terms": remapper.term_map,
            "templates": remapper.template_map,
            "documents": remapper.document_map,
            "files": remapper.file_map,
        }
        # A reference to a document snapshots that document's namespace,
        # template and identity hash, so the target document's own values are
        # read here. One the Registry knows but the store does not hold is
        # not a document this restore can point at.
        snapshots = await self._document_snapshots(
            list(resolution.mapping.get("documents", {}).values())
        )
        unresolved: dict[str, _UnresolvedReferent] = {
            u.entry_id: _UnresolvedReferent(u.entity_type, u.describe())
            for u in resolution.unresolved
        }
        mapped = 0
        for entity_type, mapping in resolution.mapping.items():
            existing = maps.get(entity_type)
            if existing is None:
                continue
            for old_id, new_id in mapping.items():
                if entity_type == "documents":
                    snapshot = snapshots.get(new_id)
                    if snapshot is None:
                        unresolved[old_id] = _UnresolvedReferent(
                            "documents",
                            f"documents {old_id} resolves to {new_id}, which "
                            "this install's document store does not hold",
                        )
                        continue
                    remapper.add_external_document_mapping(old_id, new_id, snapshot)
                else:
                    existing[old_id] = new_id
                mapped += 1

        self._emit(
            "phase_referents",
            f"[{label}] {mapped} of {len(rows)} referenced entit(ies) outside "
            "the archive identified on this install",
        )
        self._unresolved_referents = unresolved
        return {
            "recorded": True,
            "total": len(rows),
            "resolved": mapped,
            "unresolved": len(unresolved),
        }

    async def _report_unresolved_references(
        self,
        reader: ArchiveReader,
        sources: list[str],
        remapper: IDRemapper,
        referents: dict[str, Any],
        label: str,
        *,
        drop_edges: bool,
        unresolved_hint: str = "",
    ) -> set[str]:
        """Say which archived documents point at the referents
        ``_map_external_referents`` could not identify, and drop the edges
        among them when asked.

        Each unresolved referent gets one warning naming how many documents
        reference it, and ``referents`` gains ``unresolved_references``: per
        referent and referencing namespace, the count of documents and up to
        _REFERENCE_SAMPLE_CAP (document_id, field_path) samples. A plain
        reference keeps its archived value, so the document is restored as
        archived.

        With ``drop_edges``, an edge document (its template's usage is
        relationship) whose source_ref or target_ref points at an unresolved
        document is not restored, with all its versions and its registry
        entry. Every dropped edge is listed in ``referents['dropped_edges']``,
        in full: a drop removes archived data, so it is recorded, not sampled.
        Returns the dropped document ids.
        """
        unresolved = self._unresolved_referents
        if not unresolved:
            return set()
        edge_templates = (
            await self._edge_template_ids(reader, sources, remapper)
            if drop_edges else set()
        )
        references, dropped = await self._off_loop(
            _scan_unresolved_references, reader, sources, unresolved, edge_templates,
        )
        documents_by_referent: dict[str, int] = {}
        for entry in references:
            documents_by_referent[entry["referent"]] = (
                documents_by_referent.get(entry["referent"], 0) + entry["documents"]
            )
        problems = list(unresolved.items())
        for entry_id, referent in problems[:_REFERENT_WARNING_CAP]:
            count = documents_by_referent.get(entry_id, 0)
            self._emit(
                "warning",
                f"[{label}] referenced {referent.description}; "
                f"{count} archived document(s) reference it and keep the "
                f"archived id{unresolved_hint}",
            )
        hidden = len(problems) - _REFERENT_WARNING_CAP
        if hidden > 0:
            self._emit(
                "warning",
                f"[{label}] … and {hidden} more referenced entit(ies) not "
                "identified on this install",
            )
        if dropped:
            self._emit(
                "warning",
                f"[{label}] {len(dropped)} edge document(s) not restored: an "
                "endpoint is not on this install (drop_unresolved_edges). "
                "The job result and the restore provenance list each one.",
            )
        referents["unresolved_references"] = references
        if drop_edges:
            referents["dropped_edges"] = list(dropped.values())
        return set(dropped)

    async def _edge_template_ids(
        self, reader: ArchiveReader, sources: list[str], remapper: IDRemapper
    ) -> set[str]:
        """Template ids, as archived rows name them, of edge types: the
        archive's own relationship templates, and this install's, under the
        archived id a referent mapping gives them."""
        ids = {
            str(row["template_id"])
            for src in sources
            for row in reader.read_entities("templates", namespace=src)
            if row.get("usage") == "relationship" and row.get("template_id")
        }
        db_name, coll_name = COLLECTION_MAP["templates"]
        here = {
            str(tid)
            for tid in await self._mongo[db_name][coll_name].distinct(
                "template_id", {"usage": "relationship"}
            )
            if tid
        }
        ids |= here
        ids |= {old for old, new in remapper.template_map.items() if new in here}
        return ids

    async def _document_snapshots(
        self, document_ids: list[str]
    ) -> dict[str, dict[str, str]]:
        """{document_id: {namespace, template_id, identity_hash}} of the
        target's documents, from each one's latest version."""
        if not document_ids:
            return {}
        db_name, coll_name = COLLECTION_MAP["documents"]
        cursor = self._mongo[db_name][coll_name].find(
            {"document_id": {"$in": document_ids}},
            {"_id": 0, "document_id": 1, "namespace": 1, "template_id": 1,
             "identity_hash": 1, "version": 1},
        ).sort([("document_id", 1), ("version", -1)])
        snapshots: dict[str, dict[str, str]] = {}
        async for row in cursor:
            snapshots.setdefault(row["document_id"], {
                key: row[key]
                for key in ("namespace", "template_id", "identity_hash")
                if row.get(key) is not None
            })
        return snapshots

    async def _resolve_on_registry(
        self, items: list[dict[str, Any]]
    ) -> list[str | None]:
        """POST resolve items to the Registry; per item, the entry_id found.

        A read, so a dry run may call it: the preview then shows exactly
        which referents the apply would map.
        """
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._registry_url}/api/registry/entries/resolve",
                json=items,
                headers={
                    "X-API-Key": self._registry_api_key,
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            raise RestoreEngineError(
                f"Could not resolve {len(items)} referent key(s) on the "
                f"Registry: {resp.status_code} — {resp.text}"
            )
        return [
            r.get("entry_id") if r.get("status") == "found" else None
            for r in resp.json()["results"]
        ]

    async def _repair_endpoint_declarations(
        self,
        mapping: dict[str, str],
        plans: dict[str, RemapPlan],
        remapper: IDRemapper,
    ) -> None:
        """Make every edge type's declared endpoints honest on the target.

        Runs after the remap pass, before rows are written. Three cases per
        entry of a relationship template's ``source_templates`` /
        ``target_templates``:

        - **Covered by the id map** — the endpoint is in the archive; its
          resolved half already names the target's re-minted id. Untouched.
        - **In the archive by value** — a legacy bare-string entry (pre-3.1
          archive) naming an archived template by value. The Registry cannot
          answer for these yet (provisioned identities are *reserved*, and
          reserved does not resolve), so they resolve against the archive's
          own value index and are upgraded to two-half entries.
        - **Out of archive** — no map entry can exist (the entity was never
          re-registered), so the lookup half is the only mechanism: check it
          resolves on the target install, and repair the resolved half to the
          target's canonical id. If it does not resolve, the resolved half is
          cleared and a warning lands on the job — a stale source-install id
          kept in a fresh namespace is a leak, and enforcement still runs
          from the lookup half (the `latest` strategy resolves it per write).
        """
        new_ids = set(remapper.template_map.values())

        # Per-target value index over the archive's own (post-remap) template
        # rows: value → new template_id, plus each row's new id keyed to
        # itself for id-form legacy strings the map already rewrote.
        value_index: dict[str, dict[str, str]] = {}
        for src, target in mapping.items():
            idx = value_index.setdefault(target, {})
            for row in plans[src].rows.get("templates") or []:
                if row.get("value") and row.get("template_id"):
                    idx.setdefault(row["value"], row["template_id"])
                    # An id-form legacy string was already rewritten to the
                    # new id by the reference-string pass — key it to itself
                    # so it recognizes as in-archive here.
                    idx.setdefault(row["template_id"], row["template_id"])

        for src, target in mapping.items():
            for row in plans[src].rows.get("templates") or []:
                if row.get("usage") != "relationship":
                    continue
                for prop in ("source_templates", "target_templates"):
                    entries = row.get(prop) or []
                    repaired: list[dict[str, Any]] = []
                    for item in entries:
                        # A legacy bare string is upgraded to a two-half
                        # entry on the way through the door.
                        entry = (
                            {"lookup_value": item, "resolved": None}
                            if isinstance(item, str) else dict(item)
                        )
                        resolved = entry.get("resolved")
                        if resolved and resolved in new_ids:
                            repaired.append(entry)
                            continue
                        lookup = entry.get("lookup_value")
                        if not isinstance(lookup, str) or not lookup:
                            # Malformed entry — nothing to resolve from;
                            # clear the resolved half rather than leak.
                            entry["resolved"] = None
                            repaired.append(entry)
                            continue
                        entry["lookup_value"] = lookup
                        hit, why = await self._resolve_endpoint_on_target(value_index, target, lookup)
                        entry["resolved"] = hit
                        if hit is None:
                            self._emit(
                                "warning",
                                f"[{target}] edge type '{row.get('value')}' "
                                f"endpoint '{entry['lookup_value']}' {why} — "
                                "resolved half cleared; the lookup anchor is "
                                "kept and enforcement falls back to resolving "
                                "it per write.",
                            )
                        repaired.append(entry)
                    if entries:
                        row[prop] = repaired

    @staticmethod
    async def _resolve_endpoint_on_target(
        value_index: dict[str, dict[str, str]], target: str, lookup: str,
    ) -> tuple[str | None, str | None]:
        """An edge endpoint's id on the target, or None with why it has none.

        In-archive first (reserved identities are invisible to the Registry
        until activation): a bare value → this target's index; qualified →
        the named (already-remapped) namespace's index. Out of archive it is
        resolved on the target the way every write does, through the shared
        resolver — a qualified 'ns:VALUE' names its own namespace, a value
        or id is read in the target's — bypassing the cache, since the
        result is persisted.

        Only an answer from the Registry clears an endpoint: "not found", or
        "several templates carry it" (ambiguous). A Registry that does not
        answer is no answer — the restore fails rather than persist an
        outage as a cleared endpoint and blame the endpoint for it.
        """
        hit = value_index.get(target, {}).get(lookup)
        if hit:
            return hit, None
        if ":" in lookup:
            ns_half, rest = lookup.split(":", 1)
            hit = value_index.get(ns_half, {}).get(rest)
            if hit:
                return hit, None
        from wip_auth.registry_client import RegistryUnavailableError
        from wip_auth.resolve import AmbiguousEntityError, EntityNotFoundError, resolve_entity_id

        try:
            return await resolve_entity_id(lookup, "template", target, bypass_cache=True), None
        except AmbiguousEntityError as exc:
            return None, f"is ambiguous on this install ({exc.total} templates carry it)"
        except EntityNotFoundError:
            return None, "does not resolve on this install"
        except RegistryUnavailableError as exc:
            raise RestoreEngineError(
                f"[{target}] edge-type endpoint '{lookup}' could not be checked: the "
                f"Registry did not answer ({exc}). Nothing was cleared; the target "
                "namespace was already provisioned, so a fresh restore needs it "
                "deleted before a retry."
            ) from exc

    async def _write_remapped(
        self, namespace: str, plan: RemapPlan, batch_size: int
    ) -> None:
        """Insert the rewritten rows, in dependency order.

        Registry entries are absent on purpose: provisioning created them, so
        writing the archive's would duplicate every identity under its old id.
        """
        for entity_type in (*REMAP_ENTITY_ORDER, "term_relations"):
            db_name, coll_name = COLLECTION_MAP[entity_type]
            collection = self._mongo[db_name][coll_name]
            if entity_type == "documents" and plan.document_stream is not None:
                written = 0
                batch: list[dict[str, Any]] = []
                for row in plan.iter_documents():
                    batch.append(row)
                    if len(batch) >= batch_size:
                        await self._insert_batch(collection, batch, entity_type)
                        written += len(batch)
                        batch = []
                if batch:
                    await self._insert_batch(collection, batch, entity_type)
                    written += len(batch)
                if written:
                    self._emit(
                        f"phase_{entity_type}",
                        f"[{namespace}] wrote {written} {entity_type} under new ids",
                    )
                continue
            rows = plan.rows.get(entity_type) or []
            if not rows:
                continue
            for start in range(0, len(rows), batch_size):
                await self._insert_batch(
                    collection, rows[start:start + batch_size], entity_type
                )
            self._emit(
                f"phase_{entity_type}",
                f"[{namespace}] wrote {len(rows)} {entity_type} under new ids",
            )

    async def _remap_blobs(
        self, reader: ArchiveReader, file_id_map: dict[str, str]
    ) -> None:
        """Copy each blob under its file's new id.

        The storage key follows the file id, so re-minting the id means the
        bytes need a new home rather than a second reference to the old one —
        which would leave two files sharing one object and either one able to
        delete it. A file the archive carries no bytes for has none under its
        new id either, and is named on the job with the ones whose bytes do
        not match their checksum.
        """
        assert self._storage is not None
        if not file_id_map:
            return
        self._emit("phase_blobs", f"Copying {len(file_id_map)} blob(s)")
        checksums = await self._off_loop(self._archive_file_checksums, reader)
        blobs = set(reader.list_blobs())
        without: dict[str, list[str]] = {
            "missing": [new for old, new in file_id_map.items() if old not in blobs],
            "mismatched": [],
        }
        carried = {old: new for old, new in file_id_map.items() if old in blobs}
        copied = await self._copy_blobs_under_new_ids(reader, carried, checksums, without)
        self._emit_restored_without_bytes(reader, without)
        logger.info("Copied %d blob(s) under new ids", copied)

    async def _copy_blobs_under_new_ids(
        self,
        reader: ArchiveReader,
        carried: dict[str, str],
        checksums: dict[str, str | None],
        without: dict[str, list[str]],
    ) -> int:
        """Upload each carried blob (old id → new id) under its new id; the
        ones not uploaded for a reason go into ``without`` by their new id.
        Returns how many were uploaded."""
        assert self._storage is not None
        present = await self._blob_keys_holding_archive_bytes(
            reader, {new_id: old_id for old_id, new_id in carried.items()},
        )
        copied = 0
        for old_id, new_id in carried.items():
            if new_id in present:
                continue
            data, reason = await self._off_loop(
                self._checked_blob, reader, old_id, checksums.get(old_id),
            )
            if data is None:
                without[reason or "missing"].append(new_id)
                continue
            await self._storage.upload(
                storage_key=new_id,
                content=data,
                content_type="application/octet-stream",
            )
            copied += 1
        return copied

    async def _activate_entries(
        self, namespace: str, items: list[dict[str, Any]]
    ) -> None:
        """Make the provisioned entries resolvable, in one step.

        Until this runs the namespace is invisible: reserved entries do not
        resolve. That is the property that makes a failed remap recoverable
        rather than half-live.

        Each item is ``{entry_id, composite_key?}``. A document carries its
        FINAL composite key here — its claim was deferred at provision and is
        committed now, when the rehashed key is known (CASE-787). A collision
        at this point is a genuine duplicate identity: it fails loud rather than
        leaving a written-but-unresolvable document behind.
        """
        import httpx

        if not items:
            return
        self._emit(
            "phase_activate",
            f"[{namespace}] activating {len(items)} identit(ies)",
        )
        url = f"{self._registry_url}/api/registry/entries/activate"
        headers = {
            "X-API-Key": self._registry_api_key,
            "Content-Type": "application/json",
        }
        # Activation goes out in batches, and an earlier batch stays active when
        # a later one fails: the job must say exactly what is live, never that
        # the namespace is still invisible.
        progress = self._activation.setdefault(
            namespace, {"total": len(items), "active": 0, "failed": 0}
        )
        progress["total"] = len(items)
        async with httpx.AsyncClient(timeout=120.0) as client:
            for start in range(0, len(items), 500):
                batch = items[start:start + 500]
                resp = await client.post(url, json=batch, headers=headers)
                if resp.status_code != 200:
                    self._raise_activation_failure(
                        namespace,
                        f"the Registry answered {resp.status_code} for a batch of "
                        f"{len(batch)} — {resp.text[:300]}",
                    )
                body = resp.json()
                results = body.get("results", [])
                live = sum(
                    1 for r in results if r.get("status") in ("activated", "already_active")
                )
                progress["active"] += live
                if body.get("errors", 0):
                    failed = [
                        r for r in results
                        if r.get("status") not in ("activated", "already_active")
                    ]
                    progress["failed"] += len(failed)
                    self._raise_activation_failure(namespace, _activation_failure_cause(failed))

    def _raise_activation_failure(self, namespace: str, cause: str) -> None:
        """Stop a fresh restore whose activation failed, saying what is live.

        Activation is the step that makes restored identities resolvable, and
        it runs in batches, target by target: by the time one fails, earlier
        batches (and earlier targets) are already live. The error says so per
        target, and the job record carries the counts.
        """
        lines = []
        for target, counts in self._activation.items():
            pending = counts["total"] - counts["active"] - counts["failed"]
            lines.append(
                f"'{target}': {counts['active']} of {counts['total']} active"
                + (f", {counts['failed']} failed" if counts["failed"] else "")
                + (f", {pending} not activated" if pending else "")
            )
        targets = ", ".join(f"'{t}'" for t in self._activation)
        raise RestoreEngineError(
            f"Activation failed in '{namespace}': {cause}. What is live now — "
            f"{'; '.join(lines)}. Restored identities that are active resolve; "
            f"the rest do not. Delete the target namespace(s) ({targets}) and "
            "retry the restore.",
            details={
                "result_kind": "activation_failed",
                "namespace": namespace,
                "activation": {t: dict(c) for t, c in self._activation.items()},
            },
        )

    def _rewritten_synonyms(
        self, reader: Any, src: str, target: str, plan: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        """The archived synonyms of ``src``, rewritten for their re-minted
        owners in ``target``: ``{new_id: [synonym item, …]}`` (CASE-792).

        The fresh path re-mints every entity, and the value-form lookup
        synonyms the create services auto-register ({ns, type, value} for a
        terminology, the template-qualified identity values for a document, …)
        plus any custom synonyms live only in the archive's registry_entries.
        Without them a fresh-restored entity would resolve only by canonical
        id, never by value (Vision: every synonym must resolve identically to
        the canonical id).

        Each synonym is rewritten for the target — namespace, any id-valued key
        part via the id_map, and (for a document whose identity hash was
        recomputed during remap) the identity_hash. A synonym the archive holds
        inactive was released when its entity was retired; claiming it again
        could fail the restore if another archived entity took the key since,
        so it is not carried over.
        """
        flat_id_map: dict[str, str] = {}
        for m in plan.id_map.values():
            flat_id_map.update(m)
        # A document claims its (possibly recomputed) identity hash at
        # activation; its synonym must carry the SAME new hash, not the
        # archived pre-remap one (id-valued identity fields — edges — rehash).
        new_doc_hash: dict[str, str] = {
            doc_id: key["identity_hash"]
            for doc_id, key in plan.final_document_keys(target).items()
            if key.get("identity_hash")
        }

        syn_by_old: dict[str, list[dict[str, Any]]] = {}
        for entry in reader.read_entities("registry_entries", namespace=src):
            syns = entry.get("synonyms")
            if isinstance(syns, list) and syns:
                syn_by_old[entry.get("entry_id")] = [
                    s for s in syns
                    if not (isinstance(s, dict) and s.get("status") == "inactive")
                ]

        by_new: dict[str, list[dict[str, Any]]] = {}
        for id_map in plan.id_map.values():
            for old_id, new_id in id_map.items():
                for syn in syn_by_old.get(old_id, []):
                    item = self._rewrite_synonym_for_restore(
                        syn, src, target, flat_id_map, new_doc_hash.get(new_id)
                    )
                    if item is not None:
                        by_new.setdefault(new_id, []).append(
                            {"created_by": "restore-remap", **item}
                        )
        return by_new

    def _refuse_foreign_scoped_keys(
        self, reader: Any, mapping: dict[str, str], skipped_types: set[str] | None = None,
    ) -> None:
        """Refuse, before anything is provisioned, an archive carrying a
        synonym key that names a namespace other than its entry's. The
        entries of a type the restore leaves out (``skipped_types``:
        documents or files) are not restored, so their keys do not count.

        The Registry refuses such a key (a key's `ns` must be its entry's
        namespace, unless the key is system-qualified with an `@` member).
        Archives taken before that was enforced can carry one; found at the
        synonym step, it failed the restore after the target was written and
        activated. A dry run reports it too.
        """
        foreign = [
            f"{entry.get('entry_id')}: {key}"
            for src in mapping
            for entry in reader.read_entities("registry_entries", namespace=src)
            if entry.get("entity_type") not in (skipped_types or ())
            for key in _foreign_keys(entry, src)
        ]
        if foreign:
            raise RestoreEngineError(
                f"{len(foreign)} archived synonym key(s) name a namespace other than "
                f"their entry's, which the Registry refuses: {'; '.join(foreign[:5])}"
                f"{' …' if len(foreign) > 5 else ''}. Nothing was written. Remove or "
                "re-key these synonyms on the source install and take a new backup.",
                details={"rule": "foreign_scoped_synonym", "keys": foreign[:50]},
            )

    async def _restore_synonyms(
        self,
        mapping: dict[str, str],
        rewritten: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> None:
        """Register the rewritten synonyms activation did not carry, through
        /synonyms/add, which claims each key exactly as original creation did.
        """
        for src, target in mapping.items():
            items = [
                {"target_id": new_id, **syn}
                for new_id, syns in rewritten.get(src, {}).items()
                for syn in syns
            ]
            if items:
                await self._add_synonyms(target, items)

    @staticmethod
    def _rewrite_synonym_for_restore(
        syn: dict[str, Any],
        source: str,
        target: str,
        flat_id_map: dict[str, str],
        new_identity_hash: str | None,
    ) -> dict[str, Any] | None:
        """Rewrite one archived synonym for its re-minted owner in the target."""
        if not isinstance(syn, dict):
            return None
        key = syn.get("composite_key")
        entity_type = syn.get("entity_type")
        if not isinstance(key, dict) or not entity_type:
            return None
        new_key: dict[str, Any] = {}
        for k, v in key.items():
            if k == "ns" and v == source:
                new_key[k] = target
            elif k == "identity_hash" and new_identity_hash is not None:
                new_key[k] = new_identity_hash
            elif isinstance(v, str) and v in flat_id_map:
                new_key[k] = flat_id_map[v]
            else:
                new_key[k] = v
        # The scope fields are left out: a carried-over synonym belongs to
        # its own entry, and the Registry gives an omitted scope the entry's.
        # An archive from before scope was enforced can carry a foreign
        # scope; sending it through made the Registry refuse the synonym
        # after the target was already written and activated.
        return {"synonym_composite_key": new_key}

    async def _retired_entry_ids(self, namespace: str) -> list[str]:
        """The Registry entry IDs of the namespace's retired entities: documents
        and templates with no active version, inactive terminologies, inactive
        or deprecated terms."""
        ids: list[str] = []
        for entity_type, id_field in (("documents", "document_id"), ("templates", "template_id")):
            db_name, coll_name = COLLECTION_MAP[entity_type]
            async for row in self._mongo[db_name][coll_name].aggregate([
                {"$match": {"namespace": namespace}},
                {"$group": {
                    "_id": f"${id_field}",
                    "active": {"$max": {"$cond": [{"$eq": ["$status", "active"]}, 1, 0]}},
                }},
                {"$match": {"active": 0}},
            ], allowDiskUse=True):
                ids.append(row["_id"])
        for entity_type, id_field, statuses in (
            ("terminologies", "terminology_id", ["inactive"]),
            ("terms", "term_id", ["inactive", "deprecated"]),
        ):
            db_name, coll_name = COLLECTION_MAP[entity_type]
            async for row in self._mongo[db_name][coll_name].find(
                {"namespace": namespace, "status": {"$in": statuses}}, {id_field: 1},
            ):
                ids.append(row[id_field])
        return ids

    async def _release_retired_synonyms(self, namespace: str) -> None:
        """Release the synonyms of the entities the restored namespace holds
        retired, as their stores would have when retiring them.

        Rows arrive by direct writes, so no store saw a retirement. Archives
        taken before retirement released synonyms carry every synonym active,
        and the claim rebuild / synonym re-add claimed them: a retired entity
        would again block another identity with the same spellings. Idempotent
        — an entry with nothing left to release is unchanged. A failure is a
        warning: the data is committed, and each store's
        /maintenance/reconcile-synonym-lifecycle pass repairs it.
        """
        import httpx

        url = f"{self._registry_url}/api/registry/entries/deactivate"
        headers = {"X-API-Key": self._registry_api_key, "Content-Type": "application/json"}
        ids: list[str] = []
        released = 0
        failed: list[dict[str, Any]] = []
        try:
            ids = await self._retired_entry_ids(namespace)
            if not ids:
                return
            async with httpx.AsyncClient(timeout=120.0) as client:
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    resp = await client.post(
                        url, json=[{"entry_id": i, "updated_by": "restore"} for i in batch],
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        raise RestoreEngineError(f"{resp.status_code} — {resp.text}")
                    done, not_done = _release_outcome(resp.json().get("results", []))
                    released += done
                    failed += not_done
        except Exception as e:
            self._emit(
                "warning",
                f"[{namespace}] the synonyms of {len(ids) or 'the'} retired entities could "
                f"not be released ({e}); they still block other identities with the same "
                "keys until each store's /maintenance/reconcile-synonym-lifecycle runs",
            )
            return
        self._warn_unreleased(namespace, failed)
        if released:
            self._emit(
                "phase_synonyms",
                f"[{namespace}] released the synonyms of {released} retired entities",
            )

    def _warn_unreleased(self, namespace: str, failed: list[dict[str, Any]]) -> None:
        """Name the retired entities whose synonyms the Registry could not
        release (its per-item answers inside a 200). An entity with no
        Registry entry (not_found) has no synonyms to release and blocks
        nothing, so it is named apart, without the repair advice."""
        absent = [r for r in failed if r.get("status") == "not_found"]
        blocked = [r for r in failed if r.get("status") != "not_found"]
        if absent:
            self._emit(
                "warning",
                f"[{namespace}] {len(absent)} retired entities have no Registry entry "
                f"({_sample_ids(absent)}); there was nothing of theirs to release",
            )
        if blocked:
            self._emit(
                "warning",
                f"[{namespace}] the synonyms of {len(blocked)} retired entities could not be "
                f"released ({_sample_ids(blocked)}); they still block other identities with "
                "the same keys until each store's /maintenance/reconcile-synonym-lifecycle runs",
            )

    async def _add_synonyms(
        self, namespace: str, items: list[dict[str, Any]]
    ) -> None:
        """Register carried-over synonyms; /synonyms/add claims each key (CASE-792).

        The entries are already active (activation ran first), which /synonyms/add
        requires. Idempotent (already_exists) so a re-run converges; a genuine
        key collision fails loud rather than leaving an unresolvable synonym.
        """
        import httpx

        if not items:
            return
        self._emit(
            "phase_synonyms",
            f"[{namespace}] restoring {len(items)} synonym(s)",
        )
        url = f"{self._registry_url}/api/registry/synonyms/add"
        headers = {
            "X-API-Key": self._registry_api_key,
            "Content-Type": "application/json",
        }
        ok_statuses = {"added", "already_exists"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            for start in range(0, len(items), 500):
                batch = items[start:start + 500]
                resp = await client.post(url, json=batch, headers=headers)
                if resp.status_code != 200:
                    raise RestoreEngineError(
                        f"Could not restore {len(batch)} synonym(s) in "
                        f"'{namespace}': {resp.status_code} — {resp.text}"
                    )
                failed = [
                    r for r in resp.json().get("results", [])
                    if r.get("status") not in ok_statuses
                ]
                if failed:
                    raise RestoreEngineError(
                        f"[{namespace}] {len(failed)} synonym(s) failed to "
                        f"restore (a carried synonym could not claim its key). "
                        f"First failures: {failed[:5]}"
                    )

    async def _refuse_bare_identity_synonyms(self, reader: Any, manifest: Any) -> None:
        """Refuse an archive that carries document identity synonyms in the
        old, bare form, before anything is written. Every restore mode.

        A document's identity values are registered as a synonym qualified
        by its template ({"@template": <template value>, <field>: <value>,
        …}). Archives taken before that carry them bare ({<field>: <value>,
        …}), which claims the spelling for every template in the namespace.
        The bare form has no use any more and is not brought forward: a
        restore that wrote it would put it back into an install. A synonym is
        bare when its key set is exactly its template's effective identity:
        its own identity_fields, else the nearest ancestor's along
        ``extends`` (wip_auth.template_identity.effective_identity_fields).
        Templates are read from the archive, and one it does not carry from
        this install's template-store, as the Registry's synonym doors read
        it: the restore must refuse up front whatever the Registry would
        refuse halfway through. A document whose template is found nowhere
        cannot be judged and is not counted.

        The Registry refuses such a synonym at every door that writes one
        (add, register, activate, merge); an archive can still carry one
        from before that, or from a source install whose template-store
        could not be read when it was written.
        """
        sources = self._archive_namespaces(reader, manifest)
        entries = [
            (src, entry) for src in sources
            for entry in reader.read_entities("registry_entries", namespace=src)
            if entry.get("entity_type") == "documents"
        ]
        rows = _archived_template_rows(reader, sources)
        await self._add_live_template_rows(rows, {
            tid for _src, entry in entries if (tid := _entry_template_id(entry))
        })
        identity = {tid: set(f) for tid in rows if (f := effective_identity_fields(tid, rows))}
        counts, examples = _bare_identity_synonyms(entries, identity)
        if counts:
            listed = ", ".join(f"{ns}: {n}" for ns, n in sorted(counts.items()))
            raise RestoreEngineError(
                "This archive carries document synonyms whose keys are exactly their "
                f"template's identity fields, without '@template' ({listed} document(s); "
                f"e.g. {'; '.join(examples[:3])}). That is the old bare form of identity "
                "synonyms, which is not restored: it claims the spelling for every "
                "template in the namespace. If the archive was taken before identity "
                "synonyms were qualified, restore it with an earlier WIP release, which "
                "re-qualifies them, then take a new backup there. If it was taken by "
                "this release, a caller added these synonyms: remove them, or give them "
                "a key that is not exactly the identity fields, on the source install, "
                "then take a new backup."
            )

    async def _add_live_template_rows(
        self, rows: dict[str, TemplateIdentityRow], template_ids: set[str],
    ) -> None:
        """Add to ``rows`` the templates, and the ancestors their identity
        walk needs, that the archive does not carry, read from this install's
        template-store level by level (the Registry's doors read the same)."""
        if self._mongo is None:
            return
        db_name, coll_name = COLLECTION_MAP["templates"]
        templates = self._mongo[db_name][coll_name]
        wanted = template_ids
        for _ in range(10):  # inheritance chains are shallow; a cycle stops here
            missing = sorted(wanted - rows.keys())
            if not missing:
                return
            async for row in templates.find(
                {"template_id": {"$in": missing}},
                {"template_id": 1, "identity_fields": 1, "extends": 1},
            ):
                rows.setdefault(row["template_id"], (row.get("identity_fields") or [], row.get("extends")))
            wanted = {parent for fields, parent in rows.values() if not fields and parent}

    def _check_derived_archive(
        self, raw_manifest: Any, *, allow_failed_verify: bool
    ) -> None:
        """Gate an archive that declares itself derived (``derived_from``).

        An archive transform verifies its own output and records the verdict
        in the newest hop of the chain. A ``fail`` verdict means the archive
        is known to be broken, so it is refused unless the request accepts it
        with ``allow_failed_verify``. Every other derived archive — ``pass``,
        ``warn``, an unknown verdict, a hop without one — is deliberately
        incomplete and gets a warning. Only the newest hop counts: it
        verified the bytes being restored. Runs before anything is written,
        dry runs included, in every restore mode.
        """
        raw = raw_manifest.get("derived_from") if isinstance(raw_manifest, dict) else None
        chain = _derived_chain(raw)
        if not chain:
            return
        newest = chain[-1]
        verify = newest.get("verify")
        verdict = verify.get("verdict") if isinstance(verify, dict) else None
        transform = newest.get("transform") or "an unnamed transform"
        if verdict == "fail":
            findings = verify.get("findings") if isinstance(verify, dict) else None
            counts = (
                ", ".join(f"{k}={v}" for k, v in findings.items())
                if isinstance(findings, dict) and findings
                else "no counts recorded"
            )
            produced_by = (
                f"'{transform}' (toolkit {newest['toolkit_version']})"
                if newest.get("toolkit_version")
                else f"'{transform}'"
            )
            if not allow_failed_verify:
                raise RestoreEngineError(
                    f"Archive failed its own verification: the {produced_by} "
                    f"transform that wrote it recorded verdict 'fail' "
                    f"({counts}). It is known to be broken and is not restored. "
                    "Inspect it with `wip-toolkit inspect`, or set "
                    "allow_failed_verify=true to restore it anyway."
                )
            self._accepted_failed_verify = True
            self._emit(
                "warning",
                f"Archive failed its own verification ({produced_by}, "
                f"verdict 'fail', {counts}) — restoring it anyway because "
                "allow_failed_verify=true.",
            )
            return
        self._emit(
            "warning",
            f"Archive declares itself DERIVED from a full backup (newest step: "
            f"'{transform}', verify verdict {verdict!r}) — its contents are "
            "deliberately incomplete; verify the restored namespace covers "
            "what you expect.",
        )

    async def _write_restore_provenance(
        self,
        archive_path: Any,
        manifest: Any,
        raw_manifest: Any,
        *,
        source: str,
        target: str,
        mode: str,
        merge: dict[str, Any] | None = None,
        referents: dict[str, Any] | None = None,
    ) -> None:
        """Record that this run restored ``source`` into ``target``.

        Every restore mode writes one, the id-preserving one included: the
        record is a row about the restore, not a change to any restored
        entity, so the exact-copy contract holds. The archive's own records
        for ``source`` are copied in first, marked inherited, so the target
        carries the whole history. A record the target already holds (a
        merge of a backup of itself) is not copied twice.

        A restore that wrote its data must not fail over its provenance: a
        failed write is a loud warning, not an error.
        """
        if self._archive_sha256 is None:
            self._archive_sha256 = await asyncio.to_thread(_file_sha256, archive_path)

        entry = next(
            (e for e in getattr(manifest, "namespaces", []) if e.prefix == source),
            None,
        )
        inherited = list(getattr(entry, "provenance", None) or [])
        exported = _recorded_export_time(manifest)
        record: dict[str, Any] = {
            "namespace": target,
            "restored_into": target,
            "restore_job_id": self._job_id,
            "mode": mode,
            "at": datetime.now(UTC),
            "actor": self._actor,
            "archive": {
                "sha256": self._archive_sha256,
                "format_version": getattr(manifest, "format_version", None),
                "tool_version": getattr(manifest, "tool_version", None),
            },
            "source": {
                "namespace": source,
                "host": getattr(manifest, "source_host", None) or None,
                "exported_at": exported.isoformat() if exported else None,
                "install": getattr(manifest, "source_install", None),
            },
            "derived_from": _derived_chain(
                raw_manifest.get("derived_from") if isinstance(raw_manifest, dict) else None
            ),
            "merge": merge,
            "referents": referents,
            "validation_job_id": None,
            "inherited": False,
        }
        # Present only when the restore overrode a failed verify verdict;
        # absent means it did not (the chain above carries the verdict).
        if self._accepted_failed_verify:
            record["accepted_failed_verify"] = True

        db_name, coll_name = PROVENANCE_COLLECTION
        collection = self._mongo[db_name][coll_name]
        try:
            held = {
                (row.get("restore_job_id"), row.get("restored_into"))
                async for row in collection.find(
                    {"namespace": target},
                    {"_id": 0, "restore_job_id": 1, "restored_into": 1},
                )
            }
            rows: list[dict[str, Any]] = []
            for old in inherited:
                row = {k: v for k, v in old.items() if k != "_id"}
                key = (row.get("restore_job_id"), row.get("restored_into"))
                if key in held:
                    continue
                held.add(key)
                row["namespace"] = target
                row.setdefault("restored_into", source)
                row["inherited"] = True
                # Archived as JSON, so the timestamp is an ISO string; store
                # it as a date like every live record, or ordering by `at`
                # would split the two types.
                if isinstance(row.get("at"), str):
                    with contextlib.suppress(ValueError):
                        row["at"] = datetime.fromisoformat(row["at"])
                rows.append(row)
            rows.append(record)
            await collection.insert_many(rows, ordered=True)
        except Exception as exc:
            logger.warning(
                "Restore provenance for %s not recorded", target, exc_info=True,
            )
            self._emit(
                "warning",
                f"[{target}] restore provenance could not be recorded ({exc}) — "
                "the data is restored, but this restore leaves no record",
            )
            return
        self._emit(
            "phase_namespace",
            f"[{target}] recorded restore provenance ({mode} from '{source}'"
            + (f", {len(rows) - 1} inherited record(s)" if len(rows) > 1 else "")
            + ")",
        )

    async def _record_provenance(
        self, namespace: str, source: str, manifest: Any
    ) -> None:
        """Note where a remapped namespace came from, on the namespace itself.

        Per-entity lineage was rejected: a synonym asserts that two ids denote
        the same entity, and a re-minted copy is a fork that diverges on the
        first write. Provenance belongs to the namespace, which is one line
        that cannot drift — and it travels forward, since a later backup
        captures the description into its own manifest.

        Written after the namespace upsert, which sets the description from
        the manifest and would otherwise overwrite it.
        """
        exported = _recorded_export_time(manifest)
        host = getattr(manifest, "source_host", None) or "unknown host"
        note = (
            f"Restored {datetime.now(UTC).date().isoformat()} from an archive "
            f"of '{source}' taken {exported or 'at an unrecorded time'} on {host}."
        )
        collection = self._mongo[_DB_REGISTRY]["namespaces"]
        live = await collection.find_one({"prefix": namespace})
        existing = (live or {}).get("description") or ""
        result = await collection.update_one(
            {"prefix": namespace},
            {"$set": {"description": f"{existing} {note}".strip()}},
        )
        if not getattr(result, "matched_count", 1):
            # The namespace upsert should have created this row. Losing the
            # provenance line is not worth failing a completed restore over,
            # but it should not vanish quietly either.
            self._emit(
                "warning",
                f"[{namespace}] could not record restore provenance — no "
                "namespace record to write it to",
            )
            return
        self._emit("phase_namespace", f"[{namespace}] {note}")

    async def run_merge(
        self,
        archive_path: Path,
        target_namespace: str,
        *,
        on_clash: str = "skip",
        older_versions: str = "retire",
        add_missing: bool = False,
        extend_terminologies: bool = False,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        dry_run: bool = False,
        drop_unresolved_edges: bool = False,
        allow_failed_verify: bool = False,
        align_templates: bool = False,
        carry_retirements: bool = False,
    ) -> None:
        """Merge an archive into an existing, possibly non-empty namespace.

        Two passes. The first checks that the two sides' definitions —
        terminologies, terms and templates — are compatible by content, and
        produces the ID mapping that says which of them are the same thing
        under different IDs. Only then do documents move, against a schema both
        sides are known to agree on.

        Definitions are a precondition, not a policy: a mismatch refuses.
        ``add_missing`` and ``extend_terminologies`` are the two opt-ins that
        let the merge extend the target's definitions instead of refusing,
        and ``carry_retirements`` the one that lets it retire the target's
        active terms the archive retired. With all three off, a merge never
        changes the target's definitions — neither its schema nor a term's
        lifecycle: a term the archive retired stays active and is reported
        as kept.

        Documents get the policy: ``on_clash`` decides what happens where the
        target already holds an identity, and ``older_versions`` whether the
        target's versions under an appended one are retired (the default) or
        stay active.

        ``align_templates`` matches templates by value instead of by
        ``(value, version)``: each archive version maps onto the target
        version whose schema can take its documents, documents are re-pinned
        accordingly, and a document whose versions map nowhere is listed and
        left out (docs/design/merge-template-version-alignment.md).

        Both passes, and the reference check, run for every namespace in the
        archive before anything is written to any of them, so a refusal never
        follows a write and ``dry_run`` is exact rather than indicative: it
        fails on everything a real run would refuse, and nothing else.
        """
        self._check_merge_policies(on_clash, older_versions)
        policy = {
            "on_clash": on_clash,
            "older_versions": older_versions,
            "add_missing": add_missing,
            "extend_terminologies": extend_terminologies,
            "align_templates": align_templates,
            "carry_retirements": carry_retirements,
        }

        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            # Before anything else: a derived archive whose own verify failed
            # is refused here, dry runs included.
            raw_manifest = reader.read_manifest_raw()
            self._check_derived_archive(
                raw_manifest, allow_failed_verify=allow_failed_verify
            )
            await self._refuse_bare_identity_synonyms(reader, manifest)
            targets = self._resolve_targets(
                reader, manifest, target_namespace, allow_redirect=True
            )

            self._emit(
                "start",
                f"Starting merge into {len(targets)} namespace(s)",
                percent=0,
            )

            # The empty-target precondition inverts: merge REQUIRES the
            # namespace to exist (creating it would be a plain restore), and
            # its reporting schema is expected to be there already.
            self._emit("phase_validate", "Checking target namespaces", percent=2)
            for _src, tgt in targets:
                await self._check_namespace_exists(tgt)
                await self._check_merge_reporting_precondition(tgt)

            entry_by_prefix = {e.prefix: e for e in manifest.namespaces}
            # Nothing is written yet: every namespace is planned and checked
            # first, so a refusal in a later namespace never follows a write
            # into an earlier one, and a dry run refuses exactly what the
            # real run refuses.
            staged = [
                await self._stage_merge(
                    reader, manifest, targets, src, tgt, entry_by_prefix.get(src),
                    policy=policy, skip_documents=skip_documents, skip_files=skip_files,
                    drop_unresolved_edges=drop_unresolved_edges, dry_run=dry_run,
                )
                for src, tgt in targets
            ]
            await self._check_staged_references(staged)

            if not dry_run and not skip_files:
                await self._upload_merge_blobs(reader, staged, targets[0][1])

            if dry_run:
                self._emit(
                    "complete",
                    "Merge dry run complete — plan computed, no changes made",
                    percent=100,
                    details=self.result,
                )
                return

            for stage in staged:
                await self._apply_staged_merge(
                    stage, archive_path, manifest, raw_manifest,
                    policy=policy, batch_size=batch_size, skip_documents=skip_documents,
                )

        self.result["reporting_synced"] = list(self._reporting_synced)
        self._finish_reporting_verification(
            [tgt for _, tgt in targets], "reporting was not verified for this namespace"
        )
        self._emit("complete", "Merge complete", percent=100, details=self.result)

    def _check_merge_policies(self, on_clash: str, older_versions: str) -> None:
        if on_clash not in self.MERGE_CLASH_POLICIES:
            raise RestoreEngineError(
                f"Invalid on_clash '{on_clash}' — must be one of "
                f"{', '.join(self.MERGE_CLASH_POLICIES)}"
            )
        if older_versions not in self.MERGE_OLDER_VERSIONS:
            raise RestoreEngineError(
                f"Invalid older_versions '{older_versions}' — must be one of "
                f"{', '.join(self.MERGE_OLDER_VERSIONS)}"
            )

    async def _stage_merge(
        self,
        reader: ArchiveReader,
        manifest: Any,
        targets: list[tuple[str, str]],
        src: str,
        tgt: str,
        entry: Any,
        *,
        policy: dict[str, Any],
        skip_documents: bool,
        skip_files: bool,
        drop_unresolved_edges: bool,
        dry_run: bool,
    ) -> _StagedMerge:
        """Plan the merge of one archived namespace into its target, writing
        nothing: both passes, the gates, and what its writes will need."""
        ns_config = entry.namespace_config if entry else manifest.namespace_config
        await self._report_namespace_config_drift(tgt, ns_config)

        archive_entities, doc_groups = await self._off_loop(
            self._build_merge_plan_inputs,
            reader, src, skip_documents=skip_documents,
            skip_files=skip_files,
        )
        if src != tgt:
            doc_groups = await self._rescope_merge_inputs(archive_entities, doc_groups, src, tgt)

        # References to entities the archive does not carry (another
        # namespace's terminology, say) are mapped first: the
        # definitions pass compares a template's references by what
        # they name here, and these are some of them. They never
        # overlap with pass 1's mappings, which are archived entities.
        remapper = _AligningRemapper()
        referents = await self._map_external_referents(
            reader, manifest, remapper, tgt
        )

        # ---- Pass 1: definitions -------------------------------
        definitions = await self._plan_merge_definitions(
            archive_entities, tgt, remapper, policy=policy, dry_run=dry_run,
        )
        alignment_report: dict[str, Any] | None = None
        if definitions.alignment:
            doc_groups, alignment_report = await self._align_merge(
                definitions, reader, src, tgt, archive_entities, doc_groups, remapper,
                dry_run=dry_run,
            )
        dropped = await self._report_unresolved_references(
            reader, [src], remapper, referents, tgt,
            drop_edges=drop_unresolved_edges,
        )
        if dropped:
            doc_groups = _drop_merge_rows(archive_entities, doc_groups, dropped)

        # ---- Pass 2: everything else ---------------------------
        built, rewritten, chain_transform, doc_groups, rehash = await self._plan_merge_entities(
            reader, manifest, targets, src, tgt, archive_entities, doc_groups, remapper,
        )
        self._record_merge_plan(
            src, tgt, built, rewritten, definitions, referents, rehash, alignment_report,
            on_clash=policy["on_clash"], dry_run=dry_run,
        )
        self._enforce_merge_gates(tgt, built)

        streamed, wanted = await self._merge_wanted_ids(
            reader, src, built, doc_groups, chain_transform, on_clash=policy["on_clash"],
        )
        return _StagedMerge(
            src=src, tgt=tgt, definitions=definitions, remapper=remapper,
            built=built, doc_groups=doc_groups, streamed=streamed,
            arriving=_merge_arriving(definitions, built), wanted=wanted,
        )

    async def _rescope_merge_inputs(
        self,
        archive_entities: dict[str, list[dict[str, Any]]],
        doc_groups: dict[str, list[dict[str, Any]]] | None,
        src: str,
        tgt: str,
    ) -> dict[str, list[dict[str, Any]]] | None:
        """Redirected merge: the IDs must be free here, and every entity has
        to be moved to the target namespace before it is matched — the keys
        it matches on embed the namespace. Rewrites ``archive_entities`` in
        place and returns the re-scoped held document groups."""
        await self._check_ids_are_free(
            archive_entities.get("registry_entries") or [], src, tgt
        )
        for entity_type, entities in archive_entities.items():
            archive_entities[entity_type] = [
                self._rewrite_namespace(entity_type, entity, src, tgt)
                for entity in entities
            ]
        if doc_groups is not None:
            doc_groups = {
                doc_id: [
                    self._rewrite_namespace("documents", row, src, tgt)
                    for row in rows
                ]
                for doc_id, rows in doc_groups.items()
            }
        self._emit(
            "phase_merge_plan",
            f"[{tgt}] merging from namespace '{src}' — entities "
            "and composite keys re-scoped to the target",
        )
        return doc_groups

    async def _plan_merge_definitions(
        self,
        archive_entities: dict[str, list[dict[str, Any]]],
        tgt: str,
        remapper: _AligningRemapper,
        *,
        policy: dict[str, Any],
        dry_run: bool,
    ) -> Any:
        """Pass 1: compare the definitions, refuse a mismatch, and give the
        remapper the mapping that lets pass 2 point the incoming data at the
        definitions that survived."""
        definitions = await DefinitionsPlanner(
            self._mongo, COLLECTION_MAP,
            add_missing=policy["add_missing"],
            extend_terminologies=policy["extend_terminologies"],
            align_templates=policy["align_templates"],
            carry_retirements=policy["carry_retirements"],
        ).plan(archive_entities, tgt, external=remapper)
        self._emit_definitions_plan(tgt, definitions, dry_run=dry_run)
        self._enforce_definitions_gate(tgt, definitions)

        # The mapping pass 1 produced is what lets pass 2 point the
        # incoming data at the definitions that survived.
        for old_id, new_id in definitions.mapping["terminologies"].items():
            remapper.add_terminology_mapping(old_id, new_id)
        for old_id, new_id in definitions.mapping["terms"].items():
            remapper.add_term_mapping(old_id, new_id)
        for old_id, new_id in definitions.mapping["templates"].items():
            remapper.add_template_mapping(old_id, new_id)
        return definitions

    async def _align_merge(
        self,
        definitions: Any,
        reader: ArchiveReader,
        src: str,
        tgt: str,
        archive_entities: dict[str, list[dict[str, Any]]],
        doc_groups: dict[str, list[dict[str, Any]]] | None,
        remapper: _AligningRemapper,
        *,
        dry_run: bool,
    ) -> tuple[dict[str, list[dict[str, Any]]] | None, dict[str, Any]]:
        """Settle the template alignment: re-pin documents onto the target
        versions, and leave out the documents whose versions map nowhere.
        Returns the held document groups and the alignment report."""
        unplaceable, report = await self._off_loop(
            self._settle_alignment,
            definitions.alignment, reader, src, doc_groups,
        )
        remapper.version_map = {
            (archive_id, version): target_version
            for archive_id, alignment in definitions.alignment.items()
            for version, target_version in alignment.version_map().items()
        }
        self._emit_alignment(tgt, report, dry_run=dry_run)
        if unplaceable:
            doc_groups = _drop_merge_rows(archive_entities, doc_groups, unplaceable)
        return doc_groups, report

    async def _plan_merge_entities(
        self,
        reader: ArchiveReader,
        manifest: Any,
        targets: list[tuple[str, str]],
        src: str,
        tgt: str,
        archive_entities: dict[str, list[dict[str, Any]]],
        doc_groups: dict[str, list[dict[str, Any]]] | None,
        remapper: _AligningRemapper,
    ) -> tuple[dict[str, EntityPlan], int, Any, Any, _IdentityRehash]:
        """Pass 2: plan every non-definition type against the target.

        Returns the plans by type, how many entities had references
        rewritten, the chain transform streamed documents are written
        through (None when they are held), the held document groups, and the
        identity rehash.
        """
        planner = MergePlanner(self._mongo, COLLECTION_MAP)
        built: dict[str, EntityPlan] = {}
        rewritten = 0
        # Streamed documents are rewritten as they are read again,
        # with the remapper as it stands once documents are planned —
        # by then it holds every file and document mapping, because
        # files are planned first and documents until no new mapping
        # appears (see _MERGE_PLAN_ORDER).
        chain_transform: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None
        # Documents whose rewritten references change their identity
        # are rehashed before they are planned: the planner matches on
        # (template_id, identity_hash), and a stale hash would miss
        # the target's copy of the same document.
        rehash = _IdentityRehash(
            reader, [s for s, _t in targets],
            await self._external_template_definitions(
                reader, manifest, remapper,
                _archived_template_ids(reader, [s for s, _t in targets]),
            ),
        )
        archived_doc_groups = doc_groups
        for entity_type in _MERGE_PLAN_ORDER:
            raw_entities = archive_entities.get(entity_type)
            if entity_type in DEFINITION_TYPES or raw_entities is None:
                continue
            rounds = 0
            seen = {_document_map_state(remapper)}
            while True:
                entities, changed = self._rewrite_entities(
                    entity_type, raw_entities, remapper
                )
                entities, doc_groups, chain_transform = await self._rehash_merge_round(
                    entity_type, reader, src, tgt, raw_entities, entities,
                    archived_doc_groups, remapper, rehash, doc_groups, chain_transform,
                )
                plan = await planner.plan(entity_type, entities, tgt)
                # A document the target holds under another ID is
                # still a collision for policy, but the ID it survives
                # under has to reach everything referencing it — other
                # documents included, whose identity may hash that very
                # reference. So documents are planned again, from the
                # archived rows, until a round learns no mapping. A round
                # follows one step of a chain of documents identified
                # through one another, so a chain of n documents settles in
                # n rounds. A round is decided by the mappings it starts
                # from, so mappings that return to a state an earlier round
                # started from would repeat forever: that is refused at
                # once, after as many rounds as the cycle is long, rather
                # than run on. The rounds also stop at one more than the
                # archived documents, which no chain needs. Each round
                # re-reads the streamed documents once.
                if not self._learn_document_mappings(entity_type, plan, remapper):
                    break
                rounds += 1
                state = _document_map_state(remapper)
                cycled = state in seen
                seen.add(state)
                if cycled or rounds > len(raw_entities):
                    raise RestoreEngineError(
                        f"Merge into '{tgt}' refused — identifying the "
                        "archived documents on the target did not settle: "
                        f"after {rounds} rounds, documents still map to "
                        "different target documents from one round to the "
                        "next. Nothing was written."
                    )
            rewritten += changed
            archive_entities[entity_type] = entities
            built[entity_type] = plan
            self._record_plan_mappings(entity_type, plan, remapper)
        return built, rewritten, chain_transform, doc_groups, rehash

    async def _rehash_merge_round(
        self,
        entity_type: str,
        reader: ArchiveReader,
        src: str,
        tgt: str,
        raw_entities: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        archived_doc_groups: dict[str, list[dict[str, Any]]] | None,
        remapper: _AligningRemapper,
        rehash: _IdentityRehash,
        doc_groups: dict[str, list[dict[str, Any]]] | None,
        chain_transform: Any,
    ) -> tuple[list[dict[str, Any]], Any, Any]:
        """The identity rehash of one planning round: documents get their
        new hashes, registry entries the keys that follow them, other types
        pass through. Returns (entities, doc_groups, chain_transform)."""
        if entity_type == "documents":
            return await self._rewrite_merge_documents(
                reader, src, tgt, raw_entities, entities, archived_doc_groups,
                remapper, rehash, doc_groups, chain_transform,
            )
        if entity_type == "registry_entries":
            entities = [rehash.rekey(entry) for entry in entities]
        return entities, doc_groups, chain_transform

    async def _rewrite_merge_documents(
        self,
        reader: ArchiveReader,
        src: str,
        tgt: str,
        raw_entities: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        archived_doc_groups: dict[str, list[dict[str, Any]]] | None,
        remapper: _AligningRemapper,
        rehash: _IdentityRehash,
        doc_groups: dict[str, list[dict[str, Any]]] | None,
        chain_transform: Any,
    ) -> tuple[list[dict[str, Any]], Any, Any]:
        """One planning round's documents: held ones are rehashed and
        regrouped; streamed ones get the chain transform their writes go
        through, and their compact heads take the hashes the rewritten
        chains produce. Returns (entities, doc_groups, chain_transform)."""
        if archived_doc_groups is not None:
            entities = [
                rehash.rehash(archived, row)
                for archived, row in zip(raw_entities, entities, strict=True)
            ]
            doc_groups = self._regroup_documents(
                archived_doc_groups, remapper, rehash
            )
            return entities, doc_groups, chain_transform
        chain_transform = self._chain_transform(
            copy.deepcopy(remapper), src, tgt, rehash
        )
        if remapper.total_mappings:
            # Compact heads carry no references: one
            # pass over the rewritten chains finds the
            # new hashes, which the heads then take.
            await self._off_loop(
                _drain, transformed_chains(reader, src, chain_transform)
            )
            entities = [
                {**head, "identity_hash": rehash.new_hash[str(head.get("document_id"))]}
                if str(head.get("document_id")) in rehash.new_hash else head
                for head in entities
            ]
        return entities, doc_groups, chain_transform

    @staticmethod
    def _learn_document_mappings(
        entity_type: str, plan: EntityPlan, remapper: _AligningRemapper,
    ) -> bool:
        """Whether a documents round found mappings the remapper did not
        hold yet (and so must be planned again); it learns them. False for
        every other type."""
        if entity_type != "documents":
            return False
        new = {
            old_id: new_id for old_id, new_id in plan.mapping.items()
            if remapper.document_map.get(old_id) != new_id
        }
        for old_id, new_id in new.items():
            remapper.add_document_mapping(old_id, new_id)
        return bool(new)

    @staticmethod
    def _record_plan_mappings(
        entity_type: str, plan: EntityPlan, remapper: _AligningRemapper,
    ) -> None:
        """The ids a planned document or file survives under, for what
        references it."""
        for old_id, new_id in plan.mapping.items():
            if entity_type == "documents":
                remapper.add_document_mapping(old_id, new_id)
            elif entity_type == "files":
                remapper.add_file_mapping(old_id, new_id)

    def _record_merge_plan(
        self,
        src: str,
        tgt: str,
        built: dict[str, EntityPlan],
        rewritten: int,
        definitions: Any,
        referents: Any,
        rehash: _IdentityRehash,
        alignment_report: dict[str, Any] | None,
        *,
        on_clash: str,
        dry_run: bool,
    ) -> None:
        """Report one namespace's plan, and record it on the job result."""
        if rewritten:
            self._emit(
                "phase_merge_plan",
                f"[{tgt}] {rewritten} incoming entit(ies) had references "
                f"rewritten onto the target's definitions",
            )
        self._emit_merge_plan(tgt, built, on_clash=on_clash, dry_run=dry_run)
        self.result.setdefault("mode", "merge")
        self.result["dry_run"] = dry_run
        # The count is in this namespace's report below; the warning is the job's.
        self._report_unrehashable(f"{src} → {tgt}", rehash.unrehashable, {})
        self.result.setdefault("namespaces", {})[tgt] = {
            # Record where this data came from, so a redirected merge
            # (src != tgt) is legible on the job record — parity with the
            # remap result, which already carries source/target. For a
            # same-namespace merge source_namespace == tgt.
            "source_namespace": src,
            "referents": referents,
            "rehashed_documents": len(rehash.new_hash),
            **({"unrehashable_documents": rehash.unrehashable} if rehash.unrehashable else {}),
            "definitions": definitions.summary(),
            "incompatibilities": len(definitions.incompatibilities),
            "target_wins": len(definitions.target_wins),
            "retirements_not_carried": len(definitions.retirements_not_carried),
            **({"template_alignment": alignment_report} if alignment_report else {}),
            "entities": {
                entity_type: plan.summary()
                for entity_type, plan in built.items()
                if any(plan.summary().values())
            },
        }

    async def _merge_wanted_ids(
        self,
        reader: ArchiveReader,
        src: str,
        built: dict[str, EntityPlan],
        doc_groups: dict[str, list[dict[str, Any]]] | None,
        chain_transform: Any,
        *,
        on_clash: str,
    ) -> tuple[_StreamedDocuments | None, dict[str, Any]]:
        """The ids the documents this merge writes reference, and for
        streamed documents the stream they are written from."""
        documents_plan = built.get("documents")
        if documents_plan is not None and doc_groups is None:
            assert chain_transform is not None
            streamed = _StreamedDocuments(
                reader, src, chain_transform, documents_plan,
                overwrite=on_clash != "skip",
            )
            return streamed, await self._off_loop(streamed.referenced_ids, self._referenced_ids)
        rows_to_write: list[dict[str, Any]] = []
        if documents_plan is not None and doc_groups is not None:
            for head in documents_plan.to_insert:
                rows_to_write.extend(
                    doc_groups.get(head.get("document_id", ""), [head])
                )
            if on_clash != "skip":
                rows_to_write.extend(
                    clash.entity for clash in documents_plan.differing_clashes
                )
        return None, (self._referenced_ids(rows_to_write) if rows_to_write else {})

    async def _check_staged_references(self, staged: list[_StagedMerge]) -> None:
        """References between the archive's namespaces resolve against what
        the whole merge supplies, not only what its own namespace does: a
        document in one namespace may point at one arriving in another."""
        arriving_anywhere: dict[str, set[str]] = {}
        for stage in staged:
            for entity_type, ids in stage.arriving.items():
                arriving_anywhere.setdefault(entity_type, set()).update(ids)
        for stage in staged:
            if stage.wanted:
                await self._check_merge_wanted(stage.tgt, stage.wanted, arriving_anywhere)

    async def _upload_merge_blobs(
        self, reader: ArchiveReader, staged: list[_StagedMerge], namespace: str,
    ) -> None:
        """Bytes before the records that need them: only for files the merge
        inserts (a file the target already has keeps its bytes), and all of
        them before any namespace is written, so a write that fails never
        leaves a file record without its bytes."""
        if not self._storage:
            return
        inserting = {
            f["file_id"]
            for stage in staged
            for f in stage.built.get("files", EntityPlan(entity_type="files")).to_insert
            if f.get("file_id")
        }
        if inserting:
            await self._restore_blobs(reader, namespace, only=inserting)

    async def _apply_staged_merge(
        self,
        stage: _StagedMerge,
        archive_path: Path,
        manifest: Any,
        raw_manifest: Any,
        *,
        policy: dict[str, Any],
        batch_size: int,
        skip_documents: bool,
    ) -> None:
        """Write one planned namespace, then record what arrived."""
        src, tgt, built = stage.src, stage.tgt, stage.built
        await self._apply_definitions(tgt, stage.definitions, stage.remapper, batch_size)
        await self._apply_merge(
            tgt, built, stage.doc_groups or {},
            on_clash=policy["on_clash"], older_versions=policy["older_versions"],
            batch_size=batch_size, streamed=stage.streamed,
        )
        await self._release_retired_synonyms(tgt)
        await self._reporting_phase_counts(tgt, skip_documents=skip_documents)
        # A merge adds to a namespace that already holds data, so its
        # record says what arrived and under which policy, not that
        # the namespace came from the archive. The plan summary is
        # what was planned per entity type; with on_clash "newer" the
        # clashes actually written are a subset of `clash`.
        planned = self.result["namespaces"][tgt]
        await self._write_restore_provenance(
            archive_path, manifest, raw_manifest,
            source=src, target=tgt, mode="merge",
            referents=planned.get("referents"),
            merge={
                **policy,
                "definitions": planned.get("definitions"),
                **({"template_alignment": planned["template_alignment"]}
                   if planned.get("template_alignment") else {}),
                "entities": planned.get("entities"),
            },
        )

    def _build_merge_plan_inputs(
        self,
        reader: ArchiveReader,
        src: str,
        *,
        skip_documents: bool,
        skip_files: bool,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]] | None]:
        """Read the archive's entities for one namespace, ready for planning.

        Documents are planned at the *logical* level: an archived document is
        a chain of version rows, and it is the chain that clashes with the
        target's chain, not each row separately. The head row represents the
        group during planning.

        Documents dominate an archive's size, so they are not held: planning
        gets a compact head per document (`document_chains.compact_heads`),
        and the chains are streamed again where they are checked and written.
        The returned ``doc_groups`` is then None. An archive whose documents
        are not sorted by document_id (exported before the backup sorted
        them) cannot be streamed that way; for it the full chains are read
        into ``doc_groups`` as before, with a warning.
        """
        entities_by_type: dict[str, list[dict[str, Any]]] = {}
        doc_groups: dict[str, list[dict[str, Any]]] | None = None
        skipped = _skipped_types(skip_documents, skip_files)

        for entity_type in MERGE_ENTITY_ORDER:
            if entity_type in skipped:
                continue

            if entity_type == "documents":
                try:
                    entities_by_type[entity_type] = compact_heads(reader, src)
                    continue
                except UnsortedArchive as e:
                    self._emit(
                        "warning",
                        f"[{src}] {e} — the merge holds every document in "
                        "memory; a backup taken with this release streams.",
                    )
                    doc_groups = {}

            entities: list[dict[str, Any]] = []
            for entity in reader.read_entities(entity_type, namespace=src):
                entity.pop("_id", None)
                if not _entry_of_skipped(entity_type, entity, skipped):
                    entities.append(entity)

            if entity_type == "documents":
                assert doc_groups is not None
                for row in entities:
                    doc_groups.setdefault(row.get("document_id", ""), []).append(row)
                entities = [
                    max(rows, key=lambda r: r.get("version", 1))
                    for rows in doc_groups.values()
                ]

            entities_by_type[entity_type] = entities

        return entities_by_type, doc_groups

    async def _check_ids_are_free(
        self, entries: list[dict[str, Any]], source: str, target: str
    ) -> None:
        """A redirected merge may only carry IDs this Registry does not know.

        Canonical IDs are preserved by a merge, and a Registry entry belongs to
        exactly one namespace — so an entity that still exists here cannot also
        be placed in another namespace under the same ID. That is not a policy
        question with a sensible default; it is a request the identity model
        cannot represent, and the only honest answer is to refuse.

        In practice this holds whenever the archive comes from somewhere else:
        another instance, or a namespace since deleted here. When it does not
        hold, the caller wants a copy, and a copy needs re-minted IDs.

        Checked before anything is written. Left to the database, the same
        collision surfaces as a duplicate-key error partway through, with some
        of the merge already applied.
        """
        entry_ids = [e["entry_id"] for e in entries if e.get("entry_id")]
        if not entry_ids:
            return

        db_name, coll_name = COLLECTION_MAP["registry_entries"]
        collection = self._mongo[db_name][coll_name]
        taken: list[dict[str, Any]] = []
        for start in range(0, len(entry_ids), 500):
            batch = entry_ids[start:start + 500]
            cursor = collection.find(
                {"entry_id": {"$in": batch}},
                {"entry_id": 1, "namespace": 1, "entity_type": 1},
            )
            async for row in cursor:
                taken.append(row)
                if len(taken) >= 5:
                    break
            if len(taken) >= 5:
                break

        if not taken:
            return

        detail = "; ".join(
            f"{row.get('entry_id')} (already in '{row.get('namespace')}' "
            f"as {row.get('entity_type')})"
            for row in taken[:5]
        )
        raise RestoreEngineError(
            f"Merge of '{source}' into '{target}' refused — the archive's "
            f"entities are still registered on this instance: {detail}. A "
            "merge preserves canonical IDs, and one ID cannot name an entity "
            "in two namespaces. Merge an archive whose namespace no longer "
            "exists here, or from another instance."
        )

    def _chain_transform(
        self, remapper: IDRemapper, source: str, target: str, rehash: _IdentityRehash
    ) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
        """What the in-memory path does to a document chain, as a function:
        the namespace rewrite of a redirected merge, then the reference
        rewrite onto the target's definitions and the rehash it implies."""
        def transform(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if source != target:
                rows = [self._rewrite_namespace("documents", r, source, target) for r in rows]
            return [rehash.rehash(r, remapper.remap_document(r)) for r in rows]
        return transform

    def _rewrite_namespace(
        self,
        entity_type: str,
        entity: dict[str, Any],
        source: str,
        target: str,
    ) -> dict[str, Any]:
        """Move one entity from the source namespace to the target.

        The ``namespace`` field is the easy half. The load-bearing half is the
        Registry composite key, which embeds the namespace as ``ns`` and is
        hashed into the value the uniqueness gate and every claim are built
        on: left alone, the imported entry would claim a key naming the old
        namespace and collide with nothing, silently opting out of the gate.

        Only ``ns`` components equal to the SOURCE namespace are rewritten. A
        synonym legitimately scoped to some third namespace keeps its own.
        """
        result = dict(entity)
        if result.get("namespace") == source:
            result["namespace"] = target
        if entity_type == "documents":
            # A document reference snapshots its referent's namespace. The
            # referents that moved with this one name the target now; left on
            # the source, the post-merge validation reads every in-archive
            # reference as pointing at the wrong namespace.
            references = result.get("references")
            if isinstance(references, list):
                result["references"] = [
                    _resolved_in(ref, source, target) for ref in references
                ]
            return result
        if entity_type != "registry_entries":
            return result

        key = result.get("primary_composite_key")
        if isinstance(key, dict) and key.get("ns") == source:
            new_key = {**key, "ns": target}
            result["primary_composite_key"] = new_key
            result["primary_composite_key_hash"] = compute_composite_key_hash(new_key)

        synonyms = result.get("synonyms")
        if isinstance(synonyms, list):
            rewritten = []
            for synonym in synonyms:
                if not isinstance(synonym, dict):
                    rewritten.append(synonym)
                    continue
                syn = dict(synonym)
                if syn.get("namespace") == source:
                    syn["namespace"] = target
                syn_key = syn.get("composite_key")
                if isinstance(syn_key, dict) and syn_key.get("ns") == source:
                    new_syn_key = {**syn_key, "ns": target}
                    syn["composite_key"] = new_syn_key
                    syn["composite_key_hash"] = compute_composite_key_hash(new_syn_key)
                rewritten.append(syn)
            result["synonyms"] = rewritten

        return result

    def _rewrite_entities(
        self,
        entity_type: str,
        entities: list[dict[str, Any]],
        remapper: IDRemapper,
    ) -> tuple[list[dict[str, Any]], int]:
        """Point one type's outward references at the surviving IDs.

        Returns the rewritten entities and how many actually changed. Skipping
        an entity because the target already has it is only half the job — the
        rows that referenced it are still carrying the other install's ID, and
        inserting those unrewritten is how a merge imports dangling
        references.
        """
        rewriter = {
            "terms": remapper.remap_term,
            "term_relations": remapper.remap_term_relation,
            "templates": remapper.remap_template,
            "documents": remapper.remap_document,
            "files": lambda e: remap_file(e, remapper),
            "registry_entries": lambda e: self._rewrite_registry_entry(e, remapper),
        }.get(entity_type)
        # Terminologies have no outward references of their own.
        if rewriter is None:
            return entities, 0

        rewritten: list[dict[str, Any]] = []
        changed = 0
        for entity in entities:
            new_entity = rewriter(entity)
            if new_entity != entity:
                changed += 1
            rewritten.append(new_entity)
        return rewritten, changed

    def _rewrite_registry_entry(
        self, entry: dict[str, Any], remapper: IDRemapper
    ) -> dict[str, Any]:
        """Rewrite the IDs embedded in an entry's composite keys, and rehash.

        A registry entry's key is what the uniqueness gate and every claim are
        built on, and for most entity types it embeds a parent's canonical ID
        (a term's key carries ``terminology_id``, a document's carries
        ``template_id``). Left alone, the imported entry would claim a key
        naming an entity that does not exist here — and would fail to match
        the target's equivalent entry, which is keyed on the surviving ID.

        The hash must be recomputed from the rewritten key using the Registry's
        own algorithm, which is why it lives in wip_auth rather than being
        re-derived here.
        """
        result = dict(entry)
        key = result.get("primary_composite_key")
        if isinstance(key, dict):
            new_key = self._remap_key_values(key, remapper)
            if new_key != key:
                result["primary_composite_key"] = new_key
                result["primary_composite_key_hash"] = compute_composite_key_hash(
                    new_key
                )

        synonyms = result.get("synonyms")
        if isinstance(synonyms, list):
            new_synonyms = []
            for synonym in synonyms:
                if not isinstance(synonym, dict):
                    new_synonyms.append(synonym)
                    continue
                syn = dict(synonym)
                syn_key = syn.get("composite_key")
                if isinstance(syn_key, dict):
                    new_syn_key = self._remap_key_values(syn_key, remapper)
                    if new_syn_key != syn_key:
                        syn["composite_key"] = new_syn_key
                        syn["composite_key_hash"] = compute_composite_key_hash(
                            new_syn_key
                        )
                new_synonyms.append(syn)
            result["synonyms"] = new_synonyms

        return result

    @staticmethod
    def _remap_key_values(
        key: dict[str, Any], remapper: IDRemapper
    ) -> dict[str, Any]:
        """Replace any ID-valued component of a composite key.

        Composite keys are flat maps of scalars, and only the ID-shaped values
        are remappable — ``ns``, ``value`` and ``label`` components pass
        through because no map contains them.
        """
        maps = (
            remapper.terminology_map,
            remapper.term_map,
            remapper.template_map,
            remapper.document_map,
            remapper.file_map,
        )
        result = dict(key)
        for name, value in key.items():
            if not isinstance(value, str):
                continue
            for id_map in maps:
                if value in id_map:
                    result[name] = id_map[value]
                    break
        return result

    def _regroup_documents(
        self,
        doc_groups: dict[str, list[dict[str, Any]]],
        remapper: IDRemapper,
        rehash: _IdentityRehash,
    ) -> dict[str, list[dict[str, Any]]]:
        """Apply the document rewrite, and the rehash it implies, to every
        version row, not just the head.

        Planning sees one head row per document, but an insert carries the
        whole chain — and every row in it references the same rewritten
        template and terms.
        """
        return {
            doc_id: [rehash.rehash(row, remapper.remap_document(row)) for row in rows]
            for doc_id, rows in doc_groups.items()
        }

    def _settle_alignment(
        self,
        alignment: dict[str, TemplateAlignment],
        reader: ArchiveReader,
        src: str,
        doc_groups: dict[str, list[dict[str, Any]]] | None,
    ) -> tuple[set[str], dict[str, Any]]:
        """Finish the template alignment against the arriving documents.

        A version mapped onto a target version that lacks some of its fields
        holds only if those fields are empty in every arriving row pinned to
        it. Then every document with a row pinned to a version that maps
        nowhere is unplaceable: a document is placed whole or not at all.
        Returns the unplaceable document ids and the report for the job.
        """
        chains: Any = (
            doc_groups.values() if doc_groups is not None else iter_chains(reader, src)
        )
        rows_per_version: dict[tuple[str, int], list[str]] = {}
        carrying: dict[tuple[str, int], dict[str, int]] = {}
        for chain in chains:
            for row in chain:
                template_id = str(row.get("template_id"))
                if template_id not in alignment:
                    continue
                key = (template_id, int(row.get("template_version") or 1))
                rows_per_version.setdefault(key, []).append(str(row.get("document_id")))
                version = alignment[template_id].versions.get(key[1])
                if version is None or not version.removed_fields:
                    continue
                data = row.get("data") or {}
                for name in version.removed_fields:
                    if not _is_empty(data.get(name)):
                        counts = carrying.setdefault(key, {})
                        counts[name] = counts.get(name, 0) + 1

        for (template_id, number), counts in carrying.items():
            version = alignment[template_id].versions[number]
            version.reason = (
                f"{version.reason}, but it lacks field(s) the arriving documents "
                f"fill: {', '.join(f'{n} ({c})' for n, c in sorted(counts.items()))}"
            )
            version.target_version = None

        unplaceable: dict[str, dict[str, Any]] = {}
        for (template_id, number), doc_ids in rows_per_version.items():
            template = alignment[template_id]
            version = template.versions.get(number)
            if version is not None and version.target_version is not None:
                continue
            reason = (
                version.reason if version is not None
                else "the archive does not carry this template version"
            )
            for doc_id in doc_ids:
                entry = unplaceable.setdefault(doc_id, {
                    "document_id": doc_id, "template": template.value, "versions": {},
                })
                entry["versions"][str(number)] = reason

        report = {
            "templates": [
                {
                    "value": template.value,
                    "target_template_id": template.target_template_id,
                    "versions": [
                        {
                            "archive_version": number,
                            "target_version": version.target_version,
                            "rows": len(rows_per_version.get((archive_id, number), [])),
                            "history": (archive_id, number) not in rows_per_version,
                            "reason": version.reason,
                        }
                        for number, version in sorted(template.versions.items())
                    ],
                }
                for archive_id, template in sorted(
                    alignment.items(), key=lambda item: item[1].value
                )
            ],
            "unplaceable_count": len(unplaceable),
            "unplaceable_documents": list(unplaceable.values())[:ALIGNMENT_UNPLACEABLE_SAMPLE],
            "unplaceable_truncated": max(0, len(unplaceable) - ALIGNMENT_UNPLACEABLE_SAMPLE),
        }
        return set(unplaceable), report

    def _emit_alignment(
        self, namespace: str, report: dict[str, Any], *, dry_run: bool
    ) -> None:
        """Report the alignment: where each version went, and what stays out."""
        phase = "phase_dry_run" if dry_run else "phase_definitions"
        for template in report["templates"]:
            for version in template["versions"]:
                if version["history"]:
                    continue
                where = (
                    f"target v{version['target_version']}"
                    if version["target_version"] is not None else "nowhere"
                )
                self._emit(
                    phase,
                    f"[{namespace}] template {template['value']} v"
                    f"{version['archive_version']} ({version['rows']} document row(s)) → "
                    f"{where}: {version['reason']}",
                )
        if report["unplaceable_count"]:
            self._emit(
                "warning",
                f"[{namespace}] {report['unplaceable_count']} document(s) not merged: "
                "a version they pin maps onto no target version. They are listed "
                "on the job (template_alignment.unplaceable_documents); reshape "
                "them with wip-archive migrate, re-export and merge again.",
            )

    def _emit_definitions_plan(
        self, namespace: str, definitions: DefinitionsPlan, *, dry_run: bool
    ) -> None:
        """Report pass 1: what matched, what would be added, what the target
        kept."""
        phase = "phase_dry_run" if dry_run else "phase_definitions"
        for entity_type, counts in definitions.summary().items():
            if not any(counts.values()):
                continue
            retire = f", {counts['retire']} to retire" if counts.get("retire") else ""
            names = (
                f", {counts['extend_names']} to gain the archive's names"
                if counts.get("extend_names") else ""
            )
            self._emit(
                phase,
                f"[{namespace}] {entity_type}: {counts['unchanged']} already "
                f"identical, {counts['add']} to add, {counts['mapped']} mapped "
                f"to the target's IDs{retire}{names}",
            )
        # Never silent: an operator who loses a label or an alias list to the
        # target's copy finds out later from a UI that changed under them.
        for note in definitions.target_wins[:20]:
            self._emit(
                phase,
                f"[{namespace}] {note.entity_type} '{note.name}': target's "
                f"{', '.join(note.fields)} kept ({note.detail})",
            )
        if len(definitions.target_wins) > 20:
            self._emit(
                phase,
                f"[{namespace}] …and {len(definitions.target_wins) - 20} more "
                "definition(s) where the target's metadata was kept",
            )
        if definitions.retirements_not_carried:
            self._emit(
                phase,
                f"[{namespace}] {len(definitions.retirements_not_carried)} "
                "term(s) the archive retired stay active on the target — "
                "set carry_retirements to retire them",
            )

    def _enforce_definitions_gate(
        self, namespace: str, definitions: DefinitionsPlan
    ) -> None:
        """Definitions are the precondition — an incompatibility stops the
        merge before any document moves."""
        if definitions.compatible:
            return
        issues = definitions.incompatibilities
        detail = "; ".join(
            f"{issue.entity_type} '{issue.name}' — {issue.reason}"
            for issue in issues[:5]
        )
        cap = MERGE_REFUSAL_INCOMPATIBILITY_CAP
        recorded = (
            f"the first {cap}, with the two sides' values, are on the job "
            "record (result.incompatibilities; result."
            "incompatibilities_truncated counts the rest)"
            if len(issues) > cap
            else "every one, with the two sides' values, is on the job record "
            "(result.incompatibilities)"
        )
        more = (
            f" …and {len(issues) - 5} more; {recorded}."
            if len(issues) > 5
            else " The job record carries each with the two sides' values "
            "(result.incompatibilities)."
        )
        raise RestoreEngineError(
            f"Merge into '{namespace}' refused — "
            f"{len(issues)} definition(s) are not "
            f"compatible with the target. {detail}.{more} Documents cannot be "
            "merged under definitions the two sides disagree on.",
            details={
                "result_kind": "merge_refused",
                "namespace": namespace,
                "incompatibility_count": len(issues),
                "incompatibilities": [
                    {
                        "namespace": namespace,
                        "entity_type": issue.entity_type,
                        "name": issue.name,
                        "reason": issue.reason,
                        "detail": issue.detail,
                    }
                    for issue in issues[:MERGE_REFUSAL_INCOMPATIBILITY_CAP]
                ],
                "incompatibilities_truncated": max(
                    0, len(issues) - MERGE_REFUSAL_INCOMPATIBILITY_CAP
                ),
            },
        )

    async def _apply_definitions(
        self,
        namespace: str,
        definitions: DefinitionsPlan,
        remapper: IDRemapper,
        batch_size: int,
    ) -> None:
        """Insert the definitions the opt-in strategies allowed, then carry
        the archive's term retirements and recount the affected terminologies.

        Dependency order again: a term added to the target must point at the
        terminology that survived, not at the archive's copy of it.
        """
        recount: set[str] = set()
        for entity_type in DEFINITION_TYPES:
            rows = definitions.to_add[entity_type]
            if not rows:
                continue
            if entity_type == "terms":
                rows = [remapper.remap_term(row) for row in rows]
                recount.update(
                    str(r["terminology_id"]) for r in rows if r.get("terminology_id")
                )
            elif entity_type == "templates":
                rows = [remapper.remap_template(row) for row in rows]
                # A template_id is stable across versions, and remap_template
                # rewrites a template's references, not its own id. A version
                # added here whose earlier version matched the target under
                # another id takes that id: otherwise the template has two
                # ids and the merged documents (remapped to the target's id)
                # pin a version that does not exist under it.
                for row in rows:
                    own = row.get("template_id")
                    if own and str(own) in remapper.template_map:
                        row["template_id"] = remapper.template_map[str(own)]

            db_name, coll_name = COLLECTION_MAP[entity_type]
            collection = self._mongo[db_name][coll_name]
            for start in range(0, len(rows), batch_size):
                await self._insert_batch(
                    collection, rows[start:start + batch_size], entity_type
                )
            self._emit(
                "phase_definitions",
                f"[{namespace}] added {len(rows)} {entity_type} the target "
                "did not have",
            )

        await self._extend_term_names(namespace, definitions)
        recount.update(await self._retire_terms(namespace, definitions, remapper))
        await self._recount_terms(recount)

    async def _extend_term_names(
        self, namespace: str, definitions: DefinitionsPlan,
    ) -> None:
        """extend_terminologies: give the target's copy of each term the
        names only the archive's copy had. The target's own names stay."""
        if not definitions.extend_names:
            return
        db_name, coll_name = COLLECTION_MAP["terms"]
        collection = self._mongo[db_name][coll_name]
        now = datetime.now(UTC)
        for extension in definitions.extend_names:
            await collection.update_one(
                {"term_id": extension["term_id"]},
                {
                    "$push": {"names": {"$each": extension["names"]}},
                    "$set": {"updated_at": now, "updated_by": "restore-merge"},
                },
            )
        self._emit(
            "phase_definitions",
            f"[{namespace}] added the archive's names to "
            f"{len(definitions.extend_names)} term(s) the target held without them",
        )

    async def _retire_terms(
        self,
        namespace: str,
        definitions: DefinitionsPlan,
        remapper: IDRemapper,
    ) -> set[str]:
        """Retire on the target the terms the archive retired. Returns the
        terminologies whose active count changed.

        The replacement term is named by its archive id; it is mapped to the
        target's, which also covers a replacement this merge has just added.
        """
        db_name, coll_name = COLLECTION_MAP["terms"]
        collection = self._mongo[db_name][coll_name]
        now = datetime.now(UTC)
        touched: set[str] = set()
        for retirement in definitions.retire_terms:
            replaced_by = retirement.get("replaced_by_term_id")
            if replaced_by:
                replaced_by = remapper.term_map.get(replaced_by, replaced_by)
            await collection.update_one(
                {"term_id": retirement["term_id"]},
                {"$set": {
                    "status": retirement["status"],
                    "deprecated_reason": retirement.get("deprecated_reason"),
                    "replaced_by_term_id": replaced_by,
                    "updated_at": now,
                    "updated_by": "restore-merge",
                }},
            )
            if retirement.get("terminology_id"):
                touched.add(str(retirement["terminology_id"]))
        if definitions.retire_terms:
            self._emit(
                "phase_definitions",
                f"[{namespace}] retired {len(definitions.retire_terms)} term(s) "
                "the archive had retired",
            )
        return touched

    async def _recount_terms(self, terminology_ids: set[str]) -> None:
        """Recompute term_count with def-store's rule: active terms only.

        The merge writes terms directly, so def-store never sees the change;
        without this the terminology keeps the count it had before the merge.
        """
        if not terminology_ids:
            return
        terms_db, terms_coll = COLLECTION_MAP["terms"]
        tl_db, tl_coll = COLLECTION_MAP["terminologies"]
        for terminology_id in terminology_ids:
            count = await self._mongo[terms_db][terms_coll].count_documents(
                {"terminology_id": terminology_id, "status": "active"}
            )
            await self._mongo[tl_db][tl_coll].update_one(
                {"terminology_id": terminology_id}, {"$set": {"term_count": count}}
            )

    def _merge_policy_for(self, entity_type: str, *, on_clash: str) -> str:
        if entity_type == "documents":
            return on_clash
        # Term relations dedup by their endpoints, files by checksum, registry
        # entries by composite key — for all three the target's copy IS the
        # entity, so there is nothing an archive copy could add.
        return "skip"

    def _emit_merge_plan(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        *,
        on_clash: str,
        dry_run: bool,
    ) -> None:
        """Report the classification — the dry run's whole output, and a real
        merge's record of what it is about to do."""
        phase = "phase_dry_run" if dry_run else "phase_merge_plan"
        verb = "would merge" if dry_run else "merging"
        for entity_type, plan in plans.items():
            counts = plan.summary()
            if not any(counts.values()):
                continue
            policy = self._merge_policy_for(entity_type, on_clash=on_clash)
            matched = (
                f"unchanged={counts['unchanged']}, clash={counts['clash']}"
                if plan.diffable
                else f"not_compared={counts['not_compared']} (already here; content is "
                "compared when written)"
            )
            self._emit(
                phase,
                f"[{namespace}] {verb} {entity_type}: "
                f"insert={counts['insert']}, {matched} (on clash: {policy}), "
                f"conflict={counts['conflict']}",
            )
            label = "clash" if plan.diffable else "already here"
            for clash in plan.differing_clashes[:5]:
                self._emit(
                    phase,
                    f"[{namespace}] {entity_type} {label}: "
                    f"{self._describe_entity(entity_type, clash.entity)} — "
                    + (
                        f"differs in {', '.join(sorted(clash.diff))}"
                        if clash.diff
                        else "target holds this identity already"
                    ),
                )

    @staticmethod
    def _describe_entity(entity_type: str, entity: dict[str, Any]) -> str:
        """Name an entity the way an operator reading a merge report would."""
        spec = MERGE_ENTITY_SPECS[entity_type]
        parts = [
            f"{name}={entity.get(name)!r}"
            for name in (spec.logical_fields or spec.id_fields)
        ]
        return " ".join(parts) or "<unidentified>"

    @staticmethod
    def _referenced_ids(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
        """Every entity id the given document rows point at, by kind — every
        kind a reference field resolves to, not only documents."""
        wanted: dict[str, set[str]] = {kind: set() for kind in _MERGE_REFERENCE_ID_FIELD}
        for row in rows:
            if row.get("template_id"):
                wanted["templates"].add(row["template_id"])
            for ref in row.get("term_references") or []:
                if ref.get("term_id"):
                    wanted["terms"].add(ref["term_id"])
            for ref in row.get("references") or []:
                _collect_resolved_ids(ref.get("resolved") or {}, wanted)
            for ref in row.get("file_references") or []:
                if ref.get("file_id"):
                    wanted["files"].add(ref["file_id"])
        return wanted

    async def _resolvable_ids(
        self, entity_type: str, wanted: set[str]
    ) -> set[str]:
        """Which of these ids the target instance already holds.

        Not namespace-scoped: a document referencing one in another namespace
        is legitimate, and shared vocabularies are the normal case.
        """
        if not wanted:
            return set()
        id_field = _MERGE_REFERENCE_ID_FIELD[entity_type]
        db_name, coll_name = COLLECTION_MAP[entity_type]
        collection = self._mongo[db_name][coll_name]
        found: set[str] = set()
        ids = list(wanted)
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            found.update(
                await collection.distinct(id_field, {id_field: {"$in": batch}})
            )
        return found

    async def _check_merge_references(
        self,
        namespace: str,
        rows_to_write: list[dict[str, Any]],
        arriving: dict[str, set[str]],
    ) -> None:
        """Refuse a merge that would import references resolving to nothing.

        Checked BEFORE writing, unlike a restore's post-hoc verification, and
        for one reason: a merge lands in a live namespace that cannot be
        deleted to recover. A restore into a fresh namespace can be thrown
        away and retried; a merge that pollutes production data cannot.

        A reference is satisfied if the target already holds its target, or if
        this merge is about to insert it, into any of the archive's
        namespaces. ``arriving`` carries the second set; the merge checks every
        namespace before writing any.

        This is the same question ``integrity_service`` answers after the fact,
        asked on a different substrate: that one walks Beanie documents through
        the service clients, this walks raw archive dicts through direct Mongo
        batches. The post-merge validation job remains the authoritative check.
        """
        if not rows_to_write:
            return
        await self._check_merge_wanted(
            namespace, self._referenced_ids(rows_to_write), arriving
        )

    async def _check_merge_wanted(
        self,
        namespace: str,
        wanted: dict[str, set[str]],
        arriving: dict[str, set[str]],
    ) -> None:
        """`_check_merge_references` on ids already collected — the form a
        streamed merge uses, which gathers them chain by chain."""
        problems: list[str] = []
        total = 0
        for entity_type, ids in wanted.items():
            unresolved = ids - arriving.get(entity_type, set())
            if not unresolved:
                continue
            unresolved -= await self._resolvable_ids(entity_type, unresolved)
            total += len(unresolved)
            for missing in sorted(unresolved)[:5]:
                problems.append(f"{_MERGE_REFERENCE_NOUN[entity_type]} '{missing}'")

        if problems:
            more = f" and {total - 5} more" if total > 5 else ""
            raise RestoreEngineError(
                f"Merge into '{namespace}' refused — incoming documents "
                f"reference {total} entit(ies) that neither the target "
                f"holds nor this merge supplies: {', '.join(problems[:5])}{more}. "
                "Merging them would import references that resolve to nothing. "
                "Include the missing entities in the archive, or merge the "
                "namespace that holds them first."
            )

    def _enforce_merge_gates(
        self, namespace: str, plans: dict[str, EntityPlan]
    ) -> None:
        """Refuse on the one thing no policy covers: an ID naming two things."""
        conflicts = [
            (entity_type, conflict)
            for entity_type, plan in plans.items()
            for conflict in plan.conflicts
        ]
        if not conflicts:
            return
        detail = "; ".join(
            f"{entity_type}: {conflict.reason}"
            for entity_type, conflict in conflicts[:5]
        )
        raise RestoreEngineError(
            f"Merge into '{namespace}' refused — {len(conflicts)} identity "
            f"conflict(s) between archive and target. {detail}. One ID names "
            "two different entities across the two sides; no automatic "
            "resolution is defensible, since picking either would destroy the "
            "other identity."
        )

    async def _apply_merge(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        doc_groups: dict[str, list[dict[str, Any]]],
        *,
        on_clash: str,
        older_versions: str = "retire",
        batch_size: int,
        streamed: _StreamedDocuments | None = None,
    ) -> None:
        """Write the plan. Inserts first, then policy-driven clash handling.

        With ``streamed``, documents are read from the archive again chain by
        chain and written in batches instead of coming from ``doc_groups``.
        """
        # The definitions' Registry entries are written with the definitions,
        # before the documents and their reporting gate; the rest at the end
        # (_DEFINITION_ENTRY_TYPES).
        entries_plan = plans.get("registry_entries")
        new_entries = entries_plan.to_insert if entries_plan else []
        definition_entries = [e for e in new_entries if _is_definition_entry(e)]
        other_entries = [e for e in new_entries if not _is_definition_entry(e)]
        recount = plans.get("documents") is not None or plans.get("files") is not None
        try:
            await self._write_merge_types(
                namespace, plans, doc_groups, definition_entries, other_entries,
                on_clash=on_clash, older_versions=older_versions,
                batch_size=batch_size, streamed=streamed,
            )
        except BaseException:
            # Documents land before files, so a merge stopped part-way has
            # written rows whose files are not yet counted: recount them
            # anyway, and let the merge's own failure stand.
            if recount:
                try:
                    await self._recount_file_references(namespace)
                except Exception as exc:  # noqa: BLE001 — the merge's error is the one to raise
                    self._emit(
                        "warning",
                        f"[{namespace}] the file reference recount after the failed "
                        f"merge failed too ({exc}); counts may be stale until the next "
                        "write to each file's documents",
                    )
            raise
        if recount:
            await self._recount_file_references(namespace)

        if new_entries:
            claimed, taken_by_other = await self._claim_inserted_entries(
                namespace, new_entries
            )
            self._report_claims(namespace, claimed, taken_by_other)

    async def _merge_documents(
        self,
        namespace: str,
        collection: Any,
        plan: EntityPlan,
        doc_groups: dict[str, list[dict[str, Any]]],
        *,
        on_clash: str,
        older_versions: str,
        batch_size: int,
        streamed: _StreamedDocuments | None,
    ) -> None:
        """A merge's documents: the reporting structure gate, the inserted
        chains (streamed from the archive, or from ``doc_groups``), then the
        clashes when the policy overwrites them."""
        # The structural gate a plain restore runs after templates: the
        # reporting tables the namespace's templates imply must exist and be
        # correctly shaped before any document moves. A merge plans templates
        # separately (definitions), so it runs here, once, ahead of the
        # documents.
        overwrites = on_clash in ("overwrite", "newer")
        written = {
            str(row.get("template_id"))
            for row in [*plan.to_insert, *(c.entity for c in plan.clashes if overwrites)]
            if row.get("template_id")
        }
        await self._reporting_phase_structure(namespace, merge=True, templates=written)

        if streamed is not None:
            await self._merge_insert_streamed(namespace, collection, streamed, batch_size)
            if overwrites:
                await self._overwrite_documents(
                    namespace, plan, doc_groups,
                    on_clash=on_clash, older_versions=older_versions,
                    sources=streamed.clash_sources(), batch_size=batch_size,
                )
            return

        # An inserted document brings its whole version chain.
        rows = [
            row
            for head in plan.to_insert
            for row in doc_groups.get(head.get("document_id", ""), [head])
        ]
        await self._merge_insert_rows(namespace, collection, "documents", rows, batch_size)
        if overwrites:
            await self._overwrite_documents(
                namespace, plan, doc_groups,
                on_clash=on_clash, older_versions=older_versions,
            )

    async def _merge_insert_rows(
        self,
        namespace: str,
        collection: Any,
        entity_type: str,
        rows: list[dict[str, Any]],
        batch_size: int,
    ) -> None:
        """Insert rows in batches of ``batch_size``, and say how many."""
        for start in range(0, len(rows), batch_size):
            await self._insert_batch(collection, rows[start:start + batch_size], entity_type)
        if rows:
            self._emit(
                f"phase_{entity_type}",
                f"[{namespace}] inserted {len(rows)} {entity_type}",
            )

    async def _merge_insert_streamed(
        self,
        namespace: str,
        collection: Any,
        streamed: _StreamedDocuments,
        batch_size: int,
    ) -> None:
        """Insert the streamed inserted chains, whole chains at a time, in
        batches of at least ``batch_size`` rows, and say how many."""
        inserted = 0
        batch: list[dict[str, Any]] = []
        for chain in streamed.inserted_chains():
            batch.extend(chain)
            if len(batch) >= batch_size:
                await self._insert_batch(collection, batch, "documents")
                inserted += len(batch)
                batch = []
        if batch:
            await self._insert_batch(collection, batch, "documents")
            inserted += len(batch)
        if inserted:
            self._emit("phase_documents", f"[{namespace}] inserted {inserted} documents")

    @staticmethod
    def _as_timestamp(value: Any) -> datetime | None:
        """Normalize a stored timestamp for comparison, or None if unusable.

        The two sides arrive in different shapes: the target's rows come from
        MongoDB as naive datetimes that are UTC by convention, the archive's
        come from JSONL as ISO-8601 strings. Comparing them raw raises rather
        than answering, so both are made timezone-aware (a naive value is read
        as UTC; an explicit offset is kept, which compares as the same
        instant) and anything unparseable becomes None — which the caller
        treats as "cannot tell".
        """
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None

    def _archive_is_newer(
        self, archive_row: dict[str, Any], target_row: dict[str, Any]
    ) -> bool | None:
        """Is the archive's copy more recently updated? None = cannot tell.

        Compares ``updated_at`` — when the content last changed — rather than
        the document_id's embedded UUID7 time, which records when the document
        was first CREATED. A document created in January and edited yesterday
        would otherwise lose to one created in June and never touched.

        Across two installs this is only as good as the two machines' clock
        discipline: UTC removes timezone error, not skew.
        """
        archive_at = self._as_timestamp(archive_row.get("updated_at"))
        target_at = self._as_timestamp(target_row.get("updated_at"))
        if archive_at is None or target_at is None:
            return None
        return archive_at > target_at

    async def _overwrite_documents(
        self,
        namespace: str,
        plan: EntityPlan,
        doc_groups: dict[str, list[dict[str, Any]]],
        *,
        on_clash: str = "overwrite",
        older_versions: str = "retire",
        sources: Iterator[tuple[Clash, dict[str, Any]]] | None = None,
        batch_size: int = 500,
    ) -> None:
        """Let the archive win, going forward.

        The archive's LATEST version of a clashing identity is appended as one
        new version on top of the target's head, adopting the target's
        document_id. The target's history is preserved and the archive's is not
        spliced in: interleaving two independent version chains has no defined
        order and would corrupt the (document_id, version) contract.

        ``older_versions='retire'`` then soft-deletes the target's active
        versions below the appended one, as a normal write retires the version
        it supersedes. ``'keep'`` leaves them active alongside it.

        A ``versioned: false`` template has no such contract to protect — its
        lifecycle IS overwrite-in-place — so there the single row is replaced.

        Under ``newer`` the same write happens, but only where the archive's
        copy is STRICTLY more recently updated. A tie keeps the target: equal
        timestamps carry no information about which side to prefer, and
        writing on no information is worse than leaving a live namespace
        alone. An unusable timestamp on either side is reported and also keeps
        the target.
        """
        clashes = plan.differing_clashes
        if not clashes:
            return

        db_name, coll_name = COLLECTION_MAP["documents"]
        collection = self._mongo[db_name][coll_name]
        versioned = await self._template_versioned_flags(
            namespace, [c.entity.get("template_id") for c in clashes]
        )

        if sources is None:
            sources = _clash_heads(clashes, doc_groups)

        # The plan read the target through a projection, so it cannot tell an
        # identical clash from a differing one. The full target heads are read
        # here, for the clashes only, so an archive copy equal to the target's
        # adds no version: a new version that changes nothing is noise.
        target_heads = await self._full_target_heads(collection, namespace, clashes)

        tally = _OverwriteTally()
        new_rows: list[dict[str, Any]] = []
        for clash, latest in sources:
            target_head = max(clash.targets, key=lambda r: r.get("version", 1))
            full_head = target_heads.get(
                (target_head.get("document_id"), target_head.get("version", 1))
            )
            if not self._archive_wins(latest, target_head, full_head, on_clash, tally):
                continue

            row = dict(latest)
            row["document_id"] = target_head.get("document_id")
            retire_archived_row(row)

            if _is_unversioned(versioned, clash):
                if await self._replace_unversioned(collection, namespace, row, target_head, full_head):
                    tally.replaced += 1
                else:
                    tally.changed_meanwhile += 1
                continue
            row["version"] = target_head.get("version", 1) + 1
            new_rows.append(row)
            tally.appended += 1
            if len(new_rows) >= batch_size:
                tally.retired += await self._flush_overwrites(
                    collection, namespace, new_rows, older_versions,
                )
                new_rows = []

        if new_rows:
            tally.retired += await self._flush_overwrites(
                collection, namespace, new_rows, older_versions,
            )
        self._report_overwrites(namespace, tally, on_clash=on_clash, older_versions=older_versions)

    def _archive_wins(
        self,
        latest: dict[str, Any],
        target_head: dict[str, Any],
        full_head: dict[str, Any] | None,
        on_clash: str,
        tally: _OverwriteTally,
    ) -> bool:
        """Whether the archive's copy is written over the target's, counting
        the clashes it is not: an identical copy adds nothing, and under
        ``newer`` a copy not strictly newer (or not comparable) keeps the
        target."""
        if full_head is not None and _same_document_content(latest, full_head):
            tally.identical += 1
            return False
        if on_clash != "newer":
            return True
        verdict = self._archive_is_newer(latest, target_head)
        if verdict is None:
            tally.undecidable += 1
            return False
        if verdict:
            return True
        if self._as_timestamp(latest.get("updated_at")) == self._as_timestamp(
            target_head.get("updated_at")
        ):
            tally.ties += 1
        else:
            tally.kept += 1
        return False

    @staticmethod
    async def _replace_unversioned(
        collection: Any,
        namespace: str,
        row: dict[str, Any],
        target_head: dict[str, Any],
        full_head: dict[str, Any] | None,
    ) -> bool:
        """Replace the single row of a versioned:false document in place, only
        if it still holds the revision the merge read. False when another
        write landed on it meanwhile (or it is gone): nothing is replaced."""
        row["version"] = target_head.get("version", 1)
        # Replacing the row in place is a write like any other: its
        # if_match token moves on, so a writer still holding the old
        # one cannot overwrite what the merge brought in.
        head_revision = (full_head or target_head).get("revision")
        row["revision"] = (
            head_revision if head_revision is not None else row["version"]
        ) + 1
        # Archive rows carry ISO strings; the services store dates.
        # _insert_batch re-hydrates for every other write path.
        _rehydrate_timestamps(row)
        result = await collection.replace_one(
            {
                "namespace": namespace,
                "document_id": row["document_id"],
                "version": row["version"],
                "revision": head_revision,
                # A soft delete moves no revision: the status the merge read
                # must hold too, or a delete landing meanwhile is undone.
                "status": (full_head or target_head).get("status", "active"),
            },
            row,
        )
        return result.matched_count > 0

    async def _flush_overwrites(
        self,
        collection: Any,
        namespace: str,
        rows: list[dict[str, Any]],
        older_versions: str,
    ) -> int:
        """Insert one batch of appended heads, then retire what they
        supersede — after the insert, so a failed insert never leaves a
        document with no active version. A batch that fails partway still
        retires what the rows that did land supersede, so no document is
        left with two active versions, and the error says how many
        landed. Returns the versions retired."""
        try:
            await self._insert_batch(collection, rows, "documents")
        except Exception as exc:
            # Any failure, the driver's own included (a lost connection, a
            # timeout): some rows may have landed either way.
            landed = await self._landed_rows(collection, namespace, rows)
            await _retire_superseded(collection, namespace, landed, older_versions)
            raise RestoreEngineError(
                f"{exc} — in '{namespace}', {len(landed)} of {len(rows)} "
                "overwriting version(s) in that batch were written (their "
                "older versions retired as the policy says); the merge "
                "stopped there."
            ) from exc
        return await _retire_superseded(collection, namespace, rows, older_versions)

    def _report_overwrites(
        self, namespace: str, tally: _OverwriteTally, *, on_clash: str, older_versions: str,
    ) -> None:
        """Say what the overwrite did, and warn about clashes it could not
        compare by update time."""
        summary = (
            f"[{namespace}] {tally.appended + tally.replaced} clashing document(s) taken "
            f"from the archive ({tally.appended} appended as a new version, "
            f"{tally.replaced} replaced in place on versioned:false templates)"
        )
        if older_versions == "retire":
            summary += f", {tally.retired} older version(s) retired"
        else:
            summary += ", older versions kept active"
        if on_clash == "newer":
            summary += f", {tally.kept} kept because the target's copy is newer"
        if tally.identical:
            summary += f", {tally.identical} left alone because the archive's copy is identical"
        self._emit("phase_documents", summary)

        if tally.changed_meanwhile:
            self._emit(
                "warning",
                f"[{namespace}] {tally.changed_meanwhile} versioned:false document(s) "
                "were written, deleted or removed on the target while the merge "
                "ran — the target's state was kept for those.",
            )
        if tally.ties:
            self._emit(
                "warning",
                f"[{namespace}] {tally.ties} document(s) were updated at the same "
                "time on both sides, so neither copy is newer — the target's copy "
                "was kept for those.",
            )
        if tally.undecidable:
            self._emit(
                "warning",
                f"[{namespace}] {tally.undecidable} document(s) could not be compared "
                "by update time (a missing or unparseable updated_at on one "
                "side) — the target's copy was kept for those.",
            )

    async def _full_target_heads(
        self, collection: Any, namespace: str, clashes: list[Any],
    ) -> dict[tuple[Any, int], dict[str, Any]]:
        """The target's current rows for these clashes, unprojected, keyed by
        (document_id, version)."""
        wanted = []
        for clash in clashes:
            head = max(clash.targets, key=lambda r: r.get("version", 1))
            wanted.append({"document_id": head.get("document_id"), "version": head.get("version", 1)})
        found: dict[tuple[Any, int], dict[str, Any]] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            async for row in collection.find({"namespace": namespace, "$or": chunk}):
                found[(row.get("document_id"), row.get("version", 1))] = row
        return found

    async def _landed_rows(
        self, collection: Any, namespace: str, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Which of these rows a failed batch insert did write."""
        keys = [{"document_id": r["document_id"], "version": r["version"]} for r in rows]
        present: set[tuple[Any, Any]] = set()
        for start in range(0, len(keys), 500):
            async for row in collection.find(
                {"namespace": namespace, "$or": keys[start:start + 500]},
                {"document_id": 1, "version": 1},
            ):
                present.add((row.get("document_id"), row.get("version")))
        return [r for r in rows if (r["document_id"], r["version"]) in present]

    async def _write_merge_types(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        doc_groups: dict[str, list[dict[str, Any]]],
        definition_entries: list[dict[str, Any]],
        other_entries: list[dict[str, Any]],
        *,
        on_clash: str,
        older_versions: str,
        batch_size: int,
        streamed: _StreamedDocuments | None,
    ) -> None:
        """Each planned type's rows, in MERGE_ENTITY_ORDER; the definitions'
        Registry entries go with the first of documents / registry_entries."""
        entries_written = False
        for entity_type in MERGE_ENTITY_ORDER:
            plan = plans.get(entity_type)
            if plan is None:
                continue
            db_name, coll_name = COLLECTION_MAP[entity_type]
            collection = self._mongo[db_name][coll_name]
            if entity_type in ("documents", "registry_entries") and not entries_written:
                entries_db, entries_coll = COLLECTION_MAP["registry_entries"]
                await self._merge_insert_rows(
                    namespace, self._mongo[entries_db][entries_coll],
                    "registry_entries", definition_entries, batch_size,
                )
                entries_written = True
            if entity_type == "registry_entries":
                await self._merge_insert_rows(
                    namespace, collection, entity_type, other_entries, batch_size,
                )
                continue
            if entity_type != "documents":
                await self._merge_insert_rows(
                    namespace, collection, entity_type, plan.to_insert, batch_size,
                )
                continue
            await self._merge_documents(
                namespace, collection, plan, doc_groups, on_clash=on_clash,
                older_versions=older_versions, batch_size=batch_size, streamed=streamed,
            )

    async def _recount_file_references(self, namespace: str) -> None:
        """Recount the reference_count (and the status that follows it) of
        every file this merge can have changed: the files of the namespace,
        and every file its documents reference, wherever that file lives.

        A merge writes documents as raw rows, so counts are recomputed
        afterwards rather than adjusted, by the rule document writes keep
        (document_service._update_file_reference_counts): a file's count is
        the number of file_references entries naming it across the head
        version of each document, in any namespace — one per entry, so a
        head that names a file in two fields holds it twice. The head is a
        document's highest active version; an older version that
        older_versions=keep leaves active is not counted, since an edit or a
        delete of the document releases only its head's references. Files no
        document of this namespace references and that live elsewhere are
        never read or written. Every id list is sent in batches."""
        files_db, files_coll = COLLECTION_MAP["files"]
        docs_db, docs_coll = COLLECTION_MAP["documents"]
        files = self._mongo[files_db][files_coll]
        docs = self._mongo[docs_db][docs_coll]

        touched: set[str] = {
            str(f) for f in await files.distinct("file_id", {"namespace": namespace}) if f
        }
        touched |= {
            str(f) for f in await docs.distinct(
                "file_references.file_id", {"namespace": namespace},
            ) if f
        }
        if not touched:
            return
        ids = sorted(touched)
        counts = await self._head_file_reference_counts(docs, ids)

        recounted = status_only = 0
        for start in range(0, len(ids), _RECOUNT_BATCH):
            async for f in files.find(
                {"file_id": {"$in": ids[start:start + _RECOUNT_BATCH]}},
                {"file_id": 1, "reference_count": 1, "status": 1},
            ):
                update = _recounted_file(f, counts.get(str(f.get("file_id")), 0))
                if update:
                    await files.update_one({"_id": f["_id"]}, {"$set": update})
                    if "reference_count" in update:
                        recounted += 1
                    else:
                        status_only += 1
        if recounted or status_only:
            self._emit(
                "phase_files",
                f"[{namespace}] merge: {recounted} file reference count(s) recounted "
                f"and {status_only} file status(es) corrected from the documents "
                "(files in any namespace its documents reference)",
            )

    @staticmethod
    async def _head_file_reference_counts(docs: Any, ids: list[str]) -> dict[str, int]:
        """Per file id, its file_references entries across the head version
        of each document that holds one of ``ids`` in an active version."""
        holders: set[str] = set()
        for start in range(0, len(ids), _RECOUNT_BATCH):
            holders |= {
                str(d) for d in await docs.distinct("document_id", {
                    "status": "active",
                    "file_references.file_id": {"$in": ids[start:start + _RECOUNT_BATCH]},
                }) if d
            }
        wanted = set(ids)
        counts: dict[str, int] = {}
        documents = sorted(holders)
        for start in range(0, len(documents), _RECOUNT_BATCH):
            async for row in docs.aggregate([
                {"$match": {"status": "active",
                            "document_id": {"$in": documents[start:start + _RECOUNT_BATCH]}}},
                {"$sort": {"document_id": 1, "version": -1}},
                {"$group": {"_id": "$document_id",
                            "refs": {"$first": "$file_references.file_id"}}},
            ]):
                for file_id in row.get("refs") or []:
                    if file_id and str(file_id) in wanted:
                        counts[str(file_id)] = counts.get(str(file_id), 0) + 1
        return counts

    async def _template_versioned_flags(
        self, namespace: str, template_ids: list[str | None]
    ) -> dict[str, bool]:
        """``versioned`` per template — immutable after create, so any row of
        a template answers for all its versions. A document may be pinned to a
        template in another namespace (a shared one); a template_id is a
        canonical id, so the ids the target namespace does not hold are looked
        up wherever they live."""
        wanted = [t for t in template_ids if t]
        flags: dict[str, bool] = {}
        if not wanted:
            return flags
        db_name, coll_name = COLLECTION_MAP["templates"]
        templates = self._mongo[db_name][coll_name]
        projection = {"template_id": 1, "versioned": 1}
        async for row in templates.find(
            {"namespace": namespace, "template_id": {"$in": wanted}}, projection,
        ):
            flags[row.get("template_id")] = row.get("versioned", True)
        elsewhere = [t for t in wanted if t not in flags]
        if elsewhere:
            async for row in templates.find({"template_id": {"$in": elsewhere}}, projection):
                flags.setdefault(row.get("template_id"), row.get("versioned", True))
        return flags

    async def _check_namespace_exists(self, namespace: str) -> None:
        """Merge inverts the empty-target precondition: the namespace must be
        there. Creating it would mean there is nothing to merge into, which is
        a plain restore."""
        ns_doc = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": namespace}
        )
        if ns_doc is None:
            raise RestoreEngineError(
                f"Namespace '{namespace}' does not exist — merge restores into "
                "an existing namespace. Use a plain restore to create it from "
                "this archive."
            )

    async def _check_merge_reporting_precondition(self, namespace: str) -> None:
        """Reporting must be usable, but a populated schema is expected here.

        The plain restore refuses a non-empty reporting schema because stale
        tables would shadow restored data. A merge is *supposed* to land in a
        live namespace, so the same finding is normal; only unusable
        bookkeeping — which would make the merge complete with a silently
        broken reporting layer — still refuses.
        """
        if not self._reporting:
            return
        parity = await self._reporting.parity(namespace, include_counts=False)
        if parity is None:
            self._reporting_unavailable(namespace, "precondition check")
            return
        if not parity.get("bookkeeping_tables_ok", True):
            raise RestoreEngineError(
                f"Reporting bookkeeping tables are unusable: "
                f"{parity.get('bookkeeping_error')} — the merge would complete "
                "with a silently broken reporting layer. Remediate first."
            )
        if not parity.get("schema_present"):
            self._emit(
                "warning",
                f"[{namespace}] has no reporting schema yet — the merge will "
                "create it, but a live namespace without one means reporting "
                "was never synced here. Verify after the merge.",
            )

    async def _report_namespace_config_drift(
        self, namespace: str, ns_config: NamespaceConfig | None
    ) -> None:
        """Compare the archive's namespace config against the live one.

        Merge never applies it: the target namespace is live and its
        configuration belongs to whoever is running it, not to an archive
        taken at some earlier point. Drift is still worth naming — an
        id_config or isolation_mode that has moved changes how the merged
        data will behave.
        """
        if ns_config is None:
            return
        live = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": namespace}
        )
        if live is None:
            return

        drift: list[str] = []
        for field_name, archived in (
            ("isolation_mode", ns_config.isolation_mode),
            ("id_config", ns_config.id_config),
            ("allowed_external_refs", ns_config.allowed_external_refs),
            ("deletion_mode", ns_config.deletion_mode),
            ("inbound_references", ns_config.inbound_references),
        ):
            if archived is None:
                continue
            current = live.get(field_name)
            if current != archived:
                drift.append(f"{field_name}: target={current!r} archive={archived!r}")

        if drift:
            self._emit(
                "warning",
                f"[{namespace}] namespace config differs from the archive's "
                f"({'; '.join(drift)}) — the target's config is kept; a merge "
                "never applies the archive's.",
            )

    def _archive_namespaces(
        self, reader: ArchiveReader, manifest: Any
    ) -> list[str]:
        """The namespaces an archive actually carries, or a loud failure.

        Belt behind the endpoint's synchronous 400: a pre-v3 archive is flat,
        so every namespaces/<ns>/<entity>.jsonl read would find nothing and the
        job would complete "successfully" having written zero entities.

        A manifest may also *claim* 3.x yet carry no namespaces/ subtree
        (hand-assembled or truncated zip). ``namespace_prefixes()`` can be
        non-empty from the manifest alone, so the actual layout is checked too
        — otherwise the same silent zero-entity run happens.

        The one archive that legitimately has no subtree is a backup of
        namespaces holding no entities: the writer skips empty entity files,
        so only the manifest remains. It is told apart from a truncated zip by
        the manifest itself listing its namespaces with every count at zero.
        A manifest without that list (a legacy shape, or a derived archive that
        dropped its empty namespaces) proves nothing and is still refused.
        """
        if not manifest.format_version.startswith("3"):
            raise RestoreEngineError(
                f"Archive is format v{manifest.format_version} — the restore "
                "engine reads the v3 layout. Convert it first: "
                "python -m wip_toolkit convert-archive <src> <dst>"
            )
        declares_only_empty = (
            bool(manifest.namespaces)
            and all(entry.counts.total == 0 for entry in manifest.namespaces)
            and manifest.counts.total == 0
        )
        if not reader.list_namespaces() and not declares_only_empty:
            raise RestoreEngineError(
                "Archive manifest claims v3 but the zip has no namespaces/ "
                "tree — malformed archive, nothing to restore"
            )
        # The restore route admin-checks namespaces by this same rule
        # (backup_service.read_archive_restore_scope); change both together.
        namespaces: list[str] = manifest.namespace_prefixes() or reader.list_namespaces()
        if not namespaces:
            raise RestoreEngineError("Archive contains no namespaces to restore")
        return namespaces

    def _resolve_targets(
        self,
        reader: ArchiveReader,
        manifest: Any,
        target_namespace: str,
        *,
        allow_redirect: bool = False,
    ) -> list[tuple[str, str]]:
        """Validate the archive's shape and pair each source namespace with
        its target. Shared by every mode that reads an archive.

        A plain restore writes each namespace to itself and rejects a
        different target: it preserves everything verbatim, and the archived
        entities and composite keys embed the source namespace, so writing
        them elsewhere would produce records still carrying it.

        A merge may redirect (``allow_redirect``). It rewrites the namespace
        on every entity and rehashes the composite keys that embed it, which
        is what makes "fold NS2's archive into NS1" work. The IDs still have
        to be free — see :meth:`_check_ids_are_free`.
        """
        source_namespaces = self._archive_namespaces(reader, manifest)

        if target_namespace:
            if len(source_namespaces) > 1:
                raise RestoreEngineError(
                    "target_namespace override is not supported for a "
                    "multi-namespace archive; each namespace restores "
                    "to itself."
                )
            if target_namespace != source_namespaces[0]:
                if allow_redirect:
                    return [(source_namespaces[0], target_namespace)]
                raise RestoreEngineError(
                    f"target_namespace '{target_namespace}' differs from "
                    f"the archive's namespace '{source_namespaces[0]}' — "
                    "an ID-preserving restore cannot re-namespace data. "
                    "Restore to the archive's own namespace, or merge it into "
                    "the namespace you want it in."
                )
        return [(ns, ns) for ns in source_namespaces]

    @staticmethod
    def _claim_rows_for(
        entries: list[dict[str, Any]], now: datetime
    ) -> list[dict[str, Any]]:
        """Derive the claim rows a batch of registry entries implies.

        One per entry primary key, one per active embedded synonym. Synonyms carry
        their own namespace/entity_type — a synonym may live in a different
        namespace than the entry it points at — so claims are keyed from the
        synonym's fields, not the owner's.

        An empty hash means "this entity opts out of dedup" (legacy template
        entries, identity-less documents). The claims unique index exempts it
        and so does this.
        """
        rows: list[dict[str, Any]] = []
        for entry in entries:
            pairs = [(
                entry.get("namespace"),
                entry.get("entity_type"),
                entry.get("primary_composite_key_hash"),
                "primary",
            )]
            pairs += [
                (
                    syn.get("namespace"),
                    syn.get("entity_type"),
                    syn.get("composite_key_hash"),
                    "synonym",
                )
                for syn in entry.get("synonyms", [])
                # An inactive synonym belongs to a retired entity: recorded,
                # but it holds no claim.
                if isinstance(syn, dict) and syn.get("status") != "inactive"
            ]
            for ns, entity_type, key_hash, kind in pairs:
                if not key_hash or not ns or not entity_type:
                    continue
                rows.append({
                    "namespace": ns,
                    "entity_type": entity_type,
                    "composite_key_hash": key_hash,
                    "owner_entry_id": entry.get("entry_id"),
                    "kind": kind,
                    "state": "confirmed",
                    "created_at": now,
                })
        return rows

    async def _insert_claims(
        self, namespace: str, claim_rows: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Insert derived claims. Returns (claimed, taken_by_other).

        A duplicate is one of two things and they are told apart before
        anything is said: the key is already claimed by *this* entry (the
        rebuild is simply idempotent, and a merge into a namespace that
        already has claims hits this on every row) or by a *different* one,
        which means the incumbent keeps the key and the entry sharing it is
        not gate-protected. Only the second is worth a warning, and neither
        fails a restore whose data is already committed.
        """
        from pymongo.errors import BulkWriteError

        if not claim_rows:
            return (0, 0)

        claims_db, claims_coll_name = CLAIMS_COLLECTION
        claims = self._mongo[claims_db][claims_coll_name]
        try:
            result = await claims.insert_many(claim_rows, ordered=False)
            return (len(result.inserted_ids), 0)
        except BulkWriteError as exc:
            write_errors = exc.details.get("writeErrors", [])
            duplicates = [e for e in write_errors if e.get("code") == 11000]
            if len(duplicates) != len(write_errors):
                raise RestoreEngineError(
                    f"Claim rebuild failed for namespace '{namespace}': "
                    f"{write_errors[0].get('errmsg', 'unknown error')}"
                ) from exc
            mine = 0
            for write_error in duplicates:
                rejected = write_error.get("op") or claim_rows[write_error["index"]]
                incumbent = await claims.find_one({
                    "namespace": rejected["namespace"],
                    "entity_type": rejected["entity_type"],
                    "composite_key_hash": rejected["composite_key_hash"],
                })
                if incumbent and incumbent.get("owner_entry_id") == rejected["owner_entry_id"]:
                    mine += 1
            return (len(claim_rows) - len(duplicates), len(duplicates) - mine)

    async def _claim_inserted_entries(
        self, namespace: str, entries: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Claim the keys of registry entries as they are written.

        Derived inline rather than by re-reading the collection afterwards:
        each entry is already in hand at this point, and a namespace-wide
        re-scan is pure waste on top of the inserts that are genuinely
        required.
        """
        rows = self._claim_rows_for(entries, datetime.now(UTC))
        return await self._insert_claims(namespace, rows)

    def _report_claims(
        self, namespace: str, claimed: int, taken_by_other: int
    ) -> None:
        logger.info(
            "Claimed %d composite key(s) for namespace %s (%d owned by other "
            "entries)", claimed, namespace, taken_by_other,
        )
        if taken_by_other:
            self._emit(
                "warning",
                f"[{namespace}] {taken_by_other} composite key(s) were already "
                "claimed by other entries and were left with their existing "
                "owner — the entries sharing those keys are not gate-protected. "
                "Review with the Registry's claim reconcile.",
            )
        elif claimed:
            self._emit(
                "phase_claims",
                f"[{namespace}] claimed {claimed} composite key(s)",
            )

    async def _check_namespace_empty(self, namespace: str) -> None:
        """Verify no data exists for this namespace across all collections."""
        non_empty: list[str] = []
        for _entity_type, (db_name, coll_name) in COLLECTION_MAP.items():
            count = await self._mongo[db_name][coll_name].count_documents(
                {"namespace": namespace}, limit=1
            )
            if count > 0:
                non_empty.append(f"{db_name}.{coll_name}")

        if non_empty:
            raise RestoreEngineError(
                f"Namespace '{namespace}' is not empty. "
                f"Found data in: {', '.join(non_empty)}. "
                "Restore requires an empty namespace."
            )

    def _dry_run_report(
        self,
        reader: ArchiveReader,
        targets: list[tuple[str, str]],
        entry_by_prefix: dict[str, Any],
        restore_order: list[str],
        *,
        skip_documents: bool,
        skip_files: bool,
        dropped: Any = None,
    ) -> None:
        """Emit what a real run would restore, then complete without writing.

        Counts are what the real run writes (_would_restore): the manifest's
        per-namespace entry when nothing is left out, else the archive's rows
        less the skipped types and the dropped edges. All preconditions
        have already run at this point — a dry run that reaches this method
        would have started writing if it were a real run.
        """
        for src, tgt in targets:
            entry = entry_by_prefix.get(src)
            parts: list[str] = []
            for entity_type in restore_order:
                if skip_documents and entity_type == "documents":
                    parts.append("documents=skipped")
                    continue
                if skip_files and entity_type == "files":
                    parts.append("files=skipped")
                    continue
                count = _would_restore(
                    reader, entry, src, entity_type,
                    _skipped_types(skip_documents, skip_files), dropped,
                )
                parts.append(f"{entity_type}={count}")
            self._emit(
                "phase_dry_run",
                f"[{tgt}] dry run — would restore: {', '.join(parts)} "
                "(plus rebuilt composite-key claims)",
            )

        self._emit(
            "complete",
            "Dry run complete — preconditions passed, no changes made",
            percent=100,
            details=self.result,
        )

    # -- Reporting verification phases (restore-verification design) --------
    #
    # Poll pacing: structure materializes within a couple of batch-sync
    # seconds. Counts follow the batch sync of the full document set, which
    # for a large namespace takes minutes — so the count wait follows
    # progress: it lasts while reporting rows keep landing and gives up after
    # _REPORTING_COUNTS_STALL_S without a change, or at the overall cap.
    # Exceeding either delays only the warning, never the restore's data.
    _REPORTING_STRUCTURE_TIMEOUT_S = 30
    _REPORTING_COUNTS_STALL_S = 60
    _REPORTING_COUNTS_MAX_S = 1800
    _REPORTING_POLL_INTERVAL_S = 3

    def _reporting_unavailable(self, namespace: str, what: str) -> None:
        """Disable further reporting phases and surface why, loudly once."""
        self._emit(
            "warning",
            f"[{namespace}] reporting-sync unreachable during {what} — "
            "reporting verification skipped for this restore",
        )
        self._set_reporting_verdict(
            namespace, "unavailable", f"reporting-sync unreachable during {what}"
        )
        self._reporting = None

    def _set_reporting_verdict(
        self,
        namespace: str,
        status: str,
        reason: str | None = None,
        gaps: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record one namespace's reporting verdict. The first verdict
        stands: reporting-sync going away mid-restore must not overwrite a
        namespace that was already verified."""
        self._reporting_verdicts.setdefault(
            namespace, {"status": status, "reason": reason, "gaps": gaps or []}
        )

    def _finish_reporting_verification(
        self, namespaces: list[str], unchecked_reason: str
    ) -> None:
        """Put the reporting verdict on the result: one per namespace the
        run wrote, and the worst of them as the overall status, so a script
        can tell "verified" from "gave up" without reading warnings."""
        for namespace in dict.fromkeys(namespaces):
            self._set_reporting_verdict(namespace, "not_checked", unchecked_reason)
        verdicts = {
            ns: v for ns, v in self._reporting_verdicts.items() if ns in namespaces
        }
        overall = max(
            (v["status"] for v in verdicts.values()),
            key=_VERDICT_RANK.__getitem__,
            default="not_checked",
        )
        self.result["reporting_verification"] = {
            "status": overall, "namespaces": verdicts,
        }

    async def _check_reporting_precondition(
        self, namespace: str, drop_stale: bool, *, dry_run: bool = False
    ) -> None:
        """The namespace's reporting schema must be absent/empty before restore.

        Stale tables would shadow the restored data (a complete-looking but
        dead copy). With ``drop_stale`` the stale schema is dropped after the
        operator's explicit opt-in; without it, the restore refuses and names
        the flag. A bookkeeping-table shape problem also fails here — loudly,
        before anything is written — instead of surfacing as per-type sync
        failures afterwards.

        A dry run still *fails* on the same conditions a real run would refuse
        (that is the information a dry run exists to surface), but never
        mutates: with ``drop_stale`` set it reports the schema that would be
        dropped instead of dropping it.
        """
        if not self._reporting:
            return
        parity = await self._reporting.parity(namespace, include_counts=False)
        if parity is None:
            self._reporting_unavailable(namespace, "precondition check")
            return
        if not parity.get("bookkeeping_tables_ok", True):
            raise RestoreEngineError(
                f"Reporting bookkeeping tables are unusable: "
                f"{parity.get('bookkeeping_error')} — restore would complete "
                "with a silently empty reporting layer. Remediate first."
            )
        if parity.get("schema_present") and parity.get("table_count", 0) > 0:
            if not drop_stale:
                raise RestoreEngineError(
                    f"Reporting schema '{parity.get('schema_name')}' already "
                    f"holds {parity.get('table_count')} table(s) for namespace "
                    f"'{namespace}' — stale reporting data would shadow the "
                    "restore. Re-run with drop_stale_reporting=true to drop "
                    "it, or clear it manually."
                )
            if dry_run:
                self._emit(
                    "phase_dry_run",
                    f"[{namespace}] dry run — stale reporting schema "
                    f"({parity.get('table_count')} table(s)) would be dropped "
                    "(drop_stale_reporting is set)",
                )
                return
            if not await self._reporting.drop_namespace_schema(namespace):
                raise RestoreEngineError(
                    f"Could not drop stale reporting schema for '{namespace}' "
                    "(drop_stale_reporting was set) — refusing to restore "
                    "over shadowed reporting data."
                )
            self._emit(
                "phase_reporting_drop",
                f"[{namespace}] dropped stale reporting schema "
                f"({parity.get('table_count')} table(s))",
            )

    async def _reporting_phase_structure(
        self, namespace: str, *, merge: bool = False, templates: set[str] | None = None,
    ) -> None:
        """Post-templates gate: reporting tables must materialize correctly.

        Triggers a namespace batch sync (no documents are restored yet, so
        tables materialize empty) and polls the structure-only parity until
        green. A persistent structural failure HALTS the restore before any
        document moves. ``merge`` says the namespace held data before this
        run, which the halt's advice must not tell the operator to delete.
        ``templates``: for a merge, the ids of the templates whose documents
        it writes. Only their tables (and unusable bookkeeping) halt it; a
        live namespace's issue with another template's table was there
        before the merge and is reported, not a reason to refuse it.
        """
        if not self._reporting:
            return
        self._emit(
            "phase_reporting_structure",
            f"[{namespace}] verifying reporting tables for restored templates",
        )
        await self._reporting.trigger_batch_sync(namespace)
        deadline = asyncio.get_event_loop().time() + self._REPORTING_STRUCTURE_TIMEOUT_S
        parity: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            parity = await self._reporting.parity(namespace, include_counts=False)
            if parity is None:
                self._reporting_unavailable(namespace, "structure verification")
                return
            if parity.get("bookkeeping_tables_ok", True) and parity.get("structural_issues", 0) == 0:
                return
            if templates is not None and await self._merge_passes_structure(
                namespace, parity, templates,
            ):
                return
            await asyncio.sleep(self._REPORTING_POLL_INTERVAL_S)
        raise RestoreEngineError(_structure_halt(
            namespace, self._REPORTING_STRUCTURE_TIMEOUT_S,
            await self._structure_issues(namespace, parity, templates), merge=merge,
        ))

    async def _structure_issues(
        self, namespace: str, parity: dict[str, Any] | None, templates: set[str] | None,
    ) -> list[str]:
        """The failing tables to name in the halt (for a merge, only those of
        templates it writes), each with reporting-sync's reason for it. The
        jobs are matched by template id, since another namespace can hold a
        template of the same value; a missing table says only that the sync
        did not get there."""
        failing = _structure_failures(parity)
        ids = await self._template_ids_of(namespace, failing)
        if templates is not None:
            failing = [t for t in failing if ids.get(_parity_owner(namespace, t)) in templates]
        sync_errors = await self._reporting.failed_jobs(namespace, set(ids.values()))
        return [
            _structure_issue(t, sync_errors.get(ids.get(_parity_owner(namespace, t), "")))
            for t in failing
        ]

    async def _merge_passes_structure(
        self, namespace: str, parity: dict[str, Any], templates: set[str],
    ) -> bool:
        """Whether a merge may go on although the structure check is not
        green: the bookkeeping is usable and no failing table is one of a
        template whose documents it writes. The others are reported."""
        if not parity.get("bookkeeping_tables_ok", True):
            return False
        failing = _structure_failures(parity)
        ids = await self._template_ids_of(namespace, failing)
        if any(ids.get(_parity_owner(namespace, t)) in templates for t in failing):
            return False
        self._emit(
            "warning",
            f"[{namespace}] reporting tables of templates this merge writes no "
            "documents of are missing or misshaped; the merge goes on: "
            f"{', '.join(sorted(str(t.get('template_value')) for t in failing))}",
        )
        return True

    async def _template_ids_of(
        self, namespace: str, templates: list[dict[str, Any]]
    ) -> dict[tuple[str, str], str]:
        """The template_id of each parity row's template, keyed by
        (owning namespace, value): the namespace's own, or the other
        namespace that owns a shared one."""
        wanted = {_parity_owner(namespace, t) for t in templates}
        if not wanted:
            return {}
        db_name, coll_name = COLLECTION_MAP["templates"]
        ids: dict[tuple[str, str], str] = {}
        async for row in self._mongo[db_name][coll_name].find(
            {"$or": [{"namespace": ns, "value": value} for ns, value in wanted]},
            {"namespace": 1, "value": 1, "template_id": 1},
        ):
            ids[(str(row.get("namespace")), str(row.get("value")))] = str(
                row.get("template_id")
            )
        return ids

    async def _reporting_phase_counts(
        self, namespace: str, *, skip_documents: bool
    ) -> None:
        """Post-data count parity: wait while the sync progresses, then
        complete WITH warning if parity still does not hold.

        Never a hard fail — data is safely in MongoDB at this point and the
        reporting layer can catch up or be replayed; the warning makes the
        gap visible instead of silent. Progress is the namespace's total
        reporting row count from the parity check itself: while it keeps
        changing the sync is alive, however long the namespace takes.
        """
        if skip_documents:
            self._set_reporting_verdict(
                namespace, "not_checked", "documents were skipped"
            )
            return
        if not self._reporting:
            self._set_reporting_verdict(
                namespace, "unavailable", "reporting-sync not reachable"
            )
            return
        self._emit(
            "phase_reporting_parity",
            f"[{namespace}] verifying reporting row-count parity",
        )
        if await self._reporting.trigger_batch_sync(namespace):
            self._reporting_synced.append(namespace)
        clock = asyncio.get_event_loop()
        started = last_change = clock.time()
        last_rows: int | None = None
        parity: dict[str, Any] | None = None
        while True:
            parity = await self._reporting.parity(namespace, include_counts=True)
            if parity is None:
                self._reporting_unavailable(namespace, "count parity")
                return
            if parity.get("ok"):
                self._set_reporting_verdict(namespace, "verified")
                self._emit(
                    "phase_reporting_parity",
                    f"[{namespace}] reporting parity verified "
                    f"({len(parity.get('templates', []))} template(s))",
                )
                return
            templates = parity.get("templates", [])
            rows = sum(int(t.get("actual_rows") or 0) for t in templates)
            now = clock.time()
            if rows != last_rows:
                last_rows, last_change = rows, now
                expected = sum(int(t.get("expected_documents") or 0) for t in templates)
                self._emit(
                    "phase_reporting_parity",
                    f"[{namespace}] reporting catching up: {rows} of {expected} rows",
                )
            if now - last_change >= self._REPORTING_COUNTS_STALL_S:
                reason = f"no reporting rows written for {self._REPORTING_COUNTS_STALL_S}s"
                break
            if now - started >= self._REPORTING_COUNTS_MAX_S:
                reason = f"still catching up after {self._REPORTING_COUNTS_MAX_S}s"
                break
            await asyncio.sleep(self._REPORTING_POLL_INTERVAL_S)
        gaps = _parity_gaps(parity or {})
        self._set_reporting_verdict(namespace, "incomplete", reason, gaps)
        mismatches = [_describe_gap(g) for g in gaps]
        self._emit(
            "warning",
            f"[{namespace}] reporting count parity incomplete ({reason}): "
            f"{'; '.join(mismatches[:5]) or 'no per-item detail'} — "
            "restore data is complete in MongoDB; the batch sync may still be "
            "running: watch GET /api/reporting-sync/sync/batch/jobs or "
            f"GET /api/reporting-sync/parity?namespace={namespace}, or re-run "
            "the batch sync",
        )

    async def _upsert_namespace(
        self,
        namespace: str,
        ns_config: NamespaceConfig | None,
        *,
        preserve_id_config: bool = True,
        dry_run: bool = False,
    ) -> None:
        """Create or update the target namespace as the requester.

        A platform admin's restore upserts the archive's settings, as it
        always has. Anyone else never changes a namespace's settings: an
        existing namespace keeps its own, and one that does not exist is
        created with the requester's own credential (an API key or a login's
        bearer token), so the Registry applies its rules (the key that owns
        the prefix's reservation, or a caller holding admin on it, gets the
        reservation's settings; anyone else is refused). Every archive setting that is not applied is a job
        warning.

        ``preserve_id_config`` carries the source's id_config onto the target.
        That is correct for an id-preserving restore (the DR case): the archived
        ids are re-inserted unchanged and the copy should continue the source's
        minting sequence. A FRESH restore passes False, because it re-mints every
        id — inheriting a prefixed source scheme would re-mint the source's exact
        prefixed ids and collide with the live original on the global entry_id
        index, and stamp the copy's ids with the source's prefix. With it False
        the fresh target defaults to UUID7, or keeps its own config if the
        operator pre-created the target namespace.

        ``dry_run`` asks the Registry the same question with its dry_run
        flag: every refusal the real upsert would meet, and nothing written.
        This step is the one a restore takes as its requester, so a dry run
        that skipped it passed requests the real restore refused.
        """
        if preserve_id_config:
            self._warn_without_namespace_config(namespace, ns_config)
        body = self._namespace_body(ns_config, preserve_id_config=preserve_id_config)
        params = {"dry_run": "true"} if dry_run else None
        if self._requester.platform_admin:
            await self._admin_upsert_namespace(
                namespace, {**body, **self._actor_field()},
                self._requester_headers(namespace), dry_run=dry_run,
            )
            return

        import httpx

        url = f"{self._registry_url}/api/registry/namespaces/{namespace}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Existence is read with the service's key: the requester may hold
            # admin on a prefix that does not exist yet, and a read as the
            # requester could not tell that from having no access.
            resp = await client.get(url, headers={"X-API-Key": self._registry_api_key})
            if resp.status_code == 200:
                live = resp.json()
            elif resp.status_code == 404:
                create = {"description": body["description"]} if "description" in body else {}
                resp = await client.put(url, json={**create, **self._actor_field()}, params=params,
                                        headers={
                    **self._requester_headers(namespace),
                    "Content-Type": "application/json",
                })
                if resp.status_code not in (200, 201):
                    raise RestoreEngineError(
                        f"Failed to create namespace '{namespace}': "
                        f"{resp.status_code} — {resp.text}"
                    )
                live = resp.json()
                if not dry_run:
                    logger.info("Created namespace %s as the restore's requester", namespace)
            else:
                raise RestoreEngineError(
                    f"Failed to read namespace '{namespace}': {resp.status_code} — {resp.text}"
                )
        if "id_config" in body:
            # The archive holds id_config as stored; the Registry serves it
            # with every entity type's default merged in. Compare like with
            # like: the stored form, as the archive itself was read.
            stored = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
                {"prefix": namespace}, {"id_config": 1}
            )
            live = {**live, "id_config": (stored or {}).get("id_config") or {}}
        self._check_identity(namespace, body.get("namespace_id"), live)
        for field in _NAMESPACE_SETTINGS:
            if field in body and body[field] != live.get(field):
                self._emit("warning", (
                    f"Namespace '{namespace}': {field} {body[field]!r} from the archive "
                    f"was NOT applied (it is {live.get(field)!r} here); only a platform "
                    "admin changes a namespace's settings"
                ))

    def _warn_without_namespace_config(self, namespace: str, ns_config: NamespaceConfig | None) -> None:
        """An archive backed up while its namespace had no Registry record
        carries no config, so no id: nothing says which namespace it was, and
        restoring it is not a re-creation."""
        if ns_config is None:
            self._emit("warning", (
                f"Namespace '{namespace}': the archive records no namespace config, "
                "so no namespace id; the namespace is restored under the id and "
                "settings this install gives it, not as the archived namespace"
            ))

    def _requester_headers(self, namespace: str) -> dict[str, str]:
        """The credential a namespace create or configure goes out with: the
        requester's own. Only the service acting as itself uses the
        service's key."""
        if self._requester.service:
            return {"X-API-Key": self._registry_api_key}
        if self._requester.credential is None:
            raise RestoreEngineError(
                f"Namespace '{namespace}': a restore creates and configures "
                "namespaces with its requester's own credential, and this login's "
                "cannot be passed on (it arrived through a gateway). Start the "
                "restore with an API key or a direct login, or have a platform "
                "admin create the namespace first."
            )
        return dict(self._requester.credential)

    def _actor_field(self) -> dict[str, str]:
        """Who the Registry records as the namespace's creator or updater."""
        return {"updated_by": self._requester.actor} if self._requester.actor else {}

    @staticmethod
    def _namespace_body(
        ns_config: NamespaceConfig | None, *, preserve_id_config: bool
    ) -> dict[str, Any]:
        """The namespace settings the archive carries, as an upsert body."""
        body: dict[str, Any] = {}
        if ns_config:
            body["description"] = ns_config.description
            body["isolation_mode"] = ns_config.isolation_mode
            if preserve_id_config and ns_config.id_config:
                body["id_config"] = ns_config.id_config
            # An ID-preserving restore re-creates the archived namespace, so
            # it carries its id (applied only when the call creates it). A
            # fresh restore is a copy: a new namespace, a new id.
            if preserve_id_config:
                body["namespace_id"] = ns_config.namespace_id
            # None means the archive predates these manifest fields — omit
            # them so the PUT leaves an existing namespace's config untouched
            # instead of resetting it to platform defaults. An explicit value
            # (including an empty allowlist) round-trips.
            if ns_config.allowed_external_refs is not None:
                body["allowed_external_refs"] = ns_config.allowed_external_refs
            if ns_config.deletion_mode is not None:
                body["deletion_mode"] = ns_config.deletion_mode
            if ns_config.inbound_references is not None:
                body["inbound_references"] = ns_config.inbound_references
        return body

    async def _admin_upsert_namespace(
        self, namespace: str, body: dict[str, Any], credential: dict[str, str], *,
        dry_run: bool = False,
    ) -> None:
        """A platform admin's upsert: the archive's settings, as sent, with
        the requester's credential."""
        import httpx

        url = f"{self._registry_url}/api/registry/namespaces/{namespace}"
        headers = {**credential, "Content-Type": "application/json"}
        params = {"dry_run": "true"} if dry_run else None
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(url, json=body, params=params, headers=headers)
            if (
                resp.status_code == 400
                and "confirm_enable_deletion" in resp.text
                and "deletion_mode" in body
            ):
                # An existing retain-mode namespace cannot be flipped to full
                # without explicit confirmation — that guard is deliberate and
                # a restore must not bypass it silently. Apply the rest of the
                # config and surface the skipped field loudly instead of
                # aborting the data restore over a config nuance.
                skipped = body.pop("deletion_mode")
                warning = (
                    f"deletion_mode '{skipped}' NOT applied to existing "
                    f"namespace '{namespace}' — flipping retain to full "
                    "requires a manual PUT with confirm_enable_deletion=true"
                )
                logger.warning(warning)
                self._emit("warning", warning)
                resp = await client.put(url, json=body, params=params, headers=headers)
            if resp.status_code not in (200, 201):
                raise RestoreEngineError(
                    f"Failed to upsert namespace '{namespace}': "
                    f"{resp.status_code} — {resp.text}"
                )
        self._check_identity(namespace, body.get("namespace_id"), resp.json())
        if not dry_run:
            logger.info("Upserted namespace %s from manifest config", namespace)

    def _check_identity(
        self, namespace: str, archived_id: str | None, live: dict[str, Any],
    ) -> None:
        """Warn when the namespace restored into is not the archived one:
        the grants a system restore wrote for the archived namespace then
        stay orphaned."""
        for warning in live.get("warnings") or []:
            self._emit("warning", f"Namespace '{namespace}': {warning}")
        if archived_id and live.get("namespace_id") != archived_id:
            self._emit("warning", (
                f"Namespace '{namespace}' here is not the archived namespace (id "
                f"{live.get('namespace_id')} here, {archived_id} in the archive): the "
                "archive's data is restored into it, and grants a system restore "
                "wrote for the archived namespace stay orphaned"
            ))

    async def _insert_batch(
        self,
        collection: Any,
        batch: list[dict[str, Any]],
        entity_type: str,
    ) -> None:
        """Bulk insert a batch, handling partial failures.

        Rows arrive as parsed archive JSON, where every timestamp is an ISO
        string; the service models declare these fields as datetime and
        store BSON dates. Inserting the strings raw splits the corpus into
        two BSON types, and Mongo's type-bracketed comparisons then make
        timestamp filters silently skip whichever cohort mismatches the
        filter value's type. Re-hydrate top-level timestamp keys to
        datetime before insert so restored rows carry the same type the
        services write.
        """
        from pymongo.errors import BulkWriteError

        for row in batch:
            _rehydrate_timestamps(row)
            # A pre-names archive carries terms with legacy aliases /
            # translations fields. Def-store converts those only at ITS
            # startup, so restoring into a running def-store would leave
            # the rows unmigrated (names invisible to resolution) until
            # the next restart — convert at restore-write instead, with
            # the same rule def-store's migration applies: exact-match
            # dedupe, first occurrence wins, own-value echoes dropped.
            if entity_type == "terms" and "names" not in row:
                _convert_legacy_term_names(row)
            # An archive taken before the `archived` document status was
            # removed still carries it; it is a soft delete under another name.
            if entity_type == "documents":
                retire_archived_row(row)
            # A file's bytes live under its file id: every upload writes them
            # there, and a restore uploads the archive's blob there when the
            # archive carries one (a fresh restore under the re-minted id; a
            # file it carries none for is named on the job). The row must
            # point at the same key, or it serves another file's bytes (the
            # source's, on the same install), and deleting it deletes the
            # other's.
            if entity_type == "files" and row.get("file_id"):
                row["storage_key"] = row["file_id"]
            # The Registry answers value searches from search_values, which
            # it derives from the keys on every write it makes. A restore
            # that rewrote a key (a rehash, an id or namespace rewrite)
            # writes the row itself, so it derives them here: the archived
            # copy would still match the old values and miss the new ones.
            # An archive always carries the field; a row without it is left
            # as written.
            if entity_type == "registry_entries" and "search_values" in row:
                row["search_values"] = registry_search_values(row)
        try:
            await collection.insert_many(batch, ordered=False)
        except BulkWriteError as exc:
            n_errors = len(exc.details.get("writeErrors", []))
            n_ok = len(batch) - n_errors
            logger.error(
                "Bulk insert for %s: %d succeeded, %d failed",
                entity_type, n_ok, n_errors,
            )
            # For v1.0 restore into empty namespace, any failure is unexpected
            raise RestoreEngineError(
                f"Bulk insert failed for {entity_type}: "
                f"{n_errors} errors out of {len(batch)} documents. "
                f"First error: {exc.details['writeErrors'][0].get('errmsg', 'unknown')}"
            ) from exc

    @staticmethod
    def _archive_file_checksums(reader: ArchiveReader) -> dict[str, str | None]:
        """file_id → checksum of every file row the archive carries."""
        out: dict[str, str | None] = {}
        namespaces: list[str | None] = list(reader.list_namespaces() or []) or [None]
        for ns in namespaces:
            for row in reader.read_entities("files", namespace=ns) or []:
                file_id = row.get("file_id")
                if file_id:
                    out[file_id] = row.get("checksum")
        return out

    @staticmethod
    def _archive_includes_files(reader: ArchiveReader) -> bool:
        """Whether the backup was made with its files: it then carries the
        bytes of every file it could read, and a file row without them is a
        file that lost its bytes, rather than one left out on request."""
        return bool(getattr(reader.read_manifest(), "include_files", False))

    @staticmethod
    def _checked_blob(
        reader: ArchiveReader, file_id: str, expected: str | None,
    ) -> tuple[bytes | None, str | None]:
        """The archive's bytes for a file, or None and why there are none to
        restore: ``missing`` (the archive carries none) or ``mismatched``
        (they do not match the file's recorded checksum: a file without bytes
        is honest, one with wrong bytes is not). It reads and hashes a whole
        blob, so it runs off the event loop, and emits nothing: the caller
        names the files on the job."""
        data: bytes | None = reader.read_blob(file_id)
        if data is None:
            logger.warning("Blob %s missing from archive; skipped", file_id)
            return None, "missing"
        if expected and hashlib.sha256(data).hexdigest() != expected:
            logger.warning("Blob %s does not match its checksum; not uploaded", file_id)
            return None, "mismatched"
        return data, None

    def _emit_restored_without_bytes(
        self, reader: ArchiveReader, without: dict[str, list[str]], *, dry_run: bool = False,
    ) -> None:
        """Name, one warning per reason, the files a restore writes without
        their bytes. Every file row is written whether its bytes come or
        not, so a file without them downloads as nothing until its bytes are
        uploaded again."""
        restored = "would be restored" if dry_run else "are restored"
        missing = (
            "the archive carries no bytes for them"
            if self._archive_includes_files(reader)
            else "the archive was made without files, so it carries no bytes for them"
        )
        for warning in _file_shortfall_warnings("", {
            f"{restored} without content: {missing}": without.get("missing", []),
            f"{restored} without content: the archive's bytes do not match their "
            "checksum": without.get("mismatched", []),
        }):
            self._emit("warning", warning)

    async def _without_objects(self, file_ids: set[str]) -> list[str]:
        """Of the files an archive carries no bytes for, the ones with no
        object under their id here either. A plain restore or merge keeps a
        file's id, so an object this install still holds under it (one a
        deletion left in the store, say) still serves the file."""
        assert self._storage is not None
        return [file_id for file_id in sorted(file_ids) if not await self._storage.exists(file_id)]

    async def _blob_keys_holding_archive_bytes(
        self, reader: ArchiveReader, keys: dict[str, str],
    ) -> set[str]:
        """Which storage keys a restore is about to write already hold the
        bytes it would write there; checked before any of them is written.

        ``keys`` maps each storage key to the archived file whose bytes go
        under it, for files the restore is about to insert. A free key is
        written. A key a file record already points at belongs to that file
        (the restore inserts none it already has), and a key holding other
        bytes would have them replaced: either refuses the restore, before
        anything is uploaded. A key no record points at that holds the same
        bytes (left by a failed run of the same restore, say) is left as it
        is — there is nothing to write. Existence is asked with a HEAD rather
        than relied on from a conditional PUT, which a store may ignore.
        """
        assert self._storage is not None
        present: set[str] = set()
        taken: list[str] = []
        for storage_key, file_id in keys.items():
            if not await self._storage.exists(storage_key):
                continue
            data = reader.read_blob(file_id)
            if data is None:
                continue  # nothing would be written under this key
            if await self._key_has_file_record(storage_key):
                taken.append(storage_key)
                continue
            stored = await self._storage.download(storage_key)
            if hashlib.sha256(stored).digest() == hashlib.sha256(data).digest():
                present.add(storage_key)
            else:
                taken.append(storage_key)
        if taken:
            shown = ", ".join(taken[:10]) + (f" and {len(taken) - 10} more" if len(taken) > 10 else "")
            raise RestoreEngineError(
                f"{len(taken)} file key(s) this archive would write already hold "
                f"another file's bytes on this install: {shown}. A restore never "
                "writes over an existing file's bytes; nothing was uploaded."
            )
        return present

    async def _key_has_file_record(self, storage_key: str) -> bool:
        """Whether any file record, in any namespace, uses this storage key."""
        db_name, coll_name = COLLECTION_MAP["files"]
        return bool(await self._mongo[db_name][coll_name].count_documents(
            {"$or": [{"storage_key": storage_key}, {"file_id": storage_key}]}, limit=1,
        ))

    async def _restore_blobs(
        self,
        reader: ArchiveReader,
        namespace: str,
        *,
        only: set[str] | None = None,
    ) -> None:
        """Upload file blobs from the archive to the object store.

        ``only`` narrows the upload to specific file ids — a merge uploads
        bytes for the files it inserted and leaves the ones the target already
        had alone. Every file restored without its bytes is named on the job:
        one the archive carries none for (it was made without files, or lost
        them), and one whose bytes do not match its checksum.
        """
        assert self._storage is not None
        checksums = await self._off_loop(self._archive_file_checksums, reader)
        blobs = set(reader.list_blobs())
        rows = set(checksums)
        if only is not None:
            blobs &= only
            rows &= only
        without: dict[str, list[str]] = {
            "missing": await self._without_objects(rows - blobs),
            "mismatched": [],
        }
        blob_ids = sorted(blobs)
        if blob_ids:
            await self._upload_archive_blobs(reader, blob_ids, checksums, without)
        self._emit_restored_without_bytes(reader, without)
        logger.info("Restored %d blobs for namespace %s", len(blob_ids), namespace)

    async def _upload_archive_blobs(
        self,
        reader: ArchiveReader,
        blob_ids: list[str],
        checksums: dict[str, str | None],
        without: dict[str, list[str]],
    ) -> None:
        """Upload the archive's blobs under their own ids; the ones not
        uploaded for a reason go into ``without``."""
        assert self._storage is not None
        present = await self._blob_keys_holding_archive_bytes(
            reader, {file_id: file_id for file_id in blob_ids},
        )

        # No percent: the bytes go up before the records, so the step sits
        # early in the job and a figure here would make the bar jump back.
        self._emit("phase_blobs", f"Uploading {len(blob_ids)} file blobs")

        for i, file_id in enumerate(blob_ids):
            if file_id in present:
                continue
            data, reason = await self._off_loop(
                self._checked_blob, reader, file_id, checksums.get(file_id),
            )
            if data is None:
                without[reason or "missing"].append(file_id)
                continue
            # Use application/octet-stream as default; the File metadata
            # in MongoDB has the real content_type.
            await self._storage.upload(
                storage_key=file_id,
                content=data,
                content_type="application/octet-stream",
            )
            if (i + 1) % 100 == 0:
                self._emit(
                    "phase_blobs",
                    f"Uploaded {i + 1}/{len(blob_ids)} blobs",
                    current=i + 1,
                    total=len(blob_ids),
                )

    async def _dry_run_blob_report(self, reader: ArchiveReader) -> None:
        """What a plain restore's blob phase would upload: only the bytes
        that match their file's checksum, so they are read and checked as the
        real run checks them. The files it would restore without their bytes
        are named, as the real run names them."""
        assert self._storage is not None
        checksums = await self._off_loop(self._archive_file_checksums, reader)
        blobs = set(reader.list_blobs())
        without: dict[str, list[str]] = {
            "missing": await self._without_objects(set(checksums) - blobs),
            "mismatched": [],
        }
        uploadable = 0
        for file_id in sorted(blobs):
            data, reason = await self._off_loop(
                self._checked_blob, reader, file_id, checksums.get(file_id),
            )
            if data is None:
                without[reason or "missing"].append(file_id)
            else:
                uploadable += 1
        self._emit("phase_dry_run", f"dry run — would upload {uploadable} file blob(s)")
        self._emit_restored_without_bytes(reader, without, dry_run=True)

    def _emit(
        self,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        current: int | None = None,
        total: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = ProgressEvent(
            phase=phase,
            message=message,
            percent=percent,
            current=current,
            total=total,
            details=details or {},
        )
        # The progress sink is an asyncio.Queue, which is not thread-safe. Work
        # moved off the loop (`_off_loop`) emits from a worker thread; its
        # events are handed to the loop instead of touching the queue there.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._progress, event)
                return
        self._progress(event)

    async def _off_loop(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run a synchronous, long pass (reading or planning a whole archive)
        on a worker thread, so the service keeps answering requests and health
        checks while it runs. The archive reader is only ever used by one
        thread at a time: the caller awaits the pass before touching it again."""
        self._loop = asyncio.get_running_loop()
        return await asyncio.to_thread(fn, *args, **kwargs)

    @staticmethod
    def _pct(done: int, total: int) -> float:
        if total == 0:
            return 10.0
        # 10-90% for entity inserts, 90-98% for blobs, 98-100% for finalize
        return min(round(10 + done / total * 80, 1), 90.0)
