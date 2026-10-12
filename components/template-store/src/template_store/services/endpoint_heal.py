"""Heal legacy edge-type endpoint entries to the two-half shape.

Edge types declared before endpoints carried a resolved half serve
``{lookup_value, resolved: null}`` on every entry. The model promised they
would "heal at the next write, activation, or restore repair" — but those
events essentially never touch an established edge type, so the nulls are
permanent in practice, and a consumer keying on ``resolved`` (the documented
exact-id half) reads null across whole namespaces.

This pass is the missing heal trigger: idempotent, per namespace, dry-run
first. It resolves each null ``resolved`` through the Registry in the
template's own namespace (``bypass_cache=True`` — the fill is persisted, so
it must not come from the read cache) and writes the entry in place. A
lookup that no longer resolves is reported and left null — never dropped;
the null half keeps failing closed wherever an exact id is required.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from wip_auth.resolve import EntityNotFoundError, resolve_entity_id

from ..models.template import Template

logger = logging.getLogger(__name__)


@dataclass
class HealReport:
    """Result of one heal pass."""

    namespace: str
    dry_run: bool
    templates_scanned: int = 0
    templates_changed: int = 0
    entries_healed: int = 0
    entries_already_filled: int = 0
    entries_unresolvable: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "dry_run": self.dry_run,
            "templates_scanned": self.templates_scanned,
            "templates_changed": self.templates_changed,
            "entries_healed": self.entries_healed,
            "entries_already_filled": self.entries_already_filled,
            "entries_unresolvable": self.entries_unresolvable,
            "items": self.items,
        }


async def heal_endpoints(namespace: str, dry_run: bool = True) -> HealReport:
    """Fill null ``resolved`` halves on a namespace's edge-type endpoints.

    Walks every stored relationship-template row (all versions — a
    pinned-version read serves its own stored entries) and persists the
    resolved half in place. No version bump: this is a representation fill
    on the declaration the caller already made, not a schema change.
    """
    report = HealReport(namespace=namespace, dry_run=dry_run)

    async for template in Template.find(
        {"usage": "relationship", "namespace": namespace}
    ):
        report.templates_scanned += 1
        healed_here: list[dict[str, Any]] = []
        unresolvable_here: list[dict[str, Any]] = []

        for list_name in ("source_templates", "target_templates"):
            for entry in getattr(template, list_name) or []:
                if entry.resolved:
                    report.entries_already_filled += 1
                    continue
                try:
                    entry.resolved = await resolve_entity_id(
                        entry.lookup_value, "template", template.namespace,
                        bypass_cache=True,
                    )
                except EntityNotFoundError:
                    report.entries_unresolvable += 1
                    unresolvable_here.append({
                        "list": list_name,
                        "lookup_value": entry.lookup_value,
                    })
                    continue
                report.entries_healed += 1
                healed_here.append({
                    "list": list_name,
                    "lookup_value": entry.lookup_value,
                    "resolved": entry.resolved,
                })

        if healed_here or unresolvable_here:
            report.items.append({
                "template_id": template.template_id,
                "value": template.value,
                "version": template.version,
                "healed": healed_here,
                "unresolvable": unresolvable_here,
            })
        if healed_here:
            report.templates_changed += 1
            if not dry_run:
                await template.save()

    return report
