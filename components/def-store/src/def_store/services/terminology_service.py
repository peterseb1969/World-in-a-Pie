"""Terminology service for business logic."""

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any, NamedTuple, cast

from beanie.odm.enums import SortDirection
from pymongo.errors import BulkWriteError, DuplicateKeyError

# Import identity helper from wip-auth
# This returns the authenticated identity, not the client-provided value
from ..api.auth import get_identity_string
from ..models.api_models import (
    BulkResultItem,
    CreateTerminologyRequest,
    CreateTermRequest,
    DeprecateTermRequest,
    TerminologyResponse,
    TermResponse,
    UpdateTerminologyRequest,
    UpdateTermRequest,
)
from ..models.audit_log import AuditAction, TermAuditLog
from ..models.term import CASE_INSENSITIVE_COLLATION, Term, TermName
from ..models.term_relation import TermRelation
from ..models.terminology import Terminology, TerminologyMetadata
from .errors import DefStoreError
from .nats_client import (
    EventType as NatsEventType,
)
from .nats_client import (
    publish_term_event,
    publish_term_events_bulk,
    publish_term_relation_event,
    publish_terminology_event,
)
from .registry_client import RegistryError, get_registry_client
from .system_vocabulary import refuse_reserved_value
from .term_names import (
    NAME_TYPES_TERMINOLOGY_VALUE,
    NameTypeService,
    derived_aliases,
    derived_translations,
    normalize_names,
    replace_translations,
    replace_untyped,
    validate_names,
)

logger = logging.getLogger(__name__)

_TERMINOLOGY_SORT_FIELDS = {"value", "label", "created_at", "updated_at"}
_TERM_SORT_FIELDS = {"value", "label", "sort_order", "created_at", "updated_at"}


def _build_sort(
    sort_by: str | None, sort_order: str | None,
    allowed: set[str], default_field: str, default_order: str = "asc",
    tiebreaker: str = "terminology_id",
) -> list[tuple[str, SortDirection]]:
    field = (sort_by or default_field).strip() or default_field
    order = (sort_order or default_order).strip().lower() or default_order
    if order not in ("asc", "desc"):
        raise ValueError(f"Invalid sort_order '{sort_order}'. Must be 'asc' or 'desc'.")
    if field not in allowed:
        raise ValueError(
            f"Unknown sort field '{field}'. Supported: {', '.join(sorted(allowed))}."
        )
    direction = SortDirection.ASCENDING if order == "asc" else SortDirection.DESCENDING
    if tiebreaker == field:
        # A duplicate sort key collapses to the LAST occurrence in the
        # driver's ordered spec, so appending an ascending tiebreaker on the
        # same field would silently invert a descending request.
        return [(field, direction)]
    return [(field, direction), (tiebreaker, SortDirection.ASCENDING)]


class EntityExistsError(ValueError):
    """Duplicate create: the value already exists in scope (CASE-465).

    Subclasses ValueError so every existing handler keeps working;
    error_fields reports it as error_code 'already_exists'. Carries the
    existing entity's ID and the config diff so routes can implement
    `on_conflict` semantics without string-matching.
    `changed` empty means the request is an identical re-create.
    """

    def __init__(self, message: str, existing_id: str, changed: list[str]) -> None:
        super().__init__(message)
        self.existing_id = existing_id
        self.changed = changed


class AmbiguousTerminologyValueError(ValueError):
    """An unscoped value lookup matched terminologies in several namespaces.

    A terminology value is unique only within its namespace. When no
    namespace is supplied and the value exists in more than one, there is
    no correct pick among the matches — returning whichever document the
    storage engine yields first silently hands the caller another
    namespace's data. The lookup fails loud instead, carrying the
    candidate namespaces so callers can tell the user how to disambiguate.
    """

    def __init__(self, value: str, namespaces: list[str]) -> None:
        self.value = value
        self.namespaces = namespaces
        super().__init__(
            f"Terminology value '{value}' exists in multiple namespaces "
            f"{namespaces} — pass namespace to disambiguate"
        )


class NameCollisionError(ValueError):
    """A unique_names terminology rejects a write whose names collide."""

    def __init__(self, collisions: list[dict[str, Any]]) -> None:
        self.collisions = collisions
        shown = ", ".join(
            f"'{c['name']}' collides with term '{c['conflicts_with_value']}'"
            for c in collisions[:5]
        )
        if len(collisions) > 5:
            shown += f" (and {len(collisions) - 5} more)"
        super().__init__(
            f"Name collision in a unique_names terminology: {shown}"
        )


class RegistryEntryNotRemovedError(Exception):
    """A hard-deleted term or terminology whose Registry entry could not be
    removed. The object itself IS gone; until the entry is cleaned up its
    synonyms (the `ns:terminology:value` key) still resolve to the deleted
    id, and re-creating the value would reuse it."""


class ValueResolution(NamedTuple):
    """Result of resolving a submitted value against a terminology.

    candidates lists EVERY (term, matched name) bearing the value when more
    than one term bears it — the name is None for a term whose value
    matched. A non-empty list means the value is ambiguous in this
    vocabulary, and matched_term is merely the first bearer in database
    order, not a principled choice.
    """

    valid: bool
    matched_term: Term | None
    matched_via: str | None
    suggestion: Term | None
    matched_name: TermName | None
    candidates: list[tuple[Term, TermName | None]]


def conflict_result(
    index: int,
    exc: EntityExistsError,
    on_conflict: str,
    value: str | None = None,
) -> BulkResultItem:
    """Map a duplicate-create onto a bulk result item per `on_conflict`.

    CASE-465 idempotent bootstrap: 'validate' makes an identical re-create
    a no-op ('unchanged' + existing id) and a config-divergent re-create a
    loud, machine-readable error; 'error' (the default) preserves the
    historical failure but stamps error_code so callers can branch on the
    code instead of the message string.
    """
    if on_conflict == "validate":
        if not exc.changed:
            return BulkResultItem(
                index=index, status="unchanged", id=exc.existing_id, value=value
            )
        return BulkResultItem(
            index=index,
            status="error",
            id=exc.existing_id,
            value=value,
            error=f"{exc} — existing config differs: {', '.join(exc.changed)}",
            error_code="incompatible_config",
            details={"changed": exc.changed},
        )
    return BulkResultItem(
        index=index,
        status="error",
        value=value,
        error=str(exc),
        error_code="already_exists",
    )


def _pointer_refusal(
    field: str, pointer: str, own_id: str | None, target: Term | None, terminology_id: str,
) -> DefStoreError | None:
    """Why a term may not point at `target` through `field`
    (parent_term_id, replaced_by_term_id), or None when it may: the target
    is an existing, active term of the same terminology, and not the term
    itself."""
    if own_id is not None and pointer == own_id:
        return DefStoreError(f"{field} cannot name the term itself", "reference_violation", rule="self_reference")
    if target is None:
        return DefStoreError(f"{field} '{pointer}' names no term", "reference_violation", rule="term_not_found")
    if target.terminology_id != terminology_id:
        return DefStoreError(
            f"{field} '{pointer}' names a term of another terminology; it must be a term of this one",
            "reference_violation", rule="other_terminology",
        )
    if target.status != "active":
        return DefStoreError(
            f"{field} '{pointer}' names the {target.status} term '{target.value}'; only an active term can be named",
            "reference_violation", rule="not_active",
        )
    return None


def _lifecycle_refusal(results: list[dict], expected: int) -> tuple[list[str], str] | None:
    """None when the Registry recorded every entry; else the entries it did
    record (to undo) and what it refused."""
    refused = [r for r in results if r.get("status") in ("error", "not_found")]
    if not refused and len(results) == expected:
        return None
    recorded = [r["registry_id"] for r in results if r not in refused]
    detail = "; ".join(f"{r.get('registry_id')}: {r.get('status')} {r.get('error') or ''}".strip() for r in refused[:5])
    return recorded, detail or "the Registry did not answer every entry"


class TerminologyService:
    """Service for managing terminologies and terms."""

    # =========================================================================
    # TERMINOLOGY OPERATIONS
    # =========================================================================

    @staticmethod
    def _terminology_config_diff(
        existing: Terminology, request: CreateTerminologyRequest
    ) -> list[str]:
        """Field names where the existing terminology differs from a
        creation request — empty list means an identical re-create
        (CASE-465 on_conflict=validate)."""
        # mutable implies extensible at create time — compare the
        # effective value, mirroring create_terminology.
        effective_extensible = request.extensible or request.mutable
        checks = {
            "label": existing.label != request.label,
            "description": (existing.description or None) != (request.description or None),
            "case_sensitive": existing.case_sensitive != request.case_sensitive,
            "allow_multiple": existing.allow_multiple != request.allow_multiple,
            "extensible": existing.extensible != effective_extensible,
            "mutable": existing.mutable != request.mutable,
            "unique_names": existing.unique_names != request.unique_names,
            "metadata": existing.metadata != (request.metadata or TerminologyMetadata()),
        }
        return [name for name, differs in checks.items() if differs]

    @staticmethod
    def _term_config_diff(existing: Term, request: CreateTermRequest) -> list[str]:
        """Field names where the existing term differs from a creation
        request — empty list means an identical re-create (CASE-465)."""
        # Label defaults to value at create time — compare the effective value.
        effective_label = request.label or request.value
        # Legacy aliases/translations fold into names before comparing, so a
        # re-create submitted in either shape compares against one stored shape.
        request_names = normalize_names(request.names, request.aliases, request.translations)
        checks = {
            "label": existing.label != effective_label,
            "names": [n.model_dump() for n in existing.names]
                     != [n.model_dump() for n in request_names],
            "description": (existing.description or None) != (request.description or None),
            "sort_order": existing.sort_order != request.sort_order,
            "parent_term_id": existing.parent_term_id != request.parent_term_id,
            "metadata": existing.metadata != request.metadata,
        }
        return [name for name, differs in checks.items() if differs]

    @staticmethod
    async def find_name_collisions(
        terminology: Terminology,
        term_value: str,
        names: list[TermName],
        exclude_term_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Submitted strings that collide with OTHER active terms.

        A collision is the submitted term's value or any of its names
        equalling another active term's value or name in the same
        terminology — the raw material of resolution ambiguity. Comparison
        follows the terminology's case sensitivity (collation strength 2
        when case-insensitive), matching how resolution itself compares.

        Returns one entry per (submitted string, conflicting term):
        {name, conflicts_with_term_id, conflicts_with_value, via} where
        via says whether the conflict is with the other term's 'value' or
        one of its 'names'.
        """

        submitted = [term_value] + [n.value for n in names]
        query: dict[str, Any] = {
            "namespace": terminology.namespace,
            "terminology_id": terminology.terminology_id,
            "status": "active",
            "$or": [
                {"value": {"$in": submitted}},
                {"names.value": {"$in": submitted}},
            ],
        }
        if exclude_term_id:
            query["term_id"] = {"$ne": exclude_term_id}

        coll = Term.get_motor_collection()
        cursor = coll.find(
            query,
            projection={"term_id": 1, "value": 1, "names.value": 1},
        )
        if not terminology.case_sensitive:
            cursor = cursor.collation(CASE_INSENSITIVE_COLLATION)

        def _fold(s: str) -> str:
            return s if terminology.case_sensitive else s.lower()

        submitted_folded = {_fold(s): s for s in submitted}
        collisions: list[dict[str, Any]] = []
        async for other in cursor:
            other_strings = (
                [("value", other.get("value", ""))]
                + [("names", n.get("value", "")) for n in other.get("names", [])]
            )
            for via, other_string in other_strings:
                hit = submitted_folded.get(_fold(other_string))
                if hit is not None:
                    collisions.append({
                        "name": hit,
                        "conflicts_with_term_id": other.get("term_id"),
                        "conflicts_with_value": other.get("value"),
                        "via": via,
                    })
        return collisions

    @staticmethod
    async def create_terminology(
        request: CreateTerminologyRequest,
        namespace: str,
        *,
        actor: str | None = None,
    ) -> TerminologyResponse:
        """
        Create a new terminology.

        1. Register with Registry to get ID
        2. Create terminology document in MongoDB

        Args:
            request: Creation request
            namespace: Namespace for the terminology (required — no default)

        Returns:
            Created terminology

        Raises:
            ValueError: If value already exists
            RegistryError: If Registry communication fails
        """
        refuse_reserved_value(namespace, request.value)

        # Check if value already exists within namespace
        existing = await Terminology.find_one({"namespace": namespace, "value": request.value})
        if existing:
            raise EntityExistsError(
                f"Terminology with value '{request.value}' already exists in namespace '{namespace}'",
                existing_id=existing.terminology_id,
                changed=TerminologyService._terminology_config_diff(existing, request),
            )

        # Authenticated identity by default. An explicit `actor` is honored only
        # for internal/system callers (e.g. the startup bootstrap) — the API
        # route never forwards a client-provided value, so it cannot be forged.
        actor = actor or get_identity_string()

        # Register with Registry to get ID (or use pre-assigned ID for restore)
        client = get_registry_client()
        terminology_id = await client.register_terminology(
            value=request.value,
            label=request.label,
            created_by=actor,
            namespace=namespace,
            entry_id=request.terminology_id,
        )

        # mutable implies extensible
        extensible = request.extensible or request.mutable

        # Create terminology document
        terminology = Terminology(
            namespace=namespace,
            terminology_id=terminology_id,
            value=request.value,
            label=request.label,
            description=request.description,
            case_sensitive=request.case_sensitive,
            allow_multiple=request.allow_multiple,
            extensible=extensible,
            mutable=request.mutable,
            unique_names=request.unique_names,
            metadata=request.metadata or TerminologyMetadata(),
            created_by=actor,
        )
        try:
            await terminology.insert()
        except DuplicateKeyError as e:
            raise DefStoreError(
                f"Terminology ID '{terminology_id}' already exists (collision across namespaces)", "already_exists"
            ) from e

        # Register auto-synonym for human-readable resolution
        # On failure, roll back the MongoDB document and re-raise
        try:
            await client.register_auto_synonym(
                target_id=terminology_id,
                namespace=namespace,
                entity_type="terminologies",
                composite_key={
                    "ns": namespace,
                    "type": "terminology",
                    "value": request.value,
                },
                created_by=actor,
            )
        except RegistryError:
            logger.error(
                "Auto-synonym registration failed for terminology %s — rolling back",
                terminology_id,
            )
            await terminology.delete()
            raise

        # Create audit log entry for terminology creation
        await TerminologyService._create_audit_log(
            term_id=terminology_id,
            terminology_id=terminology_id,
            action="created",
            changed_by=actor,
            new_values={
                "value": request.value,
                "label": request.label,
                "description": request.description,
            },
            namespace=namespace
        )

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_CREATED,
            TerminologyService._terminology_to_event_dict(terminology),
            changed_by=actor,
        )

        return TerminologyService._to_terminology_response(terminology)

    @staticmethod
    async def get_terminology(
        terminology_id: str | None = None,
        value: str | None = None,
        namespace: str | None = None
    ) -> TerminologyResponse | None:
        """
        Get a terminology by ID or value.

        Args:
            terminology_id: Terminology ID
            value: Terminology value (e.g., 'DOC_STATUS')
            namespace: Namespace to search in (if None, searches globally by ID)

        Returns:
            Terminology if found, None otherwise

        Raises:
            AmbiguousTerminologyValueError: unscoped value lookup with the
                value present in more than one namespace.
        """
        if terminology_id:
            # ID lookups can be global (for cross-namespace refs in open mode)
            query = {"terminology_id": terminology_id}
            if namespace:
                query["namespace"] = namespace
            terminology = await Terminology.find_one(query)
        elif value:
            terminology = await TerminologyService.find_by_value(value, namespace)
        else:
            return None

        if terminology:
            return TerminologyService._to_terminology_response(terminology)
        return None

    @staticmethod
    async def find_by_value(value: str, namespace: str | None) -> Terminology | None:
        """Find a terminology document by its value code.

        With a namespace: exact scoped match. Without one, the lookup is
        only safe while the value is globally unique — the same value live
        in several namespaces has no correct arbitrary pick, so that case
        raises AmbiguousTerminologyValueError instead of returning
        whichever document the storage engine yields first.
        """
        if namespace is not None:
            return await Terminology.find_one({"value": value, "namespace": namespace})
        matches = await Terminology.find({"value": value}).to_list()
        if len(matches) > 1:
            raise AmbiguousTerminologyValueError(
                value, sorted({m.namespace for m in matches})
            )
        return matches[0] if matches else None

    @staticmethod
    async def list_terminologies(
        status: str | None = None,
        value: str | None = None,
        page: int = 1,
        page_size: int = 50,
        ns_filter: dict | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[TerminologyResponse], int]:
        """
        List terminologies with pagination.

        Args:
            status: Filter by status (active, inactive)
            value: Filter by exact value match
            page: Page number (1-indexed)
            page_size: Items per page
            ns_filter: Namespace filter dict from resolve_namespace_filter()
            sort_by: Sort field (value, label, created_at, updated_at)
            sort_order: Sort direction (asc or desc)

        Returns:
            Tuple of (terminologies, total_count)
        """
        query: dict = {}
        if ns_filter:
            query.update(ns_filter)
        if status:
            query["status"] = status
        if value:
            query["value"] = value

        total = await Terminology.find(query).count()
        skip = (page - 1) * page_size

        sort_clauses = _build_sort(sort_by, sort_order, _TERMINOLOGY_SORT_FIELDS, "label")
        terminologies = await Terminology.find(query) \
            .sort(sort_clauses) \
            .skip(skip) \
            .limit(page_size) \
            .to_list()

        return (
            [TerminologyService._to_terminology_response(t) for t in terminologies],
            total
        )

    @staticmethod
    async def update_terminology(
        terminology_id: str,
        request: UpdateTerminologyRequest
    ) -> TerminologyResponse | None:
        """
        Update a terminology.

        If value changes, adds a synonym in the Registry.

        Args:
            terminology_id: Terminology to update
            request: Update request

        Returns:
            Updated terminology, or None if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return None

        # Track if value is changing
        old_value = terminology.value
        value_changed = request.value and request.value != old_value

        # If value changes, add auto-synonym in Registry for new value
        if value_changed:
            refuse_reserved_value(terminology.namespace, request.value)
            # Check new value doesn't exist within the same namespace
            existing = await Terminology.find_one({"namespace": terminology.namespace, "value": request.value})
            if existing:
                raise DefStoreError(f"Terminology with value '{request.value}' already exists in namespace '{terminology.namespace}'", "already_exists")

            # Register auto-synonym for the new value (old auto-synonym persists)
            client = get_registry_client()
            await client.register_auto_synonym(
                target_id=terminology_id,
                namespace=terminology.namespace,
                entity_type="terminologies",
                composite_key={
                    "ns": terminology.namespace,
                    "type": "terminology",
                    "value": request.value,
                },
                created_by=get_identity_string(),
            )

        # Track changes for audit log
        changed_fields = []
        previous_values = {}
        new_values = {}

        def _track(field_name, old_val, new_val):
            if new_val is not None and new_val != old_val:
                changed_fields.append(field_name)
                previous_values[field_name] = old_val
                new_values[field_name] = new_val

        _track("value", terminology.value, request.value)
        _track("label", terminology.label, request.label)
        _track("description", terminology.description, request.description)
        # _track skips None new values; a clear (explicit null) is a real
        # change and must reach the audit trail
        if (
            request.description is None
            and "description" in request.model_fields_set
            and terminology.description is not None
        ):
            changed_fields.append("description")
            previous_values["description"] = terminology.description
            new_values["description"] = None
        _track("case_sensitive", terminology.case_sensitive, request.case_sensitive)
        _track("allow_multiple", terminology.allow_multiple, request.allow_multiple)
        _track("extensible", terminology.extensible, request.extensible)
        _track("mutable", terminology.mutable, request.mutable)
        _track("unique_names", terminology.unique_names, request.unique_names)

        # Reject mutable changes while the terminology holds a term of ANY
        # status. term_count counts active terms only, and a deprecated or
        # soft-deleted term is still referenced by documents — a mutable
        # terminology would hard-delete it.
        if (
            request.mutable is not None
            and request.mutable != terminology.mutable
            and await Term.find_one({"terminology_id": terminology_id}) is not None
        ):
            raise DefStoreError(
                "Cannot change mutable flag on terminology with existing terms", "invalid_state", rule="has_terms"
            )

        await TerminologyService._refuse_case_variant_values(terminology, request.case_sensitive)

        # Apply updates
        if request.value is not None:
            terminology.value = request.value
        if request.label is not None:
            terminology.label = request.label
        if request.description is not None:
            terminology.description = request.description
        elif "description" in request.model_fields_set:
            # An explicitly submitted null clears the description — RFC
            # 7396 semantics, same as term fields; omitted keeps it
            terminology.description = None
        if request.case_sensitive is not None:
            terminology.case_sensitive = request.case_sensitive
        if request.allow_multiple is not None:
            terminology.allow_multiple = request.allow_multiple
        if request.extensible is not None:
            terminology.extensible = request.extensible
        if request.mutable is not None:
            terminology.mutable = request.mutable
            # mutable implies extensible
            if request.mutable:
                terminology.extensible = True
        if request.unique_names is not None:
            terminology.unique_names = request.unique_names
        if request.metadata is not None:
            terminology.metadata = request.metadata

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        terminology.updated_at = datetime.now(UTC)
        terminology.updated_by = actor
        await terminology.save()

        # Create audit log entry if there were changes
        if changed_fields:
            await TerminologyService._create_audit_log(
                term_id=terminology_id,
                terminology_id=terminology_id,
                action="updated",
                changed_by=actor,
                changed_fields=changed_fields,
                previous_values=previous_values,
                new_values=new_values,
                namespace=terminology.namespace
            )

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_UPDATED,
            TerminologyService._terminology_to_event_dict(terminology),
            changed_by=actor,
        )

        return TerminologyService._to_terminology_response(terminology)

    @staticmethod
    async def delete_terminology(
        terminology_id: str,
        updated_by: str | None = None,  # Deprecated: uses authenticated identity
        hard_delete: bool = False,
    ) -> bool:
        """
        Delete a terminology. Hard-deletes if mutable (whatever hard_delete
        says, and whatever the namespace's deletion_mode) OR if
        hard_delete=True and namespace deletion_mode is 'full'. Soft-deletes
        otherwise.

        Also deletes all terms and relations (hard), or deactivates the
        active terms (soft). This method applies no dependency guard: the
        route refuses a hard delete — mutable included — while templates of
        any status reference the terminology.

        Args:
            terminology_id: Terminology to delete
            updated_by: Deprecated - uses authenticated identity
            hard_delete: Hard-delete a non-mutable terminology (requires namespace deletion_mode='full')

        Returns:
            True if deleted, False if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return False

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Determine if we should hard-delete
        should_hard_delete = terminology.mutable
        if hard_delete and not should_hard_delete:
            # Check namespace deletion_mode
            client = get_registry_client()
            deletion_mode = await client.get_namespace_deletion_mode(terminology.namespace)
            if deletion_mode != "full":
                raise DefStoreError(
                    f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')", "invalid_state", rule="deletion_mode_retain"
                )
            should_hard_delete = True

        prior_status = terminology.status
        if should_hard_delete:
            event_dict, not_removed, removed = await TerminologyService._hard_delete_terminology(terminology, actor)
            outcome: dict[str, Any] = {"hard_delete": True, "terms_removed": removed}
        else:
            event_dict, deactivated = await TerminologyService._soft_delete_terminology(terminology, actor)
            not_removed = []
            outcome = {"status": "inactive", "terms_deactivated": deactivated}
        TerminologyService._drop_derived_caches(terminology.value)
        await TerminologyService._create_audit_log(
            term_id=terminology.terminology_id, terminology_id=terminology.terminology_id,
            action="deleted", namespace=terminology.namespace, changed_by=actor,
            changed_fields=["status"], previous_values={"status": prior_status},
            new_values=outcome,
        )

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_DELETED,
            event_dict,
            changed_by=actor,
        )

        if not_removed:
            raise RegistryEntryNotRemovedError(
                f"Terminology {terminology_id} was deleted, but {len(not_removed)} "
                f"Registry entr{'y' if len(not_removed) == 1 else 'ies'} could not be "
                f"removed ({', '.join(not_removed[:5])}{'…' if len(not_removed) > 5 else ''}); "
                "their values still resolve to the deleted ids until removed."
            )
        return True

    @staticmethod
    async def _hard_delete_terminology(
        terminology: Terminology, actor: str | None,
    ) -> tuple[dict, list[str], int]:
        """Remove the terminology, all its terms and their relations, and
        their Registry entries. Returns the event payload, the entries the
        Registry did not remove, and how many terms were removed."""
        terminology_id = terminology.terminology_id
        term_ids = [
            t.term_id
            for t in await Term.find({"terminology_id": terminology_id}).to_list()
        ]
        if term_ids:
            await TermRelation.find({
                "$or": [
                    {"source_term_id": {"$in": term_ids}},
                    {"target_term_id": {"$in": term_ids}}
                ]
            }).delete()
        await Term.find({"terminology_id": terminology_id}).delete()

        event_dict = TerminologyService._terminology_to_event_dict(terminology)
        event_dict["hard_delete"] = True
        await terminology.delete()

        # A mutable terminology is hard-deletable regardless of the
        # namespace's deletion_mode, so its entries carry that exemption.
        client = get_registry_client()
        not_removed: list[str] = []
        for entry_id in [terminology_id, *term_ids]:
            try:
                await client.hard_delete_entry(
                    entry_id, updated_by=actor, owner_hard_deleted=terminology.mutable,
                )
            except RegistryError as e:
                logger.error(f"Registry entry {entry_id} not removed after hard delete: {e}")
                not_removed.append(entry_id)
        return event_dict, not_removed, len(term_ids)

    @staticmethod
    async def _soft_delete_terminology(terminology: Terminology, actor: str | None) -> tuple[dict, int]:
        """Deactivate the terminology and its ACTIVE terms.

        A deprecated term stays deprecated — its reason and replacement are
        its own lifecycle, and its synonyms were released when it was
        deprecated — and an already-inactive term stays inactive. Only the
        terms this retirement deactivates are reported to the Registry.
        Returns the event payload and how many terms were deactivated.
        """
        terminology_id = terminology.terminology_id
        now = datetime.now(UTC)
        retired_ids = [
            t["term_id"] async for t in Term.get_motor_collection().find(
                {"terminology_id": terminology_id, "status": "active"}, {"term_id": 1},
            )
        ]

        async def write() -> None:
            await Term.find({"terminology_id": terminology_id, "status": "active"}).update_many({
                "$set": {"status": "inactive", "updated_at": now, "updated_by": actor}
            })
            terminology.status = "inactive"
            terminology.term_count = 0  # active terms; none are left
            terminology.updated_at = now
            terminology.updated_by = actor
            await terminology.save()

        await TerminologyService._lifecycle_write(
            [terminology_id, *retired_ids], retired=True, actor=actor, write=write,
        )
        return TerminologyService._terminology_to_event_dict(terminology), len(retired_ids)

    @staticmethod
    def _require_active(terminology: Terminology) -> None:
        """A retired terminology takes no new terms: restore it first."""
        if terminology.status != "active":
            raise DefStoreError(
                f"Terminology '{terminology.value}' is {terminology.status}; restore it "
                "before adding terms", "invalid_state", rule="not_active",
            )

    @staticmethod
    def _drop_derived_caches(terminology_value: str | None) -> None:
        """Drop the process-lifetime caches derived from a system vocabulary
        when a write changes it: the name types (_NAME_TYPES) and the
        ontology relation types (_ONTOLOGY_RELATIONSHIP_TYPES)."""
        if terminology_value == NAME_TYPES_TERMINOLOGY_VALUE:
            NameTypeService.invalidate_name_type_cache()
        elif terminology_value == "_ONTOLOGY_RELATIONSHIP_TYPES":
            from .ontology_service import OntologyService
            OntologyService.invalidate_relation_type_cache()

    @staticmethod
    async def restore_terminology(
        terminology_id: str,
        restore_terms: bool = True
    ) -> TerminologyResponse | None:
        """
        Restore a soft-deleted terminology (set status back to active).

        Optionally reactivates its inactive terms: those deactivated with it,
        and terms soft-deleted on their own before it. A deprecated term is
        not inactive and stays deprecated.

        Args:
            terminology_id: Terminology to restore
            restore_terms: If True, also reactivate inactive terms

        Returns:
            Restored terminology, or None if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return None

        if terminology.status == "active":
            return TerminologyService._to_terminology_response(terminology)

        actor = get_identity_string()
        now = datetime.now(UTC)

        restored_term_ids: list[str] = [
            t["term_id"] async for t in Term.get_motor_collection().find(
                {"terminology_id": terminology_id, "status": "inactive"}, {"term_id": 1},
            )
        ] if restore_terms else []

        async def write() -> None:
            # Reactivate the terminology, then its inactive terms
            terminology.status = "active"
            terminology.updated_at = now
            terminology.updated_by = actor
            if restored_term_ids:
                await Term.find({"terminology_id": terminology_id, "status": "inactive"}).update_many({
                    "$set": {"status": "active", "updated_at": now, "updated_by": actor}
                })
                terminology.term_count = await Term.find({
                    "terminology_id": terminology_id, "status": "active",
                }).count()
            await terminology.save()

        await TerminologyService._lifecycle_write(
            [terminology_id, *restored_term_ids], retired=False, actor=actor, write=write,
        )
        TerminologyService._drop_derived_caches(terminology.value)
        await TerminologyService._create_audit_log(
            term_id=terminology_id, terminology_id=terminology_id, action="restored",
            namespace=terminology.namespace, changed_by=actor, changed_fields=["status"],
            previous_values={"status": "inactive"},
            new_values={"status": "active", "terms_reactivated": len(restored_term_ids)},
        )

        # Publish NATS event. restore_terms tells reporting whether the
        # terminology's inactive terms came back with it.
        event_dict = TerminologyService._terminology_to_event_dict(terminology)
        event_dict["restore_terms"] = restore_terms
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_RESTORED, event_dict, changed_by=actor,
        )

        return TerminologyService._to_terminology_response(terminology)

    # =========================================================================
    # TERM OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_term(
        terminology_id: str,
        request: CreateTermRequest,
        collision_out: list[dict[str, Any]] | None = None,
    ) -> TermResponse:
        """
        Create a new term in a terminology.

        Namespace is inherited from the parent terminology.

        Args:
            terminology_id: Parent terminology ID
            request: Creation request
            collision_out: When provided, name collisions with other terms
                are appended here for the caller to surface as a warning
                (unique_names terminologies raise instead)

        Returns:
            Created term

        Raises:
            ValueError: If terminology not found or value exists
            NameCollisionError: If names collide and the terminology
                declares unique_names
        """
        # Verify terminology exists
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            raise DefStoreError(f"Terminology '{terminology_id}' not found", "not_found")
        TerminologyService._require_active(terminology)

        namespace = terminology.namespace

        # Check value uniqueness within the terminology, compared as the
        # terminology compares values (a case-insensitive terminology holds
        # one of `Foo` and `foo`, never both)
        existing = (await TerminologyService._existing_by_value(terminology, [request.value])).get(request.value)
        if existing:
            raise EntityExistsError(
                f"Term with value '{request.value}' already exists in terminology",
                existing_id=existing.term_id,
                changed=TerminologyService._term_config_diff(existing, request),
            )

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Default label to value if not provided
        label = request.label or request.value

        # Fold legacy aliases/translations into names and enforce the
        # naming rules (unknown type, duplicate, preferred conflicts) —
        # loud per-term rejection, before anything is registered.
        names = normalize_names(request.names, request.aliases, request.translations)
        validate_names(names, request.value, await NameTypeService.get_valid_name_types())

        # Cross-term collisions: reject under unique_names, otherwise hand
        # them to the caller to surface as a warning — before registering.
        collisions = await TerminologyService.find_name_collisions(
            terminology, request.value, names
        )
        if collisions:
            if terminology.unique_names:
                raise NameCollisionError(collisions)
            if collision_out is not None:
                collision_out.extend(collisions)

        await TerminologyService._require_valid_pointer(
            terminology_id, "parent_term_id", request.parent_term_id, request.term_id,
        )

        # Register with Registry to get ID (or use pre-assigned ID for restore)
        client = get_registry_client()
        term_id = await client.register_term(
            terminology_id=terminology_id,
            value=request.value,
            created_by=actor,
            namespace=namespace,
            entry_id=request.term_id,
        )

        # Create term document
        term = Term(
            namespace=namespace,
            term_id=term_id,
            terminology_id=terminology_id,
            terminology_value=terminology.value,
            value=request.value,
            names=names,
            label=label,
            description=request.description,
            sort_order=request.sort_order,
            parent_term_id=request.parent_term_id,
            metadata=request.metadata,
            created_by=actor,
        )
        try:
            await term.insert()
        except DuplicateKeyError as e:
            raise DefStoreError(
                f"Term ID '{term_id}' already exists (collision across namespaces)", "already_exists"
            ) from e

        # Register auto-synonym for human-readable resolution
        # Uses "TERMINOLOGY_VALUE:TERM_VALUE" colon notation for resolution
        # On failure, roll back the MongoDB document and re-raise
        try:
            await client.register_auto_synonym(
                target_id=term_id,
                namespace=namespace,
                entity_type="terms",
                composite_key={
                    "ns": namespace,
                    "type": "term",
                    "terminology": terminology.value,
                    "value": request.value,
                },
                created_by=actor,
            )
        except RegistryError:
            logger.error(
                "Auto-synonym registration failed for term %s — rolling back",
                term_id,
            )
            await term.delete()
            raise

        # Create audit log entry
        await TerminologyService._create_audit_log(
            term_id=term_id,
            terminology_id=terminology_id,
            action="created",
            changed_by=actor,
            new_values={
                "value": request.value,
                "names": [n.model_dump() for n in names],
                "label": label,
            },
            namespace=namespace
        )

        await TerminologyService._add_to_term_count(terminology_id, 1, datetime.now(UTC))

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_CREATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=actor,
        )

        TerminologyService._drop_derived_caches(terminology.value)

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def _batch_name_collisions(
        terminology: Terminology,
        batch_terms: list[CreateTermRequest],
    ) -> dict[int, list[dict[str, Any]]]:
        """Per-item name collisions for a bulk create, in ONE query.

        Covers collisions with existing active terms AND between items of
        the batch itself (an import carrying the same alias on two rows —
        the measured real-world shape). Returns batch-local index →
        collision list; items without collisions are absent. Batch
        collisions carry via='batch' and no term_id (the other bearer has
        none yet).
        """

        def _fold(s: str) -> str:
            return s if terminology.case_sensitive else s.lower()

        # Per-item submitted strings (an item whose names are invalid gets
        # an empty list — the partition pass rejects it on its own)
        item_strings: list[list[str]] = []
        all_strings: set[str] = set()
        for req in batch_terms:
            try:
                names = normalize_names(req.names, req.aliases, req.translations)
            except ValueError:
                item_strings.append([])
                continue
            strings = [req.value] + [n.value for n in names]
            item_strings.append(strings)
            all_strings.update(strings)

        if not all_strings:
            return {}

        # One query for every existing bearer of any submitted string
        query: dict[str, Any] = {
            "namespace": terminology.namespace,
            "terminology_id": terminology.terminology_id,
            "status": "active",
            "$or": [
                {"value": {"$in": list(all_strings)}},
                {"names.value": {"$in": list(all_strings)}},
            ],
        }
        coll = Term.get_motor_collection()
        cursor = coll.find(
            query, projection={"term_id": 1, "value": 1, "names.value": 1}
        )
        if not terminology.case_sensitive:
            cursor = cursor.collation(CASE_INSENSITIVE_COLLATION)

        owners: dict[str, list[tuple[str, str, str]]] = {}
        async for other in cursor:
            for via, other_string in (
                [("value", other.get("value", ""))]
                + [("names", n.get("value", "")) for n in other.get("names", [])]
            ):
                if other_string:
                    owners.setdefault(_fold(other_string), []).append(
                        (other.get("term_id"), other.get("value"), via)
                    )

        collisions_by_index: dict[int, list[dict[str, Any]]] = {}
        seen_in_batch: dict[str, tuple[int, str]] = {}
        for i, strings in enumerate(item_strings):
            cols: list[dict[str, Any]] = []
            for s in strings:
                folded = _fold(s)
                for term_id, value, via in owners.get(folded, []):
                    cols.append({
                        "name": s,
                        "conflicts_with_term_id": term_id,
                        "conflicts_with_value": value,
                        "via": via,
                    })
                prev = seen_in_batch.get(folded)
                if prev is not None and prev[0] != i:
                    cols.append({
                        "name": s,
                        "conflicts_with_term_id": None,
                        "conflicts_with_value": prev[1],
                        "via": "batch",
                    })
            for s in strings:
                seen_in_batch.setdefault(_fold(s), (i, batch_terms[i].value))
            if cols:
                collisions_by_index[i] = cols
        return collisions_by_index

    @staticmethod
    def _partition_term_batch(
        batch_terms: list[CreateTermRequest],
        batch_start: int,
        batch_registry_results: list[dict],
        existing_by_id: dict[str, Term],
        existing_by_value: dict[str, Term],
        results: list[BulkResultItem | None],
        namespace: str,
        terminology_id: str,
        terminology_value: str,
        actor: str,
        skip_duplicates: bool,
        update_existing: bool,
        valid_name_types: dict[str, str],
        collisions_by_index: dict[int, list[dict[str, Any]]],
        unique_names: bool,
        refused: dict[int, DefStoreError] | None = None,
    ) -> tuple[list[Term], list[int]]:
        """Partition a batch of term requests into inserts + result rows.

        Walks `batch_terms` paired with their Registry results and decides
        per item:
        - Registry returned status='error' → write 'error' result row.
        - Existing term matched by term_id (from existing_by_id) or by value
          (from existing_by_value) → write 'skipped' / 'updated' / 'error'
          row depending on skip_duplicates / update_existing flags. The
          current behaviour pinned by the CASE-336 test bed: the
          /terms endpoint hardcodes skip_duplicates=True; the alternative
          flag combinations are reachable only via the import-export path.
        - Refused before partition (`refused`: an invalid parent, a value
          that repeats an earlier item of the request) → that refusal.
        - Otherwise → build a Term document and queue it for insert_many.

        Mutates `results` in place (the coordinator owns the list, the
        helper just fills in slots). Returns (terms_to_insert,
        insert_indices) where insert_indices maps each insert position
        back to its global result index for Phase E bookkeeping.
        """
        terms_to_insert: list[Term] = []
        insert_indices: list[int] = []

        for i, (term_req, reg_result) in enumerate(
            zip(batch_terms, batch_registry_results, strict=False)
        ):
            global_idx = batch_start + i

            refusal = (refused or {}).get(i)
            if refusal is not None:
                results[global_idx] = BulkResultItem(
                    index=global_idx, status="error", value=term_req.value,
                    error=str(refusal), error_code=refusal.code, details=refusal.details,
                )
                continue

            if reg_result.get("status") == "error":
                results[global_idx] = BulkResultItem(
                    index=global_idx,
                    status="error",
                    value=term_req.value,
                    error=reg_result.get("error"),
                    error_code="registry_error",
                )
                continue

            term_id = reg_result["registry_id"]

            # Check duplicates by term_id or by value within terminology
            existing = existing_by_id.get(term_id) or existing_by_value.get(term_req.value)
            if existing:
                if skip_duplicates or update_existing:
                    # Existing terms are left untouched — import is extend-only by
                    # WIP contract (CASE-797). Neither skip_duplicates nor
                    # update_existing mutates an existing term (real in-place
                    # update is a future --force feature), so report it honestly
                    # as skipped rather than a no-op "updated".
                    results[global_idx] = BulkResultItem(
                        index=global_idx,
                        status="skipped",
                        id=existing.term_id,
                        value=term_req.value,
                        error="Already exists",
                    )
                else:
                    results[global_idx] = BulkResultItem(
                        index=global_idx,
                        status="error",
                        value=term_req.value,
                        error=f"Term with value '{term_req.value}' already exists",
                        error_code="already_exists",
                    )
                continue

            # Default label to value if not provided
            label = term_req.label or term_req.value

            # Fold legacy shapes into names and enforce the naming rules —
            # a violation is this item's error row, not the batch's
            try:
                names = normalize_names(
                    term_req.names, term_req.aliases, term_req.translations
                )
                validate_names(names, term_req.value, valid_name_types)
            except ValueError as e:
                results[global_idx] = BulkResultItem(
                    index=global_idx,
                    status="error",
                    value=term_req.value,
                    error=str(e),
                    error_code="invalid_names",
                )
                continue

            # Cross-term name collisions reject the item in a unique_names
            # terminology; otherwise they decorate the created row as a
            # warning after the write phase
            item_collisions = collisions_by_index.get(i)
            if item_collisions and unique_names:
                results[global_idx] = BulkResultItem(
                    index=global_idx,
                    status="error",
                    value=term_req.value,
                    error=str(NameCollisionError(item_collisions)),
                    error_code="name_collision",
                    details={"name_collisions": item_collisions},
                )
                continue

            # Build Term document for batch insert
            term = Term(
                namespace=namespace,
                term_id=term_id,
                terminology_id=terminology_id,
                terminology_value=terminology_value,
                value=term_req.value,
                names=names,
                label=label,
                description=term_req.description,
                sort_order=term_req.sort_order,
                parent_term_id=term_req.parent_term_id,
                metadata=term_req.metadata,
                created_by=actor,
            )
            terms_to_insert.append(term)
            insert_indices.append(global_idx)

        return terms_to_insert, insert_indices

    @staticmethod
    async def _write_term_batch(
        terms_to_insert: list[Term],
        insert_indices: list[int],
        results: list[BulkResultItem | None],
        namespace: str,
        terminology_id: str,
        terminology_value: str,
        actor: str,
        now: datetime,
        client: Any,
    ) -> int:
        """Insert this batch's queued Terms and fire all per-create side effects.

        Phase E inserts (a failed insert is that item's error). Phase F
        claims each inserted term's value synonym in the Registry — the key
        `ns:terminology:value` resolves by — and takes back every term the
        Registry refused, so a term is only reported created once it is
        addressable by its value, the same as the single-term door. Only
        then do Phase G (audit) and Phase H (NATS events) run, for exactly
        the terms that stayed. All phases filter on
        `results[idx].status == "created"`.

        Mutates `results` in place. Returns the number of terms created in
        this batch. Raises RegistryError when the Registry did not answer
        the synonym claim; the batch's inserts are taken back first, so
        nothing of the batch remains.
        """
        batch_created = 0
        if terms_to_insert:
            try:
                await Term.insert_many(terms_to_insert, ordered=False)
                # All succeeded
                for pos, idx in enumerate(insert_indices):
                    term = terms_to_insert[pos]
                    results[idx] = BulkResultItem(
                        index=idx,
                        status="created",
                        id=term.term_id,
                        value=term.value,
                    )
                    batch_created += 1
            except BulkWriteError as bwe:
                # Some inserts may have failed (e.g. race condition duplicates)
                failed_indices = {
                    err["index"] for err in bwe.details.get("writeErrors", [])
                }
                error_messages = {
                    err["index"]: err.get("errmsg", "Insert failed")
                    for err in bwe.details.get("writeErrors", [])
                }
                # MongoDB's own code: 11000 is a duplicate key (the term
                # already exists); anything else is a server-side fault.
                error_codes = {
                    err["index"]: "already_exists" if err.get("code") == 11000 else "internal_error"
                    for err in bwe.details.get("writeErrors", [])
                }
                for pos, idx in enumerate(insert_indices):
                    term = terms_to_insert[pos]
                    if pos in failed_indices:
                        results[idx] = BulkResultItem(
                            index=idx,
                            status="error",
                            value=term.value,
                            error=error_messages.get(pos, "Insert failed"),
                            error_code=error_codes.get(pos, "internal_error"),
                        )
                    else:
                        results[idx] = BulkResultItem(
                            index=idx,
                            status="created",
                            id=term.term_id,
                            value=term.value,
                        )
                        batch_created += 1

        batch_created -= await TerminologyService._claim_value_synonyms(
            terms_to_insert, insert_indices, results, namespace, terminology_value, actor, client,
        )

        # Phase G: Batch insert audit logs for this batch
        audit_entries = [
            TermAuditLog(
                namespace=namespace,
                term_id=terms_to_insert[pos].term_id,
                terminology_id=terminology_id,
                action="created",
                changed_by=actor,
                changed_at=now,
                new_values={
                    "value": terms_to_insert[pos].value,
                    "names": [n.model_dump() for n in terms_to_insert[pos].names],
                    "label": terms_to_insert[pos].label,
                },
            )
            for pos, idx in enumerate(insert_indices)
            if (r := results[idx]) is not None and r.status == "created"
        ]
        if audit_entries:
            await TermAuditLog.insert_many(audit_entries)

        # Phase H: Publish NATS events for created terms
        created_term_dicts = [
            TerminologyService._term_to_event_dict(terms_to_insert[pos])
            for pos, idx in enumerate(insert_indices)
            if (r := results[idx]) is not None and r.status == "created"
        ]
        if created_term_dicts:
            await publish_term_events_bulk(
                NatsEventType.TERM_CREATED,
                created_term_dicts,
                changed_by=actor,
            )

        return batch_created

    @staticmethod
    async def _claim_value_synonyms(
        terms_to_insert: list[Term],
        insert_indices: list[int],
        results: list[BulkResultItem | None],
        namespace: str,
        terminology_value: str,
        actor: str,
        client: Any,
    ) -> int:
        """Claim the value synonym of every term this batch inserted, and
        take back each term whose claim the Registry refused — its row
        becomes the refusal. Returns how many terms were taken back.

        When the Registry gives no per-item answer, every inserted term of
        the batch is taken back and RegistryError is raised: a term whose
        value does not resolve to it must not stay behind as created.
        """
        claimed = [
            (pos, idx) for pos, idx in enumerate(insert_indices)
            if (r := results[idx]) is not None and r.status == "created"
        ]
        if not claimed:
            return 0
        items = [
            TerminologyService._value_synonym_item(terms_to_insert[pos], namespace, terminology_value, actor)
            for pos, _ in claimed
        ]
        try:
            refusals = await client.register_auto_synonyms_per_item(items)
        except RegistryError:
            await Term.find(
                {"term_id": {"$in": [terms_to_insert[pos].term_id for pos, _ in claimed]}}
            ).delete()
            raise
        taken_back = [
            (terms_to_insert[pos], idx, refusal)
            for (pos, idx), refusal in zip(claimed, refusals, strict=True)
            if refusal is not None
        ]
        await TerminologyService._take_back(taken_back, results)
        return len(taken_back)

    @staticmethod
    async def _process_term_batch(
        batch_terms: list[CreateTermRequest],
        batch_start: int,
        results: list[BulkResultItem | None],
        terminology: Terminology,
        namespace: str,
        actor: str,
        now: datetime,
        client: Any,
        registry_batch_size: int,
        skip_duplicates: bool,
        update_existing: bool,
        batch_num: int,
    ) -> int:
        """Run one batch end-to-end: register IDs, query existing, partition, write.

        Phase B (Registry bulk-register, with per-term entry_id pass-through
        when the request carries one), Phase C (existing-by-id and
        existing-by-value queries), then dispatch to _partition_term_batch
        and _write_term_batch. The coordinator owns the chunking loop and
        the cross-batch counter; this helper owns one batch's worth of work.

        Returns the count of terms successfully created in this batch.
        """
        terminology_id = terminology.terminology_id

        # Phase A2: items refused before anything is registered
        refused = await TerminologyService._batch_refusals(terminology, batch_terms)

        # Phase B: Registry call for this batch (with sub-batching)
        logger.debug(f"Registering {len(batch_terms)} terms with registry...")
        batch_registry_results = await TerminologyService._register_batch(
            client, terminology_id, batch_terms, refused, actor, registry_batch_size, namespace,
        )
        logger.debug(f"Registry registration complete for batch {batch_num}")

        # Phase C: Duplicate check for this batch
        batch_ids = [
            r["registry_id"] for r in batch_registry_results
            if r.get("status") != "error" and r.get("registry_id")
        ]
        batch_values = [t.value for t in batch_terms]

        existing_by_id: dict[str, Term] = {}
        if batch_ids:
            existing_terms = await Term.find(
                {"term_id": {"$in": batch_ids}}
            ).to_list()
            existing_by_id = {t.term_id: t for t in existing_terms}
        existing_by_value = await TerminologyService._existing_by_value(terminology, batch_values)

        # Phase C2: Cross-term and intra-batch name collisions (one query)
        collisions_by_index = await TerminologyService._batch_name_collisions(
            terminology, batch_terms
        )

        # Phase D: Partition this batch into create/skip/error
        terms_to_insert, insert_indices = TerminologyService._partition_term_batch(
            batch_terms=batch_terms,
            batch_start=batch_start,
            batch_registry_results=batch_registry_results,
            existing_by_id=existing_by_id,
            existing_by_value=existing_by_value,
            results=results,
            namespace=namespace,
            terminology_id=terminology_id,
            terminology_value=terminology.value,
            actor=actor,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
            valid_name_types=await NameTypeService.get_valid_name_types(),
            collisions_by_index=collisions_by_index,
            unique_names=terminology.unique_names,
            refused=refused,
        )

        # Phases E-H: insert, claim value synonyms, audit, events
        created = await TerminologyService._write_term_batch(
            terms_to_insert=terms_to_insert,
            insert_indices=insert_indices,
            results=results,
            namespace=namespace,
            terminology_id=terminology_id,
            terminology_value=terminology.value,
            actor=actor,
            now=now,
            client=client,
        )

        # Collisions on successfully created rows surface as per-item
        # warnings (details), never silently
        for local_idx, cols in collisions_by_index.items():
            row = results[batch_start + local_idx]
            if row is not None and row.status == "created":
                row.details = {**(row.details or {}), "name_collisions": cols}

        return created

    @staticmethod
    def _value_synonym_item(term: Term, namespace: str, terminology_value: str, actor: str) -> dict[str, Any]:
        """The Registry item claiming `term`'s value synonym."""
        return {
            "target_id": term.term_id,
            "namespace": namespace,
            "entity_type": "terms",
            "composite_key": {
                "ns": namespace,
                "type": "term",
                "terminology": terminology_value,
                "value": term.value,
            },
            "created_by": actor,
        }

    @staticmethod
    async def _take_back(
        refused: list[tuple[Term, int, str]],
        results: list[BulkResultItem | None],
    ) -> None:
        """Delete each refused term and make its row the Registry's refusal."""
        if not refused:
            return
        await Term.find({"term_id": {"$in": [t.term_id for t, _, _ in refused]}}).delete()
        for term, idx, refusal in refused:
            results[idx] = BulkResultItem(
                index=idx,
                status="error",
                value=term.value,
                error=f"The Registry refused the term's value synonym ({refusal}); the term was not created",
                error_code="registry_error",
            )

    @staticmethod
    def _fail_unanswered(
        results: list[BulkResultItem | None],
        terms: list[CreateTermRequest],
        from_index: int,
        error: str,
    ) -> None:
        """Every result slot from `from_index` on that holds no answer yet
        becomes a registry_error row naming `error`."""
        for idx in range(from_index, len(terms)):
            row = results[idx]
            if row is None or row.status == "created":
                results[idx] = BulkResultItem(
                    index=idx, status="error", value=terms[idx].value,
                    error=error, error_code="registry_error",
                )

    @staticmethod
    async def _refuse_case_variant_values(terminology: Terminology, case_sensitive: bool | None) -> None:
        """Refuse making a case-sensitive terminology case-insensitive while
        two of its terms (any status) carry values that differ only by case:
        afterwards both would be one value, and resolution would pick one."""
        if case_sensitive is not False or not terminology.case_sensitive:
            return
        values = await Term.get_motor_collection().distinct(
            "value", {"terminology_id": terminology.terminology_id}
        )
        seen: dict[str, str] = {}
        clashes: list[str] = []
        for value in values:
            other = seen.setdefault(value.lower(), value)
            if other != value:
                clashes.append(f"'{other}' / '{value}'")
        if clashes:
            raise DefStoreError(
                "Cannot make the terminology case-insensitive: its terms carry values that "
                f"differ only by case ({', '.join(clashes[:5])}"
                f"{f', … {len(clashes) - 5} more' if len(clashes) > 5 else ''})",
                "invalid_state", rule="case_variant_values",
            )

    @staticmethod
    async def _register_batch(
        client: Any,
        terminology_id: str,
        batch_terms: list[CreateTermRequest],
        refused: dict[int, DefStoreError],
        actor: str,
        registry_batch_size: int,
        namespace: str,
    ) -> list[dict]:
        """Register the batch's items that were not refused; a refused item's
        slot is an empty result, so no Registry entry is minted for it."""
        to_register = [i for i in range(len(batch_terms)) if i not in refused]
        registered = await client.register_terms_bulk(
            terminology_id=terminology_id,
            terms=[
                {"value": batch_terms[i].value, "entry_id": batch_terms[i].term_id}
                if batch_terms[i].term_id
                else {"value": batch_terms[i].value}
                for i in to_register
            ],
            created_by=actor,
            registry_batch_size=registry_batch_size,
            namespace=namespace,
        )
        results: list[dict] = [{} for _ in batch_terms]
        for i, result in zip(to_register, registered, strict=True):
            results[i] = result
        return results

    @staticmethod
    async def _existing_by_value(terminology: Terminology, values: list[str]) -> dict[str, Term]:
        """Each submitted value mapped to the stored term (any status) that
        already carries it, compared as the terminology compares values: a
        case-insensitive terminology treats `Foo` and `foo` as one value."""
        if not values:
            return {}
        find_kwargs: dict[str, Any] = (
            {} if terminology.case_sensitive else {"collation": CASE_INSENSITIVE_COLLATION}
        )
        stored = await Term.find({
            "namespace": terminology.namespace,
            "terminology_id": terminology.terminology_id,
            "value": {"$in": values},
        }, **find_kwargs).to_list()
        fold = TerminologyService._value_fold(terminology)
        by_fold = {fold(t.value): t for t in stored}
        return {v: by_fold[fold(v)] for v in values if fold(v) in by_fold}

    @staticmethod
    def _value_fold(terminology: Terminology) -> Any:
        """How the terminology compares values: exactly, or case-folded the
        way resolve_values folds them."""
        return (lambda v: v) if terminology.case_sensitive else (lambda v: v.lower())

    @staticmethod
    async def _batch_refusals(
        terminology: Terminology, batch_terms: list[CreateTermRequest],
    ) -> dict[int, DefStoreError]:
        """Items of one batch refused before anything is registered: a
        parent_term_id that is not an active term of this terminology, and a
        value that repeats an earlier item of the batch (compared as the
        terminology compares values)."""
        refused: dict[int, DefStoreError] = {}
        parents = await TerminologyService._pointer_refusals(
            terminology.terminology_id, "parent_term_id",
            [(t.parent_term_id, t.term_id) for t in batch_terms],
        )
        fold = TerminologyService._value_fold(terminology)
        first_at: dict[str, int] = {}
        for i, (term_req, parent_refusal) in enumerate(zip(batch_terms, parents, strict=True)):
            earlier = first_at.setdefault(fold(term_req.value), i)
            if earlier != i:
                refused[i] = DefStoreError(
                    f"Value '{term_req.value}' repeats item {earlier} of this request "
                    f"('{batch_terms[earlier].value}')",
                    "already_exists", rule="repeated_in_request",
                )
            elif parent_refusal is not None:
                refused[i] = parent_refusal
        return refused

    @staticmethod
    async def _pointer_refusals(
        terminology_id: str, field: str, pairs: list[tuple[str | None, str | None]],
    ) -> list[DefStoreError | None]:
        """For each (pointer, own term id), the refusal of that pointer, or
        None when it is unset or valid. One query for the whole list."""
        wanted = list({p for p, _ in pairs if p})
        found = {t.term_id: t for t in await Term.find({"term_id": {"$in": wanted}}).to_list()} if wanted else {}
        return [
            _pointer_refusal(field, p, own, found.get(p), terminology_id) if p else None
            for p, own in pairs
        ]

    @staticmethod
    async def _require_valid_pointer(
        terminology_id: str, field: str, pointer: str | None, own_id: str | None,
    ) -> None:
        """Raise the refusal of one term pointer; nothing when unset or valid.
        An update passes None for a pointer it leaves as it was: only a
        pointer a write sets is checked."""
        (refusal,) = await TerminologyService._pointer_refusals(terminology_id, field, [(pointer, own_id)])
        if refusal is not None:
            raise refusal

    @staticmethod
    async def _add_to_term_count(terminology_id: str, delta: int, now: datetime) -> None:
        """Move the terminology's term_count by `delta` in one atomic update,
        touching no other field."""
        await Terminology.get_motor_collection().update_one(
            {"terminology_id": terminology_id},
            {"$inc": {"term_count": delta}, "$set": {"updated_at": now}},
        )

    @staticmethod
    async def create_terms_bulk(
        terminology_id: str,
        terms: list[CreateTermRequest],
        created_by: str | None = None,  # Actor override for internal/system callers; else the authenticated identity
        skip_duplicates: bool = True,
        update_existing: bool = False,
        batch_size: int = 1000,
        registry_batch_size: int = 100,
    ) -> list[BulkResultItem]:
        """
        Create multiple terms in a terminology using batch operations.

        Uses bulk MongoDB operations (insert_many) instead of per-term inserts
        for significantly better performance on large imports.

        Namespace is inherited from the parent terminology.

        All operations are chunked to handle large imports (100k+ terms).

        Args:
            terminology_id: Parent terminology ID
            terms: Terms to create
            created_by: Deprecated - uses authenticated identity
            skip_duplicates: If True, skip terms whose value already exists
            update_existing: Extend-only — existing terms are left unchanged and
                reported as "skipped" (in-place update is a future --force
                feature; CASE-797). Accepting an existing term non-fatally.
            batch_size: Number of terms to process per MongoDB batch (default 1000)
            registry_batch_size: Number of terms per registry HTTP call (default 100)

        Returns:
            List of operation results
        """
        if not terms:
            return []

        total_terms = len(terms)
        logger.info(f"Starting bulk import of {total_terms} terms to {terminology_id}")

        # Phase A: Verify terminology exists (1 query)
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            raise DefStoreError(f"Terminology '{terminology_id}' not found", "not_found")
        TerminologyService._require_active(terminology)

        namespace = terminology.namespace

        # Authenticated identity by default; an explicit `created_by` is honored
        # for internal/system callers (e.g. the startup bootstrap).
        actor = created_by or get_identity_string()
        now = datetime.now(UTC)

        # Initialize results array
        results: list[BulkResultItem | None] = [None] * len(terms)
        total_created = 0
        client = get_registry_client()
        num_batches = (total_terms + batch_size - 1) // batch_size

        # Process in batches to avoid MongoDB/HTTP limits
        for batch_num, batch_start in enumerate(range(0, len(terms), batch_size), 1):
            batch_end = min(batch_start + batch_size, len(terms))
            batch_terms = terms[batch_start:batch_end]
            logger.info(
                f"Processing batch {batch_num}/{num_batches}: "
                f"terms {batch_start+1}-{batch_end} of {total_terms}"
            )

            try:
                batch_created = await TerminologyService._process_term_batch(
                    batch_terms=batch_terms,
                    batch_start=batch_start,
                    results=results,
                    terminology=terminology,
                    namespace=namespace,
                    actor=actor,
                    now=now,
                    client=client,
                    registry_batch_size=registry_batch_size,
                    skip_duplicates=skip_duplicates,
                    update_existing=update_existing,
                    batch_num=batch_num,
                )
            except RegistryError as e:
                # The Registry did not answer: nothing of this batch is
                # kept, and the batches after it are not attempted. Every
                # item without an answer says so; what earlier batches
                # created stays created and is counted below.
                logger.warning("Bulk term create stopped at batch %d: %s", batch_num, e)
                TerminologyService._fail_unanswered(
                    results, terms, batch_start,
                    f"Not created — the Registry did not answer: {e}",
                )
                break
            total_created += batch_created
            logger.info(
                f"Batch {batch_num}/{num_batches} complete: "
                f"{batch_created} created, {total_created} total so far"
            )

            # Pause between batches to allow memory cleanup and prevent resource exhaustion
            # This is especially important on memory-constrained environments
            if batch_end < len(terms):
                await asyncio.sleep(0.1)  # 100ms pause between MongoDB batches

        # Count what landed, atomically: never a save of the terminology
        # read at the start, which would undo a concurrent count change or
        # a config edit made while the import ran
        if total_created > 0:
            await TerminologyService._add_to_term_count(terminology_id, total_created, now)

        if total_created > 0:
            TerminologyService._drop_derived_caches(terminology.value)

        logger.info(
            f"Bulk import complete: {total_created} terms created out of {total_terms} submitted"
        )
        return cast(list[BulkResultItem], results)

    @staticmethod
    async def get_term(
        term_id: str | None = None,
    ) -> TermResponse | None:
        """
        Get a term by ID.

        Args:
            term_id: Term ID

        Returns:
            Term if found, None otherwise
        """
        if not term_id:
            return None

        term = await Term.find_one({"term_id": term_id})
        if term:
            return TerminologyService._to_term_response(term)
        return None

    @staticmethod
    async def list_terms(
        terminology_id: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        ns_filter: dict | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        namespace: str | None = None,
    ) -> tuple[list[TermResponse], int]:
        """
        List terms in a terminology with pagination.

        Args:
            terminology_id: Terminology to list terms from
            status: Filter by status
            page: Page number (1-based)
            page_size: Number of items per page
            search: Search string for value or aliases
            ns_filter: Namespace filter dict from resolve_namespace_filter()
            sort_by: Sort field (value, label, sort_order, created_at, updated_at)
            sort_order: Sort direction (asc or desc)
            namespace: The terminology's own namespace. A term always lives
                in its terminology's namespace, so naming it changes no
                result but lets the namespace-led indexes serve the query —
                a superadmin's empty ns_filter otherwise leaves only
                terminology_id, which no index leads with. It narrows
                ns_filter and never replaces it: a caller who cannot read
                that namespace still gets nothing.

        Returns:
            Tuple of (list of terms, total count)
        """
        query: dict = {"terminology_id": terminology_id}
        if ns_filter:
            query.update(ns_filter)
        if namespace is not None:
            allowed = query.get("namespace")
            if isinstance(allowed, dict):
                permitted = namespace in allowed.get("$in", [])
            else:
                permitted = allowed is None or allowed == namespace
            if not permitted:
                return [], 0
            query["namespace"] = namespace
        if status:
            query["status"] = status

        # Add search filter if provided. The search string is a LITERAL,
        # case-insensitive substring, so escape it before it becomes a
        # pattern — unescaped, an invalid pattern (`(`, `[`, a trailing
        # `\`) made MongoDB reject the query as a 500, and a valid one
        # changed meaning silently (`a.b` matched `axb`). Same rule as
        # the Registry's search (CASE-568). A real regex mode, if ever
        # wanted, is a separate explicit parameter, not this one.
        if search:
            escaped = re.escape(search)
            query["$or"] = [
                {"term_id": {"$regex": escaped, "$options": "i"}},
                {"value": {"$regex": escaped, "$options": "i"}},
                {"label": {"$regex": escaped, "$options": "i"}},
                {"names.value": {"$regex": escaped, "$options": "i"}}
            ]

        # Get total count
        total = await Term.find(query).count()

        # Get paginated results
        skip = (page - 1) * page_size
        sort_clauses = _build_sort(
            sort_by, sort_order, _TERM_SORT_FIELDS, "sort_order",
            tiebreaker="value",
        )
        terms = await Term.find(query) \
            .sort(sort_clauses) \
            .skip(skip) \
            .limit(page_size) \
            .to_list()

        return [TerminologyService._to_term_response(t) for t in terms], total

    @staticmethod
    async def update_term(
        term_id: str,
        request: UpdateTermRequest,
        collision_out: list[dict[str, Any]] | None = None,
    ) -> TermResponse | None:
        """
        Update a term.

        Creates an audit log entry for all changes. When the update changes
        names, cross-term collisions are rejected under unique_names or
        appended to collision_out for the caller to surface as a warning.
        """
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return None

        # Track changes for audit log
        changed_fields: list[str] = []
        previous_values: dict[str, Any] = {}
        new_values: dict[str, Any] = {}

        # Names: either the full structured list, or the legacy fields each
        # replacing their own partition (aliases -> untyped names,
        # translations -> language names; typed names survive legacy writes).
        if request.names is not None and (
            request.aliases is not None or request.translations is not None
        ):
            raise ValueError(
                "names is mutually exclusive with the legacy aliases/translations fields"
            )

        new_names: list[TermName] | None = None
        if request.names is not None:
            new_names = request.names
        else:
            if request.aliases is not None:
                new_names = replace_untyped(term.names, request.aliases)
            if request.translations is not None:
                new_names = replace_translations(
                    new_names if new_names is not None else term.names,
                    request.translations,
                )

        if new_names is not None:
            effective_value = term.value
            validate_names(
                new_names,
                effective_value,
                await NameTypeService.get_valid_name_types(),
            )
            parent = await Terminology.find_one({
                "terminology_id": term.terminology_id
            })
            if parent:
                collisions = await TerminologyService.find_name_collisions(
                    parent, effective_value, new_names, exclude_term_id=term_id
                )
                if collisions:
                    if parent.unique_names:
                        raise NameCollisionError(collisions)
                    if collision_out is not None:
                        collision_out.extend(collisions)
            if [n.model_dump() for n in new_names] != [n.model_dump() for n in term.names]:
                changed_fields.append("names")
                previous_values["names"] = [n.model_dump() for n in term.names]
                new_values["names"] = [n.model_dump() for n in new_names]
                term.names = new_names

        # RFC 7396 alignment: an EXPLICITLY submitted null clears the
        # field (detected via model_fields_set — an omitted field is not
        # in the set and keeps meaning "unchanged"). Document PATCH
        # already speaks this vocabulary; terms now match. `names` is
        # excluded: the names list has an explicit clear idiom already ([]).
        fields_set = request.model_fields_set
        if "names" in fields_set and request.names is None:
            raise ValueError(
                "names cannot be null — pass [] to remove all names"
            )

        def _apply_clearable(field: str, default=None) -> None:
            submitted = getattr(request, field)
            if submitted is not None:
                new_value = submitted
            elif field in fields_set:
                new_value = default  # explicit null: clear / reset
            else:
                return  # omitted: unchanged
            if new_value == getattr(term, field):
                return
            changed_fields.append(field)
            previous_values[field] = getattr(term, field)
            new_values[field] = new_value
            setattr(term, field, new_value)

        _apply_clearable("label")
        _apply_clearable("description")
        _apply_clearable("sort_order", default=0)
        await TerminologyService._require_valid_pointer(
            term.terminology_id, "parent_term_id",
            None if request.parent_term_id == term.parent_term_id else request.parent_term_id,
            term.term_id,
        )
        _apply_clearable("parent_term_id")

        if request.metadata is not None:
            changed_fields.append("metadata")
            previous_values["metadata"] = term.metadata.copy()
            term.metadata.update(request.metadata)
            new_values["metadata"] = term.metadata

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        term.updated_at = datetime.now(UTC)
        term.updated_by = actor
        await term.save()

        # Create audit log entry if there were changes
        if changed_fields:
            await TerminologyService._create_audit_log(
                term_id=term_id,
                terminology_id=term.terminology_id,
                action="updated",
                changed_by=actor,
                changed_fields=changed_fields,
                previous_values=previous_values,
                new_values=new_values,
                namespace=term.namespace
            )

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_UPDATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=actor,
        )

        # A changed system-vocabulary term must not serve stale from the cache
        TerminologyService._drop_derived_caches(term.terminology_value)

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def _record_lifecycle(entry_ids: list[str], *, retired: bool, actor: str | None) -> None:
        """Have the Registry record these terms or terminologies as retired
        (deprecated, soft-deleted) or revived (restored) — BEFORE def-store
        writes the change.

        Retiring releases their caller-added synonyms — they stay recorded as
        inactive and neither block another entity from claiming the key nor
        resolve; reviving claims them again where free. The {ns, type, …}
        auto-synonym and the primary key are never released, so a retired
        term or terminology stays addressable by value.

        The Registry is the identity authority: a retirement it has not
        recorded leaves the entity's synonyms resolving as though it were
        alive. So when the Registry does not answer, or refuses any entry,
        what it did record is undone and DefStoreError (registry_error) is
        raised; the caller then writes nothing.
        """
        if not entry_ids:
            return
        verb = "retire" if retired else "revive"
        try:
            results = await TerminologyService._lifecycle_call(entry_ids, retired=retired, actor=actor)
        except Exception as e:
            await TerminologyService._undo_lifecycle(entry_ids, retired=retired, actor=actor)
            raise DefStoreError(
                f"Not changed — the Registry did not answer the request to {verb} it: {e}",
                "registry_error",
            ) from e
        refusal = _lifecycle_refusal(results, len(entry_ids))
        if refusal is not None:
            recorded, detail = refusal
            await TerminologyService._undo_lifecycle(recorded, retired=retired, actor=actor)
            raise DefStoreError(f"Not changed — the Registry could not {verb} it ({detail})", "registry_error")

    @staticmethod
    async def _lifecycle_call(entry_ids: list[str], *, retired: bool, actor: str | None) -> list[dict]:
        """Ask the Registry to retire or revive the entries; log its warnings
        (a revived synonym another entity took meanwhile)."""
        client = get_registry_client()
        if retired:
            results = await client.deactivate_entries(entry_ids, updated_by=actor)
        else:
            results = await client.reactivate_entries(entry_ids, updated_by=actor)
        for r in results:
            for warning in r.get("warnings") or []:
                logger.warning("Entry %s: %s", r.get("registry_id"), warning)
        return results

    @staticmethod
    async def _undo_lifecycle(entry_ids: list[str], *, retired: bool, actor: str | None) -> None:
        """Best effort: reverse a lifecycle change the Registry recorded for
        a write that is not going ahead. A failure here is logged — the
        write it served is refused either way, and
        /maintenance/reconcile-synonym-lifecycle settles what is left."""
        if not entry_ids:
            return
        try:
            client = get_registry_client()
            if retired:
                await client.reactivate_entries(entry_ids, updated_by=actor)
            else:
                await client.deactivate_entries(entry_ids, updated_by=actor)
        except Exception as e:
            logger.error("Registry lifecycle change for %d entries not undone: %s", len(entry_ids), e)

    @staticmethod
    async def _lifecycle_write(
        entry_ids: list[str], *, retired: bool, actor: str | None, write: Any,
    ) -> None:
        """Record the lifecycle change in the Registry, then run `write` (the
        def-store change). Nothing is written when the Registry cannot record
        it; when `write` fails, the Registry change is undone."""
        await TerminologyService._record_lifecycle(entry_ids, retired=retired, actor=actor)
        try:
            await write()
        except Exception:
            await TerminologyService._undo_lifecycle(entry_ids, retired=retired, actor=actor)
            raise

    @staticmethod
    async def deprecate_term(
        term_id: str,
        request: DeprecateTermRequest,
        release_synonyms: bool = True,
    ) -> TermResponse | None:
        """
        Deprecate a term (mark as deprecated but keep for historical data).

        A deprecated term is retired, so its synonyms are released in the
        Registry — except when the deprecation is part of a merge
        (release_synonyms=False): those synonyms move to the survivor and
        must stay live.
        """
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return None

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        before = TerminologyService._lifecycle_values(term)
        await TerminologyService._require_valid_pointer(
            term.terminology_id, "replaced_by_term_id", request.replaced_by_term_id, term.term_id,
        )
        term.status = "deprecated"
        term.deprecated_reason = request.reason
        term.replaced_by_term_id = request.replaced_by_term_id
        term.updated_at = datetime.now(UTC)
        term.updated_by = actor
        await TerminologyService._lifecycle_write(
            [term_id] if release_synonyms else [], retired=True, actor=actor, write=term.save,
        )
        await TerminologyService._audit_lifecycle(term, "deprecated", before, actor)

        # Update terminology term count
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_DEPRECATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=actor,
        )

        # A deprecated name or relation type stops validating at once
        TerminologyService._drop_derived_caches(term.terminology_value)

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def delete_term(
        term_id: str,
        updated_by: str | None = None,  # Deprecated: uses authenticated identity
        hard_delete: bool = False,
    ) -> bool:
        """Delete a term. Hard-deletes if terminology is mutable OR if hard_delete=True
        and namespace deletion_mode is 'full'. Soft-deletes otherwise."""
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return False

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        prior_status = term.status

        # Check if parent terminology is mutable
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        should_hard_delete = bool(terminology and terminology.mutable)
        registry_failure: RegistryError | None = None

        if hard_delete and not should_hard_delete:
            # Check namespace deletion_mode
            client = get_registry_client()
            deletion_mode = await client.get_namespace_deletion_mode(term.namespace)
            if deletion_mode != "full":
                raise DefStoreError(
                    f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')", "invalid_state", rule="deletion_mode_retain"
                )
            should_hard_delete = True

        if should_hard_delete:
            # HARD DELETE: remove term and cascade relations

            # 1. Find and delete relations involving this term
            relations = await TermRelation.find({
                "$or": [
                    {"source_term_id": term_id},
                    {"target_term_id": term_id}
                ]
            }).to_list()

            if relations:
                # Publish relation.deleted events before removing
                for rel in relations:
                    await publish_term_relation_event(
                        NatsEventType.TERM_RELATION_DELETED,
                        {
                            "namespace": rel.namespace,
                            "source_term_id": rel.source_term_id,
                            "target_term_id": rel.target_term_id,
                            "relation_type": rel.relation_type,
                            "hard_delete": True,
                        },
                        changed_by=actor,
                    )

                # Delete relations from MongoDB
                await TermRelation.find({
                    "$or": [
                        {"source_term_id": term_id},
                        {"target_term_id": term_id}
                    ]
                }).delete()

            # 2. Capture event dict before deleting the document
            event_dict = TerminologyService._term_to_event_dict(term)
            event_dict["hard_delete"] = True

            # 3. Hard-delete the term document
            await term.delete()

            # 4. Hard-delete the Registry entry, so the value stops resolving to
            # the deleted id. A mutable terminology's terms are hard-deletable
            # regardless of deletion_mode; the entry carries that exemption.
            client = get_registry_client()
            try:
                await client.hard_delete_entry(
                    term_id, updated_by=actor,
                    owner_hard_deleted=bool(terminology and terminology.mutable),
                )
            except RegistryError as e:
                logger.error(f"Registry entry for term {term_id} not removed after hard delete: {e}")
                registry_failure = e
        else:
            # SOFT DELETE: set status to inactive (existing behavior)
            term.status = "inactive"
            term.updated_at = datetime.now(UTC)
            term.updated_by = actor
            await TerminologyService._lifecycle_write([term_id], retired=True, actor=actor, write=term.save)
            event_dict = TerminologyService._term_to_event_dict(term)

        # Update terminology term count
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        TerminologyService._drop_derived_caches(term.terminology_value)
        await TerminologyService._create_audit_log(
            term_id=term_id, terminology_id=term.terminology_id, action="deleted",
            namespace=term.namespace, changed_by=actor, changed_fields=["status"],
            previous_values={"status": prior_status},
            new_values={"hard_delete": True} if should_hard_delete else {"status": "inactive"},
        )

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_DELETED,
            event_dict,
            changed_by=actor,
        )

        if registry_failure is not None:
            raise RegistryEntryNotRemovedError(
                f"Term {term_id} was deleted, but its Registry entry could not be "
                f"removed ({registry_failure}); its value still resolves to the "
                "deleted id until the entry is removed."
            )
        return True

    @staticmethod
    async def merge_terms(
        survivor_id: str,
        absorbed_id: str,
        reason: str | None = None,
        collision_out: list[dict[str, Any]] | None = None,
    ) -> tuple[TermResponse, list[TermName]]:
        """Absorb one term into another, in one vocabulary.

        From then on the absorbed term's value and names resolve to the
        survivor: they become names of the survivor; the absorbed term is
        deprecated with replaced_by_term_id = survivor (deprecated terms
        never match a new write); and the Registry merges its entry into the
        survivor's, so its id and `ns:terminology:value` key resolve to the
        survivor too. Existing documents keep the absorbed term's id —
        history is not rewritten.

        Writes run in a reversible order — deprecate, add names, then the
        Registry merge (which has no undo) — and an earlier step is undone
        when a later one fails, so a failed merge changes nothing.

        Returns the survivor and the names it gained.
        """
        survivor = await Term.find_one({"term_id": survivor_id})
        absorbed = await Term.find_one({"term_id": absorbed_id})
        if survivor is None or absorbed is None:
            raise DefStoreError(f"Term not found: {survivor_id if survivor is None else absorbed_id}", "not_found")
        if survivor.term_id == absorbed.term_id:
            raise ValueError("A term cannot be merged into itself")
        if survivor.terminology_id != absorbed.terminology_id:
            raise ValueError("Terms can only be merged within one terminology")
        for term, role in ((survivor, "survivor"), (absorbed, "absorbed term")):
            if term.status != "active":
                raise DefStoreError(f"The {role} '{term.value}' is {term.status}; only active terms can be merged", "invalid_state", rule="not_active")
        terminology = await Terminology.find_one({"terminology_id": survivor.terminology_id})
        case_sensitive = bool(terminology and terminology.case_sensitive)

        def fold(text: str) -> str:
            return text if case_sensitive else text.lower()

        borne = {fold(survivor.value), *(fold(n.value) for n in survivor.names)}
        carried: list[TermName] = []
        for name in [TermName(value=absorbed.value, source="merge"), *absorbed.names]:
            if fold(name.value) not in borne:
                borne.add(fold(name.value))
                carried.append(name.model_copy(update={"preferred": False}))
        original_names = list(survivor.names)
        new_names = [*original_names, *carried]
        validate_names(new_names, survivor.value, await NameTypeService.get_valid_name_types())

        before = (absorbed.status, absorbed.deprecated_reason, absorbed.replaced_by_term_id)
        # Not a retirement of the absorbed identity: its synonyms move to the
        # survivor with the Registry merge below, so they stay claimed.
        await TerminologyService.deprecate_term(absorbed_id, DeprecateTermRequest(
            reason=reason or f"Merged into '{survivor.value}'", replaced_by_term_id=survivor_id,
        ), release_synonyms=False)
        try:
            result = await TerminologyService.update_term(
                survivor_id, UpdateTermRequest(names=new_names), collision_out=collision_out,
            )
        except Exception:
            await TerminologyService._restore_lifecycle(absorbed_id, before)
            raise
        if result is None:  # the survivor vanished between the read and the update
            await TerminologyService._restore_lifecycle(absorbed_id, before)
            raise DefStoreError(f"Term not found: {survivor_id}", "not_found")
        try:
            await get_registry_client().merge_entries(
                survivor_id, absorbed_id, updated_by=get_identity_string(), owner_merge=True,
            )
        except RegistryError as e:
            await TerminologyService.update_term(survivor_id, UpdateTermRequest(names=original_names))
            await TerminologyService._restore_lifecycle(absorbed_id, before)
            raise DefStoreError(
                f"Merge not applied — the Registry refused to merge the entries: {e}", "registry_error"
            ) from e
        await TerminologyService._create_audit_log(
            term_id=absorbed_id, terminology_id=absorbed.terminology_id, action="merged",
            namespace=absorbed.namespace, changed_by=get_identity_string(),
            changed_fields=["merged_into"],
            new_values={"merged_into": survivor_id, "names_carried": [n.value for n in carried]},
            comment=reason,
        )
        return result, carried

    @staticmethod
    async def _restore_lifecycle(term_id: str, before: tuple[str, str | None, str | None]) -> None:
        """Undo a deprecation made by a merge that did not complete.

        The deprecation was audited when it was made, so its undo is too.
        """
        term = await Term.find_one({"term_id": term_id})
        if term is None:
            return
        deprecated = TerminologyService._lifecycle_values(term)
        term.status, term.deprecated_reason, term.replaced_by_term_id = before
        term.updated_at = datetime.now(UTC)
        term.updated_by = get_identity_string()
        await term.save()
        await TerminologyService._audit_lifecycle(
            term, "restored", deprecated, term.updated_by, comment="merge not applied",
        )
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id, "status": "active",
            }).count()
            await terminology.save()
        TerminologyService._drop_derived_caches(term.terminology_value)
        await publish_term_event(
            NatsEventType.TERM_UPDATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=get_identity_string(),
        )

    @staticmethod
    async def registry_ghosts(namespace: str, *, remove: bool) -> dict[str, Any]:
        """Active Registry entries in `namespace` whose terminology or term no
        longer exists in def-store — left behind by hard deletes the
        Registry refused — and, with remove=True, their removal.

        A ghost's synonyms still resolve (`ns:terminology:value` → a deleted
        id) and re-creating its value would reuse the dead id. Removable: a
        terminology entry (a terminology is only hard-deleted when mutable or
        in a 'full' namespace), and a term entry whose terminology is mutable
        or gone. A term missing from an IMMUTABLE terminology is reported but
        never removed: nothing should hard-delete those, so it needs a look.
        """
        client = get_registry_client()
        terminologies = {
            t.terminology_id: t for t in await Terminology.find({"namespace": namespace}).to_list()
        }
        existing_terms = set(
            await Term.get_motor_collection().distinct("term_id", {"namespace": namespace})
        )

        ghosts: list[dict[str, Any]] = []
        for entry in await client.list_active_entries(namespace, "terminologies"):
            if entry["entry_id"] not in terminologies:
                ghosts.append({
                    "entry_id": entry["entry_id"], "entity_type": "terminologies",
                    "composite_key": entry.get("primary_composite_key"),
                    "removable": True, "reason": "terminology no longer exists",
                })
        for entry in await client.list_active_entries(namespace, "terms"):
            if entry["entry_id"] in existing_terms:
                continue
            key = entry.get("primary_composite_key") or {}
            parent = terminologies.get(str(key.get("terminology_id") or ""))
            if parent is None:
                removable, reason = True, "its terminology no longer exists"
            elif parent.mutable:
                removable, reason = True, "deleted from a mutable terminology"
            else:
                removable, reason = False, "missing from an immutable terminology — not removed, investigate"
            ghosts.append({
                "entry_id": entry["entry_id"], "entity_type": "terms",
                "composite_key": key, "removable": removable, "reason": reason,
            })

        removed: list[str] = []
        errors: list[dict[str, str]] = []
        if remove:
            for ghost in ghosts:
                if not ghost["removable"]:
                    continue
                try:
                    await client.hard_delete_entry(
                        ghost["entry_id"], updated_by=get_identity_string(), owner_hard_deleted=True,
                    )
                    removed.append(ghost["entry_id"])
                except RegistryError as e:
                    errors.append({"entry_id": ghost["entry_id"], "error": str(e)})
        return {"namespace": namespace, "dry_run": not remove, "ghosts": ghosts,
                "removed": removed, "errors": errors}

    # =========================================================================
    # VALIDATION
    # =========================================================================

    @staticmethod
    async def validate_value(
        terminology_id: str | None = None,
        terminology_value: str | None = None,
        value: str = ""
    ) -> "ValueResolution":
        """
        Validate a value against a terminology.

        Matches against the term's value or any of its names, with value
        precedence: every term's value is checked before any term's names,
        so a name on one term can never shadow another term's canonical
        value. Either match collects EVERY bearer: the first (database
        order) is the matched_term, and the full candidate list and count
        make an ambiguity visible instead of silently resolved. A value is
        ambiguous only in a case-insensitive terminology holding case
        variants written before writes compared values that way.

        Args:
            terminology_id: Terminology ID
            terminology_value: Terminology value (alternative to ID)
            value: Value to validate

        Returns:
            A ValueResolution. matched_via is 'value' or 'alias';
            matched_name is the TermName that matched for 'alias';
            candidates lists every (term, name) bearing the name.
        """
        # Find terminology — an unscoped value that exists in several
        # namespaces raises rather than validating against an arbitrary one
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif terminology_value:
            terminology = await TerminologyService.find_by_value(terminology_value, None)
        else:
            return ValueResolution(False, None, None, None, None, [])

        if not terminology:
            return ValueResolution(False, None, None, None, None, [])

        (resolution,) = await TerminologyService.resolve_values(terminology, [value])
        return resolution

    @staticmethod
    async def resolve_values(
        terminology: Terminology, values: list[str]
    ) -> list["ValueResolution"]:
        """Resolve several values against one terminology, in order.

        The rule (shared by every caller — document writes resolve term
        fields through this, never through a copy of the vocabulary):
        only ACTIVE terms match; every term's canonical value is checked
        before any term's names, so a name can never shadow another term's
        value; comparison follows the terminology's `case_sensitive`. A
        name borne by several terms resolves to the first bearer in
        creation order, with every bearer listed as a candidate.

        One indexed query fetches the candidate terms for the whole batch
        — the terms whose value or any name equals a submitted value — so
        cost tracks the batch, not the vocabulary's size. For
        case-insensitive terminologies the query uses a case-insensitive
        collation (matching the collation indexes on Term); the exact
        comparison below then applies Python's rule to those candidates,
        so the collation only narrows the search and never decides a match.
        """
        def _cmp(s: str) -> str:
            return s if terminology.case_sensitive else s.lower()

        unique = list(dict.fromkeys(values))
        query: dict[str, Any] = {
            "namespace": terminology.namespace,
            "terminology_id": terminology.terminology_id,
            "status": "active",
            "$or": [{"value": {"$in": unique}}, {"names.value": {"$in": unique}}],
        }
        find_kwargs: dict[str, Any] = (
            {} if terminology.case_sensitive else {"collation": CASE_INSENSITIVE_COLLATION}
        )
        candidates = await Term.find(query, **find_kwargs).sort([("_id", SortDirection.ASCENDING)]).to_list()

        resolved: dict[str, ValueResolution] = {}
        for value in unique:
            compare_value = _cmp(value)
            # Pass 1: canonical values only. Writes keep one value per
            # terminology as it compares values, but a case-insensitive
            # terminology can hold case variants stored before that — every
            # bearer is listed then, so the pick is visible, not silent
            value_bearers = [t for t in candidates if _cmp(t.value) == compare_value]
            if value_bearers:
                resolved[value] = ValueResolution(
                    True, value_bearers[0], "value", None, None,
                    [(t, None) for t in value_bearers] if len(value_bearers) > 1 else [],
                )
                continue
            # Pass 2: names — collect every bearer, first one wins the match
            bearers: list[tuple[Term, TermName | None]] = []
            for term in candidates:
                name = next((n for n in term.names if _cmp(n.value) == compare_value), None)
                if name is not None:
                    bearers.append((term, name))
            if bearers:
                first_term, first_name = bearers[0]
                resolved[value] = ValueResolution(True, first_term, "alias", None, first_name, bearers)
                continue
            resolved[value] = ValueResolution(
                False, None, None, await TerminologyService._suggest(terminology, value), None, []
            )
        return [resolved[v] for v in values]

    @staticmethod
    async def _suggest(terminology: Terminology, value: str) -> Term | None:
        """An active term whose value starts with `value` (a hint for misses)."""
        if len(value) < 2:
            return None
        flags = "" if terminology.case_sensitive else "i"
        return await Term.find(
            {
                "namespace": terminology.namespace,
                "terminology_id": terminology.terminology_id,
                "status": "active",
                "value": {"$regex": f"^{re.escape(value)}", "$options": flags},
            }
        ).sort([("_id", SortDirection.ASCENDING)]).first_or_none()

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _to_terminology_response(t: Terminology) -> TerminologyResponse:
        """Convert Terminology document to response model."""
        return TerminologyResponse(
            terminology_id=t.terminology_id,
            namespace=t.namespace,
            value=t.value,
            label=t.label,
            description=t.description,
            case_sensitive=t.case_sensitive,
            allow_multiple=t.allow_multiple,
            extensible=t.extensible,
            mutable=t.mutable,
            unique_names=t.unique_names,
            metadata=t.metadata,
            status=t.status,
            term_count=t.term_count,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    def _to_term_response(t: Term) -> TermResponse:
        """Convert Term document to response model."""
        return TermResponse(
            term_id=t.term_id,
            namespace=t.namespace,
            terminology_id=t.terminology_id,
            terminology_value=t.terminology_value,
            value=t.value,
            names=t.names,
            aliases=derived_aliases(t.names),
            label=t.label or t.value,
            description=t.description,
            sort_order=t.sort_order,
            parent_term_id=t.parent_term_id,
            translations=derived_translations(t.names),
            metadata=t.metadata,
            status=t.status,
            deprecated_reason=t.deprecated_reason,
            replaced_by_term_id=t.replaced_by_term_id,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    def _terminology_to_event_dict(t: Terminology) -> dict:
        """Convert Terminology document to a dict for NATS event payload."""
        return {
            "terminology_id": t.terminology_id,
            "namespace": t.namespace,
            "value": t.value,
            "label": t.label,
            "description": t.description,
            "case_sensitive": t.case_sensitive,
            "allow_multiple": t.allow_multiple,
            "extensible": t.extensible,
            "mutable": t.mutable,
            "unique_names": t.unique_names,
            "status": t.status,
            "term_count": t.term_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "created_by": t.created_by,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "updated_by": t.updated_by,
        }

    @staticmethod
    def _term_to_event_dict(t: Term) -> dict:
        """Convert Term document to a dict for NATS event payload."""
        return {
            "term_id": t.term_id,
            "namespace": t.namespace,
            "terminology_id": t.terminology_id,
            "terminology_value": t.terminology_value,
            "value": t.value,
            "names": [n.model_dump() for n in t.names],
            # Derived flat projection — downstream consumers that key on
            # aliases (reporting-sync, document validation caches) keep
            # resolving every name without knowing the structured shape.
            "aliases": derived_aliases(t.names),
            "label": t.label or t.value,
            "description": t.description,
            "sort_order": t.sort_order,
            "parent_term_id": t.parent_term_id,
            "status": t.status,
            "deprecated_reason": t.deprecated_reason,
            "replaced_by_term_id": t.replaced_by_term_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "created_by": t.created_by,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "updated_by": t.updated_by,
        }

    @staticmethod
    def _lifecycle_values(term: Term) -> dict[str, Any]:
        return {
            "status": term.status,
            "deprecated_reason": term.deprecated_reason,
            "replaced_by_term_id": term.replaced_by_term_id,
        }

    @staticmethod
    async def _audit_lifecycle(
        term: Term, action: str, before: dict[str, Any], actor: str | None,
        comment: str | None = None,
    ) -> None:
        """Audit a change of a term's lifecycle state: its status,
        deprecation reason and replacement, before and after."""
        after = TerminologyService._lifecycle_values(term)
        await TerminologyService._create_audit_log(
            term_id=term.term_id, terminology_id=term.terminology_id, action=action,
            namespace=term.namespace, changed_by=actor,
            changed_fields=[k for k in after if after[k] != before.get(k)],
            previous_values=before, new_values=after, comment=comment,
        )

    @staticmethod
    async def _create_audit_log(
        term_id: str,
        terminology_id: str,
        action: AuditAction,
        namespace: str,
        changed_by: str | None = None,
        changed_fields: list[str] | None = None,
        previous_values: dict | None = None,
        new_values: dict | None = None,
        comment: str | None = None,
    ):
        """Create an audit log entry for a term change."""
        audit_entry = TermAuditLog(
            namespace=namespace,
            term_id=term_id,
            terminology_id=terminology_id,
            action=action,
            changed_by=changed_by,
            changed_fields=changed_fields or [],
            previous_values=previous_values or {},
            new_values=new_values or {},
            comment=comment
        )
        await audit_entry.insert()
