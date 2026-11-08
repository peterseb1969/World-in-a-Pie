"""Template service for business logic."""

import contextlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from beanie.odm.enums import SortDirection

from wip_auth.identity import require_current_identity
from wip_auth.permissions import (
    PERMISSION_UNAVAILABLE,
    permission_sufficient,
    resolve_permission,
)
from wip_auth.resolve import (
    EntityNotFoundError,
    resolve_entity_id,
    resolve_entity_ids,
)
from wip_auth.synonym_lifecycle import sync_entry_lifecycle

from ..api.auth import get_identity_string
from ..models.api_models import (
    BulkResultItem,
    CascadeResponse,
    CascadeResult,
    CreateTemplateRequest,
    EndpointRefIn,
    TemplateResponse,
    TemplateUpdateResponse,
    UpdateTemplateRequest,
    ValidateTemplateResponse,
    ValidationError,
    ValidationWarning,
    endpoint_lookups,
)
from ..models.field import FieldDefinition, FieldType, ReferenceType
from ..models.rule import ValidationRule
from ..models.template import (
    EndpointRef,
    ReportingConfig,
    Template,
    TemplateMetadata,
    TemplateUsage,
)
from . import referent_paths
from .def_store_client import DefStoreError, get_def_store_client
from .document_store_client import get_document_store_client
from .errors import TemplateError, TemplateUnresolvable, error_fields
from .inbound_gate import InboundGate, carried_ids
from .inheritance_service import InheritanceError, InheritanceService
from .nats_client import EventType, publish_template_event
from .reference_validator import (
    ReferenceValidationError,
    RegistryUnavailableError,
    get_reference_validator,
)
from .registry_client import RegistryError, get_registry_client
from .rule_shape import rule_shape_error

logger = logging.getLogger(__name__)

_TEMPLATE_SORT_FIELDS = {"value", "label", "created_at", "updated_at", "version"}


def _build_template_sort(
    sort_by: str | None, sort_order: str | None,
) -> list[tuple[str, SortDirection]]:
    field = (sort_by or "value").strip() or "value"
    order = (sort_order or "asc").strip().lower() or "asc"
    if order not in ("asc", "desc"):
        raise TemplateError(f"Invalid sort_order '{sort_order}'. Must be 'asc' or 'desc'.", "invalid_request", rule="sort_order")
    if field not in _TEMPLATE_SORT_FIELDS:
        raise TemplateError(
            f"Unknown sort field '{field}'. Supported: {', '.join(sorted(_TEMPLATE_SORT_FIELDS))}.", "invalid_request", rule="sort_field"
        )
    direction = SortDirection.ASCENDING if order == "asc" else SortDirection.DESCENDING
    return [(field, direction), ("template_id", SortDirection.ASCENDING)]


class TemplateService:
    """Service for managing templates."""

    # =========================================================================
    # RELATIONSHIP-TEMPLATE STRUCTURAL VALIDATION
    # =========================================================================

    @staticmethod
    async def _validate_relationship_template_shape(
        usage: TemplateUsage | None,
        source_templates: list | None,
        target_templates: list | None,
        fields: list[FieldDefinition],
    ) -> None:
        """Enforce structural constraints on relationship templates.

        A relationship template must declare:
          - non-empty source_templates and target_templates (at the
            template level) — the single declaration of its endpoints
          - a source_ref and target_ref reference field with
            reference_type=document

        The endpoint constraint carried by those two fields is NOT checked
        against the declaration, because it is no longer a separate input:
        it is projected from the declaration when the template is served and
        discarded on write. What a caller sends there is ignored.

        For non-relationship templates (entity, reference), the
        template-level source_templates / target_templates must be
        empty — they only mean something when usage=relationship.

        Called on create and on update (with the merged fields), so a new
        version cannot drop an edge type's endpoint fields.

        Raises TemplateError on violation.
        """
        usage = usage if usage is not None else TemplateUsage.ENTITY

        if usage != TemplateUsage.RELATIONSHIP:
            if source_templates or target_templates:
                raise TemplateError(
                    "source_templates and target_templates may only be set "
                    f"when usage='relationship' (got usage='{usage.value}')", "validation_failed", rule="relationship_shape"
                )
            return

        # usage == relationship from here on.
        if not source_templates:
            raise TemplateError(
                "Relationship templates require a non-empty source_templates list", "validation_failed", rule="relationship_shape"
            )
        if not target_templates:
            raise TemplateError(
                "Relationship templates require a non-empty target_templates list", "validation_failed", rule="relationship_shape"
            )

        fields_by_name = {f.name: f for f in fields}

        # The endpoint fields must exist and be document references. What they
        # may point AT is not checked here: `target_templates` on these two
        # fields is projected from the template-level declaration when the
        # template is served, and discarded on write. There is no second
        # declaration to agree with, so the cross-copy comparison this used to
        # perform has nothing left to compare — an invariant that cannot be
        # violated rather than one that is policed.
        for endpoint in ("source_ref", "target_ref"):
            field = fields_by_name.get(endpoint)
            if field is None:
                raise TemplateError(
                    f"Relationship templates require a '{endpoint}' reference field "
                    f"(missing in fields list)", "validation_failed", rule="relationship_shape"
                )
            if field.reference_type != ReferenceType.DOCUMENT:
                raise TemplateError(
                    f"Relationship template field '{endpoint}' must have "
                    f"reference_type='document' (got '{field.reference_type}')", "validation_failed", rule="relationship_shape"
                )

    # =========================================================================
    # METADATA-IN-DECLARATIVE-SLOTS VALIDATION
    # =========================================================================

    @staticmethod
    def _validate_no_metadata_in_declarative_slots(
        identity_fields: list[str] | None,
        fields: list[FieldDefinition],
    ) -> None:
        """Reject metadata.<x> references in template-level declarative
        slots — slots where the platform commits to the field's
        structural meaning (identity, FTS indexing).

        Per the data-model contract:
        - data.<field> is validated, queryable, identity-bearing,
          indexable, FTS-able. The platform commits to its meaning.
        - metadata.* is free-form caller-attached context. No schema
          commitments, no platform-side guarantees.

        A metadata.<x> reference in identity_fields or full_text_indexed
        bends the contract: the document body claims structural meaning
        for caller-context, downstream services try to honour it, the
        guarantees aren't there, and failures cascade silently. CASE-316
        + CASE-318 are the canonical cascade.

        Read-path queries (POST /documents/query filters) are NOT a
        declarative slot and remain free — operators legitimately need
        to filter on metadata for debugging / audit. Only slots that
        commit to field meaning reject metadata.
        """
        if identity_fields:
            bad_identity = [f for f in identity_fields if f.startswith("metadata.")]
            if bad_identity:
                raise TemplateError(
                    f"identity_fields must reference structural data fields, "
                    f"not metadata paths. Got {bad_identity}. metadata.* is "
                    f"caller-attached context with no schema commitments and "
                    f"cannot carry structural identity. Hoist the field(s) to "
                    f"top-level data fields and reference them by name.", "validation_failed", rule="metadata_in_declarative_slot"
                )

        fts_metadata = [
            f for f in fields
            if getattr(f, "full_text_indexed", None)
            and f.name.startswith("metadata.")
        ]
        if fts_metadata:
            names = ", ".join(f.name for f in fts_metadata)
            raise TemplateError(
                f"full_text_indexed cannot be applied to metadata.* paths. "
                f"Got: {names}. metadata.* is not part of the template "
                f"schema and is not indexed by reporting-sync.", "validation_failed", rule="metadata_in_declarative_slot"
            )

    # =========================================================================
    # FULL-TEXT-INDEX STRUCTURAL VALIDATION
    # =========================================================================

    @staticmethod
    def _validate_full_text_indexed_constraints(
        fields: list[FieldDefinition],
        reporting: ReportingConfig | None,
    ) -> None:
        """Enforce structural constraints on full_text_indexed fields.

        - Only string fields may carry full_text_indexed=true. Any other
          base type is rejected (term/reference/file/object/array carry
          their own column shape that doesn't accept tsvector indexing).
        - If any field is full_text_indexed, the template's reporting
          config must allow sync (sync_enabled=true). The flag depends
          on the reporting layer to materialise the tsvector column —
          you cannot index what isn't synced.

        Operates on the *final* fields list and reporting config so it
        can be called from both create and update paths.

        Raises ValueError on violation.
        """
        indexed_fields = [
            f for f in fields if getattr(f, "full_text_indexed", None)
        ]
        if not indexed_fields:
            return

        non_string = [
            f for f in indexed_fields if f.type != FieldType.STRING
        ]
        if non_string:
            names_and_types = ", ".join(
                f"{f.name}(type={f.type.value})" for f in non_string
            )
            raise TemplateError(
                f"full_text_indexed is only valid on type=string fields; "
                f"got {names_and_types}", "validation_failed", rule="full_text_indexed"
            )

        # Default ReportingConfig has sync_enabled=True — only an explicit
        # False conflicts. (reporting may be None; that means defaults.)
        if reporting is not None and not reporting.sync_enabled:
            indexed_names = ", ".join(f.name for f in indexed_fields)
            raise TemplateError(
                f"full_text_indexed requires reporting.sync_enabled=true; "
                f"field(s) {indexed_names} cannot be indexed without sync", "validation_failed", rule="full_text_indexed"
            )

    @staticmethod
    def _validate_versioned_requires_identity(
        versioned: bool, identity_fields: list[str] | None
    ) -> None:
        """Reject ``versioned: false`` paired with empty ``identity_fields`` (CASE-478).

        ``versioned: false`` means "overwrite the document in place on update,
        addressed by its identity". With no identity_fields there is nothing to
        address: every write is a fresh append (identity-less docs never match),
        and PATCH is rejected too (append_only). So the combination is incoherent
        — ``versioned`` is N/A without an identity. Enforced on both create and
        update, because ``versioned`` is immutable after creation but
        ``identity_fields`` is not: an update could otherwise empty
        ``identity_fields`` on a ``versioned: false`` template and reach the
        forbidden state behind the create-time check.

        Raises ValueError on violation (purely declarative — no DB calls).
        """
        if versioned is False and not identity_fields:
            raise TemplateError(
                "versioned:false requires identity_fields: an overwrite-in-place "
                "template must declare an identity to address the document being "
                "updated. Set identity_fields, or use versioned:true (the default). "
                "Append-only templates (empty identity_fields) are versioned:true.", "validation_failed", rule="versioned_requires_identity"
            )

    @staticmethod
    def validate_rules_for_write(rules: list[ValidationRule] | None) -> None:
        """Refuse a rule document-store could not evaluate (see rule_shape
        for what each type needs).

        Called from create and update, never on hydration, so stored
        templates stay readable. Purely declarative, no DB calls.
        """
        for i, rule in enumerate(rules or []):
            why = rule_shape_error(rule)
            if why:
                raise TemplateError(
                    f"Rule {i} ({rule.type.value}): {why}",
                    "validation_failed", rule="invalid_rule",
                )

    @staticmethod
    def _validate_renames(
        renames: dict[str, str] | None,
        previous_fields: list[FieldDefinition],
        new_fields: list[FieldDefinition],
        identity_fields: list[str] | None,
    ) -> None:
        """Validate a version's declared renames ({new_field: old_field}).

        A rename declaration promises "mechanically the same data under a new
        key" — every condition below keeps that promise honest: the old field
        must exist in the previous version and be gone from the new one, the
        new field must be declared now and not have existed before, the types
        must match (same data!), and identity fields can never be renamed
        (identity is immutable across versions; a re-keyed identity value
        would re-hash every document).
        """
        if not renames:
            return
        prev = {f.name: f for f in previous_fields}
        new = {f.name: f for f in new_fields}
        idf = set(identity_fields or [])
        for new_name, old_name in renames.items():
            if old_name not in prev:
                raise TemplateError(
                    f"rename '{new_name}' <- '{old_name}': '{old_name}' does not "
                    f"exist in the previous version", "validation_failed", rule="invalid_rename"
                )
            if new_name not in new:
                raise TemplateError(
                    f"rename '{new_name}' <- '{old_name}': '{new_name}' is not "
                    f"declared in this version's fields", "validation_failed", rule="invalid_rename"
                )
            if new_name in prev:
                raise TemplateError(
                    f"rename '{new_name}' <- '{old_name}': '{new_name}' already "
                    f"existed in the previous version — that is not a rename", "validation_failed", rule="invalid_rename"
                )
            if old_name in new:
                raise TemplateError(
                    f"rename '{new_name}' <- '{old_name}': '{old_name}' is still "
                    f"declared in this version — that is not a rename", "validation_failed", rule="invalid_rename"
                )
            if prev[old_name].type != new[new_name].type:
                raise TemplateError(
                    f"rename '{new_name}' <- '{old_name}': types differ "
                    f"({prev[old_name].type} -> {new[new_name].type}); a rename "
                    f"carries the same data, so the type must match", "validation_failed", rule="invalid_rename"
                )
            if old_name in idf or new_name in idf:
                raise TemplateError(
                    f"rename '{new_name}' <- '{old_name}': identity fields cannot "
                    f"be renamed (identity is immutable across versions)", "identity_fields_immutable"
                )

    @staticmethod
    def _refuse_undeclared_reference(f: FieldDefinition) -> None:
        """A reference field declares what it references, and a term
        reference which terminologies it draws from: without them it
        validates nothing at document write. Activation demanded this; a
        directly-active create or an update skipped it."""
        if f.type != FieldType.REFERENCE:
            return
        if f.reference_type is None:
            raise TemplateError(
                f"reference_type is required for reference field '{f.name}'",
                "validation_failed", rule="reference_type_required",
            )
        if f.reference_type == ReferenceType.TERM and not f.target_terminologies:
            raise TemplateError(
                f"target_terminologies is required for term reference field '{f.name}'",
                "validation_failed", rule="target_terminologies_required",
            )

    @staticmethod
    def _refuse_empty_allowed_types(f: FieldDefinition) -> None:
        """A file field's allowed_types lists what may be uploaded; an empty
        list would allow nothing, so it is refused rather than stored.
        (document-store read it as "every type".) Omit it to allow every
        type."""
        for config in (f.file_config, f.array_file_config):
            if config is not None and not config.allowed_types:
                raise TemplateError(
                    f"allowed_types for file field '{f.name}' is empty: list "
                    "the MIME types it accepts, or omit it to accept every type",
                    "validation_failed", rule="allowed_types_empty",
                )

    @staticmethod
    def validate_fields_for_write(fields: list[FieldDefinition]) -> None:
        """Enforce field-shape authoring invariants at the write seam (CASE-629).

        These checks used to be pydantic @model_validators on FieldDefinition.
        FieldDefinition is embedded in the Beanie Template document, so Beanie
        re-ran them on hydration (model_validate of the stored Mongo dict) — a
        template legally written before a guard existed became unreadable the
        moment the guard shipped (read endpoints 500 on the stored shape). A
        write-time authoring rule must not live on the shared persistence
        model. Enforced here instead: called explicitly from create/bulk/update
        (never on hydration), so stored history always reconstructs while
        new/updated definitions are still rejected. Same shape as
        _validate_versioned_requires_identity (CASE-478); purely declarative,
        no DB calls. Draft creation runs this too, so a draft can never reach
        activation carrying a shape these forbid — no separate activation seam
        is needed.

        Raises ValueError on the first offending field.
        """
        for f in fields:
            # Field names become double-quoted column names in the reporting
            # database. A double quote would close that quoting and a NUL
            # truncates the name, so neither can be quoted safely. Every other
            # character stays allowed.
            # "@template" qualifies the synonym document-store mints from a
            # document's identity spellings; a field of that name would
            # collide with the qualifier inside that synonym.
            if f.name == "@template":
                raise TemplateError(
                    "field name '@template' is reserved: it qualifies the "
                    "synonym minted from a document's identity values",
                    "validation_failed", rule="field_name_reserved",
                )
            if '"' in f.name or "\x00" in f.name:
                raise TemplateError(
                    f"field name {f.name!r} contains a double quote or NUL "
                    "character, which reporting cannot store as a column name", "validation_failed", rule="field_name_unquotable"
                )
            # A nested template reference must pin an explicit version: schema
            # refs never resolve to "latest", so a parent validated against a
            # floating nested schema strands when that schema ships an
            # incompatible version (CASE-493).
            if f.template_ref and f.template_ref_version is None:
                raise TemplateError(
                    f"template_ref_version is required for field '{f.name}': a "
                    "nested template reference must pin an explicit version (CASE-493)", "validation_failed", rule="template_ref_version_required"
                )
            if f.array_template_ref and f.array_template_ref_version is None:
                raise TemplateError(
                    f"array_template_ref_version is required for field '{f.name}': "
                    "an array-item template reference must pin an explicit version "
                    "(CASE-493)", "validation_failed", rule="template_ref_version_required"
                )
            TemplateService._refuse_undeclared_reference(f)
            TemplateService._refuse_empty_allowed_types(f)
            # An array of references must declare what it references. Without it,
            # each item is queued for resolution carrying reference_type=None,
            # matches no resolution branch, and is dropped silently — a bogus id
            # validates clean with references:[] (CASE-550).
            if (
                f.array_item_type == FieldType.REFERENCE
                and f.reference_type is None
            ):
                raise TemplateError(
                    f"reference_type is required for field '{f.name}': an array "
                    "of references (array_item_type='reference') must declare which "
                    "entity type it references, otherwise its items are never "
                    "existence-checked (CASE-550)", "validation_failed", rule="reference_type_required"
                )

    # =========================================================================
    # TEMPLATE CRUD OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_template(
        request: CreateTemplateRequest,
        namespace: str
    ) -> TemplateResponse:
        """
        Create a new template.

        1. Validate extends reference if provided
        2. Register with Registry to get ID
        3. Create template document in MongoDB

        Args:
            request: Creation request
            namespace: Namespace for the template (required — no default)

        Returns:
            Created template

        Raises:
            ValueError: If value already exists or extends invalid
            RegistryError: If Registry communication fails
        """
        # Validate status value
        is_draft = request.status == "draft"
        if request.status is not None and request.status not in ("active", "draft"):
            raise TemplateError(f"Invalid status '{request.status}': must be 'active' or 'draft'", "validation_failed", rule="invalid_status")

        # Structural validation for relationship templates. Runs in draft
        # mode too, and now purely structural: with the endpoint constraint
        # projected rather than supplied, there is nothing here that needs the
        # Registry or the database.
        await TemplateService._validate_relationship_template_shape(
            request.usage, request.source_templates, request.target_templates,
            request.fields,
        )
        # The endpoint constraint on source_ref/target_ref is derived from the
        # declaration when the template is served, so what a caller sends
        # there is discarded before anything resolves it: a typo there, or a
        # served edge type carrying another install's ids, is not a reference.
        TemplateService._strip_endpoint_projection(
            request.usage or TemplateUsage.ENTITY, request.fields
        )

        # Structural validation for full_text_indexed fields (also
        # purely declarative — runs in draft mode too).
        TemplateService._validate_full_text_indexed_constraints(
            request.fields, request.reporting
        )

        # Reject metadata.<x> in declarative slots (identity_fields,
        # full_text_indexed) — declarative slots commit the platform
        # to structural meaning; metadata.* has no schema guarantees.
        # Read-path query filters remain free.
        TemplateService._validate_no_metadata_in_declarative_slots(
            request.identity_fields, request.fields
        )

        # Reject versioned:false + empty identity_fields (CASE-478) — declarative,
        # runs in draft mode too.
        TemplateService._validate_versioned_requires_identity(
            request.versioned, request.identity_fields
        )
        TemplateService.validate_fields_for_write(request.fields)
        TemplateService.validate_rules_for_write(request.rules)

        # Check if value already exists within namespace — skip in restore mode
        # (restoring version 2+ of a template will find version 1 already present)
        is_restore = request.template_id and request.version is not None

        # Renames are relative to a PREVIOUS version — meaningless on a first
        # version. The upsert dispatcher validates them on the versioning
        # path; restore trusts the archive (the declaration was validated
        # when the version was originally created).
        if request.renames and not is_restore:
            raise TemplateError(
                "renames require a previous version to rename from — a first "
                "version has none. Declare renames on the version that "
                "introduces the new field names.", "validation_failed", rule="invalid_rename"
            )
        if not is_restore:
            existing = await Template.find_one({"namespace": namespace, "value": request.value})
            if existing:
                raise TemplateError(f"Template with value '{request.value}' already exists in namespace '{namespace}'", "already_exists")

        # A create has no previous version, so every reference it names is new
        # and passes the inbound gate.
        gate = InboundGate(namespace)

        # Validate extends if provided
        parent_namespace: str | None = None
        if request.extends:
            submitted_extends = request.extends
            # CASE-432: resolve `extends` through the Registry first so a
            # registered synonym for the parent resolves identically to its
            # canonical ID (Vision §"References Must Resolve"), matching how
            # activation (resolve_entity_id) and field references already
            # resolve. On a miss, fall through to the legacy lookups — draft
            # mode may legitimately name a not-yet-created parent, and a draft
            # parent is still found by the any-status value lookup below.
            with contextlib.suppress(EntityNotFoundError):
                request.extends = await resolve_entity_id(request.extends, "template", namespace)
            # Find latest version of parent (template_id is stable across versions)
            parent_results = await Template.find({"template_id": request.extends}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
            parent = parent_results[0] if parent_results else None
            if parent and await gate.refuses(parent.namespace, parent.template_id):
                # A parent the writer may not reference is not found, and
                # what follows is exactly what a missing parent gets.
                parent = None
                request.extends = submitted_extends
            if not parent:
                # Try by value within same namespace
                parent_results = await Template.find({"namespace": namespace, "value": request.extends}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
                parent = parent_results[0] if parent_results else None
                if parent:
                    request.extends = parent.template_id
                else:
                    if not is_draft:
                        raise TemplateError(f"Parent template '{request.extends}' not found", "reference_violation", rule="parent_not_found")
                    # Draft mode: store raw extends value even if parent doesn't exist yet
            if parent:
                parent_namespace = parent.namespace
                TemplateService._refuse_inactive_parent(parent, is_draft)

        # Normalize all field references to canonical IDs — skip for drafts.
        # Normalization implicitly validates (raises EntityNotFoundError for invalid refs),
        # which is converted to ValueError for the API boundary.
        resolved_template_ids: list[str] = []
        resolved_terminology_ids: list[str] = []
        source_entries: list[EndpointRef]
        target_entries: list[EndpointRef]
        if not is_draft:
            try:
                resolved_template_ids, resolved_terminology_ids = (
                    await TemplateService._normalize_field_references(
                        request.fields, namespace, gate=gate,
                    )
                )
                # An edge type's declared endpoints resolve on the same write
                # path as its field references: the submitted anchor is kept
                # verbatim in lookup_value and the canonical id fills the
                # server-owned resolved half.
                source_entries, target_entries = (
                    await TemplateService._resolve_endpoint_declarations(
                        request.source_templates, request.target_templates, namespace,
                        gate=gate,
                    )
                )
            except EntityNotFoundError as e:
                raise TemplateError(str(e), "reference_violation", rule="unresolved_reference") from e
        else:
            # Drafts skip Registry resolution (their referents may not exist
            # yet); the declaration is stored lookup-only and the resolved
            # half fills at activation.
            source_entries = [
                EndpointRef(lookup_value=ref)
                for ref in endpoint_lookups(request.source_templates)
            ]
            target_entries = [
                EndpointRef(lookup_value=ref)
                for ref in endpoint_lookups(request.target_templates)
            ]

        # CASE-493: every schema reference (extends + nested template refs) must
        # pin an explicit version. Presence is enforced on all paths (nested via
        # the FieldDefinition model validator, extends here); existence of the
        # pinned (template_id, version) pairs is checked on non-draft writes —
        # drafts re-check at activation, when their referents must exist.
        await TemplateService._validate_pinned_versions(
            request.fields, request.extends, request.extends_version,
            check_existence=not is_draft,
        )
        # A chain the read path cannot resolve (too deep, a missing
        # ancestor) is refused here, before the Registry registers anything,
        # rather than accepted and then served as a 409. Drafts are checked at
        # activation; a restore trusts its archive, as it does for renames.
        if not is_draft and not is_restore:
            await TemplateService._refuse_unresolvable_inheritance(
                None, request.extends, request.extends_version
            )

        # Validate cross-namespace references (the two gates) over ALL
        # resolved schema refs — extends, field-level template/terminology
        # refs and an edge type's declared endpoints — skip for drafts
        # (re-checked at activation)
        if not is_draft:
            await TemplateService._refuse_inactive_template_refs([
                *resolved_template_ids,
                *TemplateService._endpoint_ids(source_entries, target_entries),
            ])
            await TemplateService._check_reference_isolation(
                namespace,
                template_ids=[
                    *resolved_template_ids,
                    *TemplateService._endpoint_ids(source_entries, target_entries),
                ],
                terminology_ids=resolved_terminology_ids,
                extends_namespace=parent_namespace,
            )

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register the template's identity with the Registry — ALWAYS,
        # including restore mode (pre-assigned template_id + version). Every
        # later resolution of a template_id (activation, references, synonym
        # lookup) goes through the Registry, so an unregistered pre-assigned
        # ID makes a restored template unresolvable: created but unusable.
        # The composite key {ns, type, value} is the template's registered
        # identity; the Registry upserts on it. The Registry also honors a
        # provided entry_id and errors loudly if that ID already exists
        # ("restore requires a clean target"), which is the correct
        # double-restore behavior.
        client = get_registry_client()
        template_id, reg_status = await client.register_template(
            created_by=actor,
            namespace=namespace,
            value=request.value,
            entry_id=request.template_id,
        )
        if reg_status == "already_exists":
            if is_restore:
                # The value's identity resolved to an entry that is NOT the
                # archive's pre-assigned ID (an identical ID would have
                # errored above as an entry_id collision). Restoring onto a
                # target that already owns this value under a different
                # identity is a dirty-target restore — fail loudly rather
                # than silently re-parenting the archive's versions.
                raise TemplateError(
                    f"Restore conflict: value '{request.value}' in namespace "
                    f"'{namespace}' is already registered as '{template_id}', "
                    f"but the archive carries '{request.template_id}'. "
                    f"Restore requires a clean target.", "already_exists", rule="restore_conflict"
                )
            # Non-restore create of an existing value: the upsert decision
            # (unchanged / new version / fork rejection) is made one level up
            # in create_templates_with_conflict_policy, which checks for the
            # existing template BEFORE calling here. Reaching this branch
            # means a concurrent create won the race — surface the same
            # error the pre-check raises, so the caller can retry as an
            # upsert.
            raise TemplateError(
                f"Template with value '{request.value}' already exists in namespace '{namespace}'", "already_exists"
            )
        version = request.version if is_restore else 1

        # Create template document
        template = Template(
            namespace=namespace,
            template_id=template_id,
            version=version,
            value=request.value,
            label=request.label,
            description=request.description,
            extends=request.extends,
            extends_version=request.extends_version,
            identity_fields=request.identity_fields,
            header_fields=request.header_fields,
            renames=request.renames,
            usage=request.usage,
            source_templates=source_entries,
            target_templates=target_entries,
            versioned=request.versioned,
            fields=request.fields,
            rules=request.rules,
            metadata=request.metadata or TemplateMetadata(),
            reporting=request.reporting,
            status=request.status or "active",
            created_by=actor,
        )
        # The identity is claimed. A create that fails from here on releases
        # an entry it minted, so the next create of the same (namespace,
        # value) does not collide with an entry that owns no template.
        try:
            await TemplateService._store_new_template(template, bool(is_restore), actor)
        except Exception:
            if reg_status == "created":
                await TemplateService._release_minted_entry(template_id)
            raise

        # Publish template created event — skip for drafts
        if not is_draft:
            await publish_template_event(
                EventType.TEMPLATE_CREATED,
                TemplateService._template_to_event_payload(template),
                changed_by=actor
            )

        return TemplateService._to_template_response(template)

    @staticmethod
    def _refuse_inactive_parent(parent: Template, is_draft: bool) -> None:
        """An active template extends an active parent, as activation
        demands: a draft or retired parent is refused. ``parent`` is the
        parent's latest version, the one activation judges. A draft may
        extend a draft; activation checks it."""
        if not is_draft and parent.status != "active":
            raise TemplateError(
                f"Parent template '{parent.value}' is {parent.status}, not active",
                "reference_violation", rule="parent_not_active",
            )

    @staticmethod
    async def _refuse_inactive_template_refs(
        template_ids: list[str], previous: Any | None = None,
    ) -> None:
        """A template reference a non-draft write adds must name a template
        with an active version, as activation demands. The Registry resolves
        a draft's or a retired template's id like any other, so resolving is
        not enough. References ``previous`` already held are carried."""
        carried = (
            TemplateService._stored_reference_ids(previous) if previous is not None else set()
        )
        ids = set(template_ids) - carried
        if not ids:
            return
        active = set(await Template.distinct(
            "template_id", {"template_id": {"$in": sorted(ids)}, "status": "active"},
        ))
        inactive = sorted(ids - active)
        if inactive:
            raise TemplateError(
                f"Referenced template(s) {', '.join(inactive)} have no active "
                "version (a draft or a retired template); activate or reactivate "
                "them first",
                "reference_violation", rule="template_not_active",
            )

    @staticmethod
    async def _store_new_template(template: Template, is_restore: bool, actor: str) -> None:
        """Insert a just-registered template and its auto-synonym. A failed
        synonym takes the inserted row back out; either failure is raised."""
        await TemplateService._stamp_referent_paths(template)
        await template.insert()

        # Register auto-synonym for human-readable resolution
        # Only for version 1 (auto-synonym resolves to entity_id, stable across versions)
        # Restore mode registers it on EVERY version create: a restored template
        # must be resolvable by value no later than its own activation (edge
        # types resolve value-form source/target_templates there), and archives
        # may not contain version 1 at all (latest-only exports). Repeat
        # registrations of the same value are absorbed by the Registry's
        # idempotent already_exists path, which also lets the archive's later
        # synonym replay re-add the same key harmlessly.
        # On failure, roll back the MongoDB document and re-raise
        if template.version == 1 or is_restore:
            try:
                client = get_registry_client()
                await client.register_auto_synonym(
                    target_id=template.template_id,
                    namespace=template.namespace,
                    composite_key={
                        "ns": template.namespace,
                        "type": "template",
                        "value": template.value,
                    },
                    created_by=actor,
                )
            except RegistryError:
                logger.error(
                    "Auto-synonym registration failed for template %s — rolling back",
                    template.template_id,
                )
                await template.delete()
                raise

    @staticmethod
    async def _release_minted_entry(template_id: str) -> None:
        """Release a Registry entry this create minted and will not use.
        Best effort: a failure is logged, and the error that caused the
        rollback is what the caller reports."""
        try:
            await get_registry_client().hard_delete_entry(
                template_id, rollback_uncommitted=True,
            )
        except (RegistryError, httpx.HTTPError) as e:
            logger.error("Failed to release unused Registry entry %s: %s", template_id, e)

    @staticmethod
    async def _refuse_unresolvable_inheritance(
        template_id: str | None,
        extends: str | None,
        extends_version: int | None,
        overrides: dict[tuple[str, int], Template] | None = None,
    ) -> None:
        """Refuse a write whose pinned inheritance chain the read path could
        not resolve. A missing or unpinned ancestor is a reference_violation;
        a cycle or a chain deeper than the limit is invalid_state. The rule
        is the resolver's own (missing_parent, unpinned_parent,
        circular_inheritance, max_depth)."""
        if not extends:
            return
        try:
            await InheritanceService.walk_ancestors(
                template_id, extends, extends_version, overrides
            )
        except InheritanceError as e:
            code = (
                "reference_violation"
                if e.rule in ("missing_parent", "unpinned_parent")
                else "invalid_state"
            )
            raise TemplateError(str(e), code, rule=e.rule) from e

    @staticmethod
    async def _stamp_referent_paths(template: Template) -> None:
        """Set a version's server-owned referent_identity_paths before it is
        saved. Drafts stay unset: their parents and nested templates may not
        exist yet, and activation sets it."""
        if template.status == "draft":
            template.referent_identity_paths = None
            return
        template.referent_identity_paths = await TemplateService._referent_paths_or_refuse(
            template
        )

    @staticmethod
    async def _referent_paths_or_refuse(
        template: Template,
        overrides: dict[tuple[str, int], Template] | None = None,
    ) -> list[str]:
        """The version's referent identity paths, or a refused write when a
        parent or nested template on an identity path cannot be read (the
        answer would be a guess)."""
        try:
            return await referent_paths.referent_identity_paths(template, overrides)
        except InheritanceError as e:
            code = (
                "reference_violation"
                if e.rule in ("missing_parent", "unpinned_parent", "missing_nested_template")
                else "invalid_state"
            )
            raise TemplateError(str(e), code, rule=e.rule) from e

    @staticmethod
    async def _resolve_or_refuse(template: Template) -> Template:
        """The template with its inheritance resolved, or TemplateUnresolvable.

        A chain that cannot be resolved (missing or unpinned parent, a cycle,
        too deep) is an error the caller sees: documents must never be
        validated against the child's own fields as if they were the schema.
        """
        try:
            return await InheritanceService.resolve_template(template)
        except InheritanceError as exc:
            raise TemplateUnresolvable(
                f"Template '{template.value}' v{template.version} cannot be "
                f"resolved: {exc}. Its stored form is readable at the /raw "
                "routes.",
                exc.rule,
            ) from exc

    @staticmethod
    async def get_template(
        template_id: str | None = None,
        value: str | None = None,
        version: int | None = None,
        resolve_inheritance: bool = True,
        namespace: str | None = None
    ) -> TemplateResponse | None:
        """
        Get a template by ID or value.

        Args:
            template_id: Template ID (stable across versions)
            value: Template value (e.g., 'PERSON')
            version: Specific version number (default: latest)
            resolve_inheritance: Whether to resolve inheritance
            namespace: Namespace to search in (if None, searches globally by ID)

        Returns:
            Template if found, None otherwise
        """
        if template_id:
            # ID lookups can be global (for cross-namespace refs in open mode)
            query: dict = {"template_id": template_id}
            if namespace:
                query["namespace"] = namespace
            if version is not None:
                query["version"] = version
                template = await Template.find_one(query)
            else:
                # Return latest version (highest version number)
                results = await Template.find(query).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
                template = results[0] if results else None
        elif value:
            # Value lookups require namespace — no silent fallback to "wip"
            if not namespace:
                raise TemplateError("Namespace is required for value-based template lookup", "invalid_request", rule="namespace_required")
            query = {"namespace": namespace, "value": value}
            if version is not None:
                query["version"] = version
                template = await Template.find_one(query)
            else:
                results = await Template.find(query).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
                template = results[0] if results else None
        else:
            return None

        if not template:
            return None

        if resolve_inheritance and template.extends:
            template = await TemplateService._resolve_or_refuse(template)

        return TemplateService._to_template_response(template)

    @staticmethod
    async def get_template_raw(
        template_id: str | None = None,
        value: str | None = None,
        namespace: str | None = None,
    ) -> TemplateResponse | None:
        """
        Get a template by ID or value without inheritance resolution.

        Args:
            template_id: Template ID
            value: Template value
            namespace: Namespace (required for value-based lookups)

        Returns:
            Template as stored, without inheritance resolution
        """
        return await TemplateService.get_template(
            template_id=template_id,
            value=value,
            resolve_inheritance=False,
            namespace=namespace,
        )

    @staticmethod
    async def list_templates(
        status: str | None = None,
        extends: str | None = None,
        value: str | None = None,
        latest_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        ns_filter: dict | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[TemplateResponse], int]:
        """
        List templates with pagination.

        Args:
            status: Filter by status (draft, active, inactive)
            extends: Filter by parent template ID
            value: Filter by template value (shows all versions of that value)
            latest_only: If True, only return the latest version of each template
            page: Page number (1-indexed)
            page_size: Items per page
            ns_filter: Namespace filter dict from resolve_namespace_filter()

        Returns:
            Tuple of (templates, total_count)
        """
        query: dict = {}
        if ns_filter:
            query.update(ns_filter)
        if status:
            query["status"] = status
        if extends:
            query["extends"] = extends
        if value:
            query["value"] = value

        if latest_only and not value:
            # Use aggregation to get only the latest version of each
            # template. The group key is template_id — the stable canonical
            # handle for one (namespace, value) identity — NOT the bare
            # value: a value is unique only within a namespace, and grouping
            # by value collapses same-valued templates across namespaces
            # into one arbitrary survivor (after a remap restore, which
            # duplicates every value by construction, an unfiltered
            # latest_only list silently dropped one namespace's templates).
            sort_clauses = _build_template_sort(sort_by, sort_order)
            agg_sort = {f: (1 if d == SortDirection.ASCENDING else -1) for f, d in sort_clauses}
            pipeline = [
                {"$match": query} if query else {"$match": {}},
                {"$sort": {"value": 1, "version": -1}},
                {"$group": {
                    "_id": "$template_id",
                    "doc": {"$first": "$$ROOT"}
                }},
                {"$replaceRoot": {"newRoot": "$doc"}},
                {"$sort": agg_sort}
            ]

            # Get total count
            count_pipeline = [*pipeline, {"$count": "total"}]
            count_result = await Template.aggregate(count_pipeline).to_list()
            total = count_result[0]["total"] if count_result else 0

            # Get paginated results
            paginated_pipeline = [
                *pipeline,
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size},
            ]
            results = await Template.aggregate(paginated_pipeline).to_list()
            templates = [Template(**doc) for doc in results]
        else:
            total = await Template.find(query).count()
            skip = (page - 1) * page_size

            sort_clauses = _build_template_sort(sort_by, sort_order)
            templates = await Template.find(query) \
                .sort(sort_clauses) \
                .skip(skip) \
                .limit(page_size) \
                .to_list()

        return (
            [TemplateService._to_template_response(t) for t in templates],
            total
        )

    @staticmethod
    async def get_template_versions(
        value: str,
        ns_filter: dict | None = None
    ) -> list[TemplateResponse]:
        """
        Get all versions of a template by value.

        Args:
            value: Template value
            ns_filter: Namespace filter dict from resolve_namespace_filter()
                (CASE-579 — empty/None means unrestricted, superadmin only)

        Returns:
            List of all versions, sorted by version descending (newest first)
        """
        query: dict = {"value": value}
        if ns_filter:
            query.update(ns_filter)
        templates = await Template.find(query) \
            .sort([("version", SortDirection.DESCENDING)]) \
            .to_list()

        return [TemplateService._to_template_response(t) for t in templates]

    @staticmethod
    async def get_template_versions_by_id(
        template_id: str
    ) -> list[TemplateResponse]:
        """
        Get all versions of a template by its template_id (CASE-497).

        A template_id is namespace-scoped by the Registry and stable across
        versions, so this is unambiguous and needs no namespace param — the
        cleanest call when the caller already holds the id.

        Returns:
            List of all versions, sorted by version descending (newest first)
        """
        templates = await Template.find({"template_id": template_id}) \
            .sort([("version", SortDirection.DESCENDING)]) \
            .to_list()
        return [TemplateService._to_template_response(t) for t in templates]

    @staticmethod
    async def get_template_by_value_and_version(
        value: str,
        version: int,
        resolve_inheritance: bool = True,
        namespace: str | None = None,
    ) -> TemplateResponse | None:
        """
        Get a specific version of a template by value and version number.

        Args:
            value: Template value
            version: Version number
            resolve_inheritance: Whether to resolve inheritance
            namespace: Restrict to this namespace — a value is unique only
                within a namespace, so omitting it is ambiguous across
                namespaces (CASE-497). None searches all namespaces.

        Returns:
            Template if found, None otherwise
        """
        query: dict = {"value": value, "version": version}
        if namespace is not None:
            query["namespace"] = namespace
        template = await Template.find_one(query)
        if not template:
            return None

        if resolve_inheritance and template.extends:
            template = await TemplateService._resolve_or_refuse(template)

        return TemplateService._to_template_response(template)

    @staticmethod
    def _template_has_changed(
        original: Template,
        request: UpdateTemplateRequest
    ) -> bool:
        """
        Check if the update request would change the template.

        Compares all changeable fields between the original and the request.
        """
        import json

        # Check each field that can be updated
        if request.value is not None and request.value != original.value:
            return True
        if request.label is not None and request.label != original.label:
            return True
        if request.description is not None and request.description != original.description:
            return True
        if request.extends is not None and request.extends != original.extends:
            return True
        if request.extends_version is not None and request.extends_version != original.extends_version:
            return True
        if request.identity_fields is not None and request.identity_fields != original.identity_fields:
            return True
        if request.header_fields is not None and request.header_fields != original.header_fields:
            return True
        if request.renames is not None and request.renames != original.renames:
            return True

        # Compare fields using JSON serialization
        if request.fields is not None:
            original_fields_json = json.dumps(
                [
                    f.model_dump()
                    for f in TemplateService._without_endpoint_projection(
                        original.usage, original.fields
                    )
                ],
                sort_keys=True, default=str
            )
            new_fields_json = json.dumps(
                [f.model_dump() for f in request.fields],
                sort_keys=True, default=str
            )
            if original_fields_json != new_fields_json:
                return True

        # Compare rules using JSON serialization
        if request.rules is not None:
            original_rules_json = json.dumps(
                [r.model_dump() for r in original.rules],
                sort_keys=True, default=str
            )
            new_rules_json = json.dumps(
                [r.model_dump() for r in request.rules],
                sort_keys=True, default=str
            )
            if original_rules_json != new_rules_json:
                return True

        # Compare metadata
        if request.metadata is not None:
            original_meta_json = json.dumps(
                original.metadata.model_dump() if original.metadata else {},
                sort_keys=True, default=str
            )
            new_meta_json = json.dumps(
                request.metadata.model_dump() if request.metadata else {},
                sort_keys=True, default=str
            )
            if original_meta_json != new_meta_json:
                return True

        # Compare reporting config
        if request.reporting is not None:
            original_reporting_json = json.dumps(
                original.reporting.model_dump() if original.reporting else {},
                sort_keys=True, default=str
            )
            new_reporting_json = json.dumps(
                request.reporting.model_dump() if request.reporting else {},
                sort_keys=True, default=str
            )
            if original_reporting_json != new_reporting_json:
                return True

        return False

    @staticmethod
    def _only_reporting_changed(
        original: "Template",
        request: "UpdateTemplateRequest",
    ) -> bool:
        """True when reporting config differs but no schema field does."""
        import json

        if request.reporting is None:
            return False
        orig_json = json.dumps(
            original.reporting.model_dump() if original.reporting else {},
            sort_keys=True, default=str,
        )
        new_json = json.dumps(
            request.reporting.model_dump(), sort_keys=True, default=str,
        )
        if orig_json == new_json:
            return False
        saved = request.reporting
        request.reporting = None
        schema_changed = TemplateService._template_has_changed(original, request)
        request.reporting = saved
        return not schema_changed

    # CASE-406 — Field-level reference properties: prop_name -> entity_type.
    # When comparing two FieldDefinitions, these properties must resolve
    # through the Registry before comparison so that value-form and UUID-form
    # references to the same entity compare equal. The principle: synonyms
    # MUST work identically to canonical IDs at every comparison site
    # (Vision.md §"References Must Resolve", docs/design/synonym-resolution-gaps.md).
    _FIELD_REF_SCALAR_PROPS: ClassVar = {
        "terminology_ref": "terminology",
        "template_ref": "template",
        "array_terminology_ref": "terminology",
        "array_template_ref": "template",
    }
    _FIELD_REF_LIST_PROPS: ClassVar = {
        "target_templates": "template",
        "target_terminologies": "terminology",
    }
    # Populated during resolution (inheritance walking), never stored.
    # Excluding them from comparison is a precondition for "stored ==
    # round-tripped" equality.
    _FIELD_INHERITANCE_PROPS = frozenset({"inherited", "inherited_from"})

    @staticmethod
    async def _refs_equivalent(
        a: str | None,
        b: str | None,
        entity_type: str,
        namespace: str,
    ) -> bool:
        """Two reference strings are equivalent iff they resolve to the same
        canonical entity. None == None; None != any non-None.

        Trivial byte-equality short-circuits the Registry call. If both
        sides are non-None and not byte-equal, both are resolved; if either
        resolve fails, the references are treated as different (conservative
        — prefer a noisy false positive over a silent false negative).
        """
        if a == b:
            return True  # Covers None==None and identical-string cases.
        if a is None or b is None:
            return False  # One side has a ref, the other doesn't.
        try:
            a_canonical = await resolve_entity_id(a, entity_type, namespace)
            b_canonical = await resolve_entity_id(b, entity_type, namespace)
        except EntityNotFoundError:
            return False
        return a_canonical == b_canonical

    @staticmethod
    async def _ref_lists_equivalent(
        a: list[str] | None,
        b: list[str] | None,
        entity_type: str,
        namespace: str,
    ) -> bool:
        """Two reference-list properties are equivalent iff their element-wise
        canonical IDs form the same set. Order-insensitive (these lists are
        treated as sets semantically — target_templates ['A','B'] means
        "allowed: A or B", same as ['B','A']).

        None and empty list compare equal — both mean "no constraint."
        """
        a_list = list(a or [])
        b_list = list(b or [])
        if set(a_list) == set(b_list):
            return True  # Trivial set-equal (incl. both empty).
        try:
            all_refs = list({*a_list, *b_list})
            resolved = await resolve_entity_ids(all_refs, entity_type, namespace) if all_refs else {}
        except EntityNotFoundError:
            return False
        a_canonical = {resolved[r] for r in a_list}
        b_canonical = {resolved[r] for r in b_list}
        return a_canonical == b_canonical

    @staticmethod
    async def _compare_field_definitions(
        old: FieldDefinition,
        new: FieldDefinition,
        namespace: str,
    ) -> bool:
        """True if two FieldDefinitions are semantically equivalent.

        Reference-typed properties (terminology_ref / template_ref /
        target_templates / target_terminologies / array_*) are resolved
        through Registry before comparison — value-form ≡ UUID-form ≡ any
        synonym for the same referent. Inheritance-populated properties
        (inherited, inherited_from) are excluded; they are not part of the
        stored shape.

        All other properties compare via structural (Pydantic dict) equality.
        Replaces the previous JSON-byte equality which silently flagged
        value↔UUID asymmetry as `modified_existing` (CASE-406).
        """
        for prop, entity_type in TemplateService._FIELD_REF_SCALAR_PROPS.items():
            if not await TemplateService._refs_equivalent(
                getattr(old, prop), getattr(new, prop), entity_type, namespace,
            ):
                return False
        for prop, entity_type in TemplateService._FIELD_REF_LIST_PROPS.items():
            if not await TemplateService._ref_lists_equivalent(
                getattr(old, prop), getattr(new, prop), entity_type, namespace,
            ):
                return False
        # Everything else: structural equality, excluding reference props
        # (already compared above) and inheritance props (resolution-time
        # only, never stored).
        exclude = (
            set(TemplateService._FIELD_REF_SCALAR_PROPS)
            | set(TemplateService._FIELD_REF_LIST_PROPS)
            | TemplateService._FIELD_INHERITANCE_PROPS
        )
        return old.model_dump(exclude=exclude) == new.model_dump(exclude=exclude)

    @staticmethod
    async def _compare_template_relationship_refs(
        existing: Template,
        proposed: CreateTemplateRequest,
        namespace: str,
    ) -> dict | None:
        """Template-level reference comparison for relationship templates.

        Edge types carry `source_templates` and `target_templates` at the
        template level (independent of any field). Both arrays follow the
        same value↔UUID-equivalence principle as field-level reference
        properties.

        Returns a diff dict with raw before/after lists when the resolved
        sets differ, or None when equivalent. Empty/None on both sides
        compares equal — a non-relationship template legitimately has both
        empty.
        """
        diff: dict[str, dict] = {}

        def _stored_side(entries: list[EndpointRef] | None) -> list[str]:
            # An entry's comparable handle is its resolved half; a legacy
            # lookup-only entry falls back to its lookup, which
            # _ref_lists_equivalent resolves like any other string.
            return [e.resolved or e.lookup_value for e in (entries or [])]

        if not await TemplateService._ref_lists_equivalent(
            _stored_side(existing.source_templates),
            endpoint_lookups(proposed.source_templates),
            "template",
            namespace,
        ):
            diff["source_templates"] = {
                "old": _stored_side(existing.source_templates),
                "new": endpoint_lookups(proposed.source_templates),
            }
        if not await TemplateService._ref_lists_equivalent(
            _stored_side(existing.target_templates),
            endpoint_lookups(proposed.target_templates),
            "template",
            namespace,
        ):
            diff["target_templates"] = {
                "old": _stored_side(existing.target_templates),
                "new": endpoint_lookups(proposed.target_templates),
            }

        return diff or None

    @staticmethod
    async def compute_template_compatibility(
        existing: Template,
        proposed: CreateTemplateRequest | Template,
        namespace: str,
    ) -> tuple[str, dict]:
        """
        Compare a proposed CreateTemplateRequest against an existing Template.

        `proposed` may also be a stored Template: the update path compares two
        already-minted versions after the fact to describe a version event.
        Only the shape both types share is read (fields, identity_fields,
        source/target_templates).

        Returns a (verdict, diff) tuple where verdict is one of:
        - "identical": no schema differences — proposed matches existing exactly
        - "compatible": only differences are added optional fields (mandatory=false)
        - "incompatible": any other change (removed field, type change, made-required,
          identity_fields change, modified existing field, added required field,
          changed source/target_templates on a relationship template)

        Reference-typed properties are resolved through Registry before
        comparison — synonyms and canonical IDs compare equal at every
        site. The async signature (CASE-406) replaces the prior synchronous
        byte-equality implementation which produced phantom `modified_existing`
        on stored-canonical ↔ submitted-value-form pairs.

        Used by POST /templates?on_conflict=validate to decide whether to
        silently adopt the existing template, bump the version, or reject
        with a structured diff.
        """
        # An edge type's source_ref/target_ref constraint is derived from the
        # endpoint declaration (compared below), never declared on the fields:
        # whatever either side carries there is not part of the schema. The
        # stored side can carry it from versions minted before the update
        # path stripped it; the proposed side whenever a caller re-posts the
        # served shape.
        existing_fields = {
            f.name: f
            for f in TemplateService._without_endpoint_projection(existing.usage, existing.fields)
        }
        proposed_fields = {
            f.name: f
            for f in TemplateService._without_endpoint_projection(existing.usage, proposed.fields)
        }

        existing_names = set(existing_fields)
        proposed_names = set(proposed_fields)

        added_names = proposed_names - existing_names
        removed_names = existing_names - proposed_names
        common_names = existing_names & proposed_names

        added_optional: list[str] = []
        added_required: list[str] = []
        for name in sorted(added_names):
            f = proposed_fields[name]
            if f.mandatory and f.default_value is None:
                added_required.append(name)
            else:
                added_optional.append(name)

        changed_type: list[dict] = []
        made_required: list[str] = []
        modified_existing: list[str] = []
        for name in sorted(common_names):
            old = existing_fields[name]
            new = proposed_fields[name]
            if old.type != new.type:
                changed_type.append({
                    "name": name,
                    "old_type": old.type.value if hasattr(old.type, "value") else str(old.type),
                    "new_type": new.type.value if hasattr(new.type, "value") else str(new.type),
                })
                continue
            if (not old.mandatory) and new.mandatory:
                made_required.append(name)
                continue
            # CASE-406: semantic field equality, not JSON byte equality.
            if not await TemplateService._compare_field_definitions(old, new, namespace):
                modified_existing.append(name)

        identity_changed: dict | None = None
        if list(existing.identity_fields or []) != list(proposed.identity_fields or []):
            identity_changed = {
                "old": list(existing.identity_fields or []),
                "new": list(proposed.identity_fields or []),
            }

        # CASE-406: template-level relationship-ref comparison (source/target_templates).
        relationship_refs_changed = await TemplateService._compare_template_relationship_refs(
            existing, proposed, namespace,
        )

        diff: dict = {
            "added_optional": added_optional,
            "added_required": added_required,
            "removed": sorted(removed_names),
            "changed_type": changed_type,
            "made_required": made_required,
            "modified_existing": modified_existing,
            "identity_changed": identity_changed,
            "relationship_refs_changed": relationship_refs_changed,
        }

        is_incompatible = bool(
            added_required
            or removed_names
            or changed_type
            or made_required
            or modified_existing
            or identity_changed
            or relationship_refs_changed
        )
        if is_incompatible:
            return "incompatible", diff
        if added_optional:
            return "compatible", diff
        return "identical", diff

    @staticmethod
    async def update_template(
        template_id: str,
        request: UpdateTemplateRequest
    ) -> TemplateUpdateResponse | None:
        """
        Update a template by creating a new version.

        Creates a NEW template document with incremented version, rather than
        modifying in-place. This allows multiple versions to exist simultaneously,
        supporting gradual migration of documents to new template versions.

        If no changes are detected, returns the current template info without
        creating a new version.

        Args:
            template_id: Template to update (any version)
            request: Update request

        Returns:
            Update response indicating if a new version was created, or None if not found
        """
        # Find the latest version of this template
        originals = await Template.find({"template_id": template_id}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        original = originals[0] if originals else None
        if not original:
            return None

        # Same rule as create: an edge type's endpoint constraint is derived on
        # serve, so what a caller sends for it is neither compared nor stored.
        TemplateService._strip_endpoint_projection(original.usage, request.fields)

        # Check if anything has actually changed
        if not TemplateService._template_has_changed(original, request):
            # No changes - return current template info
            return TemplateUpdateResponse(
                template_id=original.template_id,
                value=original.value,
                version=original.version,
                is_new_version=False,
                previous_version=None
            )

        # Reporting config is operational metadata, not document schema.
        # A change to sync_enabled, sync_strategy, etc. mutates in place
        # without a version bump — same pattern as add_edge_type_endpoints.
        if TemplateService._only_reporting_changed(original, request):
            TemplateService._validate_full_text_indexed_constraints(
                original.fields, request.reporting,
            )
            actor = get_identity_string()
            original.reporting = request.reporting
            original.updated_at = datetime.now(UTC)
            original.updated_by = actor
            await original.save()
            await publish_template_event(
                EventType.TEMPLATE_UPDATED,
                TemplateService._template_to_event_payload(original),
                changed_by=actor,
            )
            return TemplateUpdateResponse(
                template_id=original.template_id,
                value=original.value,
                version=original.version,
                is_new_version=False,
                previous_version=None,
            )

        # The template's name IS its identity — a rename is a fork (a new
        # template), never a new version. Versions under different names
        # sharing one ID would break name-based resolution and the
        # registered {ns, type, value} identity key.
        if request.value is not None and request.value != original.value:
            raise TemplateError(
                f"value is immutable: renaming '{original.value}' to "
                f"'{request.value}' is a fork, not a version — create a new "
                f"template with the new value instead.", "immutable_property", rule="value"
            )
        new_value = original.value

        # identity_fields are immutable across versions: document identity
        # must stay comparable across the whole version catalog (the
        # cross-version upsert hashes documents against one declaration), so
        # a changed identity is a different kind of thing — a fork. Without
        # this guard the platform mints versions whose documents silently
        # re-key on next write (the parallel-orphan failure shape).
        if (
            request.identity_fields is not None
            and list(request.identity_fields) != list(original.identity_fields or [])
        ):
            raise TemplateError(
                f"identity_fields are immutable across versions of "
                f"'{original.value}' (existing: {list(original.identity_fields or [])}, "
                f"requested: {list(request.identity_fields)}). Changing identity "
                f"is a fork — create a new template with a new value instead.", "identity_fields_immutable"
            )

        # Calculate new version number (max version for this value + 1),
        # scoped to the namespace — (namespace, value, version) is the
        # uniqueness key; an unscoped query would inflate version numbers
        # from same-named templates in other namespaces.
        max_version_template = await Template.find(
            {"namespace": original.namespace, "value": original.value}
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        new_version = max_version_template[0].version + 1 if max_version_template else 1

        # References the original already held are carried; every other one
        # passes the inbound gate.
        gate = InboundGate(original.namespace, carried=carried_ids(original))

        # Validate extends if changing
        extends_value = request.extends if request.extends is not None else original.extends
        if extends_value and extends_value != original.extends:
            submitted_extends = extends_value
            # CASE-432: resolve `extends` through the Registry first (synonym →
            # canonical), then fall through to the legacy lookups on a miss.
            with contextlib.suppress(EntityNotFoundError):
                extends_value = await resolve_entity_id(extends_value, "template", original.namespace)
            parent_results = await Template.find({"template_id": extends_value}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
            parent = parent_results[0] if parent_results else None
            if parent and await gate.refuses(parent.namespace, parent.template_id):
                # A parent the writer may not reference is not found, and
                # what follows is exactly what a missing parent gets.
                parent = None
                extends_value = submitted_extends
            if not parent:
                # A bare value names a template in the template's own
                # namespace, as on create.
                parent_results = await Template.find(
                    {"namespace": original.namespace, "value": extends_value}
                ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
                parent = parent_results[0] if parent_results else None
                if parent:
                    extends_value = parent.template_id
                else:
                    raise TemplateError(f"Parent template '{extends_value}' not found", "reference_violation", rule="parent_not_found")
            TemplateService._refuse_inactive_parent(parent, original.status == "draft")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Normalize field references to canonical IDs (same as create_template)
        new_fields = request.fields if request.fields is not None else original.fields
        updated_template_ids: list[str] = []
        updated_terminology_ids: list[str] = []
        if request.fields is not None:
            try:
                updated_template_ids, updated_terminology_ids = (
                    await TemplateService._normalize_field_references(
                        new_fields, original.namespace, gate=gate,
                    )
                )
            except EntityNotFoundError as e:
                raise TemplateError(str(e), "reference_violation", rule="unresolved_reference") from e

        if original.status != "draft":
            await TemplateService._refuse_inactive_template_refs(
                updated_template_ids, previous=original,
            )
        # Isolation check over the updated refs + the (possibly unchanged)
        # parent — same boundary as create; extends_value is canonical here
        await TemplateService._check_reference_isolation(
            original.namespace,
            template_ids=updated_template_ids,
            terminology_ids=updated_terminology_ids,
            extends_template_id=extends_value,
            previous=original,
        )

        # CASE-493: enforce mandatory, pinned versions on the merged schema
        # references (extends + nested template refs). extends_value is the
        # resolved canonical id of the (possibly unchanged) parent; the merged
        # extends_version falls back to the original when not being changed.
        new_extends_version = (
            request.extends_version if request.extends_version is not None
            else original.extends_version
        )
        await TemplateService._validate_pinned_versions(
            new_fields, extends_value, new_extends_version,
            check_existence=original.status != "draft",
        )
        # The new version's whole pinned chain must resolve (cycle, depth,
        # a missing ancestor), walked exactly as the read path walks it.
        if original.status != "draft":
            await TemplateService._refuse_unresolvable_inheritance(
                original.template_id, extends_value or None, new_extends_version
            )

        # Validate full_text_indexed constraints against the merged final
        # state — fields may add/remove the flag, reporting.sync_enabled
        # may flip; both can break the invariant.
        new_reporting = (
            request.reporting if request.reporting is not None else original.reporting
        )
        TemplateService._validate_full_text_indexed_constraints(
            new_fields, new_reporting
        )

        # Reject metadata.<x> in declarative slots after the merge —
        # an update may newly introduce identity_fields=["metadata.x"]
        # or flip full_text_indexed on a metadata-prefixed field.
        new_identity_fields = (
            request.identity_fields
            if request.identity_fields is not None
            else original.identity_fields
        )
        TemplateService._validate_no_metadata_in_declarative_slots(
            new_identity_fields, new_fields
        )

        # Reject versioned:false + empty identity_fields after the merge (CASE-478).
        # versioned is immutable (preserved from original below), but identity_fields
        # can be edited — so an update could empty it on a versioned:false template;
        # catch that here, not just at create.
        TemplateService._validate_versioned_requires_identity(
            original.versioned, new_identity_fields
        )
        TemplateService.validate_fields_for_write(new_fields)
        await TemplateService._validate_relationship_template_shape(
            original.usage, original.source_templates, original.target_templates,
            new_fields,
        )
        TemplateService.validate_rules_for_write(
            request.rules if request.rules is not None else original.rules
        )

        # Declared renames for this new version are validated against the
        # version they rename FROM (the current latest) and the merged new
        # field set. Renames are per-version declarations — they are never
        # inherited from the previous version.
        TemplateService._validate_renames(
            request.renames, original.fields, new_fields, new_identity_fields
        )

        # Stable ID: reuse original template_id (no Registry call for updates)
        # Create new template document for this version. usage,
        # versioned, source_templates, and target_templates are
        # immutable after creation — preserve from original.
        new_template = Template(
            namespace=original.namespace,
            template_id=original.template_id,
            value=new_value,
            label=request.label if request.label is not None else original.label,
            description=request.description if request.description is not None else original.description,
            version=new_version,
            extends=extends_value if extends_value else None,
            extends_version=request.extends_version if request.extends_version is not None else original.extends_version,
            identity_fields=request.identity_fields if request.identity_fields is not None else original.identity_fields,
            header_fields=request.header_fields if request.header_fields is not None else original.header_fields,
            renames=request.renames,
            usage=original.usage,
            source_templates=original.source_templates,
            target_templates=original.target_templates,
            versioned=original.versioned,
            fields=new_fields,
            rules=request.rules if request.rules is not None else original.rules,
            metadata=request.metadata if request.metadata is not None else original.metadata,
            reporting=request.reporting if request.reporting is not None else original.reporting,
            # A draft's next version is a draft: it becomes active only
            # through activation, which checks what a draft write skips.
            status="draft" if original.status == "draft" else "active",
            created_at=datetime.now(UTC),
            created_by=actor,
            updated_at=datetime.now(UTC),
            updated_by=actor,
        )
        await TemplateService._stamp_referent_paths(new_template)
        was_retired = await Template.find_one(
            {"template_id": original.template_id, "status": "active"}
        ) is None
        await new_template.insert()
        if was_retired:
            await TemplateService._sync_registry_lifecycle(original.template_id, actor)

        # Publish template updated event — skip for drafts, as create does
        if new_template.status != "draft":
            await publish_template_event(
                EventType.TEMPLATE_UPDATED,
                TemplateService._template_to_event_payload(new_template),
                changed_by=actor
            )

        return TemplateUpdateResponse(
            template_id=original.template_id,
            value=new_value,
            version=new_version,
            is_new_version=True,
            previous_version=original.version
        )

    @staticmethod
    async def delete_template(
        template_id: str,
        updated_by: str | None = None,  # Deprecated: uses authenticated identity
        version: int | None = None,
        hard_delete: bool = False,
    ) -> bool:
        """
        Delete a template version. Soft-delete by default, hard-delete if requested
        and namespace deletion_mode is 'full'.

        Args:
            template_id: Template to delete
            updated_by: Deprecated - uses authenticated identity
            version: Specific version to delete (None = latest for soft, all for hard)
            hard_delete: Permanently remove (requires namespace deletion_mode='full')

        Returns:
            True if deleted, False if not found
        """
        # Check if any templates extend this one (blocks both soft and hard delete)
        children = await InheritanceService.get_children(template_id)
        if children:
            raise TemplateError(
                f"Cannot delete template: {len(children)} template(s) extend it", "invalid_state", rule="has_children"
            )

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        if hard_delete:
            # Validate namespace deletion_mode
            # Need any version to get the namespace
            any_version = await Template.find_one({"template_id": template_id})
            if not any_version:
                return False

            client = get_registry_client()
            deletion_mode = await client.get_namespace_deletion_mode(any_version.namespace)
            if deletion_mode != "full":
                raise TemplateError(
                    f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')", "invalid_state", rule="deletion_mode_retain"
                )

            if version is not None:
                # Version-specific hard-delete
                target = await Template.find_one({"template_id": template_id, "version": version})
                if not target:
                    return False

                event_payload = TemplateService._template_to_event_payload(target)
                event_payload["hard_delete"] = True
                event_payload["version"] = version

                await target.delete()

                # Check if any versions remain
                remaining = await Template.find({"template_id": template_id}).count()
                if remaining == 0:
                    try:
                        await client.hard_delete_entry(template_id, updated_by=actor)
                    except Exception as e:
                        logger.warning(f"Failed to hard-delete Registry entry for template {template_id}: {e}")
            else:
                # All versions hard-delete
                event_payload = TemplateService._template_to_event_payload(any_version)
                event_payload["hard_delete"] = True

                await Template.find({"template_id": template_id}).delete()

                try:
                    await client.hard_delete_entry(template_id, updated_by=actor)
                except Exception as e:
                    logger.warning(f"Failed to hard-delete Registry entry for template {template_id}: {e}")

            # Publish template deleted event
            await publish_template_event(
                EventType.TEMPLATE_DELETED,
                event_payload,
                changed_by=actor,
            )
            return True

        # SOFT DELETE path (existing behavior)
        if version is not None:
            template = await Template.find_one({"template_id": template_id, "version": version})
        else:
            results = await Template.find({"template_id": template_id}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
            template = results[0] if results else None
        if not template:
            return False

        if template.status == "inactive":
            return True  # Already inactive

        # Deactivate template
        template.status = "inactive"
        template.updated_at = datetime.now(UTC)
        template.updated_by = actor
        await template.save()
        await TemplateService._sync_registry_lifecycle(template_id, actor)

        # Publish template deleted event
        await publish_template_event(
            EventType.TEMPLATE_DELETED,
            TemplateService._template_to_event_payload(template),
            changed_by=actor
        )

        return True

    @staticmethod
    async def reactivate_template(
        template_id: str,
        version: int,
    ) -> TemplateResponse:
        """Reactivate a soft-deleted (inactive) template version (CASE-490).

        The symmetric inverse of deactivate. Flips a specific inactive
        version back to active so documents pinned to it can be updated
        again. Version is required and version-specific — there is no
        "latest" default, because the caller is targeting a known frozen
        version (`activate_template` is draft-only and cannot address this).

        Idempotent: an already-active version is returned unchanged. A draft
        version is rejected — drafts are activated, not reactivated. The
        version must pass what activation demands (its references active and
        readable, its chain resolvable) and is brought to its active form the
        same way, so a soft-deleted draft cannot become active unchecked and
        a version whose parent or referents have since retired is refused.

        Raises:
            ValueError: version not found, or the version is a draft.
        """
        template = await Template.find_one(
            {"template_id": template_id, "version": version}
        )
        if not template:
            raise TemplateError(
                f"Template '{template_id}' version {version} not found", "not_found"
            )

        if template.status == "draft":
            raise TemplateError(
                f"Template '{template_id}' version {version} is 'draft', not "
                "'inactive'. Use activate_template for drafts; reactivate only "
                "restores a soft-deleted (inactive) version.", "invalid_state", rule="not_inactive"
            )

        if template.status == "active":
            # Idempotent: already active, served like every other response.
            return TemplateService._to_template_response(template)

        errors, _warnings = await TemplateService._validate_activation_set(
            [template], template.namespace
        )
        if errors:
            raise TemplateError(
                f"Template '{template.value}' version {version} cannot be "
                "reactivated: " + "; ".join(e.message for e in errors),
                "validation_failed", rule="not_activatable",
            )
        await TemplateService._prepare_activation([template], template.namespace)

        actor = get_identity_string()
        template.status = "active"
        template.updated_at = datetime.now(UTC)
        template.updated_by = actor
        await template.save()
        await TemplateService._sync_registry_lifecycle(template_id, actor)

        await publish_template_event(
            EventType.TEMPLATE_UPDATED,
            TemplateService._template_to_event_payload(template),
            changed_by=actor,
        )

        return TemplateService._to_template_response(template)

    @staticmethod
    async def _sync_registry_lifecycle(template_id: str, actor: str | None) -> None:
        """Tell the Registry whether this template is retired.

        A template keeps one Registry entry across its versions, so it is
        retired only when no version is active: then its synonyms are
        released (they neither block another template from claiming the key
        nor resolve), and they are claimed again as soon as a version is
        active. Its {ns, type, value} key is the entry's primary key, never
        released — a retired template stays addressable by value, to
        reactivate it. The
        template write has already committed, so a failed call is logged;
        /maintenance/reconcile-synonym-lifecycle repairs it.
        """
        active = await Template.find_one({"template_id": template_id, "status": "active"})
        for warning in await sync_entry_lifecycle(
            get_registry_client(), template_id,
            retired=active is None, actor=actor, noun="template",
        ):
            logger.warning("Template %s: %s", template_id, warning)

    @staticmethod
    async def add_edge_type_endpoints(
        template_id: str,
        add_source_templates: list[str | EndpointRefIn] | None = None,
        add_target_templates: list[str | EndpointRefIn] | None = None,
        namespace: str | None = None,
    ) -> TemplateResponse:
        """Additively widen an edge type's allowed endpoint set (CASE-515).

        An edge type's endpoints — the template-level ``source_templates`` /
        ``target_templates`` two-half entries, from which the ``source_ref`` /
        ``target_ref`` constraint is projected at serve — are otherwise
        frozen. The only previous way
        to add a legal endpoint was delete+recreate, which strands every existing
        edge. This op adds endpoint template(s) IN PLACE on the latest active
        version, preserving every existing edge:

        - **Additive-only.** The resulting set is a superset of the current one;
          this op never removes (removal is the non-monotonic, edge-stranding
          direction, and is out of scope — endpoints are append-only).
        - **Referential integrity preserved.** Each new endpoint resolves through
          the Registry (``bypass_cache=True`` — write path, CASE-56) and must be a
          real template, exactly like the create path.
        - **Monotonic & cheap.** Widening can never invalidate an existing edge
          (its endpoints stay in the superset), so there is no per-edge
          revalidation, no reindex (the relationship Mongo indexes are generic on
          ``data.source_ref`` / ``data.target_ref``), and no reporting schema
          change (``source_ref_id`` / ``target_ref_id`` are generic columns).
        - **Idempotent.** Adding an already-allowed endpoint is a no-op.

        Raises:
            ValueError: no active relationship template found, the template is not
                a relationship template, or a new endpoint template doesn't exist.
        """
        add_source = endpoint_lookups(add_source_templates)
        add_target = endpoint_lookups(add_target_templates)
        if not add_source and not add_target:
            raise TemplateError(
                "add_edge_type_endpoints requires add_source_templates and/or "
                "add_target_templates", "invalid_request", rule="no_endpoints"
            )

        # Resolve the edge type to its latest ACTIVE version — that is the version
        # new edges resolve against. Accept a canonical id or a value/synonym.
        resolved_id = template_id
        with contextlib.suppress(EntityNotFoundError):
            resolved_id = await resolve_entity_id(
                template_id, "template", namespace or "wip"
            )
        by_id = await Template.find(
            {"template_id": resolved_id, "status": "active"}
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        template = by_id[0] if by_id else None
        if not template:
            # A bare value names a template in the caller's namespace only.
            by_value = await Template.find(
                {"value": template_id, "namespace": namespace, "status": "active"}
            ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
            template = by_value[0] if by_value else None
        if not template:
            raise TemplateError(
                f"No active template found for '{template_id}'", "not_found"
            )

        if template.usage != TemplateUsage.RELATIONSHIP:
            raise TemplateError(
                f"Template '{template.value}' is usage='{template.usage.value}', "
                "not 'relationship'. Endpoint widening only applies to edge types.", "invalid_state", rule="not_relationship"
            )

        # Resolve the new endpoints to canonical IDs (existence-checked). This is a
        # write path — bypass the resolver cache so a stale entry can't pin a dead
        # ID into the endpoint list (CASE-56).
        # An endpoint the edge type already declares is carried; a new one
        # passes the inbound gate, and a refused one is not found.
        ns = template.namespace
        # A legacy lookup-only entry is resolved first (and healed in place,
        # the "fills at the next write" promise for rows that predate the
        # two-half shape), so the endpoints the edge type already declares
        # are carried in either form: naming one again is not a new reference.
        healed = await TemplateService._heal_endpoint_entries(
            [*(template.source_templates or []), *(template.target_templates or [])], ns,
        )
        gate = InboundGate(ns, carried=carried_ids(template))
        try:
            resolved = (
                await gate.visible(await resolve_entity_ids(
                    sorted({*add_source, *add_target}), "template", ns,
                    bypass_cache=True, strict=False,
                ))
                if add_source or add_target else {}
            )
            for ref in add_source + add_target:
                if ref not in resolved:
                    raise EntityNotFoundError(ref, "template", ns)
        except EntityNotFoundError as e:
            raise TemplateError(str(e), "reference_violation", rule="unresolved_reference") from e
        resolved_source = {ref: resolved[ref] for ref in add_source}
        resolved_target = {ref: resolved[ref] for ref in add_target}

        # Widening adds references: the endpoints not already declared must
        # be active templates and pass the same two gates as any other
        # template reference.
        await TemplateService._refuse_inactive_template_refs(
            [*resolved_source.values(), *resolved_target.values()], previous=template,
        )
        await TemplateService._check_reference_isolation(
            ns,
            template_ids=[*resolved_source.values(), *resolved_target.values()],
            terminology_ids=[],
            previous=template,
        )

        # Union into the existing entry lists, order-stable, deduplicated by
        # canonical identity (CASE-515: comparing a resolved addition against
        # raw stored strings appended already-allowed endpoints a second time
        # in the other form; the resolved half is the comparison key now). An
        # entry that no longer resolves keys on its raw lookup — never dropped
        # (data loss); its resolved half stays None and fails closed where an
        # exact id is required.
        async def _widen(
            existing: list[EndpointRef] | None, additions: dict[str, str]
        ) -> tuple[list[EndpointRef], bool]:
            # Existing entries keep their lookup_value verbatim — the anchor a
            # declarer chose is information (Vision §4), and the synonym half
            # is what survives a fresh re-anchoring. Additions keep the
            # submitted form as their lookup and the canonical id as their
            # resolved half.
            out: list[EndpointRef] = []
            seen: set[str] = set()
            changed = False
            for entry in existing or []:
                key = entry.resolved or entry.lookup_value
                if key in seen:
                    changed = True  # canonical duplicate collapsed
                    continue
                out.append(entry)
                seen.add(key)
                if id(entry) in healed:
                    changed = True  # legacy entry healed in place
            for lookup, canonical in additions.items():
                if canonical in seen:
                    continue
                out.append(EndpointRef(lookup_value=lookup, resolved=canonical))
                seen.add(canonical)
                changed = True
            return out, changed

        new_source, source_changed = await _widen(
            template.source_templates, resolved_source
        )
        new_target, target_changed = await _widen(
            template.target_templates, resolved_target
        )

        # Idempotent: every requested endpoint already present (in any form)
        # and nothing healed or collapsed.
        if not source_changed and not target_changed:
            return TemplateService._to_template_response(template)

        # Mutate the declaration in place. The source_ref / target_ref
        # endpoint constraint follows automatically — it is projected from
        # these lists when the template is served, so there is no second copy
        # to keep in step.
        template.source_templates = new_source
        template.target_templates = new_target

        actor = get_identity_string()
        template.updated_at = datetime.now(UTC)
        template.updated_by = actor
        await template.save()

        await publish_template_event(
            EventType.TEMPLATE_UPDATED,
            TemplateService._template_to_event_payload(template),
            changed_by=actor,
        )

        return TemplateService._to_template_response(template)

    @staticmethod
    async def _heal_endpoint_entries(entries: list[EndpointRef], namespace: str) -> set[int]:
        """Fill the resolved half of each lookup-only entry that resolves in
        ``namespace``; return the ``id()`` of every entry filled. One that does
        not resolve keeps a null resolved half."""
        healed: set[int] = set()
        for entry in entries:
            if entry.resolved:
                continue
            with contextlib.suppress(EntityNotFoundError):
                entry.resolved = await resolve_entity_id(
                    entry.lookup_value, "template", namespace, bypass_cache=True
                )
                healed.add(id(entry))
        return healed

    @staticmethod
    async def get_namespace_template_stamp(namespace: str) -> str:
        """Cheap change-detection stamp over all templates in a namespace (CASE-490).

        Returns ``"<count>:<max_updated_at>"``. The ``count`` catches creates
        and hard-deletes; ``max(updated_at)`` catches updates and status flips
        (deactivate and reactivate both touch ``updated_at``). A consumer that
        caches templates can poll this single value per namespace to decide
        whether its cache is still valid, instead of re-fetching every template
        — O(namespaces) freshness checks rather than O(templates).
        """
        count = await Template.find({"namespace": namespace}).count()
        latest = (
            await Template.find({"namespace": namespace})
            .sort([("updated_at", SortDirection.DESCENDING)])
            .limit(1)
            .to_list()
        )
        max_updated = latest[0].updated_at if latest else None
        stamp_ts = max_updated.isoformat() if max_updated else ""
        return f"{count}:{stamp_ts}"

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    @staticmethod
    async def _version_event_impact(
        template_id: str,
        namespace: str,
        diff: dict,
        renames: dict[str, str] | None,
    ) -> tuple[dict, dict]:
        """Advisory impact + migration-eligibility for a version event.

        Never raises and never blocks the version event: a failed
        document-store call yields an EXPLICIT "unavailable" impact — never a
        silent zero, which would read as "no documents affected".

        Eligibility mirrors the decided rule (added fields / dropped-empty
        fields / combinations are offerable; type changes, newly-required
        fields, and modified fields need app-side data decisions), with
        declared renames excluded on both sides of the diff — a declared
        rename migrates losslessly, so its removed old name is not "stranded
        data" and its added new name is not "missing data".
        """
        rename_targets = set((renames or {}).keys())
        rename_sources = set((renames or {}).values())
        removed = [f for f in (diff.get("removed") or []) if f not in rename_sources]
        added_required = [
            f for f in (diff.get("added_required") or []) if f not in rename_targets
        ]

        try:
            stats = await get_document_store_client().get_impact_stats(
                template_id, namespace, removed,
            )
            impact = {
                "status": "ok",
                "total_live_docs": stats.get("total_live_docs", 0),
                "docs_per_version": stats.get("docs_per_version", {}),
                "field_nonempty_counts": stats.get("field_nonempty_counts", {}),
            }
        except Exception as exc:  # advisory path — any failure is "unavailable"
            impact = {
                "status": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
            }

        via = "migrate_documents (dry_run first)"
        if (
            diff.get("changed_type")
            or diff.get("made_required")
            or diff.get("modified_existing")
            or added_required
        ):
            migration = {
                "eligible": False,
                "reason": (
                    "type changes, newly-required fields, or modified fields "
                    "require app-side data decisions"
                ),
                "via": via,
            }
        elif not removed:
            migration = {"eligible": True, "reason": "additive or rename-only change", "via": via}
        elif impact["status"] != "ok":
            migration = {
                "eligible": None,
                "reason": (
                    "removed fields present but live counts unavailable — run "
                    "the migrate dry-run for a per-document readiness report"
                ),
                "via": via,
            }
        else:
            counts = impact["field_nonempty_counts"]
            stranded = {f: counts[f] for f in removed if counts.get(f, 0) > 0}
            if stranded:
                migration = {
                    "eligible": False,
                    "reason": f"removed fields carry live data: {stranded}",
                    "via": via,
                }
            else:
                migration = {
                    "eligible": True,
                    "reason": "removed fields are empty in all live documents",
                    "via": via,
                }
        return impact, migration

    @staticmethod
    async def version_event_details(
        template_id: str,
        version: int,
        previous_version: int | None,
        renames: dict[str, str] | None,
    ) -> dict | None:
        """Schema diff + advisory impact for a version event on the UPDATE path.

        The create-as-upsert path builds this inline because it already holds
        the diff it used to decide whether to version at all. PUT /templates
        has no such diff — it versions on any change — so the two stored
        versions are compared after the fact here. Both paths mint a version
        with identical consequences for live documents, so both must report
        them; an interactive template editor saves through PUT, which makes
        it the surface where a human is MOST likely to create a version
        without having thought about the blast radius.

        Comparing two stored templates is safe against the phantom-diff
        problem that forced semantic comparison on the create path: both
        sides carry canonical reference forms, so there is no value-vs-UUID
        mismatch to resolve. The comparator is reused anyway rather than
        reimplemented, so the two paths cannot drift.

        Returns None instead of raising. This is advisory data riding on a
        write that has already succeeded — a failure to describe the change
        must never turn into a failure to make it.
        """
        if previous_version is None:
            return None
        try:
            new_doc = await Template.find_one(
                {"template_id": template_id, "version": version}
            )
            old_doc = await Template.find_one(
                {"template_id": template_id, "version": previous_version}
            )
            if not new_doc or not old_doc:
                return None
            _, diff = await TemplateService.compute_template_compatibility(
                old_doc, new_doc, namespace=new_doc.namespace,
            )
            impact, migration = await TemplateService._version_event_impact(
                template_id, new_doc.namespace, diff, renames,
            )
            return {**diff, "impact": impact, "migration": migration}
        except Exception:
            logger.exception(
                "version_event_details failed for %s v%s — update stands, "
                "impact not reported", template_id, version,
            )
            return None

    @staticmethod
    async def create_templates_with_conflict_policy(
        items: list[CreateTemplateRequest],
        on_conflict: str,
    ) -> list[BulkResultItem]:
        """
        Per-item create dispatcher. Template create is an UPSERT: the
        template's identity is its name, so creating an existing
        (namespace, value) is a version event, mirroring the document
        upsert (same identity → new version).

        Behavior per non-draft item:
        - no existing (namespace, value) → standard create (status='created')
        - identical existing schema → status='unchanged' (id/version of existing)
        - any schema difference → version N+1 via the update path
          (status='updated', is_new_version=True, details=<diff> — the loud
          report; never blocking)
        - EXCEPT identity-bearing / immutable differences, which are forks or
          contract violations, not versions → status='error':
            * identity_fields differ → error_code='identity_fields_immutable'
              (declare a new template value instead — a fork)
            * usage / versioned differ → error_code='immutable_property'
            * source/target_templates differ → error_code='immutable_property'
              (endpoint lists grow only via the add-endpoints route)

        `on_conflict` is retained for API compatibility but no longer
        selects behavior: 'error' and 'validate' both upsert. Draft items
        skip the upsert (drafts may have unresolved references and live in
        their own value-space; a draft onto an existing value errors).
        Used by POST /templates.

        Returns one BulkResultItem per input item, in input order.
        """
        results: list[BulkResultItem] = []

        for i, item in enumerate(items):
            try:
                if item.status == "draft":
                    # Drafts: direct create (raises ValueError on an existing
                    # value — draft-onto-existing-family is not a version
                    # event, it is a modeling error).
                    try:
                        created = await TemplateService.create_template(
                            item, namespace=item.namespace
                        )
                        results.append(BulkResultItem(
                            index=i,
                            status="created",
                            id=created.template_id,
                            value=item.value,
                            version=created.version,
                        ))
                    except ValueError as e:
                        results.append(BulkResultItem(
                            index=i,
                            status="error",
                            value=item.value,
                            **error_fields(e),
                        ))
                    continue

                existing_list = await Template.find(
                    {"namespace": item.namespace, "value": item.value}
                ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
                existing = existing_list[0] if existing_list else None

                if existing is None:
                    created = await TemplateService.create_template(
                        item, namespace=item.namespace
                    )
                    results.append(BulkResultItem(
                        index=i,
                        status="created",
                        id=created.template_id,
                        value=item.value,
                        version=created.version,
                    ))
                    continue

                # Immutable-after-create properties can never ride a new
                # version — the update path silently preserves the originals,
                # so honoring the request would create a version that does
                # not match what was asked for. Reject loudly instead.
                # Enforced only when the request set the field explicitly:
                # an omitted field inherits the existing template's value
                # (model_fields_set distinguishes omitted from default —
                # both usage and versioned carry non-optional defaults that
                # would otherwise false-trigger against relationship /
                # versioned:false templates).
                if "usage" in item.model_fields_set and item.usage != existing.usage:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        id=existing.template_id,
                        value=item.value,
                        version=existing.version,
                        error_code="immutable_property",
                        error=(
                            f"usage is immutable after creation: existing "
                            f"'{existing.value}' has usage='{existing.usage}', "
                            f"request asks '{item.usage}'. Declare a new "
                            f"template value instead."
                        ),
                    ))
                    continue
                if "versioned" in item.model_fields_set and item.versioned != existing.versioned:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        id=existing.template_id,
                        value=item.value,
                        version=existing.version,
                        error_code="immutable_property",
                        error=(
                            f"versioned is immutable after creation: existing "
                            f"'{existing.value}' has versioned={existing.versioned}. "
                            f"Declare a new template value instead."
                        ),
                    ))
                    continue

                verdict, diff = await TemplateService.compute_template_compatibility(
                    existing, item, namespace=item.namespace,
                )

                if diff.get("identity_changed"):
                    # The identity_fields declaration is immutable across
                    # versions: document identity must stay comparable across
                    # the whole version catalog, or cross-version upsert
                    # breaks. A different identity is a different kind of
                    # thing — a fork, not a version.
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        id=existing.template_id,
                        value=item.value,
                        version=existing.version,
                        error_code="identity_fields_immutable",
                        error=(
                            f"identity_fields are immutable across versions of "
                            f"'{item.value}' (existing: "
                            f"{diff['identity_changed']['old']}, requested: "
                            f"{diff['identity_changed']['new']}). Changing "
                            f"identity is a fork — declare a new template "
                            f"value instead."
                        ),
                        details=diff,
                    ))
                    continue
                if diff.get("relationship_refs_changed"):
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        id=existing.template_id,
                        value=item.value,
                        version=existing.version,
                        error_code="immutable_property",
                        error=(
                            f"source_templates / target_templates on "
                            f"'{item.value}' cannot change via create — "
                            f"endpoint lists grow only via the add-endpoints "
                            f"route."
                        ),
                        details=diff,
                    ))
                    continue

                # Unchanged detection is two-part, and the parts are NOT
                # interchangeable: schema equality must come from the
                # SEMANTIC comparator (which resolves reference forms through
                # the Registry — a value-form resubmit of a stored-canonical
                # template is identical, not a phantom modification), while
                # cosmetic equality (label, description, extends, rules,
                # metadata, reporting, header_fields) comes from the update
                # path's change detection probed WITHOUT fields (its field
                # comparison is byte-level and would re-introduce the
                # phantom). Fully identical re-posts — the idempotent
                # bootstrap re-run — come back 'unchanged' minting nothing.
                cosmetic_probe = UpdateTemplateRequest(
                    label=item.label,
                    description=item.description,
                    extends=item.extends,
                    extends_version=item.extends_version,
                    header_fields=item.header_fields,
                    renames=item.renames,
                    rules=item.rules,
                    metadata=item.metadata,
                    reporting=item.reporting,
                )
                if verdict == "identical" and not TemplateService._template_has_changed(
                    existing, cosmetic_probe
                ):
                    results.append(BulkResultItem(
                        index=i,
                        status="unchanged",
                        id=existing.template_id,
                        value=existing.value,
                        version=existing.version,
                        details=diff,
                    ))
                    continue

                # Any difference — schema or cosmetic — versions the
                # template, with the diff as the loud report. The document
                # analogy: the upsert never blocks on "your new data looks
                # different"; it records a new version and says so.
                update_req = UpdateTemplateRequest(
                    label=item.label,
                    description=item.description,
                    extends=item.extends,
                    extends_version=item.extends_version,
                    identity_fields=item.identity_fields,
                    header_fields=item.header_fields,
                    renames=item.renames,
                    fields=item.fields,
                    rules=item.rules,
                    metadata=item.metadata,
                    reporting=item.reporting,
                )
                update_resp = await TemplateService.update_template(
                    template_id=existing.template_id,
                    request=update_req,
                )
                if update_resp and update_resp.is_new_version:
                    # A version event carries its consequences: live-document
                    # impact counts and a migration-eligibility verdict ride
                    # in details next to the schema diff. Advisory only —
                    # computed after the version exists and never blocking.
                    impact, migration = await TemplateService._version_event_impact(
                        existing.template_id, item.namespace, diff, item.renames,
                    )
                    results.append(BulkResultItem(
                        index=i,
                        status="updated",
                        id=existing.template_id,
                        value=item.value,
                        version=update_resp.version,
                        is_new_version=True,
                        details={**diff, "impact": impact, "migration": migration},
                    ))
                else:
                    results.append(BulkResultItem(
                        index=i,
                        status="unchanged",
                        id=existing.template_id,
                        value=existing.value,
                        version=existing.version,
                        details=diff,
                    ))
            except ValueError as e:
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    value=item.value,
                    **error_fields(e),
                ))
            except RegistryError as e:
                # Bulk-first contract: a Registry rejection for one item
                # (e.g. a double-restore's entry_id collision) is a per-item
                # error, never a whole-request 502.
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    value=item.value,
                    error=f"Registry error: {e!s}",
                    error_code="registry_error",
                ))

        return results

    @staticmethod
    async def validate_template(
        template_id: str,
        check_terminologies: bool = True,
        check_templates: bool = True
    ) -> ValidateTemplateResponse:
        """
        Validate a template's latest version against the checks activation
        applies: references exist, are active and pass the reference gates,
        pinned versions exist, reference fields declare what they reference,
        an edge type's endpoints are real templates. A draft is checked with
        the drafts it would activate alongside it.

        Args:
            template_id: Template to validate
            check_terminologies: Whether to check terminology refs
            check_templates: Whether to check template refs

        Returns:
            Validation response with errors and warnings
        """
        results = await Template.find({"template_id": template_id}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        template = results[0] if results else None
        if not template:
            return ValidateTemplateResponse(
                valid=False,
                template_id=template_id,
                errors=[ValidationError(
                    field="template_id",
                    code="not_found",
                    message=f"Template '{template_id}' not found"
                )]
            )

        # One set of checks: what activation demands of a draft is what an
        # active (or retired) version is held to, so /validate previews the
        # write doors faithfully, reference gates included. A draft is
        # checked with the drafts it would activate alongside it.
        if template.status == "draft":
            activation_set = await TemplateService._build_activation_set(
                template, template.namespace
            )
        else:
            activation_set = [template]
        errors, warnings = await TemplateService._validate_activation_set(
            activation_set, template.namespace
        )
        errors = TemplateService._findings_in_scope(errors, check_terminologies, check_templates)
        warnings = TemplateService._findings_in_scope(warnings, check_terminologies, check_templates)
        other_ids = [
            t.template_id for t in activation_set if t.template_id != template_id
        ]
        return ValidateTemplateResponse(
            valid=len(errors) == 0,
            template_id=template_id,
            errors=errors,
            warnings=warnings,
            will_also_activate=other_ids or None,
        )

    @staticmethod
    def _findings_in_scope(findings: list, terminologies: bool, templates: bool) -> list:
        """The findings a /validate call asked for: check_terminologies=false
        drops those about terminology references, check_templates=false those
        about template references. Shape findings (a missing reference_type)
        are always kept."""
        terminology_attrs = {"terminology_ref", "array_terminology_ref", "target_terminologies"}
        template_attrs = {
            "extends", "extends_version", "template_ref", "template_ref_version",
            "array_template_ref", "array_template_ref_version", "target_templates",
        }

        def wanted(finding: Any) -> bool:
            # The last path segment names the attribute; earlier ones are the
            # caller's field names and say nothing about the kind.
            attr = finding.field.rsplit(".", 1)[-1]
            if attr in terminology_attrs:
                return terminologies
            if attr in template_attrs:
                return templates
            return True
        return [f for f in findings if wanted(f)]

    # =========================================================================
    # CASCADE
    # =========================================================================

    @staticmethod
    async def cascade_to_children(template_id: str) -> CascadeResponse:
        """
        Cascade a parent template update to all child templates.

        When a parent template is updated (creating a new version), child
        templates still extend the old version. This method creates new
        versions of all direct children that extend the new parent version.

        The cascade only updates the `extends` pointer — child-specific fields
        are preserved as-is.

        Args:
            template_id: The new parent template_id to cascade from

        Returns:
            CascadeResponse with results for each child

        Raises:
            ValueError: If template not found
        """
        # Find the target parent template (latest version)
        parent_results = await Template.find({"template_id": template_id}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        parent = parent_results[0] if parent_results else None
        if not parent:
            raise TemplateError(f"Template '{template_id}' not found", "not_found")

        # A template keeps one template_id across its versions, so its children
        # are the templates whose extends names that id. A bare value is not an
        # identity: another namespace's template can carry the same value.
        children = await Template.find({
            "extends": parent.template_id, "status": "active",
        }).to_list()
        # The latest active version of each child template.
        latest: dict[str, Template] = {}
        for child in children:
            held = latest.get(child.template_id)
            if held is None or child.version > held.version:
                latest[child.template_id] = child

        actor = get_identity_string()
        identity = require_current_identity()
        results = []
        for child in latest.values():
            refusal = await TemplateService._cascade_refusal(identity, child)
            if refusal == "hidden":
                # A namespace the caller cannot see: its templates are neither
                # changed nor reported.
                continue
            if isinstance(refusal, tuple):
                results.append(CascadeResult(
                    value=child.value, old_template_id=child.template_id,
                    status="error", error=refusal[0], error_code=refusal[1],
                ))
            else:
                results.append(await TemplateService._cascade_child(child, parent, actor))
        updated = sum(1 for r in results if r.status == "updated")
        unchanged = sum(1 for r in results if r.status == "unchanged")
        failed = sum(1 for r in results if r.status == "error")

        return CascadeResponse(
            parent_template_id=parent.template_id,
            parent_value=parent.value,
            parent_version=parent.version,
            total=len(results),
            updated=updated,
            unchanged=unchanged,
            failed=failed,
            results=results
        )

    @staticmethod
    async def _cascade_refusal(identity: Any, child: Template) -> tuple[str, str] | str | None:
        """Why the caller may not cascade into ``child``: "hidden" for a
        namespace it cannot see, (error, error_code) for one it may only read
        or whose permission the Registry could not answer, or None to proceed."""
        permission = await resolve_permission(identity, child.namespace)
        if permission == PERMISSION_UNAVAILABLE:
            return ("Could not check write access: the Registry is unreachable",
                    "registry_error")
        if permission == "none":
            return "hidden"
        if not permission_sufficient(permission, "write"):
            return (f"Requires write access to namespace '{child.namespace}'", "forbidden")
        return None

    @staticmethod
    async def _cascade_child(child: Template, parent: Template, actor: str) -> CascadeResult:
        """Write the next version of ``child``, re-pinned to ``parent``'s
        version and otherwise exactly the child (usage, endpoints, versioned,
        header fields, renames and all), or report why not."""
        if child.extends_version == parent.version:
            return CascadeResult(value=child.value, old_template_id=child.template_id,
                                 status="unchanged")
        try:
            # The new extends must pass the reference gates, as on any update.
            await TemplateService._check_reference_isolation(
                child.namespace, template_ids=[], terminology_ids=[],
                extends_template_id=parent.template_id, previous=child,
            )
            latest = await Template.find({"template_id": child.template_id}).sort(
                [("version", SortDirection.DESCENDING)]
            ).limit(1).to_list()
            now = datetime.now(UTC)
            new_child = child.model_copy(update={
                "id": None,
                "version": latest[0].version + 1,
                "extends": parent.template_id,
                # Cascade is the explicit re-pin: the new version inherits from
                # this exact parent version, never "latest".
                "extends_version": parent.version,
                "status": "active",
                "created_at": now, "created_by": actor,
                "updated_at": now, "updated_by": actor,
            })
            await TemplateService._stamp_referent_paths(new_child)
            await new_child.insert()
            await publish_template_event(
                EventType.TEMPLATE_UPDATED,
                TemplateService._template_to_event_payload(new_child),
                changed_by=actor,
            )
        except Exception as e:
            return CascadeResult(value=child.value, old_template_id=child.template_id,
                                 status="error", **error_fields(e))
        return CascadeResult(
            value=child.value, old_template_id=child.template_id,
            new_template_id=child.template_id, new_version=new_child.version,
            status="updated",
        )

    # =========================================================================
    # DRAFT ACTIVATION
    # =========================================================================

    @staticmethod
    def _collect_template_references(template: Template) -> list[str]:
        """
        Collect all template IDs/values referenced by a template.

        Walks extends, field template_ref, array_template_ref, and
        reference-type target_templates. An edge type's fields are read in
        their served form, so its declared source/target endpoints are walked
        as the target_templates of source_ref/target_ref.

        Returns:
            List of template IDs or values referenced
        """
        refs = []

        if template.extends:
            refs.append(template.extends)

        for field in TemplateService._project_endpoint_constraints(template):
            if field.template_ref:
                refs.append(field.template_ref)
            if field.array_template_ref:
                refs.append(field.array_template_ref)
            if field.target_templates:
                refs.extend(field.target_templates)

        return refs

    @staticmethod
    async def _build_activation_set(
        root: Template,
        namespace: str
    ) -> list[Template]:
        """
        BFS from root template, collecting all draft templates that must
        be activated together.

        For each reference:
        - If it points to a draft template in ``namespace`` -> add to set, recurse
        - If it points to a draft elsewhere -> skip (validation refuses it)
        - If it points to an active template -> skip (already valid)
        - If not found -> skip (validation will catch it later)

        Handles circular references via a visited set.

        Returns:
            List of all draft Templates needing activation (including root)
        """
        activation_set = [root]
        visited = {root.template_id}
        queue = [root]

        while queue:
            current = queue.pop(0)
            refs = TemplateService._collect_template_references(current)

            for ref in refs:
                # Try to find the referenced template
                target = await TemplateService._find_template_by_ref(ref, namespace)
                if not target:
                    # Not found — validation will catch it
                    continue

                if target.template_id in visited:
                    continue
                visited.add(target.template_id)

                # Only the namespace being activated: a draft elsewhere is not
                # this caller's to activate. Left out of the set, a reference
                # to it is checked like any other (visibility, inbound gate)
                # and fails as a reference to a template that is not active.
                if target.status == "draft" and target.namespace == namespace:
                    activation_set.append(target)
                    queue.append(target)
                # If active or other status, no need to recurse

        return activation_set

    @staticmethod
    async def _validate_activation_set(
        activation_set: list[Template],
        namespace: str
    ) -> tuple[list, list]:
        """
        Validate all templates in the activation set as a unit.

        For each template, check:
        - extends: must be in set OR exist as active
        - terminology_ref / array_terminology_ref: must exist and be active
        - template_ref / array_template_ref: must be in set OR exist as active
        - target_templates: each value must be in set OR have an active template
        - target_terminologies: must exist and be active
        - reference_type required for reference fields

        A reference outside the set into a closed namespace the writer cannot
        read, and not held by the template's previous version, is reported
        exactly as a missing one (the inbound gate).

        Returns:
            Tuple of (errors, warnings) as ValidationError/ValidationWarning lists
        """
        from ..models.api_models import ValidationError

        # Build lookups for the activation set
        set_ids = {t.template_id for t in activation_set}
        set_namespaces = TemplateService._set_namespaces(activation_set)
        set_values = {t.value for t in activation_set}
        # CASE-493: version-aware membership for pinned-ref existence — a pinned
        # (ref, version) is satisfied by a co-activating template only if that
        # template carries the pinned version.
        set_pinned = (
            {(t.template_id, t.version) for t in activation_set}
            | {(t.value, t.version) for t in activation_set}
        )

        async def _visible(tpl: Template | None, gate: InboundGate) -> Template | None:
            if tpl is not None and await gate.refuses(tpl.namespace, tpl.template_id):
                return None
            return tpl

        async def _terminology_exists(ref: str, gate: InboundGate) -> bool:
            if not await def_store.terminology_exists(ref, namespace=namespace):
                return False
            terminology_id = None
            with contextlib.suppress(EntityNotFoundError):
                terminology_id = await resolve_entity_id(
                    ref, "terminology", namespace, bypass_cache=True,
                )
            return not await gate.refuses_entry(terminology_id)

        async def _pinned_version_exists(
            ref: str, version: int | None, gate: InboundGate,
        ) -> bool:
            """A pinned (ref, version) is satisfiable iff it is in the activation
            set at that version, or a stored template has that (template_id,
            version). ref may be a canonical id or a value. A None version can
            never match — the mandatory ref/version pairing is enforced before
            this validation runs, so None only reaches here if that enforcement
            regressed, and it fails the same way an unknown version does."""
            if version is None:
                return False
            if (ref, version) in set_pinned:
                return True
            if await _visible(
                await Template.find_one({"template_id": ref, "version": version}), gate,
            ):
                return True
            resolved = await _visible(
                await TemplateService._find_template_by_ref(ref, namespace), gate,
            )
            if resolved is None:
                return False
            return await Template.find_one(
                {"template_id": resolved.template_id, "version": version}
            ) is not None

        errors: list[ValidationError] = []
        warnings: list[ValidationWarning] = []

        def_store = get_def_store_client()

        async def _template_ref_problem(
            ref: str, gate: InboundGate, what: str = "Template",
        ) -> str | None:
            """Why a template reference does not hold, or None. A member of
            the set holds by id, value, or any form that finds it (ns:VALUE,
            a synonym); anything else must be a visible, active template."""
            if ref in set_ids or ref in set_values:
                return None
            target = await _visible(
                await TemplateService._find_template_by_ref(ref, namespace), gate,
            )
            if target is None:
                return f"{what} '{ref}' not found"
            if target.template_id in set_ids or target.status == "active":
                return None
            return f"{what} '{ref}' is {target.status}, not active"

        def _error(field: str, code: str, message: str) -> None:
            errors.append(ValidationError(field=field, code=code, message=message))

        async def _check_template_ref(field: str, ref: str, gate: InboundGate) -> None:
            problem = await _template_ref_problem(ref, gate)
            if problem:
                _error(field, "invalid_reference", problem)

        async def _check_terminology(field: str, ref: str, gate: InboundGate) -> None:
            try:
                if not await _terminology_exists(ref, gate):
                    _error(field, "invalid_reference", f"Terminology '{ref}' not found or inactive")
            except DefStoreError as e:
                warnings.append(ValidationWarning(
                    field=field, code="validation_failed",
                    message=f"Could not validate terminology: {e!s}",
                ))

        async def _check_pinned(
            field: str, ref: str, version: int | None, gate: InboundGate, what: str,
        ) -> None:
            # CASE-493: a schema reference must pin an existing version.
            if not await _pinned_version_exists(ref, version, gate):
                _error(field, "invalid_reference", f"{what} '{ref}' has no version {version}")

        async def _check_extends(
            template: Template, extends: str, prefix: str, gate: InboundGate,
        ) -> None:
            problem = await _template_ref_problem(extends, gate, "Parent template")
            if problem:
                _error(f"{prefix}extends", "invalid_reference", problem)
            if template.extends_version is None:
                _error(f"{prefix}extends_version", "invalid_reference",
                       "extends_version is required when 'extends' is set (CASE-493)")
            else:
                await _check_pinned(f"{prefix}extends_version", extends,
                                    template.extends_version, gate, "Parent template")

        async def _check_reference_field(field: Any, path: str, gate: InboundGate) -> None:
            if not field.reference_type:
                _error(f"{path}.reference_type", "required",
                       "reference_type is required for reference fields")
            elif field.reference_type.value == "document":
                if not field.target_templates:
                    # target_templates is optional — warn only
                    warnings.append(ValidationWarning(
                        field=f"{path}.target_templates", code="recommended",
                        message="target_templates is recommended for document references",
                    ))
                for tpl_val in field.target_templates or []:
                    await _check_template_ref(f"{path}.target_templates", tpl_val, gate)
            elif field.reference_type.value == "term":
                if not field.target_terminologies:
                    _error(f"{path}.target_terminologies", "required",
                           "target_terminologies is required for term references")
                for term_val in field.target_terminologies or []:
                    await _check_terminology(f"{path}.target_terminologies", term_val, gate)

        async def _check_field(field: Any, prefix: str, gate: InboundGate) -> None:
            path = f"{prefix}fields.{field.name}"
            for attr in ("terminology_ref", "array_terminology_ref"):
                if getattr(field, attr):
                    await _check_terminology(f"{path}.{attr}", getattr(field, attr), gate)
            for attr, what in (("template_ref", "Nested template"),
                               ("array_template_ref", "Array-item template")):
                ref = getattr(field, attr)
                if ref:
                    await _check_template_ref(f"{path}.{attr}", ref, gate)
                    await _check_pinned(f"{path}.{attr}_version", ref,
                                        getattr(field, f"{attr}_version"), gate, what)
            if field.type.value == "reference":
                await _check_reference_field(field, path, gate)

        for template in activation_set:
            prefix = f"[{template.value}] "
            gate = InboundGate(
                namespace,
                carried=carried_ids(await TemplateService._previous_version(template)),
                placed=set_namespaces,
            )
            if template.extends:
                await _check_extends(template, template.extends, prefix, gate)
            # Fields in their served form: an edge type's declared endpoints
            # are checked as the target_templates of source_ref / target_ref.
            for field in TemplateService._project_endpoint_constraints(template):
                await _check_field(field, prefix, gate)

        return errors, warnings

    @staticmethod
    async def _prepare_activation(activation_set: list[Template], namespace: str) -> None:
        """Bring a validated activation set to its active form, in memory:
        references normalized to canonical ids, endpoint declarations
        resolved, the reference gates applied, the inheritance chain and the
        referent identity paths worked out. Raises TemplateError, and nothing
        is saved, when any member cannot be brought there. Used by activation
        and by reactivation, so a version becomes active only in this form."""
        # The set's own members: their Registry entries are not active (a
        # draft's is retired until activation), so the gates learn their
        # (same) namespace from the set itself.
        set_namespaces = TemplateService._set_namespaces(activation_set)

        # Build known_templates from activation set for cross-resolution
        known_templates = {}
        for t in activation_set:
            known_templates[t.value] = t.template_id
            known_templates[f"{t.namespace}:{t.value}"] = t.template_id
            known_templates[t.template_id] = t.template_id

        # Normalize all references to canonical IDs. Validation has already
        # held every reference outside the set to an active template, so the
        # other statuses only ever reach a set member named in a form
        # known_templates does not list (a synonym).
        activation_statuses = ["active", "reserved", "inactive"]
        try:
            for t in activation_set:
                previous = await TemplateService._previous_version(t)
                gate = InboundGate(namespace, carried=carried_ids(previous), placed=set_namespaces)
                act_template_ids, act_terminology_ids = (
                    await TemplateService._normalize_field_references(
                        t.fields, namespace,
                        known_templates=known_templates,
                        include_statuses=activation_statuses,
                        gate=gate,
                    )
                )
                # Endpoint declarations resolve here too: a draft skipped
                # resolution at create, so activation is where its entries'
                # resolved halves fill (lookups stay verbatim).
                if t.usage == TemplateUsage.RELATIONSHIP:
                    t.source_templates, t.target_templates = (
                        await TemplateService._resolve_endpoint_declarations(
                            t.source_templates, t.target_templates, namespace,
                            known_templates=known_templates,
                            include_statuses=activation_statuses,
                            gate=gate,
                        )
                    )
                # Also resolve extends (known_templates checked first, then Registry)
                if t.extends and t.extends in known_templates:
                    t.extends = known_templates[t.extends]
                elif t.extends:
                    parent_id = await resolve_entity_id(
                        t.extends, "template", namespace,
                        include_statuses=activation_statuses,
                    )
                    if await gate.refuses_entry(parent_id):
                        raise EntityNotFoundError(t.extends, "template", namespace)
                    t.extends = parent_id
                # Isolation check — drafts skipped it at create; activation is
                # where their coverage lands. Activation-set members are
                # same-namespace by construction (their reserved entries are
                # placed from the set); any other reference must have an
                # active Registry entry the lookup can place, or is refused.
                await TemplateService._check_reference_isolation(
                    namespace,
                    template_ids=[
                        *act_template_ids,
                        *TemplateService._endpoint_ids(t.source_templates, t.target_templates),
                    ],
                    terminology_ids=act_terminology_ids,
                    extends_template_id=t.extends,
                    previous=previous,
                    known_namespaces=set_namespaces,
                )
        except EntityNotFoundError as e:
            raise TemplateError(str(e), "reference_violation", rule="unresolved_reference") from e

        # Every activating template's pinned chain must resolve. Set members
        # are walked in their normalized in-memory form (their stored drafts
        # may still name parents by value); nothing is saved if one fails.
        set_by_version = {(t.template_id, t.version): t for t in activation_set}
        for t in activation_set:
            await TemplateService._refuse_unresolvable_inheritance(
                t.template_id, t.extends, t.extends_version, overrides=set_by_version
            )

        # Work out each member's referent identity paths before saving any,
        # so nothing is activated if one cannot be classified.
        for t in activation_set:
            t.referent_identity_paths = await TemplateService._referent_paths_or_refuse(
                t, overrides=set_by_version
            )


    @staticmethod
    async def activate_template(
        template_id: str,
        namespace: str,
        dry_run: bool = False
    ):
        """
        Activate a draft template and all draft templates it references (cascading).

        1. Find template -> verify status is "draft"
        2. Build activation set (BFS through references)
        3. Validate the full set as a unit
        4. If errors -> return errors, no state changes
        5. If dry_run -> return preview
        6. Set status="active" on all, save, publish events

        Args:
            template_id: The draft template to activate
            namespace: Namespace for lookups
            dry_run: If True, return preview without making changes

        Returns:
            ActivateTemplateResponse

        Raises:
            ValueError: If template not found or not in draft status
        """
        from ..models.api_models import ActivateTemplateResponse, ActivationDetail

        query: dict = {"template_id": template_id}
        if namespace:
            query["namespace"] = namespace
        results = await Template.find(query).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        template = results[0] if results else None
        if not template:
            raise TemplateError(f"Template '{template_id}' not found", "not_found")
        if template.status != "draft":
            raise TemplateError(
                f"Template '{template_id}' is '{template.status}', not 'draft'. "
                f"Only draft templates can be activated.", "invalid_state", rule="not_draft"
            )

        # Build the full activation set (all reachable drafts)
        activation_set = await TemplateService._build_activation_set(template, namespace)

        # Validate the full set
        errors, warnings = await TemplateService._validate_activation_set(
            activation_set, namespace
        )

        if errors:
            return ActivateTemplateResponse(
                activated=[],
                activation_details=[],
                total_activated=0,
                errors=errors,
                warnings=warnings,
            )

        if dry_run:
            return ActivateTemplateResponse(
                activated=[],
                activation_details=[
                    ActivationDetail(
                        template_id=t.template_id,
                        value=t.value,
                        status="would_activate"
                    )
                    for t in activation_set
                ],
                total_activated=0,
                errors=[],
                warnings=warnings,
            )

        await TemplateService._prepare_activation(activation_set, namespace)

        # Activate all templates in the set
        actor = get_identity_string()
        now = datetime.now(UTC)
        activated_ids = []

        for t in activation_set:
            t.status = "active"
            t.updated_at = now
            t.updated_by = actor
            await t.save()
            activated_ids.append(t.template_id)
            # A draft version activated on a template whose other versions
            # are all inactive brings the template back.
            await TemplateService._sync_registry_lifecycle(t.template_id, actor)

            # Publish activation event
            await publish_template_event(
                EventType.TEMPLATE_ACTIVATED,
                TemplateService._template_to_event_payload(t),
                changed_by=actor
            )

        return ActivateTemplateResponse(
            activated=activated_ids,
            activation_details=[
                ActivationDetail(
                    template_id=t.template_id,
                    value=t.value,
                    status="activated"
                )
                for t in activation_set
            ],
            total_activated=len(activated_ids),
            errors=[],
            warnings=warnings,
        )

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _template_to_event_payload(t: Template) -> dict:
        """Convert Template document to event payload for NATS publishing."""
        return {
            "template_id": t.template_id,
            "namespace": t.namespace,
            "value": t.value,
            "label": t.label,
            "description": t.description,
            "version": t.version,
            "extends": t.extends,
            "extends_version": t.extends_version,
            "identity_fields": t.identity_fields,
            # Reporting builds an edge type's endpoint columns from the event's
            # template, so the event says what kind of template it carries.
            "usage": (t.usage or TemplateUsage.ENTITY).value,
            "versioned": t.versioned,
            "fields": [f.model_dump() for f in t.fields] if t.fields else [],
            "rules": [r.model_dump() for r in t.rules] if t.rules else [],
            "metadata": t.metadata.model_dump() if t.metadata else {},
            "reporting": t.reporting.model_dump() if t.reporting else None,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "created_by": t.created_by,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "updated_by": t.updated_by,
        }

    @staticmethod
    def _project_endpoint_constraints(t: Template) -> list:
        """Compute the endpoint constraint the generic reference validator reads.

        ``source_ref`` / ``target_ref`` carry a ``target_templates`` constraint
        so document-store's reference validator — which knows nothing about
        edge types and simply sees a reference field — enforces the declared
        endpoints. That constraint is a PROJECTION of the template-level
        declaration, not a second declaration of it, so it is computed here
        rather than stored. Storing it is what let the two disagree: a fresh
        restore rewrote the stored projection while the declaration kept
        naming the source install (CASE-830).

        Each entry contributes its resolved half when present, else its
        lookup — the validator's `latest` branch resolves every allowed entry
        through the Registry before matching anyway (CASE-525), so either
        half enforces identically there; `pinned` needs the exact id and
        fails closed on a lookup-only entry.
        """
        if t.usage != TemplateUsage.RELATIONSHIP:
            return t.fields
        by_endpoint = {
            "source_ref": [
                e.resolved or e.lookup_value for e in (t.source_templates or [])
            ],
            "target_ref": [
                e.resolved or e.lookup_value for e in (t.target_templates or [])
            ],
        }
        # include_subtypes is forced off: an edge type's endpoints are exactly
        # the templates it declares. Admitting subtypes implicitly would make
        # the declared catalogue — what the API, Console and `inspect` report —
        # narrower than what is actually accepted, and it would change
        # app-visible behaviour (/relationships, /traverse) without changing
        # the declaration. Plain reference fields keep the feature.
        return [
            field.model_copy(update={
                "target_templates": by_endpoint[field.name],
                "include_subtypes": False,
            })
            if field.name in by_endpoint
            else field
            for field in t.fields
        ]

    @staticmethod
    def _without_endpoint_projection(usage: TemplateUsage | None, fields: list) -> list:
        """Copies of `fields` with the derived endpoint constraint removed.

        The non-mutating form of _strip_endpoint_projection, for comparing a
        stored template with a proposed one.
        """
        if usage != TemplateUsage.RELATIONSHIP:
            return list(fields)
        return [
            field.model_copy(update={"target_templates": None, "include_subtypes": None})
            if field.name in ("source_ref", "target_ref")
            else field
            for field in fields
        ]

    @staticmethod
    def _strip_endpoint_projection(usage: TemplateUsage, fields: list | None) -> None:
        """Drop the projection before persisting — it is derived on serve.

        Whatever a caller sends for ``source_ref``/``target_ref``
        ``target_templates`` is discarded: the template-level declaration is
        the only place endpoints are declared.
        """
        if usage != TemplateUsage.RELATIONSHIP:
            return
        for field in fields or []:
            if field.name in ("source_ref", "target_ref"):
                field.target_templates = None
                # Not a projection but the same rule: an endpoint's subtype
                # policy is not the caller's to set, so it is not persisted
                # either. See _project_endpoint_constraints.
                field.include_subtypes = None

    @staticmethod
    def _to_template_response(t: Template) -> TemplateResponse:
        """Convert Template document to response model."""
        return TemplateResponse(
            template_id=t.template_id,
            namespace=t.namespace,
            value=t.value,
            label=t.label,
            description=t.description,
            version=t.version,
            extends=t.extends,
            extends_version=t.extends_version,
            identity_fields=t.identity_fields,
            referent_identity_paths=t.referent_identity_paths,
            header_fields=t.header_fields,
            renames=t.renames,
            usage=t.usage,
            source_templates=t.source_templates,
            target_templates=t.target_templates,
            versioned=t.versioned,
            fields=TemplateService._project_endpoint_constraints(t),
            rules=t.rules,
            metadata=t.metadata,
            reporting=t.reporting,
            status=t.status,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    async def _previous_version(template: Template) -> Template | None:
        """The latest other version of a template, whose references are carried."""
        previous = await Template.find(
            {"template_id": template.template_id, "version": {"$ne": template.version}},
        ).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        return previous[0] if previous else None

    @staticmethod
    async def _find_template_by_ref(
        ref: str,
        namespace: str
    ) -> Template | None:
        """
        Find a template by reference: its id, its value, or any identifier
        the Registry resolves to it (``ns:VALUE``, a synonym).

        Returns the latest version, at any status — callers judge the status.
        Tries the id, then the value within ``namespace``, then the Registry.

        Args:
            ref: Template ID, value, or another registered identifier
            namespace: Namespace for value lookups
        """
        # Try by template_id within namespace first (return latest version)
        results = await Template.find({"template_id": ref, "namespace": namespace}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        if results:
            return results[0]
        # Fallback: try by template_id cross-namespace (for external refs)
        results = await Template.find({"template_id": ref}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        if results:
            return results[0]
        # Try by value within namespace (return latest version)
        results = await Template.find({"namespace": namespace, "value": ref}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        if results:
            return results[0]
        # Any other form goes through the Registry, the identity authority. A
        # draft's or a retired template's entry is not active, so every
        # status is asked for; the caller decides what the status means.
        try:
            template_id = await resolve_entity_id(
                ref, "template", namespace,
                include_statuses=["active", "reserved", "inactive"],
            )
        except EntityNotFoundError:
            return None
        results = await Template.find({"template_id": template_id}).sort([("version", SortDirection.DESCENDING)]).limit(1).to_list()
        return results[0] if results else None

    @staticmethod
    async def _resolve_endpoint_declarations(
        source_templates: list | None,
        target_templates: list | None,
        namespace: str,
        known_templates: dict[str, str] | None = None,
        include_statuses: list[str] | None = None,
        gate: InboundGate | None = None,
    ) -> tuple[list[EndpointRef], list[EndpointRef]]:
        """Build an edge type's declared endpoints as two-half entries.

        Each entry keeps the caller's anchor verbatim in ``lookup_value`` (a
        submitted string, or an entry's own lookup — a caller-supplied
        ``resolved`` is never honored; that half is server-owned) and fills
        ``resolved`` with the canonical template_id from the Registry. Storing
        both halves is what lets the declaration survive a fresh restore: the
        resolved half rides the id mapping table, and the lookup half is the
        synonym that re-resolves on a target for endpoints the mapping table
        cannot cover.

        Returns the (source, target) entry lists, order-preserving. Raises
        EntityNotFoundError (surfaced as a per-item error by the caller) when
        a lookup does not resolve — declaring an edge type against a template
        the Registry does not know is a loud failure at the API door; only
        the restore door is lenient (warning + null resolved half). The
        first unresolved lookup in declaration order is the one reported, and
        a target the ``gate`` refuses is unresolved like a missing one.
        """
        src_lookups = endpoint_lookups(source_templates)
        tgt_lookups = endpoint_lookups(target_templates)
        pending = {
            ref for ref in (src_lookups + tgt_lookups)
            if not (known_templates and ref in known_templates)
        }
        resolved: dict[str, str] = {}
        if pending:
            # Same write-path discipline as field normalization: bypass the
            # resolver cache so a stale entry cannot be baked into durable
            # state.
            resolved = await resolve_entity_ids(
                sorted(pending), "template", namespace,
                include_statuses=include_statuses,
                bypass_cache=True,
                strict=False,
            )
            if gate is not None:
                resolved = await gate.visible(resolved)
        resolved.update(known_templates or {})
        for ref in src_lookups + tgt_lookups:
            if ref not in resolved:
                raise EntityNotFoundError(ref, "template", namespace)
        return (
            [EndpointRef(lookup_value=ref, resolved=resolved.get(ref))
             for ref in src_lookups],
            [EndpointRef(lookup_value=ref, resolved=resolved.get(ref))
             for ref in tgt_lookups],
        )

    @staticmethod
    async def _normalize_field_references(
        fields: list,
        namespace: str,
        known_templates: dict[str, str] | None = None,
        include_statuses: list[str] | None = None,
        gate: InboundGate | None = None,
    ) -> tuple[list[str], list[str]]:
        """
        Normalize all reference fields to canonical IDs via batch Registry resolution.

        Collects all template and terminology refs across fields, resolves them
        in two batch calls (one for templates, one for terminologies), then
        applies the resolved IDs back to field objects. Mutates fields in-place.

        Args:
            fields: List of FieldDefinition objects
            namespace: Namespace for lookups
            known_templates: Optional dict for activation set cross-references
                (entries found here skip the Registry call)
            include_statuses: Status filter for resolution (e.g. ["active", "reserved"]
                during activation). Default: active only.
            gate: The write's inbound gate. A target it refuses is reported
                exactly as a reference that does not resolve.

        Raises:
            EntityNotFoundError: for the first reference, in field order, that
                does not resolve (or that the gate refuses). Nothing is applied
                to the fields then.

        Returns:
            (template_ids, terminology_ids) — the canonical IDs applied to the
            fields, deduplicated. Feed these to _check_reference_isolation so
            every resolved schema reference passes the namespace boundary.
        """
        # Phase 1: Collect all refs (skip known_templates hits — those are
        # resolved within the activation set without a Registry call)
        template_refs: set[str] = set()
        terminology_refs: set[str] = set()

        for field in fields:
            for ref in (field.target_templates or []):
                if not (known_templates and ref in known_templates):
                    template_refs.add(ref)
            if field.template_ref and not (known_templates and field.template_ref in known_templates):
                template_refs.add(field.template_ref)
            if field.array_template_ref and not (known_templates and field.array_template_ref in known_templates):
                template_refs.add(field.array_template_ref)

            if field.terminology_ref:
                terminology_refs.add(field.terminology_ref)
            if field.array_terminology_ref:
                terminology_refs.add(field.array_terminology_ref)
            for ref in (field.target_terminologies or []):
                terminology_refs.add(ref)

        # Phase 2: Batch resolve via Registry (all IDs verified, no format bypass)
        resolved_templates: dict[str, str] = {}
        resolved_terminologies: dict[str, str] = {}

        # CASE-56: this is a WRITE path — resolved IDs will be stored on
        # the template as canonical references. If the wip-auth cache
        # held a stale entry (e.g., from a bootstrap that ran before a
        # namespace delete+recreate), reading from it here would bake a
        # dead UUID into durable state. Bypass the cache on reads and let
        # it self-heal with the fresh Registry result on write.
        #
        # Non-strict: a miss is absent from the map rather than raised, so the
        # reference reported below is the first in field order — not whichever
        # the batch happened to meet first — and a refused target, dropped by
        # the gate, is reported by exactly the same branch.
        if template_refs:
            resolved_templates = await resolve_entity_ids(
                sorted(template_refs), "template", namespace,
                include_statuses=include_statuses,
                bypass_cache=True,
                strict=False,
            )
        if terminology_refs:
            resolved_terminologies = await resolve_entity_ids(
                sorted(terminology_refs), "terminology", namespace,
                include_statuses=include_statuses,
                bypass_cache=True,
                strict=False,
            )
        if gate is not None:
            resolved_templates = await gate.visible(resolved_templates)
            resolved_terminologies = await gate.visible(resolved_terminologies)

        # Merge known_templates into resolved map
        if known_templates:
            resolved_templates.update(known_templates)

        for field in fields:
            for ref in [
                *(field.target_templates or []), field.template_ref, field.array_template_ref,
            ]:
                if ref and ref not in resolved_templates:
                    raise EntityNotFoundError(ref, "template", namespace)
            for ref in [
                field.terminology_ref, field.array_terminology_ref,
                *(field.target_terminologies or []),
            ]:
                if ref and ref not in resolved_terminologies:
                    raise EntityNotFoundError(ref, "terminology", namespace)

        def _resolve_tpl(ref: str) -> str:
            return resolved_templates[ref]

        def _resolve_term(ref: str) -> str:
            return resolved_terminologies[ref]

        # Phase 3: Apply resolved IDs back to fields
        applied_template_ids: set[str] = set()
        applied_terminology_ids: set[str] = set()
        for field in fields:
            if field.target_templates:
                field.target_templates = [_resolve_tpl(r) for r in field.target_templates]
                applied_template_ids.update(field.target_templates)
            if field.template_ref:
                field.template_ref = _resolve_tpl(field.template_ref)
                applied_template_ids.add(field.template_ref)
            if field.array_template_ref:
                field.array_template_ref = _resolve_tpl(field.array_template_ref)
                applied_template_ids.add(field.array_template_ref)
            if field.terminology_ref:
                field.terminology_ref = _resolve_term(field.terminology_ref)
                applied_terminology_ids.add(field.terminology_ref)
            if field.array_terminology_ref:
                field.array_terminology_ref = _resolve_term(field.array_terminology_ref)
                applied_terminology_ids.add(field.array_terminology_ref)
            if field.target_terminologies:
                field.target_terminologies = [_resolve_term(r) for r in field.target_terminologies]
                applied_terminology_ids.update(field.target_terminologies)

        return sorted(applied_template_ids), sorted(applied_terminology_ids)

    @staticmethod
    def _endpoint_ids(*entry_lists: list[Any] | None) -> list[str]:
        """The resolved template ids of an edge type's endpoint declarations."""
        return [
            entry.resolved
            for entries in entry_lists
            for entry in entries or []
            if getattr(entry, "resolved", None)
        ]

    @staticmethod
    def _stored_reference_ids(template: Any) -> set[str]:
        """Every template and terminology id a stored template references,
        its edge-type endpoints included."""
        ids: set[str] = set(
            TemplateService._endpoint_ids(
                getattr(template, "source_templates", None),
                getattr(template, "target_templates", None),
            )
        )
        for field in template.fields or []:
            ids.update(field.target_templates or [])
            ids.update(field.target_terminologies or [])
            for ref in (
                field.template_ref, field.array_template_ref,
                field.terminology_ref, field.array_terminology_ref,
            ):
                if ref:
                    ids.add(ref)
        if template.extends:
            ids.add(template.extends)
        return ids

    @staticmethod
    def _set_namespaces(activation_set: list[Template]) -> dict[str, str]:
        """Each activation-set member's namespace. Their Registry entries are
        reserved, so the reference gates learn the namespace from the set."""
        return {t.template_id: t.namespace for t in activation_set}

    @staticmethod
    def _placed_or_refuse(ids: set[str], ns_map: dict[str, str]) -> dict[str, str]:
        """``ns_map`` when it places every id; otherwise the write is refused,
        since a reference whose namespace is unknown cannot be checked."""
        unplaced = sorted(i for i in ids if i not in ns_map)
        if unplaced:
            raise TemplateError(
                f"Cannot check the namespace of {', '.join(unplaced)}: "
                "the Registry has no active entry for it",
                "reference_violation", rule="unresolved_reference",
            )
        return ns_map

    @staticmethod
    async def _check_reference_isolation(
        namespace: str,
        template_ids: list[str],
        terminology_ids: list[str],
        extends_namespace: str | None = None,
        extends_template_id: str | None = None,
        previous: Any | None = None,
        known_namespaces: Mapping[str, str] | None = None,
    ) -> None:
        """Apply the outbound gate to a template's references.

        Fetches each referenced entity's owning namespace in one bulk Registry
        lookup, then applies the template namespace's isolation_mode
        (wip_auth.reference_policy). The inbound gate has already run inside
        resolution, so a reference it refused never reaches this check. Pass
        extends_namespace when the caller already knows it (single create),
        or extends_template_id to have it looked up with the rest (update /
        activation). The lookup reports active entries only; ``known_namespaces``
        supplies the ones the caller already knows (an activation set's own
        drafts, reserved in the Registry). A reference whose namespace is still
        unknown is refused: an unanswered lookup is not a pass.

        References the template's ``previous`` version already held are not
        re-checked, so a template stays updatable after its writer loses read
        on a namespace it already references.

        Raises TemplateError on a violation.
        """
        carried = (
            TemplateService._stored_reference_ids(previous) if previous is not None else set()
        )
        template_ids = [i for i in template_ids if i not in carried]
        terminology_ids = [i for i in terminology_ids if i not in carried]
        if extends_template_id in carried:
            extends_template_id = None
            extends_namespace = None
        ids = set(template_ids) | set(terminology_ids)
        if extends_namespace is None and extends_template_id:
            ids.add(extends_template_id)
        try:
            ns_map = (
                await get_registry_client().lookup_entry_namespaces(
                    sorted(ids), raise_on_error=True,
                )
                if ids else {}
            )
        except (RegistryError, httpx.HTTPError) as e:
            raise TemplateError(
                f"Cannot check the namespaces of the template's references: {e}",
                "registry_error", rule="unreachable",
            ) from e
        ns_map = TemplateService._placed_or_refuse(ids, {**ns_map, **(known_namespaces or {})})
        if extends_namespace is None and extends_template_id:
            extends_namespace = ns_map.get(extends_template_id)
        try:
            validator = get_reference_validator()
            await validator.validate_template_references(
                template_namespace=namespace,
                extends_template_namespace=extends_namespace,
                terminology_namespaces=[ns_map[i] for i in terminology_ids],
                template_ref_namespaces=[ns_map[i] for i in template_ids],
            )
        except ReferenceValidationError as e:
            raise TemplateError(f"Cross-namespace reference violation: {e.violations}", "cross_namespace_reference") from e
        except RegistryUnavailableError as e:
            # Isolation cannot be checked, so the write is refused rather
            # than let through unchecked.
            raise TemplateError(str(e), "registry_error", rule="unreachable") from e

    @staticmethod
    async def _validate_pinned_versions(
        fields: list,
        extends: str | None,
        extends_version: int | None,
        *,
        check_existence: bool,
    ) -> None:
        """Enforce mandatory, pinned versions on every schema reference (CASE-493).

        Schema references — template inheritance (``extends``) and nested-object
        / array template refs — must each name an explicit version. "Latest" is
        not a permitted resolution for a schema reference: a parent document
        validated against a floating nested (or parent) schema can silently
        strand when that schema ships an incompatible new version.

        Two checks:
        - **Presence** (always): the version is set whenever the ref is set.
          Nested-ref presence is also guarded by FieldDefinition's model
          validator; ``extends_version`` presence is enforced here because it
          lives on the template, not a field.
        - **Existence** (``check_existence``): the pinned ``(template_id,
          version)`` pair actually exists. Skipped for draft writes — a draft
          may legitimately reference a not-yet-activated template; existence is
          re-checked at activation, when the referent must exist.

        Expects ``extends`` and the fields' refs to already be normalized to
        canonical template_ids (existence queries by template_id).
        """
        # Presence — extends_version (nested-ref presence is enforced on the
        # FieldDefinition model itself).
        if extends and extends_version is None:
            raise TemplateError(
                "extends_version is required when a template declares 'extends': "
                "schema inheritance must pin an explicit parent version (CASE-493)", "validation_failed", rule="extends_version_required"
            )

        if not check_existence:
            return

        async def _pinned_exists(template_id: str, version: int) -> bool:
            return await Template.find_one(
                {"template_id": template_id, "version": version}
            ) is not None

        if extends and extends_version is not None and not await _pinned_exists(extends, extends_version):
            raise TemplateError(
                f"Parent template '{extends}' has no version {extends_version} "
                "(extends_version must pin an existing parent version — CASE-493)", "reference_violation", rule="pinned_version_missing"
            )

        for field in fields:
            if (
                field.template_ref and field.template_ref_version is not None
                and not await _pinned_exists(field.template_ref, field.template_ref_version)
            ):
                raise TemplateError(
                    f"Field '{field.name}': nested template '{field.template_ref}' "
                    f"has no version {field.template_ref_version} (CASE-493)", "reference_violation", rule="pinned_version_missing"
                )
            if (
                field.array_template_ref and field.array_template_ref_version is not None
                and not await _pinned_exists(field.array_template_ref, field.array_template_ref_version)
            ):
                raise TemplateError(
                    f"Field '{field.name}': array-item template "
                    f"'{field.array_template_ref}' has no version "
                    f"{field.array_template_ref_version} (CASE-493)", "reference_violation", rule="pinned_version_missing"
                )

    @staticmethod
    async def _validate_field_references(fields: list, namespace: str) -> list[str]:
        """
        Validate terminology_ref and template_ref values in fields via batch Registry resolution.

        Collects all refs, attempts batch resolution, and reports errors for failures.

        Args:
            fields: List of field definitions
            namespace: Namespace for cross-service lookups

        Returns:
            List of error messages for invalid references
        """
        errors = []

        # Collect refs with their field context for error reporting
        template_refs: dict[str, list[str]] = {}  # ref -> [field_name, ...]
        terminology_refs: dict[str, list[str]] = {}  # ref -> [field_name, ...]

        for field in fields:
            field_name = field.name if hasattr(field, 'name') else field.get('name', 'unknown')
            field_type = field.type if hasattr(field, 'type') else field.get('type')

            if field_type == 'term':
                term_ref = field.terminology_ref if hasattr(field, 'terminology_ref') else field.get('terminology_ref')
                if term_ref:
                    terminology_refs.setdefault(term_ref, []).append(field_name)

            if field_type == 'object':
                tpl_ref = field.template_ref if hasattr(field, 'template_ref') else field.get('template_ref')
                if tpl_ref:
                    template_refs.setdefault(tpl_ref, []).append(field_name)

            if field_type == 'array':
                array_item_type = field.array_item_type if hasattr(field, 'array_item_type') else field.get('array_item_type')
                if array_item_type == 'term':
                    array_term_ref = field.array_terminology_ref if hasattr(field, 'array_terminology_ref') else field.get('array_terminology_ref')
                    if array_term_ref:
                        terminology_refs.setdefault(array_term_ref, []).append(f"{field_name}[]")
                if array_item_type == 'object':
                    array_tpl_ref = field.array_template_ref if hasattr(field, 'array_template_ref') else field.get('array_template_ref')
                    if array_tpl_ref:
                        template_refs.setdefault(array_tpl_ref, []).append(f"{field_name}[]")

        # Batch resolve templates
        if template_refs:
            try:
                await resolve_entity_ids(list(template_refs.keys()), "template", namespace)
            except EntityNotFoundError as e:
                for field_name in template_refs.get(e.identifier, [e.identifier]):
                    errors.append(f"Field '{field_name}': template '{e.identifier}' not found")

        # Batch resolve terminologies
        if terminology_refs:
            try:
                await resolve_entity_ids(list(terminology_refs.keys()), "terminology", namespace)
            except EntityNotFoundError as e:
                for field_name in terminology_refs.get(e.identifier, [e.identifier]):
                    errors.append(f"Field '{field_name}': terminology '{e.identifier}' not found or inactive")

        return errors
