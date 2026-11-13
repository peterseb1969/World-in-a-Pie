"""Namespace deletion service.

Builds and executes a persistent delete journal for namespace deletion.
Crash-safety is a MongoDB guarantee: Mongo steps fail the journal loudly
and resume idempotently. File-storage and PostgreSQL cleanup is
best-effort by design — a dead secondary store must not make a namespace undeletable —
but a degraded step records its error and the journal terminates as
"completed_with_warnings" instead of a bare "completed", so the outcome
is visible in the journal itself, not only in service logs.

A best-effort step whose service could not be REACHED (connection refused,
timeout, 5xx) is different from one that was refused: the service may simply
be starting. Such a step stays "pending" with its error noted, the journal
stays "in_progress", and a background recovery loop retries it until it runs.
"""

import asyncio
import contextlib
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from bson import ObjectId

from wip_auth.cache_events import announce_namespace_deleted
from wip_auth.startup import retry_until_done

from ..models.deletion_journal import (
    DeletionJournal,
    DeletionStep,
    InboundReference,
)
from ..models.entry import RegistryEntry
from ..models.namespace import Namespace, set_unless_locked
from .event_bus import access_changed, get_event_bus

logger = logging.getLogger("registry.namespace_deletion")


_recovery_sleep = asyncio.sleep  # replaced in tests


# Journal statuses resume_deletion picks up: not finished cleanly.
_RESUMABLE = ("in_progress", "failed", "completed_with_warnings")


class DeletionAlreadyRunning(Exception):
    """The deletion's journal is being run already; a second run would race it."""


class _Unreachable(Exception):
    """A best-effort step's service could not be reached; retry later."""

# The install's service credential for calls from the Registry to other WIP
# services, in the order the registry manifest provides it: the legacy and
# master keys are both mapped from the install's api-key secret; API_KEY and
# REGISTRY_API_KEY are honoured for setups that name it that way.
# Journal store values for the stored-objects step. "minio" is the name
# older journals carry; it executes the same way so they stay resumable.
_FILE_STORAGE_STORES = ("file_storage", "minio")
# document-store accepts at most this many keys per request.
_STORAGE_DELETE_BATCH = 1000

# Inbound references that leave another namespace's data dangling: its
# template loses a parent, a field vocabulary or a reference target; its
# documents lose the template they were built on, or point at documents,
# terms, templates, terminologies or files that are gone; its relations
# point at gone terms. Any of these makes a deletion refuse without force. A
# synonym link is the one low-severity kind: the external synonym stays
# valid on its own.
_HIGH_SEVERITY_INBOUND = frozenset({
    "template_extends",
    "terminology_reference",
    "template_reference",
    "document_reference",
    "term_relation",
    "template_use",
    "term_reference",
    "file_reference",
})

# The ids a document holds by bare id, recording no namespace — so another
# namespace's documents are matched against the ids this namespace owns:
# (report type, document field, which of this namespace's ids, impact).
_DOCUMENT_ID_USES = (
    ("template_use", "template_id", "templates",
     "Documents are pinned to a template that is gone: they no longer validate, "
     "update or report."),
    ("template_reference", "references.resolved.template_id", "templates",
     "Reference fields point at a template that is gone."),
    ("terminology_reference", "references.resolved.terminology_id", "terminologies",
     "Reference fields point at a terminology that is gone."),
    ("term_reference", "term_references.term_id", "terms",
     "Term fields point at terms that are gone."),
    ("term_reference", "references.resolved.term_id", "terms",
     "Term fields point at terms that are gone."),
    ("file_reference", "file_references.file_id", "files",
     "File fields point at files whose records and bytes are gone."),
)

# Ids per $in when matching other namespaces' rows against this one's terms.
_INBOUND_TERM_BATCH = 1000


def deletion_refusals(ns: Namespace) -> list[tuple[str, str, int]]:
    """Why the delete refuses ``ns`` outright: (rule, message, HTTP status).

    One list, read by the delete and by its dry run, so a dry run never calls
    a namespace safe to delete that the delete refuses. Inbound references
    are not here: ``force`` overrides them, and the report has its own field.
    """
    refusals: list[tuple[str, str, int]] = []
    if ns.prefix == "wip":
        refusals.append(
            ("default_namespace", "Cannot delete the default 'wip' namespace", 400)
        )
    if ns.deletion_mode != "full":
        refusals.append((
            "deletion_mode",
            f"Namespace '{ns.prefix}' has deletion_mode='retain'. "
            "Set deletion_mode='full' first via PATCH.",
            400,
        ))
    if ns.status == "locked":
        refusals.append((
            "locked",
            f"Namespace '{ns.prefix}' is already locked (deletion in progress)",
            409,
        ))
    return refusals


def id_counter_filter(prefix: str) -> dict[str, Any]:
    """The namespace's own id counters, keyed "{namespace}:{entity_type}:{prefix}".

    The name is escaped: a namespace named before the prefix rule may hold a
    regex metacharacter, and "a.b" unescaped would also match "axb:"'s
    counters — deleting another namespace's sequence. The install-wide
    "@prefix:" counters never match: no namespace name starts with "@".
    """
    return {"counter_key": {"$regex": f"^{re.escape(prefix)}:"}}

# Template field slots, by what they point at.
_FIELD_TERMINOLOGY_SLOTS = ("terminology_ref", "array_terminology_ref")
_FIELD_TEMPLATE_SLOTS = ("template_ref", "array_template_ref")

SERVICE_KEY_VARS = (
    "WIP_AUTH_LEGACY_API_KEY",
    "MASTER_API_KEY",
    "API_KEY",
    "REGISTRY_API_KEY",
)


def _service_api_key() -> str:
    """Return the first non-empty service key from the environment, or ''."""
    for var in SERVICE_KEY_VARS:
        value = os.getenv(var)
        if value:
            return value
    return ""


# External service databases with namespace-scoped data.
# Each tuple: (database_name, collection_name, filter_field, service_label)
_EXTERNAL_COLLECTIONS = [
    ("wip_document_store", "files", "namespace", "document-store"),
    ("wip_document_store", "documents", "namespace", "document-store"),
    ("wip_document_store", "restore_provenance", "namespace", "document-store"),
    ("wip_template_store", "templates", "namespace", "template-store"),
    ("wip_def_store", "terms", "namespace", "def-store"),
    ("wip_def_store", "term_relations", "namespace", "def-store"),
    ("wip_def_store", "term_audit_log", "namespace", "def-store"),
    ("wip_def_store", "terminologies", "namespace", "def-store"),
]

# Registry-local collections — database name resolved at runtime from Beanie.
# Each tuple: (collection_name, filter_field)
_REGISTRY_COLLECTIONS = [
    ("registry_entries", "namespace"),
    ("namespace_grants", "namespace"),
    # CASE-427: composite-key claims scoped to this namespace. A claim in
    # another namespace whose owner entry this deletion removes is not swept
    # here, and nothing sweeps it at startup either (startup only settles
    # pending claims). It is healed where it next blocks a write: a claim
    # whose owner entry is gone is stale and is taken over (resolve_stale_claim).
    # reconcile_orphan_claims is the admin audit that finds such claims.
    ("composite_key_claims", "namespace"),
]


def _registry_db_name() -> str:
    """Get the actual registry database name from Beanie config."""
    return Namespace.get_motor_collection().database.name


async def _values_by_id(collection: Any, prefix: str, id_field: str) -> dict[str, str]:
    """{id: value} for the namespace's entities in one collection."""
    found: dict[str, str] = {}
    async for row in collection.find({"namespace": prefix}, {id_field: 1, "value": 1}):
        if row.get(id_field):
            found[row[id_field]] = row.get("value") or row[id_field]
    return found


def _endpoint_id(entry: Any) -> str | None:
    """An edge endpoint's id: the resolved half, or a legacy bare string."""
    if isinstance(entry, dict):
        entry = entry.get("resolved")
    return entry if isinstance(entry, str) else None


# Where a deletion's stored-object keys wait, one document per batch, until
# the step that deletes them has completed.
_KEY_BATCHES = "namespace_deletion_keys"


def _key_batches() -> Any:
    return DeletionJournal.get_motor_collection().database[_KEY_BATCHES]


async def _store_key_batch(namespace: str, keys: list[str]) -> str:
    """Persist one batch of keys outside the journal; its id, for the step."""
    result = await _key_batches().insert_one({
        "namespace": namespace, "keys": keys, "created_at": datetime.now(UTC),
    })
    return str(result.inserted_id)


async def step_storage_keys(step: DeletionStep) -> list[str]:
    """The keys a file-storage step deletes: inline (older journals) or read
    from its batch document. A batch that is gone while its step is not
    completed is lost data, and fails the step rather than reading as none."""
    if step.storage_keys is not None:
        return step.storage_keys
    if not step.storage_keys_batch:
        return []
    doc = await _key_batches().find_one({"_id": ObjectId(step.storage_keys_batch)})
    if doc is None:
        raise ValueError(
            f"the stored-object keys of step {step.order} "
            f"(batch {step.storage_keys_batch}) are missing"
        )
    return list(doc.get("keys") or [])


async def _drop_key_batch(step: DeletionStep) -> None:
    """A completed step's keys are no longer needed."""
    if step.storage_keys_batch:
        await _key_batches().delete_one({"_id": ObjectId(step.storage_keys_batch)})


def _count_step(summary: dict[str, int], step: DeletionStep) -> None:
    """Add a done step's deletions to the journal summary: under its
    collection, as object-store objects, or as PostgreSQL rows.

    A summary key can cover several steps — the stored objects are one step
    per batch of keys — hence each step adds to the running total instead of
    replacing it. The total is
    rebuilt from every step on each run, so a resumed journal counts each
    step once.
    """
    if step.collection:
        key = step.collection
    elif step.store in _FILE_STORAGE_STORES:
        key = "minio_objects"
    elif step.store == "postgresql":
        key = "postgres_rows"
    else:
        return
    summary[key] = summary.get(key, 0) + step.deleted_count


def _template_inbound_clauses(tpl_ids: list[str], ty_ids: list[str]) -> list[dict[str, Any]]:
    """The query clauses matching a template that names any of the
    namespace's templates or terminologies, in every slot one can be named."""
    clauses: list[dict[str, Any]] = []
    if tpl_ids:
        clauses += [{"extends": {"$in": tpl_ids}}]
        clauses += [{f"fields.{s}": {"$in": tpl_ids}} for s in _FIELD_TEMPLATE_SLOTS]
        clauses += [
            {"fields.target_templates": {"$in": tpl_ids}},
            {"source_templates.resolved": {"$in": tpl_ids}},
            {"target_templates.resolved": {"$in": tpl_ids}},
            {"source_templates": {"$in": tpl_ids}},
            {"target_templates": {"$in": tpl_ids}},
        ]
    if ty_ids:
        clauses += [{f"fields.{s}": {"$in": ty_ids}} for s in _FIELD_TERMINOLOGY_SLOTS]
        clauses += [{"fields.target_terminologies": {"$in": ty_ids}}]
    return clauses


# (type, source_entity, target_entity, impact) of one reference a template makes.
_TemplateRef = tuple[str, str, str, str]


def _field_refs(
    value: str, field: dict, our_templates: dict[str, str], our_terminologies: dict[str, str]
) -> list[_TemplateRef]:
    """The references one field of a template makes to the namespace's
    terminologies and templates."""
    source = f"{value} (template, field: {field.get('name', '?')})"
    refs: list[_TemplateRef] = []
    terminology_ids = [field.get(s) for s in _FIELD_TERMINOLOGY_SLOTS]
    terminology_ids += field.get("target_terminologies") or []
    for tid in terminology_ids:
        if tid in our_terminologies:
            refs.append(("terminology_reference", source,
                         f"{our_terminologies[tid]} (terminology)",
                         "Term validation will fail for this field."))
    template_ids = [field.get(s) for s in _FIELD_TEMPLATE_SLOTS]
    template_ids += field.get("target_templates") or []
    for tid in template_ids:
        if tid in our_templates:
            refs.append(("template_reference", source,
                         f"{our_templates[tid]} (template)",
                         "Validation of this field fails: its template is gone."))
    return refs


def _endpoint_refs(value: str, template: dict, our_templates: dict[str, str]) -> list[_TemplateRef]:
    """The references an edge type's endpoint declarations make to the
    namespace's templates."""
    refs: list[_TemplateRef] = []
    for role in ("source_templates", "target_templates"):
        for entry in template.get(role) or []:
            tid = _endpoint_id(entry)
            if tid in our_templates:
                refs.append(("template_reference", f"{value} (edge type, {role})",
                             f"{our_templates[tid]} (template)",
                             "Edge endpoint declaration names a template that is gone."))
    return refs


def _template_refs(
    template: dict, our_templates: dict[str, str], our_terminologies: dict[str, str]
) -> list[_TemplateRef]:
    """Every reference one template makes to the namespace: its parent, its
    fields, its edge endpoints."""
    value = template.get("value", "?")
    refs: list[_TemplateRef] = []
    parent = template.get("extends")
    if parent in our_templates:
        refs.append(("template_extends", f"{value} (template)",
                     f"{our_templates[parent]} (template)",
                     "Source template loses parent schema. Field inheritance broken."))
    for field in template.get("fields") or []:
        refs += _field_refs(value, field, our_templates, our_terminologies)
    refs += _endpoint_refs(value, template, our_templates)
    return refs


async def _inbound_from_templates(
    templates: Any,
    prefix: str,
    our_templates: dict[str, str],
    our_terminologies: dict[str, str],
) -> list[InboundReference]:
    """Templates of other namespaces pointing at templates or terminologies
    here. Every version is checked (a document can pin any of them), and a
    reference repeated across versions is reported once."""
    clauses = _template_inbound_clauses(list(our_templates), list(our_terminologies))
    seen: dict[tuple[str, str, str, str], InboundReference] = {}
    projection = {"namespace": 1, "value": 1, "extends": 1, "fields": 1,
                  "source_templates": 1, "target_templates": 1}
    async for t in templates.find(
        {"namespace": {"$ne": prefix}, "$or": clauses}, projection
    ):
        ns = t["namespace"]
        for kind, source, target, impact in _template_refs(t, our_templates, our_terminologies):
            seen.setdefault(
                (kind, ns, source, target),
                InboundReference(
                    type=kind, source_namespace=ns, source_entity=source,
                    target_entity=target, impact=impact,
                ),
            )
    return list(seen.values())


async def _inbound_from_documents(
    documents: Any, templates: Any, prefix: str
) -> list[InboundReference]:
    """Documents of other namespaces referencing documents here, one report
    per (namespace, template) with a count of document versions."""
    pipeline = [
        {"$match": {
            "namespace": {"$ne": prefix},
            "references.resolved.namespace": prefix,
        }},
        {"$group": {
            "_id": {"namespace": "$namespace", "template_id": "$template_id"},
            "count": {"$sum": 1},
        }},
    ]
    refs: list[InboundReference] = []
    async for group in documents.aggregate(pipeline):
        ns = group["_id"]["namespace"]
        template_id = group["_id"].get("template_id")
        tpl = await templates.find_one({"template_id": template_id}, {"value": 1})
        label = (tpl or {}).get("value") or template_id or "?"
        refs.append(InboundReference(
            type="document_reference",
            source_namespace=ns,
            source_entity=f"{group['count']} document version(s) of {label}",
            target_entity=f"documents in {prefix}",
            impact="Reference fields point at documents that are gone.",
        ))
    return refs


async def _documents_using(
    documents: Any, prefix: str, owned: dict[str, dict[str, str]],
) -> dict[tuple[str, str, str | None], int]:
    """Count other namespaces' document versions holding this namespace's
    ids, per (type, namespace, template_id). A row matched by two fields of
    one kind, or in two batches, counts once."""
    rows: dict[tuple[str, Any], tuple[str, str | None]] = {}
    for kind, field, source, _impact in _DOCUMENT_ID_USES:
        ids = list(owned.get(source) or {})
        for start in range(0, len(ids), _INBOUND_TERM_BATCH):
            async for row in documents.find(
                {"namespace": {"$ne": prefix},
                 field: {"$in": ids[start : start + _INBOUND_TERM_BATCH]}},
                {"namespace": 1, "template_id": 1},
            ):
                rows[(kind, row["_id"])] = (row["namespace"], row.get("template_id"))
    counts: dict[tuple[str, str, str | None], int] = {}
    for (kind, _), (ns, template_id) in rows.items():
        counts[(kind, ns, template_id)] = counts.get((kind, ns, template_id), 0) + 1
    return counts


async def _inbound_from_document_uses(
    documents: Any, templates: Any, prefix: str, owned: dict[str, dict[str, str]],
) -> list[InboundReference]:
    """Documents of other namespaces built on this namespace's templates, or
    holding its terms, files, templates or terminologies in their fields —
    one report per (kind, namespace, template) with a count of document
    versions. ``owned`` maps "templates", "terminologies", "terms" and
    "files" to {id: value} of this namespace's entities.

    Ids are matched as they are stored. Canonical ids are minted by the
    Registry and unique install-wide, as the template check assumes too."""
    counts = await _documents_using(documents, prefix, owned)
    impacts = {kind: impact for kind, _f, _s, impact in _DOCUMENT_ID_USES}
    labels: dict[str | None, str] = dict(owned["templates"].items())
    refs: list[InboundReference] = []
    for (kind, ns, template_id), n in sorted(counts.items(), key=lambda kv: [x or "" for x in kv[0]]):
        if template_id not in labels:
            tpl = await templates.find_one({"template_id": template_id}, {"value": 1})
            labels[template_id] = (tpl or {}).get("value") or template_id or "?"
        label = labels[template_id]
        target = f"{label} (template)" if kind == "template_use" else f"{kind} targets in {prefix}"
        refs.append(InboundReference(
            type=kind, source_namespace=ns,
            source_entity=f"{n} document version(s) of {label}",
            target_entity=target, impact=impacts[kind],
        ))
    return refs


async def _inbound_from_term_relations(def_store: Any, prefix: str) -> list[InboundReference]:
    """Term relations of other namespaces touching terms here, one report per
    source namespace with a count."""
    term_ids = [
        row["term_id"]
        async for row in def_store["terms"].find({"namespace": prefix}, {"term_id": 1})
        if row.get("term_id")
    ]
    # By row id: a relation whose two terms fall in different batches is
    # matched twice and must count once.
    namespace_of: dict[Any, str] = {}
    for start in range(0, len(term_ids), _INBOUND_TERM_BATCH):
        chunk = term_ids[start : start + _INBOUND_TERM_BATCH]
        async for row in def_store["term_relations"].find({
            "namespace": {"$ne": prefix},
            "$or": [{"source_term_id": {"$in": chunk}}, {"target_term_id": {"$in": chunk}}],
        }, {"namespace": 1}):
            namespace_of[row["_id"]] = row["namespace"]
    counts: dict[str, int] = {}
    for ns in namespace_of.values():
        counts[ns] = counts.get(ns, 0) + 1
    return [
        InboundReference(
            type="term_relation",
            source_namespace=ns,
            source_entity=f"{n} term relation(s)",
            target_entity=f"terms in {prefix}",
            impact="Relations point at terms that are gone; hierarchies break.",
        )
        for ns, n in sorted(counts.items())
    ]

# id_counters uses counter_key prefix, not a namespace field — handled separately


class NamespaceDeletionService:
    """Orchestrates namespace deletion with a persistent journal."""

    def __init__(self):
        # Stored objects are deleted by document-store, which owns the
        # storage client, bucket and credentials for whichever object store
        # the install runs; the registry only sends it the journal's keys.
        self._document_store_url = os.getenv("DOCUMENT_STORE_URL")
        self._postgres_url = os.getenv("REPORTING_SYNC_URL")
        # Journals this process is executing right now. The background
        # recovery loop skips them, so it never runs a journal twice at once.
        self._running: set[str] = set()
        self._recovery_task: asyncio.Task | None = None

    async def dry_run(self, prefix: str) -> dict[str, Any]:
        """Return an impact report without making any changes.

        ``safe_to_delete`` answers what the delete would answer: false when
        the delete refuses the namespace outright (``refusals`` names each
        rule and its reason, worded as the delete words it) and when inbound
        references require ``force`` (``requires_force``). The report itself
        is always given — it is what a dry run is for.
        """
        ns = await Namespace.find_one({"prefix": prefix})
        if not ns:
            return {"error": "Namespace not found"}
        refusals = deletion_refusals(ns)

        entity_counts = await self._count_entities(prefix)
        storage_keys = await self._get_storage_keys(prefix)
        inbound_refs = await self._check_inbound_references(prefix)

        has_high_severity = any(r.type in _HIGH_SEVERITY_INBOUND for r in inbound_refs)

        return {
            "namespace": prefix,
            "deletion_mode": ns.deletion_mode,
            "dry_run": True,
            "entity_counts": entity_counts,
            # Key name kept for API compatibility: it counts stored file
            # objects in whichever object store the install runs.
            "minio_objects": len(storage_keys),
            "inbound_references": [r.model_dump() for r in inbound_refs],
            "refusals": [{"rule": rule, "message": message} for rule, message, _ in refusals],
            "safe_to_delete": not refusals and not has_high_severity,
            "requires_force": has_high_severity,
        }

    async def start_deletion(
        self,
        prefix: str,
        force: bool = False,
        requested_by: str | None = None,
        requested_note: str | None = None,
    ) -> DeletionJournal:
        """Lock the namespace and build + execute the deletion journal.
        ``requested_by`` is the authenticated caller; ``requested_note`` the
        free text it sent, recorded beside it."""
        ns = await Namespace.find_one({"prefix": prefix})
        if not ns:
            raise ValueError(f"Namespace not found: {prefix}")

        if refusals := deletion_refusals(ns):
            raise ValueError(refusals[0][1])

        # Check inbound references
        inbound_refs = await self._check_inbound_references(prefix)
        high_severity = [r for r in inbound_refs if r.type in _HIGH_SEVERITY_INBOUND]
        if high_severity and not force:
            raise ValueError(
                f"Namespace '{prefix}' has {len(high_severity)} inbound reference(s) "
                "from other namespaces. Use force=true to proceed."
            )

        # Lock the namespace, in one conditional update: two deletions that
        # both read it unlocked cannot both take the lock.
        if not await set_unless_locked(ns, {"status": "locked", "updated_at": datetime.now(UTC)}):
            raise ValueError(
                f"Namespace '{prefix}' is already locked (deletion in progress)"
            )
        logger.info("Namespace '%s' locked for deletion", prefix)
        # Every service drops its cached permissions now: an answer cached
        # before the lock would otherwise keep the namespace open for the
        # rest of its lifetime (30 s) while its data is being deleted.
        await access_changed(keys=False)

        # Build journal. A failure from here to journal.create() leaves the
        # namespace locked with no journal; resume_deletion builds one then.
        journal = await self._build_journal(prefix, force, requested_by, inbound_refs)
        journal.namespace_id = ns.namespace_id
        journal.requested_note = requested_note
        await journal.create()
        logger.info("Deletion journal created for '%s' with %d steps", prefix, len(journal.steps))

        # Execute
        await self._execute_journal(journal)
        return journal

    async def resume_deletion(self, prefix: str) -> DeletionJournal:
        """Resume an unfinished deletion from where it left off.

        Unfinished is any deletion that has not completed cleanly: one still
        in progress, one that stopped on a failed step, and one whose
        best-effort steps (stored files, reporting rows) were degraded. Every
        step deletes by namespace, so running one again is safe; completed
        steps are skipped. A namespace locked with no journal (the deletion
        stopped before its journal was written) gets one built now. There is
        no other way out of a deletion: a namespace partly deleted cannot be
        made whole, so the deletion is finished, never abandoned.
        """
        journal = await DeletionJournal.find_one(
            {"namespace": prefix, "status": {"$in": list(_RESUMABLE)}},
            sort=[("requested_at", -1)],
        )
        if journal is None:
            ns = await Namespace.find_one({"prefix": prefix})
            if ns is None or ns.status != "locked":
                raise ValueError(f"No unfinished deletion found for namespace '{prefix}'")
            # The deletion was requested and its checks passed before the
            # lock; the inbound references are recorded again as they are now.
            inbound_refs = await self._check_inbound_references(prefix)
            journal = await self._build_journal(prefix, True, "resume-delete", inbound_refs)
            journal.namespace_id = ns.namespace_id
            await journal.create()
            logger.warning(
                "Namespace '%s' was locked with no deletion journal: built one (%d steps)",
                prefix, len(journal.steps),
            )
        elif journal.status != "in_progress":
            logger.warning(
                "Resuming the %s deletion of namespace '%s'", journal.status, prefix,
            )
            journal.status = "in_progress"
            journal.completed_at = None
            await journal.save()

        await self._execute_journal(journal)
        return journal

    async def get_deletion_status(self, prefix: str) -> DeletionJournal | None:
        """Get the most recent deletion journal for a namespace."""
        return await DeletionJournal.find_one(
            {"namespace": prefix},
            sort=[("requested_at", -1)],
        )

    async def recover_incomplete_deletions(self) -> int:
        """Resume every in-progress deletion this process is not running.

        Returns how many journals are still in progress afterwards: those
        whose steps are deferred because a service is still unreachable.
        """
        incomplete = await DeletionJournal.find({"status": "in_progress"}).to_list()
        remaining = 0
        for journal in incomplete:
            if str(journal.id) in self._running:
                # Being run by a request right now: still unfinished. Counting
                # it keeps the loop alive in case that run defers a step.
                remaining += 1
                continue
            logger.warning(
                "Found incomplete deletion for namespace '%s', resuming...",
                journal.namespace,
            )
            try:
                await self._execute_journal(journal)
            except Exception:
                logger.exception(
                    "Failed to recover deletion of namespace '%s'", journal.namespace
                )
                # A failed step ends the run as 'failed' (resumed by an admin);
                # anything else leaves it in progress, for the next round.
                if journal.status == "in_progress":
                    remaining += 1
                continue
            if journal.status == "in_progress":
                remaining += 1
            else:
                logger.info("Recovered deletion of namespace '%s'", journal.namespace)
        return remaining

    def start_recovery(self) -> None:
        """Run the recovery loop in the background, unless it is running.

        Called at startup and whenever a deletion defers a step, so a step
        deferred long after startup is still retried.
        """
        if self._recovery_task is None or self._recovery_task.done():
            self._recovery_task = asyncio.create_task(self.recover_until_done())

    async def stop_recovery(self) -> None:
        """Cancel the recovery loop (service shutdown)."""
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task

    async def recover_until_done(self) -> None:
        """Resume in-progress deletions until none is left.

        A journal whose steps wait on an unreachable service is retried with
        backoff until the service answers. Run through start_recovery.
        """
        async def attempt() -> bool:
            return await self.recover_incomplete_deletions() == 0

        await retry_until_done(
            attempt, description="Namespace deletion recovery", sleep=_recovery_sleep
        )

    # -------------------------------------------------------------------------
    # Internal: counting and reference checking
    # -------------------------------------------------------------------------

    async def _count_entities(self, prefix: str) -> dict[str, int]:
        """Count all entities scoped to a namespace.

        Iterates the same collection lists the journal builder deletes from
        (_REGISTRY_COLLECTIONS + _EXTERNAL_COLLECTIONS), so the dry-run
        impact report cannot drift from what the real deletion removes —
        hand-enumerating collections here is how composite_key_claims went
        uncounted while being deleted.
        """
        counts: dict[str, int] = {}

        motor_client = Namespace.get_motor_collection().database.client
        reg_db = motor_client[_registry_db_name()]

        for coll_name, filter_field in _REGISTRY_COLLECTIONS:
            counts[coll_name] = await reg_db[coll_name].count_documents(
                {filter_field: prefix}
            )

        # Older id_counters are keyed "{namespace}:{entity_type}:{prefix}", not
        # by a namespace field, and are deleted by the journal via the same
        # regex filter. The install-wide "@prefix:" counters are not a
        # namespace's and stay: a restore of its IDs must not restart them.
        counts["id_counters"] = await reg_db["id_counters"].count_documents(
            id_counter_filter(prefix)
        )

        # Count across external service databases
        for db_name, coll_name, filter_field, _ in _EXTERNAL_COLLECTIONS:
            db = motor_client[db_name]
            coll = db[coll_name]
            counts[coll_name] = await coll.count_documents({filter_field: prefix})

        return counts

    async def _get_storage_keys(self, prefix: str) -> list[str]:
        """Storage keys of every file record in this namespace (any status)."""
        motor_client = Namespace.get_motor_collection().database.client
        doc_store_db = motor_client["wip_document_store"]
        files_coll = doc_store_db["files"]
        keys = []
        async for doc in files_coll.find(
            {"namespace": prefix},
            {"storage_key": 1, "file_id": 1},
        ):
            key = doc.get("storage_key") or doc.get("file_id")
            if key:
                keys.append(key)
        return keys

    async def _check_inbound_references(self, prefix: str) -> list[InboundReference]:
        """Find references from other namespaces into this one.

        References are stored as canonical ids (a template's ``extends`` and
        field references, edge endpoints' resolved half, a relation's term
        ids, a document's template, terms, files and template/terminology
        references), so each check matches other namespaces' rows against the
        ids this namespace owns. A document reference to a document also
        records its target's namespace, which is matched directly.

        Documents are scanned for their own ids even where a template check
        exists: a document's template may itself live in this namespace, and
        is then deleted with it, so no surviving template says which terms
        and files that document holds.
        """
        refs: list[InboundReference] = []
        motor_client = Namespace.get_motor_collection().database.client
        templates_coll = motor_client["wip_template_store"]["templates"]

        our_templates = await _values_by_id(templates_coll, prefix, "template_id")
        our_terminologies = await _values_by_id(
            motor_client["wip_def_store"]["terminologies"], prefix, "terminology_id"
        )

        # 1. Templates in other namespaces: parent, field vocabularies, field
        #    and edge-endpoint template references.
        if our_templates or our_terminologies:
            refs.extend(await _inbound_from_templates(
                templates_coll, prefix, our_templates, our_terminologies
            ))

        # 2. Documents in other namespaces referencing documents here.
        refs.extend(await _inbound_from_documents(
            motor_client["wip_document_store"]["documents"], templates_coll, prefix
        ))

        # 3. Documents in other namespaces built on templates here, or holding
        #    its terms, files, templates or terminologies.
        owned = {
            "templates": our_templates,
            "terminologies": our_terminologies,
            "terms": await _values_by_id(motor_client["wip_def_store"]["terms"], prefix, "term_id"),
            "files": await _values_by_id(motor_client["wip_document_store"]["files"], prefix, "file_id"),
        }
        refs.extend(await _inbound_from_document_uses(
            motor_client["wip_document_store"]["documents"], templates_coll, prefix, owned,
        ))

        # 4. Term relations in other namespaces touching terms here.
        refs.extend(await _inbound_from_term_relations(
            motor_client["wip_def_store"], prefix
        ))

        # 5. Synonym links (low severity)
        async for entry in RegistryEntry.find({
            "namespace": {"$ne": prefix},
            "synonyms.namespace": prefix,
        }):
            for syn in entry.synonyms:
                if syn.namespace == prefix:
                    refs.append(InboundReference(
                        type="synonym_link",
                        source_namespace=entry.namespace,
                        source_entity=f"{entry.entry_id} ({entry.entity_type})",
                        target_entity=f"synonym in {prefix}",
                        impact="Cross-reference lost. External synonym remains valid standalone.",
                    ))

        return refs

    # -------------------------------------------------------------------------
    # Internal: journal building
    # -------------------------------------------------------------------------

    async def _build_journal(
        self,
        prefix: str,
        force: bool,
        requested_by: str | None,
        inbound_refs: list[InboundReference],
    ) -> DeletionJournal:
        """Build the deletion journal with all steps."""
        steps: list[DeletionStep] = []
        order = 1

        # Step 1: stored file objects. Runs before the files metadata is
        # deleted, and the keys are persisted, so a step that fails or is
        # retried later still knows what to remove after the file records are
        # gone. One step per batch of keys, each batch in its own document
        # outside the journal: the journal is one MongoDB document, and a
        # namespace with a few hundred thousand files has more keys than its
        # 16 MiB hold, however the steps divide them.
        storage_keys = await self._get_storage_keys(prefix)
        for start in range(0, len(storage_keys), _STORAGE_DELETE_BATCH):
            batch = storage_keys[start:start + _STORAGE_DELETE_BATCH]
            steps.append(DeletionStep(
                order=order,
                store="file_storage",
                action="delete_objects",
                detail=(
                    f"Delete {len(batch)} stored file objects"
                    + (f" (keys {start + 1}-{start + len(batch)} of {len(storage_keys)})"
                       if len(storage_keys) > _STORAGE_DELETE_BATCH else "")
                ),
                storage_keys_batch=await _store_key_batch(prefix, batch),
            ))
            order += 1

        # Steps 2-N: MongoDB collections (across all service databases)
        motor_client = Namespace.get_motor_collection().database.client
        reg_db_name = _registry_db_name()

        # External service databases
        for db_name, coll_name, filter_field, _ in _EXTERNAL_COLLECTIONS:
            db = motor_client[db_name]
            coll = db[coll_name]
            count = await coll.count_documents({filter_field: prefix})
            if count > 0:
                steps.append(DeletionStep(
                    order=order,
                    store="mongodb",
                    collection=coll_name,
                    database=db_name,
                    filter={filter_field: prefix},
                    detail=f"Delete {count} documents from {db_name}.{coll_name}",
                ))
                order += 1

        # Registry-local collections
        for coll_name, filter_field in _REGISTRY_COLLECTIONS:
            db = motor_client[reg_db_name]
            coll = db[coll_name]
            count = await coll.count_documents({filter_field: prefix})
            if count > 0:
                steps.append(DeletionStep(
                    order=order,
                    store="mongodb",
                    collection=coll_name,
                    database=reg_db_name,
                    filter={filter_field: prefix},
                    detail=f"Delete {count} documents from {reg_db_name}.{coll_name}",
                ))
                order += 1

        # id_counters: uses counter_key prefix, not a namespace field
        registry_db = motor_client[reg_db_name]
        counters = id_counter_filter(prefix)
        id_counter_count = await registry_db["id_counters"].count_documents(counters)
        if id_counter_count > 0:
            steps.append(DeletionStep(
                order=order,
                store="mongodb",
                collection="id_counters",
                database=reg_db_name,
                filter=counters,
                detail=f"Delete {id_counter_count} ID counters for {prefix}",
            ))
            order += 1

        # PostgreSQL rows (if reporting-sync is configured)
        if self._postgres_url:
            steps.append(DeletionStep(
                order=order,
                store="postgresql",
                action="delete_namespace_rows",
                detail=f"Delete rows WHERE namespace = '{prefix}' from all reporting tables",
                filter={"namespace": prefix},
            ))
            order += 1

        # Final step: delete the namespace record itself
        steps.append(DeletionStep(
            order=order,
            store="mongodb",
            collection="namespaces",
            database=reg_db_name,
            filter={"prefix": prefix},
            detail="Delete namespace record",
        ))

        return DeletionJournal(
            namespace=prefix,
            force=force,
            requested_by=requested_by,
            # Persisted unconditionally: the journal is the audit trail, and a
            # non-forced deletion still breaks its low-severity inbound refs
            # (synonym links) — gating on force would drop the only record of
            # what broke on the common path.
            broken_references=inbound_refs,
            steps=steps,
        )

    # -------------------------------------------------------------------------
    # Internal: journal execution
    # -------------------------------------------------------------------------

    async def _execute_journal(self, journal: DeletionJournal):
        """Execute all pending steps in the journal."""
        journal_id = str(journal.id)
        if journal_id in self._running:
            raise DeletionAlreadyRunning(
                f"The deletion of namespace '{journal.namespace}' is running already"
            )
        self._running.add(journal_id)
        try:
            await self._run_steps(journal)
        finally:
            self._running.discard(journal_id)
            # After every run, finished or not: whatever it removed may sit in
            # other services' caches, and reads during the run may have cached
            # the namespace's ids again. A repeated flush costs nothing.
            await announce_namespace_deleted(get_event_bus(), journal.namespace)

    async def _run_steps(self, journal: DeletionJournal):
        summary: dict[str, int] = {}
        deferred = 0

        # Index-based iteration is load-bearing: Beanie's save() re-syncs the
        # in-memory document from the DB afterwards, REBUILDING journal.steps
        # with fresh objects. A `for step in journal.steps` iterator captured
        # before the first save yields orphans — mutations on them are never
        # serialized, which is how completed journals persisted with every
        # step after the first still 'pending'. Re-binding through
        # journal.steps[i] each iteration always mutates the live objects.
        for i in range(len(journal.steps)):
            step = journal.steps[i]
            if step.status == "completed":
                # Already done (crash recovery)
                _count_step(summary, step)
                continue
            outcome = await self._run_step(journal, step)
            if outcome == "deferred":
                deferred += 1
            elif outcome == "executed":
                _count_step(summary, step)

        if deferred:
            await self._wait_for_deferred(journal, summary, deferred)
        else:
            await self._close_journal(journal, summary)

    async def _run_step(self, journal: DeletionJournal, step: DeletionStep) -> str:
        """Run one step not yet completed and save the journal.

        Returns "executed", "deferred" (its service was unreachable: the step
        stays pending for the recovery loop) or "kept" (left undone: a
        namespace created again under the prefix holds the name now). A failing step
        fails the journal and the error propagates.
        """
        # A re-run must not inherit the error of an earlier attempt.
        step.error = None
        if await self._namespace_recreated(journal):
            # A deferred or resumed step can run long after the namespace
            # record is gone. If the prefix was created again meanwhile, every
            # step still to run would act on the NEW namespace: they all match
            # by name (its documents, registry rows, counters, reporting
            # schema, the namespace record itself), and its stored objects may
            # carry the same keys. None of them may run on behalf of the old
            # namespace.
            step.status = "completed_with_errors"
            step.error = (
                f"namespace '{journal.namespace}' exists again (re-created after "
                "this deletion); this step was left undone to protect it"
            )
            step.completed_at = datetime.now(UTC)
            await journal.save()
            return "kept"
        try:
            deleted = await self._execute_step(step, journal.namespace)
            if step.store == "mongodb" and step.collection == "namespaces":
                # The namespace is gone, and its id with it: a reservation
                # that stays reserves a new namespace of that name.
                from .namespace_identity import after_namespace_deleted
                await after_namespace_deleted(journal.namespace)
        except _Unreachable as e:
            # The service may just be starting: keep the step pending so
            # the recovery loop runs it later, instead of closing the
            # journal with the data left behind for good.
            step.status = "pending"
            step.error = f"{e}; will retry"
            await journal.save()
            logger.warning("Step %d deferred: %s — %s", step.order, step.detail, e)
            return "deferred"
        except Exception as e:
            step.status = "failed"
            step.error = str(e)
            journal.status = "failed"
            await journal.save()
            logger.error("Step %d failed: %s — %s", step.order, step.detail, e)
            raise
        # Best-effort executors (object store/PostgreSQL) record a swallowed
        # failure on step.error instead of raising — surface it as a
        # degraded outcome rather than a clean completion.
        step.status = "completed_with_errors" if step.error else "completed"
        step.deleted_count = deleted
        step.completed_at = datetime.now(UTC)
        logger.info(
            "Step %d/%d completed: %s (%d deleted)",
            step.order, len(journal.steps), step.detail, deleted,
        )
        # Save progress after each step
        await journal.save()
        if step.status == "completed":
            await _drop_key_batch(step)
        return "executed"

    async def _wait_for_deferred(
        self, journal: DeletionJournal, summary: dict[str, int], deferred: int
    ) -> None:
        """Not finished: the recovery loop picks the journal up again."""
        journal.summary = summary
        await journal.save()
        self.start_recovery()
        logger.warning(
            "Namespace '%s' deletion waiting on %d deferred step(s); it stays "
            "in progress and is retried in the background",
            journal.namespace, deferred,
        )

    async def _close_journal(self, journal: DeletionJournal, summary: dict[str, int]) -> None:
        """All steps executed. Roll degraded best-effort steps up into the
        terminal status so a journal reader sees the outcome without
        scanning per-step errors or service logs."""
        degraded = [s for s in journal.steps if s.status == "completed_with_errors"]
        journal.status = "completed_with_warnings" if degraded else "completed"
        journal.completed_at = datetime.now(UTC)
        journal.summary = summary
        await journal.save()
        if degraded:
            logger.warning(
                "Namespace '%s' deletion completed with %d degraded step(s): %s",
                journal.namespace, len(degraded),
                "; ".join(f"{s.detail}: {s.error}" for s in degraded),
            )
        else:
            logger.info("Namespace '%s' deletion completed: %s", journal.namespace, summary)

    async def _namespace_recreated(self, journal: DeletionJournal) -> bool:
        """Whether a namespace holding the prefix now is another one than the
        namespace this journal deletes: one created again under the name.

        Compared by namespace_id. A journal written before it recorded the id
        falls back to the status: anything but the deletion's lock is a new
        namespace.
        """
        live = await Namespace.find_one({"prefix": journal.namespace})
        if live is None:
            return False
        if journal.namespace_id and live.namespace_id:
            return live.namespace_id != journal.namespace_id
        return live.status != "locked"

    async def _execute_step(self, step: DeletionStep, namespace: str) -> int:
        """Execute a single journal step. Returns count of deleted items."""
        if step.store == "mongodb":
            return await self._exec_mongodb_step(step)
        elif step.store in _FILE_STORAGE_STORES:
            return await self._exec_file_storage_step(step, namespace)
        elif step.store == "postgresql":
            return await self._exec_postgresql_step(step, namespace)
        else:
            raise ValueError(f"Unknown store: {step.store}")

    async def _exec_mongodb_step(self, step: DeletionStep) -> int:
        """Delete documents from a MongoDB collection."""
        motor_client = Namespace.get_motor_collection().database.client
        db_name = step.database or _registry_db_name()
        db = motor_client[db_name]
        coll = db[cast(str, step.collection)]
        result = await coll.delete_many(cast(dict, step.filter) or {})
        return result.deleted_count

    async def _exec_file_storage_step(self, step: DeletionStep, namespace: str) -> int:
        """Delete the step's stored objects through document-store."""
        keys = await step_storage_keys(step)
        if not keys:
            return 0
        # A re-run must not inherit the error of an earlier attempt.
        step.error = None

        if not self._document_store_url:
            logger.error("DOCUMENT_STORE_URL is not configured; stored objects not deleted")
            step.error = (
                "DOCUMENT_STORE_URL not configured; "
                f"{len(keys)} stored objects may remain"
            )
            return 0
        api_key = _service_api_key()
        if not api_key:
            logger.error(
                "No service API key configured for the file-storage cleanup "
                "(checked %s)", ", ".join(SERVICE_KEY_VARS),
            )
            step.error = (
                "no service API key configured for file-storage cleanup; "
                f"{len(keys)} stored objects may remain"
            )
            return 0

        deleted = 0
        problems: list[str] = []
        url = f"{self._document_store_url}/api/document-store/files/storage/delete"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                for i in range(0, len(keys), _STORAGE_DELETE_BATCH):
                    batch = keys[i:i + _STORAGE_DELETE_BATCH]
                    resp = await client.post(
                        url,
                        json={"namespace": namespace, "storage_keys": batch},
                        headers={"X-API-Key": api_key},
                    )
                    if resp.status_code >= 500:
                        # Deletes are idempotent: the retry re-sends every key.
                        raise _Unreachable(
                            f"document-store answered {resp.status_code}: {resp.text[:200]}"
                        )
                    if resp.status_code != 200:
                        problems.append(
                            f"document-store answered {resp.status_code} for "
                            f"{len(batch)} keys: {resp.text[:200]}"
                        )
                        continue
                    try:
                        data = resp.json()
                        deleted += int(data.get("deleted") or 0)
                        refused, failed = data.get("refused") or [], data.get("failed") or []
                        if refused:
                            problems.append(
                                f"{len(refused)} keys refused (owned by another namespace)"
                            )
                        if failed:
                            problems.append(f"{len(failed)} keys failed to delete")
                    except (ValueError, TypeError, AttributeError) as e:
                        problems.append(
                            f"document-store answered 200 with an unreadable body for "
                            f"{len(batch)} keys ({e}): {resp.text[:200]}"
                        )
        except httpx.TransportError as e:
            # Connection refused, reset, timed out: the service may be
            # restarting. Retried, never recorded as a lasting degradation.
            raise _Unreachable(f"could not reach document-store: {e}") from e
        except httpx.HTTPError as e:
            logger.warning("File-storage cleanup through document-store failed: %s", e)
            problems.append(f"document-store request failed: {e}")

        if problems:
            step.error = (
                f"{deleted} of {len(keys)} stored objects deleted; "
                + "; ".join(problems)
                + "; objects may remain in storage"
            )
        return deleted

    async def _exec_postgresql_step(self, step: DeletionStep, namespace: str) -> int:
        """Delete namespace rows from PostgreSQL via reporting-sync API."""
        if not self._postgres_url:
            return 0
        # A re-run must not inherit the error of an earlier attempt.
        step.error = None

        api_key = _service_api_key()
        if not api_key:
            # Sending an empty key would be refused by reporting-sync and
            # degrade to the generic "rows may remain" below; name the real
            # cause instead so the journal points at the misconfiguration.
            logger.error(
                "No service API key configured for the reporting-sync cleanup "
                "(checked %s)", ", ".join(SERVICE_KEY_VARS),
            )
            step.error = (
                "no service API key configured for reporting-sync cleanup; "
                "PostgreSQL rows may remain"
            )
            return 0

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.delete(
                    f"{self._postgres_url}/api/reporting-sync/namespace/{namespace}",
                    headers={"X-API-Key": api_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    total = data.get("total_deleted")
                    if total is None:
                        # A present-but-null count (the reporting-sync
                        # schema-per-namespace rewrite shipped one) must not
                        # reach the int-typed journal fields — it poisons the
                        # journal document and the DELETE reports failure
                        # after the deletion already succeeded. Record the
                        # degraded audit instead of inventing a count.
                        step.error = (
                            "postgres row count unavailable (upstream "
                            "returned null total_deleted)"
                        )
                        return 0
                    return int(total)
                elif resp.status_code == 404:
                    # Endpoint doesn't exist yet — best-effort skip, but the
                    # rows remain, so record the degraded outcome.
                    logger.warning(
                        "Reporting-sync namespace delete endpoint not available (404)"
                    )
                    step.error = (
                        "reporting-sync namespace-delete endpoint unavailable "
                        "(404); PostgreSQL rows may remain"
                    )
                    return 0
                elif resp.status_code >= 500:
                    raise _Unreachable(
                        f"reporting-sync answered {resp.status_code}: {resp.text[:200]}"
                    )
                else:
                    logger.warning(
                        "PostgreSQL cleanup returned %d: %s",
                        resp.status_code, resp.text,
                    )
                    step.error = (
                        f"PostgreSQL cleanup returned {resp.status_code}; "
                        "rows may remain"
                    )
                    return 0
        except httpx.TransportError as e:
            raise _Unreachable(f"could not reach reporting-sync: {e}") from e
        except _Unreachable:
            raise
        except Exception as e:
            logger.error("PostgreSQL deletion failed: %s", e)
            step.error = f"PostgreSQL cleanup failed: {e}; rows may remain"
            return 0
