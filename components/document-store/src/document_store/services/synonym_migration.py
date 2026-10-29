"""Re-key documents' identity-values synonyms to the template-qualified form.

Every synonym is a qualified key-value pair. Document-store used to register
a document's identity spellings as a bare synonym, e.g. ``{title: "X"}``.
The Registry claims synonyms exclusively, so a bare one claimed that spelling
for every template in the namespace: a scene titled like a chapter was
refused. New documents register ``{"@template": <template value>, title: "X"}``
instead. This pass brings existing documents to the same form, per
namespace:

- for each document whose template declares identity fields (edge types
  excepted, they never had the synonym), find its Registry entry's bare
  synonym, the one whose keys are exactly the template's identity fields;
- add the qualified synonym, then remove the bare one, which releases its
  claim.

A bare synonym that is inactive belongs to a retired document whose synonyms
were released. Its qualified form is added and released again, so the
document stays retired in the new form too; when another entity already holds
the qualified key, the bare synonym is only removed — the key is that
entity's now.

Dry-run (the default) only reports. Applying is idempotent: a document that
already has the qualified synonym and no bare one is ``unchanged``.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .registry_client import (
    SYSTEM_QUALIFIER_KEY,
    RegistryError,
    get_registry_client,
)
from .template_store_client import TemplateStoreError, get_template_store_client

logger = logging.getLogger(__name__)


@dataclass
class RequalifyItem:
    """One document's outcome."""

    document_id: str
    template_value: str | None
    status: str  # requalified | unchanged | no_entry | error
    bare: dict[str, Any] | None = None
    qualified: dict[str, Any] | None = None
    detail: str | None = None
    # The bare synonym was released (its document is retired); not reported.
    released: bool = False


@dataclass
class RequalifyReport:
    """The pass result."""

    namespace: str
    dry_run: bool
    scanned: int = 0
    requalified: int = 0
    unchanged: int = 0
    no_entry: int = 0
    errors: int = 0
    items: list[RequalifyItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "requalified": self.requalified,
            "unchanged": self.unchanged,
            "no_entry": self.no_entry,
            "errors": self.errors,
            "items": [
                {k: v for k, v in vars(i).items() if k != "released"}
                for i in self.items if i.status != "unchanged"
            ],
        }


async def _template_info(
    cache: dict[str, tuple[str, list[str], bool] | None], template_id: str,
) -> tuple[str, list[str], bool] | None:
    """(value, identity_fields, is_relationship) of a template, cached."""
    if template_id in cache:
        return cache[template_id]
    try:
        template = await get_template_store_client().get_template_resolved(template_id)
    except TemplateStoreError:
        return None  # transient — do not cache
    info = (
        None
        if not template
        else (
            str(template.get("value") or ""),
            list(template.get("identity_fields") or []),
            template.get("usage") == "relationship",
        )
    )
    cache[template_id] = info
    return info


# Items per Registry request, as in the other restore steps.
REQUALIFY_BATCH = 500


async def requalify_identity_synonyms(
    namespace: str,
    dry_run: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> RequalifyReport:
    """Re-key every document's bare identity-values synonym in a namespace.

    Applied, it works in batches of `REQUALIFY_BATCH`: the qualified
    synonyms of a batch are added in one request, then the bare ones are
    removed in one request, only for the documents whose add succeeded. A
    document is therefore never without a resolvable synonym, and it counts
    as requalified only when both its add and its remove succeeded.
    `progress(done, total)` is called after each batch.
    """
    from ..models.document import Document

    report = RequalifyReport(namespace=namespace, dry_run=dry_run)
    templates: dict[str, tuple[str, list[str], bool] | None] = {}

    # One row per document (any status: a retired document's entry holds its
    # claims too). Only documents with an identity have the synonym.
    rows = await Document.get_motor_collection().aggregate([
        {"$match": {"namespace": namespace, "identity_hash": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$document_id", "template_id": {"$first": "$template_id"}}},
    ]).to_list(length=None)

    registry = get_registry_client()
    synonyms_by_entry = await registry.get_entry_synonyms(
        [r["_id"] for r in rows], namespace,
    )

    planned: list[RequalifyItem] = []
    for row in rows:
        document_id, template_id = row["_id"], row["template_id"]
        report.scanned += 1
        info = await _template_info(templates, template_id)
        if info is None:
            report.errors += 1
            report.items.append(RequalifyItem(
                document_id, None, "error", detail=f"template {template_id} unavailable",
            ))
            continue
        template_value, identity_fields, is_relationship = info
        if is_relationship or not identity_fields:
            report.unchanged += 1
            continue
        if document_id not in synonyms_by_entry:
            report.no_entry += 1
            report.items.append(RequalifyItem(document_id, template_value, "no_entry"))
            continue

        keys = set(identity_fields)
        bare = [
            (s["composite_key"], s.get("status") == "inactive")
            for s in synonyms_by_entry[document_id]
            if set(s.get("composite_key") or {}) == keys
        ]
        if not bare:
            report.unchanged += 1
            continue

        for bare_key, released in bare:
            qualified = {SYSTEM_QUALIFIER_KEY: template_value, **bare_key}
            planned.append(RequalifyItem(
                document_id, template_value, "requalified", bare_key, qualified,
                released=released,
            ))

    if not dry_run:
        for start in range(0, len(planned), REQUALIFY_BATCH):
            await _apply_batch(registry, namespace, planned[start:start + REQUALIFY_BATCH])
            if progress is not None:
                progress(min(start + REQUALIFY_BATCH, len(planned)), len(planned))

    for item in planned:
        if item.status == "error":
            report.errors += 1
        else:
            report.requalified += 1
        report.items.append(item)

    logger.info(
        "Synonym requalification %s namespace %s: %d scanned, %d requalified, %d errors",
        "dry-run of" if dry_run else "applied to", namespace,
        report.scanned, report.requalified, report.errors,
    )
    return report


def _synonym_item(namespace: str, document_id: str, key: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_id": document_id,
        "synonym_namespace": namespace,
        "synonym_entity_type": "documents",
        "synonym_composite_key": key,
    }


async def _apply_batch(registry: Any, namespace: str, batch: list[RequalifyItem]) -> None:
    """Add the batch's qualified synonyms, then remove the bare ones for the
    items whose add succeeded. A failure marks the item `error`; an item
    whose add failed keeps its bare synonym. A released bare synonym whose
    qualified key another entry holds is removed without the add; one whose
    add succeeded is released again with its document."""
    try:
        added = await registry.add_synonyms_bulk(
            [_synonym_item(namespace, i.document_id, i.qualified or {}) for i in batch]
        )
    except RegistryError as e:
        for item in batch:
            item.status, item.detail = "error", str(e)
        return

    to_remove: list[RequalifyItem] = []
    for item, result in zip(batch, added, strict=True):
        if result.get("status") in ("added", "already_exists"):
            to_remove.append(item)
        elif item.released and result.get("error_code") == "synonym_conflict":
            item.qualified = None
            item.detail = (
                "retired document; the qualified key is held by another "
                "entry, so only the bare synonym was removed"
            )
            to_remove.append(item)
        else:
            item.status = "error"
            item.detail = str(result.get("error") or result.get("status"))

    try:
        removed = await registry.remove_synonyms_bulk(
            [_synonym_item(namespace, i.document_id, i.bare or {}) for i in to_remove]
        )
    except RegistryError as e:
        for item in to_remove:
            item.status, item.detail = "error", f"qualified synonym added, bare not removed: {e}"
        return

    for item, result in zip(to_remove, removed, strict=True):
        # not_found: the bare synonym was already gone, which is the outcome
        # the pass is after.
        if result.get("status") not in ("removed", "not_found"):
            item.status = "error"
            item.detail = (
                "qualified synonym added, bare not removed: "
                f"{result.get('error') or result.get('status')}"
            )

    # A retired document's re-keyed synonym was claimed by the add: release
    # it again, so the document stays retired in the qualified form too.
    retire = sorted({
        i.document_id for i in to_remove
        if i.released and i.qualified is not None and i.status != "error"
    })
    if retire:
        try:
            await registry.deactivate_entries(retire)
        except RegistryError as e:
            for item in to_remove:
                if item.document_id in retire:
                    item.status = "error"
                    item.detail = f"requalified, but its synonyms were not released again: {e}"
