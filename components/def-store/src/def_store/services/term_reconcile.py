"""Terminology snapshot reconcile — "make this terminology match this list".

The natural operation for an externally sourced vocabulary (a periodic
feed export): one call takes the full desired term set and a policy,
computes the diff against the live terminology, and — outside of dry-run —
applies it through the existing service doors, so Registry registration,
names validation, the collision guard, NATS events and audit logging all
behave exactly as individual writes.

Semantics, deliberately scoped:

- Terms are keyed by their exact `value` (the identity within a
  terminology). Only ACTIVE terms count as existing; a snapshot value
  colliding with a deprecated or inactive term is a per-item error
  (`exists_inactive`) — reconcile does not reactivate retired terms, that
  is a lifecycle decision the data owner makes explicitly.
- The snapshot is authoritative for a term's SUBSTANCE: label and names
  always; description, sort_order and parent_term_id only when the
  snapshot sets them (a feed that OMITS them keeps the existing values).
  An EXPLICITLY submitted null clears description or parent_term_id —
  RFC 7396 semantics, matching update_term — and stays idempotent: a
  cleared field compares equal to the explicit null on the re-run.
  `metadata` is caller-attached context by platform convention: honored on
  create, never diffed or rewritten on update.
- Absent terms (active in the terminology, not in the snapshot) are
  DEPRECATED, never deleted, and only under on_missing='deprecate' — a
  partial snapshot must not silently retire half the vocabulary. An
  optional successors map (absent value -> successor value) sets
  replaced_by_term_id; the successor may itself be new in this snapshot
  (creates apply first).
- dry_run (the default) computes the full plan — including per-item names
  validation — and writes nothing. Name-collision warnings surface on
  apply through the normal write doors (details.name_collisions /
  name_collision under unique_names).
- The response reports counts for everything and rows only for changes
  and errors: an unchanged 12,000-term snapshot answers with counts, not
  12,000 rows.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models.api_models import (
    BulkResultItem,
    CreateTermRequest,
    DeprecateTermRequest,
    StrictModel,
    UpdateTermRequest,
)
from ..models.term import Term, TermName
from ..models.terminology import Terminology
from .errors import error_fields
from .term_names import NameTypeService, normalize_names, validate_names

logger = logging.getLogger(__name__)


class ReconcileRequest(StrictModel):
    """The desired full term set for one terminology, plus policy."""

    terms: list[CreateTermRequest] = Field(
        ..., description="The complete desired term set, keyed by value"
    )
    on_missing: Literal["ignore", "deprecate"] = Field(
        default="ignore",
        description="What happens to active terms absent from the snapshot: "
                    "'ignore' (default — count them, touch nothing) or "
                    "'deprecate'",
    )
    successors: dict[str, str] = Field(
        default_factory=dict,
        description="For on_missing='deprecate': absent value -> successor "
                    "value (must exist in the snapshot or survive it)",
    )
    deprecation_reason: str = Field(
        default="Absent from reconcile snapshot",
        description="Reason recorded on terms deprecated by this reconcile",
    )
    dry_run: bool = Field(
        default=True,
        description="Compute and return the plan without writing (default). "
                    "Pass false to apply.",
    )


class ReconcileCounts(BaseModel):
    """What the reconcile did (or would do, under dry_run)."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deprecated: int = 0
    missing_ignored: int = 0
    errors: int = 0


class ReconcileResponse(BaseModel):
    """The reconcile plan or outcome.

    results carries rows for creates, updates, deprecations and errors —
    unchanged terms are counted, not listed.
    """

    dry_run: bool
    terminology_id: str
    terminology_value: str
    counts: ReconcileCounts
    results: list[BulkResultItem]
    total_submitted: int
    total_existing_active: int


def _effective_label(req: CreateTermRequest) -> str:
    return req.label or req.value


def _reconcile_diff(
    existing: Term, desired: CreateTermRequest, desired_names: list[TermName]
) -> list[str]:
    """Substance fields where the snapshot differs from the live term.

    label and names are always authoritative; description, sort_order and
    parent_term_id only when the snapshot sets them (None / 0 means "keep").
    metadata is caller context — never diffed.
    """
    checks = {
        "label": existing.label != _effective_label(desired),
        "names": [n.model_dump() for n in existing.names]
                 != [n.model_dump() for n in desired_names],
        "description": (
            desired.description is not None
            and (existing.description or None) != desired.description
        ) or (
            # explicit null in the snapshot clears
            # existing.description is not None (not `or None`): an
            # explicit null also normalizes the "" workaround to a real
            # cleared field
            "description" in desired.model_fields_set
            and desired.description is None
            and existing.description is not None
        ),
        "sort_order": (
            desired.sort_order != 0
            and existing.sort_order != desired.sort_order
        ),
        "parent_term_id": (
            desired.parent_term_id is not None
            and existing.parent_term_id != desired.parent_term_id
        ) or (
            "parent_term_id" in desired.model_fields_set
            and desired.parent_term_id is None
            and existing.parent_term_id is not None
        ),
    }
    return [name for name, differs in checks.items() if differs]


class TermReconcileService:
    """Diff a terminology against a desired snapshot and apply the delta."""

    @staticmethod
    async def reconcile(
        terminology: Terminology,
        request: ReconcileRequest,
        actor: str | None = None,
    ) -> ReconcileResponse:
        from .terminology_service import NameCollisionError, TerminologyService

        counts = ReconcileCounts()
        results: list[BulkResultItem] = []
        valid_types = await NameTypeService.get_valid_name_types()

        existing_terms = await Term.find({
            "namespace": terminology.namespace,
            "terminology_id": terminology.terminology_id,
        }).to_list()
        by_value: dict[str, Term] = {t.value: t for t in existing_terms}
        active_values = {t.value for t in existing_terms if t.status == "active"}

        # ------------------------------------------------------------------
        # Plan: classify every snapshot item
        # ------------------------------------------------------------------
        to_create: list[tuple[int, CreateTermRequest]] = []
        to_update: list[tuple[int, CreateTermRequest, Term, list[str], list[TermName]]] = []
        seen_values: set[str] = set()

        for i, item in enumerate(request.terms):
            if item.value in seen_values:
                results.append(BulkResultItem(
                    index=i, status="error", value=item.value,
                    error=f"Value '{item.value}' appears more than once in the snapshot",
                    error_code="duplicate_in_snapshot",
                ))
                counts.errors += 1
                continue
            seen_values.add(item.value)

            try:
                desired_names = normalize_names(
                    item.names, item.aliases, item.translations
                )
                validate_names(desired_names, item.value, valid_types)
            except ValueError as e:
                results.append(BulkResultItem(
                    index=i, status="error", value=item.value,
                    error=str(e), error_code="invalid_names",
                ))
                counts.errors += 1
                continue

            existing = by_value.get(item.value)
            if existing is None:
                to_create.append((i, item))
                continue
            if existing.status != "active":
                results.append(BulkResultItem(
                    index=i, status="error", value=item.value,
                    id=existing.term_id,
                    error=(
                        f"Value '{item.value}' exists with status "
                        f"'{existing.status}' — reconcile does not reactivate "
                        "retired terms; reactivate it explicitly or drop it "
                        "from the snapshot"
                    ),
                    error_code="exists_inactive",
                ))
                counts.errors += 1
                continue

            changed = _reconcile_diff(existing, item, desired_names)
            if changed:
                to_update.append((i, item, existing, changed, desired_names))
            else:
                counts.unchanged += 1

        # Absent terms: active, not in the snapshot
        missing_values = sorted(active_values - seen_values)
        if request.on_missing == "ignore":
            counts.missing_ignored = len(missing_values)
            missing_values = []

        # Successor sanity: a successor must be in the snapshot or survive it
        surviving = seen_values | (active_values - set(missing_values))
        deprecation_rows: list[tuple[int, str, str | None]] = []
        next_index = len(request.terms)
        for value in missing_values:
            successor = request.successors.get(value)
            if successor is not None and successor not in surviving:
                results.append(BulkResultItem(
                    index=next_index, status="error", value=value,
                    error=(
                        f"Successor '{successor}' for '{value}' is neither in "
                        "the snapshot nor a surviving term"
                    ),
                    error_code="unknown_successor",
                ))
                counts.errors += 1
            else:
                deprecation_rows.append((next_index, value, successor))
            next_index += 1

        # ------------------------------------------------------------------
        # Dry run: report the plan, write nothing
        # ------------------------------------------------------------------
        if request.dry_run:
            for i, item in to_create:
                results.append(BulkResultItem(
                    index=i, status="created", value=item.value,
                ))
                counts.created += 1
            for i, item, existing, changed, _ in to_update:
                results.append(BulkResultItem(
                    index=i, status="updated", value=item.value,
                    id=existing.term_id, details={"changed": changed},
                ))
                counts.updated += 1
            for idx, value, successor in deprecation_rows:
                results.append(BulkResultItem(
                    index=idx, status="deprecated", value=value,
                    id=by_value[value].term_id,
                    details={"successor": successor} if successor else None,
                ))
                counts.deprecated += 1
            results.sort(key=lambda r: r.index)
            return TermReconcileService._response(
                terminology, request, counts, results, len(active_values)
            )

        # ------------------------------------------------------------------
        # Apply: creates first (so successors can be fresh), then updates,
        # then deprecations — all through the existing doors
        # ------------------------------------------------------------------
        if to_create:
            create_results = await TerminologyService.create_terms_bulk(
                terminology_id=terminology.terminology_id,
                terms=[item for _, item in to_create],
                created_by=actor,
            )
            for (i, _item), row in zip(to_create, create_results, strict=True):
                row.index = i
                results.append(row)
                if row.status == "created":
                    counts.created += 1
                else:
                    counts.errors += 1

        for i, item, existing, changed, desired_names in to_update:
            update_kwargs: dict[str, Any] = {
                "label": _effective_label(item),
                "names": desired_names,
            }
            # A field the snapshot sets is applied; one it explicitly
            # nulls is cleared (passed as explicit None so update_term's
            # fields_set sees it); one it omits stays omitted here too
            if item.description is not None or (
                "description" in item.model_fields_set
                and existing.description is not None
            ):
                update_kwargs["description"] = item.description
            if item.sort_order != 0:
                update_kwargs["sort_order"] = item.sort_order
            if item.parent_term_id is not None or (
                "parent_term_id" in item.model_fields_set
                and existing.parent_term_id is not None
            ):
                update_kwargs["parent_term_id"] = item.parent_term_id
            update_req = UpdateTermRequest(**update_kwargs)
            try:
                collision_warnings: list[dict[str, Any]] = []
                await TerminologyService.update_term(
                    existing.term_id, update_req, collision_out=collision_warnings
                )
                details: dict[str, Any] = {"changed": changed}
                if collision_warnings:
                    details["name_collisions"] = collision_warnings
                results.append(BulkResultItem(
                    index=i, status="updated", value=item.value,
                    id=existing.term_id, details=details,
                ))
                counts.updated += 1
            except ValueError as e:
                error_code = (
                    "name_collision" if isinstance(e, NameCollisionError) else None
                )
                results.append(BulkResultItem(
                    index=i, status="error", value=item.value,
                    id=existing.term_id, error=str(e), error_code=error_code,
                ))
                counts.errors += 1

        for idx, value, successor in deprecation_rows:
            successor_id: str | None = None
            if successor is not None:
                successor_term = await Term.find_one({
                    "namespace": terminology.namespace,
                    "terminology_id": terminology.terminology_id,
                    "value": successor,
                })
                successor_id = successor_term.term_id if successor_term else None
            try:
                await TerminologyService.deprecate_term(
                    by_value[value].term_id,
                    DeprecateTermRequest(
                        reason=request.deprecation_reason,
                        replaced_by_term_id=successor_id,
                    ),
                )
                results.append(BulkResultItem(
                    index=idx, status="deprecated", value=value,
                    id=by_value[value].term_id,
                    details={"successor": successor} if successor else None,
                ))
                counts.deprecated += 1
            except ValueError as e:
                results.append(BulkResultItem(
                    index=idx, status="error", value=value,
                    id=by_value[value].term_id, **error_fields(e),
                ))
                counts.errors += 1

        results.sort(key=lambda r: r.index)
        return TermReconcileService._response(
            terminology, request, counts, results, len(active_values)
        )

    @staticmethod
    def _response(
        terminology: Terminology,
        request: ReconcileRequest,
        counts: ReconcileCounts,
        results: list[BulkResultItem],
        total_existing_active: int,
    ) -> ReconcileResponse:
        return ReconcileResponse(
            dry_run=request.dry_run,
            terminology_id=terminology.terminology_id,
            terminology_value=terminology.value,
            counts=counts,
            results=results,
            total_submitted=len(request.terms),
            total_existing_active=total_existing_active,
        )
