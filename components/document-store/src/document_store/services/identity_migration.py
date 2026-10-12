"""Identity-hash migration to the resolved-referent feed.

One explicit migration pass, per namespace: every document whose template has
term/reference-typed identity fields is re-hashed from its stored resolutions
(``term_references`` / ``references[]``), the Mongo rows are updated, and the
Registry's composite-key claims are re-keyed through the Registry's own
sanctioned endpoint. There is no dual-hash transition window — run the pass,
then re-sync reporting (replay) for the affected templates.

Dry-run first: the report lists per-document outcomes, and in particular
**collision groups** — documents that were forked under spelling-fed hashing
and now converge on one identity. Converging two existing version chains is
vocabulary curation, not migration (archive or merge one side first), so
collisions are reported and never applied.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from wip_auth.document_identity import (
    build_referent_resolutions,
    compute_hash,
    extract_identity_values,
    referent_identity_fields,
    substitute_resolved_referents,
)

from .registry_client import RegistryError, get_registry_client
from .template_store_client import TemplateStoreError, get_template_store_client

logger = logging.getLogger(__name__)


@dataclass
class RehashItem:
    """One document's migration outcome."""

    document_id: str
    template_id: str
    template_value: str | None
    status: str  # changed | unchanged | unresolved | collision | error
    old_hash: str | None = None
    new_hash: str | None = None
    detail: str | None = None
    versions_updated: int = 0
    registry_status: str | None = None


@dataclass
class RehashReport:
    """The migration pass result."""

    namespace: str
    dry_run: bool
    scanned: int = 0
    changed: int = 0
    unchanged: int = 0
    unresolved: int = 0
    collisions: int = 0
    errors: int = 0
    applied: int = 0
    items: list[RehashItem] = field(default_factory=list)
    collision_groups: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "unresolved": self.unresolved,
            "collisions": self.collisions,
            "errors": self.errors,
            "applied": self.applied,
            "items": [vars(i) for i in self.items if i.status != "unchanged"],
            "collision_groups": self.collision_groups,
            "reporting_note": (
                "Reporting is not updated by this pass — replay the affected "
                "templates (POST /replay) so the identity_hash columns re-sync."
            ),
        }


async def _template_shape(
    cache: dict[tuple[str, int], tuple[list[str], list[dict[str, Any]]] | None],
    template_id: str,
    version: int,
) -> tuple[list[str], list[dict[str, Any]]] | None:
    key = (template_id, version)
    if key in cache:
        return cache[key]
    client = get_template_store_client()
    try:
        template = await client.get_template(template_id=template_id, version=version)
    except TemplateStoreError:
        return None  # transient — do not cache
    shape = (
        None
        if template is None
        else (
            list(template.get("identity_fields") or []),
            list(template.get("fields") or []),
        )
    )
    cache[key] = shape
    return shape


async def rehash_identities(
    namespace: str,
    dry_run: bool = True,
    template_values: list[str] | None = None,
) -> RehashReport:
    """Re-hash every referent-identity document in a namespace.

    The feed is recomputed from each document's latest version row — identity
    values cannot change across versions, and the stored resolutions carry
    the referent IDs — and applied to every version row of the document.
    """
    from ..models.document import Document

    report = RehashReport(namespace=namespace, dry_run=dry_run)
    shape_cache: dict[tuple[str, int], tuple[list[str], list[dict[str, Any]]] | None] = {}
    collection = Document.get_motor_collection()

    # Latest version row per document_id in the namespace (any status — the
    # hash describes the entity, archived or not).
    pipeline: list[dict[str, Any]] = [
        {"$match": {"namespace": namespace}},
        {"$sort": {"document_id": 1, "version": -1}},
        {"$group": {"_id": "$document_id", "row": {"$first": "$$ROOT"}}},
    ]
    if template_values:
        pipeline.insert(1, {"$match": {"template_value": {"$in": template_values}}})

    # (template_id, new_hash) -> document_ids, to surface forked duplicates
    # that now converge. Seeded with every scanned doc's OUTCOME hash (new
    # when changed, current when not), so a changed doc colliding with an
    # unchanged one is caught too.
    converge: dict[tuple[str, str], set[str]] = {}
    changed_items: list[RehashItem] = []

    async for grouped in collection.aggregate(pipeline):
        row = grouped["row"]
        report.scanned += 1
        document_id = row["document_id"]
        template_id = row["template_id"]
        template_value = row.get("template_value")
        item = RehashItem(
            document_id=document_id,
            template_id=template_id,
            template_value=template_value,
            status="unchanged",
            old_hash=row.get("identity_hash"),
        )

        shape = await _template_shape(
            shape_cache, template_id, row.get("template_version", 1)
        )
        if shape is None:
            item.status = "error"
            item.detail = "template version unreadable"
            report.errors += 1
            report.items.append(item)
            continue
        identity_fields, template_fields = shape
        if not identity_fields:
            report.unchanged += 1
            continue
        referent_fields = referent_identity_fields(identity_fields, template_fields)
        if not referent_fields:
            report.unchanged += 1
            continue

        try:
            values = extract_identity_values(row.get("data") or {}, identity_fields)
            values = substitute_resolved_referents(
                values,
                referent_fields,
                build_referent_resolutions(
                    row.get("term_references"), row.get("references")
                ),
            )
        except ValueError as exc:
            # A legacy doc written during a resolution outage: no stored
            # resolution to recompute from. Needs live re-resolution (or
            # curation) before it can migrate.
            item.status = "unresolved"
            item.detail = str(exc)
            report.unresolved += 1
            report.items.append(item)
            continue

        new_hash = compute_hash(values)
        item.new_hash = new_hash
        converge.setdefault((template_id, new_hash), set()).add(document_id)

        if new_hash == row.get("identity_hash"):
            report.unchanged += 1
            continue

        item.status = "changed"
        changed_items.append(item)
        report.items.append(item)

    # Collision pass: >1 document converging on one (template_id, hash) is a
    # pre-existing fork surfacing. Report; never apply.
    collided_ids: set[str] = set()
    for (template_id, new_hash), doc_ids in converge.items():
        if len(doc_ids) > 1:
            collided_ids.update(doc_ids)
            report.collision_groups.append({
                "template_id": template_id,
                "identity_hash": new_hash,
                "document_ids": sorted(doc_ids),
            })
    for item in changed_items:
        if item.document_id in collided_ids:
            item.status = "collision"
            item.detail = (
                "another document converges on the same identity — curate "
                "(archive or merge) before migrating these"
            )
            report.collisions += 1
        else:
            report.changed += 1

    if dry_run:
        return report

    # Apply: Mongo rows first, then the Registry claims. A doc whose Registry
    # re-key fails is reported per item; re-running the pass converges.
    to_apply = [i for i in changed_items if i.status == "changed"]
    registry = get_registry_client()
    for item in to_apply:
        result = await collection.update_many(
            {"document_id": item.document_id},
            {"$set": {"identity_hash": item.new_hash}},
        )
        item.versions_updated = result.modified_count

    if to_apply:
        try:
            rekey_results = await registry.rekey_identity([
                {"entry_id": i.document_id, "identity_hash": i.new_hash}
                for i in to_apply
            ])
            by_id = {r.get("entry_id"): r for r in rekey_results}
            for item in to_apply:
                item.registry_status = (by_id.get(item.document_id) or {}).get(
                    "status", "missing"
                )
        except RegistryError as exc:
            logger.error("Registry re-key failed: %s", exc)
            for item in to_apply:
                item.registry_status = f"error: {exc}"

    report.applied = len(to_apply)
    return report
