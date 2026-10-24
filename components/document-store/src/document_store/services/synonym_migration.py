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

Dry-run (the default) only reports. Applying is idempotent: a document that
already has the qualified synonym and no bare one is ``unchanged``.
"""

import logging
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
            "items": [vars(i) for i in self.items if i.status != "unchanged"],
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


async def requalify_identity_synonyms(
    namespace: str, dry_run: bool = True,
) -> RequalifyReport:
    """Re-key every document's bare identity-values synonym in a namespace."""
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
            s["composite_key"] for s in synonyms_by_entry[document_id]
            if set(s.get("composite_key") or {}) == keys
        ]
        if not bare:
            report.unchanged += 1
            continue

        for bare_key in bare:
            qualified = {SYSTEM_QUALIFIER_KEY: template_value, **bare_key}
            item = RequalifyItem(document_id, template_value, "requalified", bare_key, qualified)
            if not dry_run:
                try:
                    added = await registry.add_synonyms(
                        document_id, namespace, "documents", [qualified],
                    )
                    if added[0].get("status") not in ("added", "already_exists"):
                        raise RegistryError(added[0].get("error") or added[0].get("status"))
                    await registry.remove_synonyms(document_id, namespace, "documents", [bare_key])
                except RegistryError as e:
                    item.status, item.detail = "error", str(e)
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
