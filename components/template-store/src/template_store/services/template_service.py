"""Template service for business logic."""

import contextlib
import logging
from datetime import UTC, datetime

from wip_auth.resolve import (
    EntityNotFoundError,
    resolve_entity_id,
    resolve_entity_ids,
)

from ..api.auth import get_identity_string
from ..models.api_models import (
    BulkResultItem,
    CascadeResponse,
    CascadeResult,
    CreateTemplateRequest,
    TemplateResponse,
    TemplateUpdateResponse,
    UpdateTemplateRequest,
    ValidateTemplateResponse,
    ValidationError,
    ValidationWarning,
)
from ..models.field import FieldDefinition, FieldType, ReferenceType
from ..models.template import ReportingConfig, Template, TemplateMetadata, TemplateUsage
from .def_store_client import DefStoreError, get_def_store_client
from .inheritance_service import InheritanceError, InheritanceService
from .nats_client import EventType, publish_template_event
from .reference_validator import ReferenceValidationError, get_reference_validator
from .registry_client import RegistryError, get_registry_client

logger = logging.getLogger(__name__)


class TemplateService:
    """Service for managing templates."""

    # =========================================================================
    # RELATIONSHIP-TEMPLATE STRUCTURAL VALIDATION
    # =========================================================================

    @staticmethod
    def _validate_relationship_template_shape(request: CreateTemplateRequest) -> None:
        """Enforce structural constraints on relationship templates.

        A relationship template must declare:
          - non-empty source_templates and target_templates (at the
            template level)
          - a source_ref and target_ref reference field with
            reference_type=document
          - the source_ref / target_ref field-level target_templates
            must equal the template-level lists (same set, order
            insensitive)

        For non-relationship templates (entity, reference), the
        template-level source_templates / target_templates must be
        empty — they only mean something when usage=relationship.

        Raises ValueError on violation.
        """
        usage = request.usage if request.usage is not None else TemplateUsage.ENTITY

        if usage != TemplateUsage.RELATIONSHIP:
            if request.source_templates or request.target_templates:
                raise ValueError(
                    "source_templates and target_templates may only be set "
                    f"when usage='relationship' (got usage='{usage.value}')"
                )
            return

        # usage == relationship from here on.
        if not request.source_templates:
            raise ValueError(
                "Relationship templates require a non-empty source_templates list"
            )
        if not request.target_templates:
            raise ValueError(
                "Relationship templates require a non-empty target_templates list"
            )

        fields_by_name = {f.name: f for f in request.fields}

        for endpoint, expected in (
            ("source_ref", request.source_templates),
            ("target_ref", request.target_templates),
        ):
            field = fields_by_name.get(endpoint)
            if field is None:
                raise ValueError(
                    f"Relationship templates require a '{endpoint}' reference field "
                    f"(missing in fields list)"
                )
            if field.reference_type != ReferenceType.DOCUMENT:
                raise ValueError(
                    f"Relationship template field '{endpoint}' must have "
                    f"reference_type='document' (got '{field.reference_type}')"
                )
            field_targets = field.target_templates or []
            if set(field_targets) != set(expected):
                raise ValueError(
                    f"Relationship template field '{endpoint}.target_templates' "
                    f"must match template-level {endpoint.replace('_ref', '_templates')}: "
                    f"expected {sorted(expected)}, got {sorted(field_targets)}"
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
            raise ValueError(
                f"full_text_indexed is only valid on type=string fields; "
                f"got {names_and_types}"
            )

        # Default ReportingConfig has sync_enabled=True — only an explicit
        # False conflicts. (reporting may be None; that means defaults.)
        if reporting is not None and not reporting.sync_enabled:
            indexed_names = ", ".join(f.name for f in indexed_fields)
            raise ValueError(
                f"full_text_indexed requires reporting.sync_enabled=true; "
                f"field(s) {indexed_names} cannot be indexed without sync"
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
            namespace: Namespace for the template (default: wip)

        Returns:
            Created template

        Raises:
            ValueError: If value already exists or extends invalid
            RegistryError: If Registry communication fails
        """
        # Validate status value
        is_draft = request.status == "draft"
        if request.status is not None and request.status not in ("active", "draft"):
            raise ValueError(f"Invalid status '{request.status}': must be 'active' or 'draft'")

        # Structural validation for relationship templates (purely
        # declarative — runs even in draft mode, no DB calls).
        TemplateService._validate_relationship_template_shape(request)

        # Structural validation for full_text_indexed fields (also
        # purely declarative — runs in draft mode too).
        TemplateService._validate_full_text_indexed_constraints(
            request.fields, request.reporting
        )

        # Check if value already exists within namespace — skip in restore mode
        # (restoring version 2+ of a template will find version 1 already present)
        is_restore = request.template_id and request.version is not None
        if not is_restore:
            existing = await Template.find_one({"namespace": namespace, "value": request.value})
            if existing:
                raise ValueError(f"Template with value '{request.value}' already exists in namespace '{namespace}'")

        # Validate extends if provided
        parent_namespace: str | None = None
        if request.extends:
            # Find latest version of parent (template_id is stable across versions)
            parent_results = await Template.find({"template_id": request.extends}).sort([("version", -1)]).limit(1).to_list()
            parent = parent_results[0] if parent_results else None
            if not parent:
                # Try by value within same namespace
                parent_results = await Template.find({"namespace": namespace, "value": request.extends}).sort([("version", -1)]).limit(1).to_list()
                parent = parent_results[0] if parent_results else None
                if parent:
                    request.extends = parent.template_id
                else:
                    if not is_draft:
                        raise ValueError(f"Parent template '{request.extends}' not found")
                    # Draft mode: store raw extends value even if parent doesn't exist yet
            if parent:
                parent_namespace = parent.namespace

        # Normalize all field references to canonical IDs — skip for drafts.
        # Normalization implicitly validates (raises EntityNotFoundError for invalid refs),
        # which is converted to ValueError for the API boundary.
        if not is_draft:
            try:
                await TemplateService._normalize_field_references(request.fields, namespace)
            except EntityNotFoundError as e:
                raise ValueError(str(e)) from e

        # Validate cross-namespace references (isolation mode check) — skip for drafts
        if not is_draft and parent_namespace:
            try:
                validator = get_reference_validator()
                await validator.validate_template_references(
                    template_namespace=namespace,
                    extends_template_namespace=parent_namespace,
                )
            except ReferenceValidationError as e:
                raise ValueError(f"Cross-namespace reference violation: {e.violations}") from e

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Restore mode: both template_id and version provided — skip Registry,
        # insert directly with the given ID and version
        if request.template_id and request.version is not None:
            template_id = request.template_id
            version = request.version
        else:
            # Normal mode: Register with Registry to get ID
            client = get_registry_client()
            template_id = await client.register_template(
                created_by=actor,
                namespace=namespace,
                entry_id=request.template_id,
            )
            version = 1

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
            usage=request.usage,
            source_templates=request.source_templates,
            target_templates=request.target_templates,
            versioned=request.versioned,
            fields=request.fields,
            rules=request.rules,
            metadata=request.metadata or TemplateMetadata(),
            reporting=request.reporting,
            status=request.status or "active",
            created_by=actor,
        )
        await template.insert()

        # Register auto-synonym for human-readable resolution
        # Only for version 1 (auto-synonym resolves to entity_id, stable across versions)
        # Skip for restore mode (synonyms are imported separately)
        # On failure, roll back the MongoDB document and re-raise
        if version == 1 and not is_restore:
            try:
                client = get_registry_client()
                await client.register_auto_synonym(
                    target_id=template_id,
                    namespace=namespace,
                    composite_key={
                        "ns": namespace,
                        "type": "template",
                        "value": request.value,
                    },
                    created_by=actor,
                )
            except RegistryError:
                logger.error(
                    "Auto-synonym registration failed for template %s — rolling back",
                    template_id,
                )
                await template.delete()
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
                results = await Template.find(query).sort([("version", -1)]).limit(1).to_list()
                template = results[0] if results else None
        elif value:
            # Value lookups require namespace — no silent fallback to "wip"
            if not namespace:
                raise ValueError("Namespace is required for value-based template lookup")
            query = {"namespace": namespace, "value": value}
            if version is not None:
                query["version"] = version
                template = await Template.find_one(query)
            else:
                results = await Template.find(query).sort([("version", -1)]).limit(1).to_list()
                template = results[0] if results else None
        else:
            return None

        if not template:
            return None

        if resolve_inheritance and template.extends:
            with contextlib.suppress(InheritanceError):
                template = await InheritanceService.resolve_template(template)

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
            # Use aggregation to get only the latest version of each value
            pipeline = [
                {"$match": query} if query else {"$match": {}},
                {"$sort": {"value": 1, "version": -1}},
                {"$group": {
                    "_id": "$value",
                    "doc": {"$first": "$$ROOT"}
                }},
                {"$replaceRoot": {"newRoot": "$doc"}},
                {"$sort": {"label": 1}}
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

            templates = await Template.find(query) \
                .sort([("value", 1), ("version", -1)]) \
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
        namespace: str | None = None
    ) -> list[TemplateResponse]:
        """
        Get all versions of a template by value.

        Args:
            value: Template value
            namespace: Namespace to search in (None for all namespaces)

        Returns:
            List of all versions, sorted by version descending (newest first)
        """
        query: dict = {"value": value}
        if namespace is not None:
            query["namespace"] = namespace
        templates = await Template.find(query) \
            .sort([("version", -1)]) \
            .to_list()

        return [TemplateService._to_template_response(t) for t in templates]

    @staticmethod
    async def get_template_by_value_and_version(
        value: str,
        version: int,
        resolve_inheritance: bool = True
    ) -> TemplateResponse | None:
        """
        Get a specific version of a template by value and version number.

        Args:
            value: Template value
            version: Version number
            resolve_inheritance: Whether to resolve inheritance

        Returns:
            Template if found, None otherwise
        """
        template = await Template.find_one({"value": value, "version": version})
        if not template:
            return None

        if resolve_inheritance and template.extends:
            with contextlib.suppress(InheritanceError):
                template = await InheritanceService.resolve_template(template)

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

        # Compare fields using JSON serialization
        if request.fields is not None:
            original_fields_json = json.dumps(
                [f.model_dump() for f in original.fields],
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
    def compute_template_compatibility(
        existing: Template,
        proposed: CreateTemplateRequest,
    ) -> tuple[str, dict]:
        """
        Compare a proposed CreateTemplateRequest against an existing Template.

        Returns a (verdict, diff) tuple where verdict is one of:
        - "identical": no schema differences — proposed matches existing exactly
        - "compatible": only differences are added optional fields (mandatory=false)
        - "incompatible": any other change (removed field, type change, made-required,
          identity_fields change, modified existing field, added required field)

        The diff dict captures the structured changes for caller-facing error messages.
        Used by POST /templates?on_conflict=validate to decide whether to silently
        adopt the existing template, bump the version, or reject with a structured
        diff.
        """
        import json

        existing_fields = {f.name: f for f in existing.fields}
        proposed_fields = {f.name: f for f in proposed.fields}

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
            # Compare full field definitions for any other change
            old_json = json.dumps(old.model_dump(), sort_keys=True, default=str)
            new_json = json.dumps(new.model_dump(), sort_keys=True, default=str)
            if old_json != new_json:
                modified_existing.append(name)

        identity_changed: dict | None = None
        if list(existing.identity_fields or []) != list(proposed.identity_fields or []):
            identity_changed = {
                "old": list(existing.identity_fields or []),
                "new": list(proposed.identity_fields or []),
            }

        diff = {
            "added_optional": added_optional,
            "added_required": added_required,
            "removed": sorted(removed_names),
            "changed_type": changed_type,
            "made_required": made_required,
            "modified_existing": modified_existing,
            "identity_changed": identity_changed,
        }

        is_incompatible = bool(
            added_required
            or removed_names
            or changed_type
            or made_required
            or modified_existing
            or identity_changed
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
        originals = await Template.find({"template_id": template_id}).sort([("version", -1)]).limit(1).to_list()
        original = originals[0] if originals else None
        if not original:
            return None

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

        # Calculate new version number (max version for this value + 1)
        max_version_template = await Template.find(
            {"value": original.value}
        ).sort([("version", -1)]).limit(1).to_list()
        new_version = max_version_template[0].version + 1 if max_version_template else 1

        # Determine the value for the new version
        new_value = request.value if request.value is not None else original.value

        # If value is changing, check it doesn't conflict with another template family
        if new_value != original.value:
            existing_other = await Template.find_one({
                "value": new_value,
                "namespace": original.namespace,
            })
            if existing_other:
                raise ValueError(f"Template with value '{new_value}' already exists")

        # Validate extends if changing
        extends_value = request.extends if request.extends is not None else original.extends
        if extends_value and extends_value != original.extends:
            parent_results = await Template.find({"template_id": extends_value}).sort([("version", -1)]).limit(1).to_list()
            parent = parent_results[0] if parent_results else None
            if not parent:
                parent_results = await Template.find({"value": extends_value}).sort([("version", -1)]).limit(1).to_list()
                parent = parent_results[0] if parent_results else None
                if parent:
                    extends_value = parent.template_id
                else:
                    raise ValueError(f"Parent template '{extends_value}' not found")

            # Check for circular inheritance
            if await InheritanceService.check_circular_inheritance(
                template_id, extends_value
            ):
                raise ValueError("Setting this parent would create circular inheritance")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Normalize field references to canonical IDs (same as create_template)
        new_fields = request.fields if request.fields is not None else original.fields
        if request.fields is not None:
            try:
                await TemplateService._normalize_field_references(new_fields, original.namespace)
            except EntityNotFoundError as e:
                raise ValueError(str(e)) from e

        # Validate full_text_indexed constraints against the merged final
        # state — fields may add/remove the flag, reporting.sync_enabled
        # may flip; both can break the invariant.
        new_reporting = (
            request.reporting if request.reporting is not None else original.reporting
        )
        TemplateService._validate_full_text_indexed_constraints(
            new_fields, new_reporting
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
            usage=original.usage,
            source_templates=original.source_templates,
            target_templates=original.target_templates,
            versioned=original.versioned,
            fields=new_fields,
            rules=request.rules if request.rules is not None else original.rules,
            metadata=request.metadata if request.metadata is not None else original.metadata,
            reporting=request.reporting if request.reporting is not None else original.reporting,
            status="active",
            created_at=datetime.now(UTC),
            created_by=actor,
            updated_at=datetime.now(UTC),
            updated_by=actor,
        )
        await new_template.insert()

        # Publish template updated event
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
            raise ValueError(
                f"Cannot delete template: {len(children)} template(s) extend it"
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
                raise ValueError(
                    f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')"
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
            results = await Template.find({"template_id": template_id}).sort([("version", -1)]).limit(1).to_list()
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

        # Publish template deleted event
        await publish_template_event(
            EventType.TEMPLATE_DELETED,
            TemplateService._template_to_event_payload(template),
            changed_by=actor
        )

        return True

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_templates_with_conflict_policy(
        items: list[CreateTemplateRequest],
        on_conflict: str,
    ) -> list[BulkResultItem]:
        """
        Per-item create dispatcher that applies an `on_conflict` policy.

        Behavior per item:
        - on_conflict='error' (default):
            * existing (namespace, value) → BulkResultItem(status='error',
              error='Template with value ... already exists ...')
            * else → standard create (status='created')
        - on_conflict='validate':
            * no existing → standard create (status='created')
            * identical existing → status='unchanged' (id/version of existing)
            * compatible existing (added optional fields only) → version N+1
              via update_template path (status='updated', is_new_version=True)
            * incompatible existing → status='error',
              error_code='incompatible_schema', details=<diff>

        For draft items (status='draft'), the conflict check is skipped because
        drafts may have unresolved references and live in their own value-space.
        Used by POST /templates?on_conflict=...

        Returns one BulkResultItem per input item, in input order.
        """
        results: list[BulkResultItem] = []

        for i, item in enumerate(items):
            try:
                if item.status == "draft" or on_conflict == "error":
                    # Fast path: defer to existing create_template (which raises
                    # ValueError on conflict). Drafts always take this path.
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
                            error=str(e),
                        ))
                    continue

                # on_conflict == "validate"
                existing_list = await Template.find(
                    {"namespace": item.namespace, "value": item.value}
                ).sort([("version", -1)]).limit(1).to_list()
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

                verdict, diff = TemplateService.compute_template_compatibility(
                    existing, item
                )

                if verdict == "identical":
                    results.append(BulkResultItem(
                        index=i,
                        status="unchanged",
                        id=existing.template_id,
                        value=existing.value,
                        version=existing.version,
                        details=diff,
                    ))
                elif verdict == "compatible":
                    # Bump version via update_template
                    update_req = UpdateTemplateRequest(
                        label=item.label,
                        description=item.description,
                        extends=item.extends,
                        extends_version=item.extends_version,
                        identity_fields=item.identity_fields,
                        fields=item.fields,
                        rules=item.rules,
                        metadata=item.metadata,
                        reporting=item.reporting,
                    )
                    update_resp = await TemplateService.update_template(
                        template_id=existing.template_id,
                        request=update_req,
                    )
                    results.append(BulkResultItem(
                        index=i,
                        status="updated",
                        id=existing.template_id,
                        value=item.value,
                        version=update_resp.version if update_resp else existing.version,
                        is_new_version=bool(update_resp and update_resp.is_new_version),
                        details=diff,
                    ))
                else:  # incompatible
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        id=existing.template_id,
                        value=item.value,
                        version=existing.version,
                        error_code="incompatible_schema",
                        error=(
                            f"Proposed schema for '{item.value}' is incompatible "
                            f"with existing version {existing.version}"
                        ),
                        details=diff,
                    ))
            except ValueError as e:
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    value=item.value,
                    error=str(e),
                ))

        return results

    @staticmethod
    async def create_templates_bulk(
        templates: list[CreateTemplateRequest],
        namespace: str,
        created_by: str | None = None,  # Deprecated: uses authenticated identity
    ) -> list[BulkResultItem]:
        """
        Create multiple templates.

        Args:
            templates: Templates to create
            created_by: Deprecated - uses authenticated identity
            namespace: Namespace for template registration

        Returns:
            List of operation results
        """
        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register all templates with Registry (empty composite keys)
        client = get_registry_client()
        registry_results = await client.register_templates_bulk(
            count=len(templates),
            created_by=actor,
            namespace=namespace,
        )

        results = []

        for i, (template_req, reg_result) in enumerate(zip(templates, registry_results, strict=False)):
            if reg_result["status"] == "error":
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    value=template_req.value,
                    error=reg_result.get("error")
                ))
                continue

            template_id = reg_result["registry_id"]

            # Check if template already exists in our DB (any version)
            existing_list = await Template.find({"template_id": template_id}).limit(1).to_list()
            existing = existing_list[0] if existing_list else None
            if existing:
                results.append(BulkResultItem(
                    index=i,
                    status="skipped",
                    id=template_id,
                    value=template_req.value,
                    error="Already exists"
                ))
                continue

            # Determine status (draft or active)
            req_status = template_req.status or "active"
            is_draft = req_status == "draft"

            # Structural validation for relationship templates
            try:
                TemplateService._validate_relationship_template_shape(template_req)
            except ValueError as e:
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    value=template_req.value,
                    error=str(e)
                ))
                continue

            # Normalize field references to canonical IDs — skip for drafts
            if not is_draft:
                try:
                    await TemplateService._normalize_field_references(
                        template_req.fields, namespace
                    )
                except (ValueError, EntityNotFoundError) as e:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        value=template_req.value,
                        error=str(e)
                    ))
                    continue

            # Create template document
            template = Template(
                template_id=template_id,
                namespace=namespace,
                value=template_req.value,
                label=template_req.label,
                description=template_req.description,
                extends=template_req.extends,
                extends_version=template_req.extends_version,
                identity_fields=template_req.identity_fields,
                usage=template_req.usage,
                source_templates=template_req.source_templates,
                target_templates=template_req.target_templates,
                versioned=template_req.versioned,
                fields=template_req.fields,
                rules=template_req.rules,
                metadata=template_req.metadata or TemplateMetadata(),
                reporting=template_req.reporting,
                status=req_status,
                created_by=actor,
            )
            await template.insert()

            # Register auto-synonym for human-readable resolution
            # Version is always 1 for bulk create (existing templates are skipped above)
            # On failure, roll back the MongoDB document and re-raise through bulk handler
            try:
                await client.register_auto_synonym(
                    target_id=template_id,
                    namespace=namespace,
                    composite_key={
                        "ns": namespace,
                        "type": "template",
                        "value": template_req.value,
                    },
                    created_by=actor,
                )
            except RegistryError:
                logger.error(
                    "Auto-synonym registration failed for template %s — rolling back",
                    template_id,
                )
                await template.delete()
                raise

            # Publish event — skip for drafts
            if not is_draft:
                await publish_template_event(
                    EventType.TEMPLATE_CREATED,
                    TemplateService._template_to_event_payload(template),
                    changed_by=actor
                )

            results.append(BulkResultItem(
                index=i,
                status="created",
                id=template_id,
                value=template_req.value
            ))

        return results

    # =========================================================================
    # VALIDATION
    # =========================================================================

    @staticmethod
    async def validate_template(
        template_id: str,
        check_terminologies: bool = True,
        check_templates: bool = True
    ) -> ValidateTemplateResponse:
        """
        Validate a template's references.

        Checks that:
        - terminology_ref fields point to existing terminologies
        - template_ref fields point to existing templates
        - extends points to existing template

        Args:
            template_id: Template to validate
            check_terminologies: Whether to check terminology refs
            check_templates: Whether to check template refs

        Returns:
            Validation response with errors and warnings
        """
        results = await Template.find({"template_id": template_id}).sort([("version", -1)]).limit(1).to_list()
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

        # Draft templates: validate via activation set and return cascade preview
        if template.status == "draft":
            activation_set = await TemplateService._build_activation_set(
                template, template.namespace
            )
            errors, warnings = await TemplateService._validate_activation_set(
                activation_set, template.namespace
            )
            # Collect other draft template IDs that would also activate
            other_ids = [
                t.template_id for t in activation_set
                if t.template_id != template_id
            ]
            return ValidateTemplateResponse(
                valid=len(errors) == 0,
                template_id=template_id,
                errors=errors,
                warnings=warnings,
                will_also_activate=other_ids if other_ids else None
            )

        errors = []
        warnings = []

        # Check extends
        if check_templates and template.extends:
            parent = await Template.find_one({"template_id": template.extends})
            if not parent:
                errors.append(ValidationError(
                    field="extends",
                    code="invalid_reference",
                    message=f"Parent template '{template.extends}' not found"
                ))

        # Check terminology refs in fields
        if check_terminologies:
            def_store = get_def_store_client()
            for field in template.fields:
                if field.terminology_ref:
                    try:
                        exists = await def_store.terminology_exists(
                            field.terminology_ref, namespace=template.namespace
                        )
                        if not exists:
                            errors.append(ValidationError(
                                field=f"fields.{field.name}.terminology_ref",
                                code="invalid_reference",
                                message=f"Terminology '{field.terminology_ref}' not found or inactive"
                            ))
                    except DefStoreError as e:
                        warnings.append(ValidationWarning(
                            field=f"fields.{field.name}.terminology_ref",
                            code="validation_failed",
                            message=f"Could not validate terminology: {e!s}"
                        ))

                if field.array_terminology_ref:
                    try:
                        exists = await def_store.terminology_exists(
                            field.array_terminology_ref, namespace=template.namespace
                        )
                        if not exists:
                            errors.append(ValidationError(
                                field=f"fields.{field.name}.array_terminology_ref",
                                code="invalid_reference",
                                message=f"Terminology '{field.array_terminology_ref}' not found or inactive"
                            ))
                    except DefStoreError as e:
                        warnings.append(ValidationWarning(
                            field=f"fields.{field.name}.array_terminology_ref",
                            code="validation_failed",
                            message=f"Could not validate terminology: {e!s}"
                        ))

        # Check template refs in fields
        if check_templates:
            for field in template.fields:
                if field.template_ref:
                    ref_template = await TemplateService._find_template_by_ref(
                        field.template_ref, template.namespace
                    )
                    if not ref_template:
                        errors.append(ValidationError(
                            field=f"fields.{field.name}.template_ref",
                            code="invalid_reference",
                            message=f"Template '{field.template_ref}' not found"
                        ))

                if field.array_template_ref:
                    ref_template = await TemplateService._find_template_by_ref(
                        field.array_template_ref, template.namespace
                    )
                    if not ref_template:
                        errors.append(ValidationError(
                            field=f"fields.{field.name}.array_template_ref",
                            code="invalid_reference",
                            message=f"Template '{field.array_template_ref}' not found"
                        ))

                # Check reference type fields
                if field.type.value == "reference":
                    # reference_type is required
                    if not field.reference_type:
                        errors.append(ValidationError(
                            field=f"fields.{field.name}.reference_type",
                            code="required",
                            message="reference_type is required for reference fields"
                        ))
                    else:
                        # Validate target_templates for document references
                        if field.reference_type.value == "document":
                            if not field.target_templates:
                                # target_templates is optional — document-store
                                # resolves references by identity lookup even
                                # without it.  Warn but don't block activation.
                                warnings.append(ValidationWarning(
                                    field=f"fields.{field.name}.target_templates",
                                    code="recommended",
                                    message="target_templates is recommended for document references"
                                ))
                            elif check_templates:
                                for tpl_ref in field.target_templates:
                                    ref_tpl = await TemplateService._find_template_by_ref(
                                        tpl_ref, template.namespace
                                    )
                                    if not ref_tpl:
                                        errors.append(ValidationError(
                                            field=f"fields.{field.name}.target_templates",
                                            code="invalid_reference",
                                            message=f"Template '{tpl_ref}' not found"
                                        ))

                        # Validate target_terminologies for term references
                        elif field.reference_type.value == "term":
                            if not field.target_terminologies:
                                errors.append(ValidationError(
                                    field=f"fields.{field.name}.target_terminologies",
                                    code="required",
                                    message="target_terminologies is required for term references"
                                ))
                            elif check_terminologies:
                                def_store = get_def_store_client()
                                for term_val in field.target_terminologies:
                                    try:
                                        exists = await def_store.terminology_exists(
                                            term_val, namespace=template.namespace
                                        )
                                        if not exists:
                                            errors.append(ValidationError(
                                                field=f"fields.{field.name}.target_terminologies",
                                                code="invalid_reference",
                                                message=f"Terminology '{term_val}' not found or inactive"
                                            ))
                                    except DefStoreError as e:
                                        warnings.append(ValidationWarning(
                                            field=f"fields.{field.name}.target_terminologies",
                                            code="validation_failed",
                                            message=f"Could not validate terminology: {e!s}"
                                        ))

        return ValidateTemplateResponse(
            valid=len(errors) == 0,
            template_id=template_id,
            errors=errors,
            warnings=warnings
        )

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
        parent_results = await Template.find({"template_id": template_id}).sort([("version", -1)]).limit(1).to_list()
        parent = parent_results[0] if parent_results else None
        if not parent:
            raise ValueError(f"Template '{template_id}' not found")

        # Find all versions of this template's value to get ALL possible old template_ids
        all_parent_versions = await Template.find({"value": parent.value}).to_list()
        all_parent_ids = [t.template_id for t in all_parent_versions]

        # Find all active children extending ANY version of this parent
        children = await Template.find({
            "extends": {"$in": all_parent_ids},
            "status": "active"
        }).to_list()

        if not children:
            return CascadeResponse(
                parent_template_id=parent.template_id,
                parent_value=parent.value,
                parent_version=parent.version,
                total=0,
                updated=0,
                unchanged=0,
                failed=0,
                results=[]
            )

        # Get authenticated identity
        actor = get_identity_string()

        results = []
        updated = 0
        unchanged = 0
        failed = 0

        # Group children by value (only cascade latest version of each child)
        children_by_value: dict[str, Template] = {}
        for child in children:
            existing = children_by_value.get(child.value)
            if not existing or child.version > existing.version:
                children_by_value[child.value] = child

        for child in children_by_value.values():
            try:
                # Skip if already extending the target parent AND the child
                # was created/updated after the parent's latest version
                # (meaning this child version already accounts for the parent update)
                if child.extends == template_id and child.created_at and parent.created_at and child.created_at >= parent.created_at:
                    unchanged += 1
                    results.append(CascadeResult(
                        value=child.value,
                        old_template_id=child.template_id,
                        status="unchanged"
                    ))
                    continue

                # Calculate new version for this child
                max_ver = await Template.find(
                    {"value": child.value}
                ).sort([("version", -1)]).limit(1).to_list()
                new_version = max_ver[0].version + 1 if max_ver else 1

                # Stable ID: reuse child's template_id (no Registry call)
                # Create new child version with updated extends pointer
                new_child = Template(
                    namespace=child.namespace,
                    template_id=child.template_id,
                    value=child.value,
                    label=child.label,
                    description=child.description,
                    version=new_version,
                    extends=template_id,  # Point to new parent
                    identity_fields=child.identity_fields,
                    fields=child.fields,  # Preserve child's own fields
                    rules=child.rules,
                    metadata=child.metadata,
                    reporting=child.reporting,
                    status="active",
                    created_at=datetime.now(UTC),
                    created_by=actor,
                    updated_at=datetime.now(UTC),
                    updated_by=actor,
                )
                await new_child.insert()

                # Publish event
                await publish_template_event(
                    EventType.TEMPLATE_UPDATED,
                    TemplateService._template_to_event_payload(new_child),
                    changed_by=actor
                )

                updated += 1
                results.append(CascadeResult(
                    value=child.value,
                    old_template_id=child.template_id,
                    new_template_id=child.template_id,
                    new_version=new_version,
                    status="updated"
                ))

            except Exception as e:
                failed += 1
                results.append(CascadeResult(
                    value=child.value,
                    old_template_id=child.template_id,
                    status="error",
                    error=str(e)
                ))

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

    # =========================================================================
    # DRAFT ACTIVATION
    # =========================================================================

    @staticmethod
    def _collect_template_references(template: Template) -> list[str]:
        """
        Collect all template IDs/values referenced by a template.

        Walks extends, field template_ref, array_template_ref, and
        reference-type target_templates.

        Returns:
            List of template IDs or values referenced
        """
        refs = []

        if template.extends:
            refs.append(template.extends)

        for field in template.fields:
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
        - If it points to a draft template -> add to set, recurse
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

                if target.status == "draft":
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

        Returns:
            Tuple of (errors, warnings) as ValidationError/ValidationWarning lists
        """
        from ..models.api_models import ValidationError, ValidationWarning

        # Build lookups for the activation set
        set_ids = {t.template_id for t in activation_set}
        set_values = {t.value for t in activation_set}

        errors = []
        warnings = []

        def_store = get_def_store_client()

        for template in activation_set:
            prefix = f"[{template.value}] "

            # Check extends
            if template.extends:
                # Resolve: is it in the set (by ID or value) or active?
                in_set = (
                    template.extends in set_ids or
                    template.extends in set_values
                )
                if not in_set:
                    parent = await TemplateService._find_template_by_ref(
                        template.extends, namespace
                    )
                    if not parent:
                        errors.append(ValidationError(
                            field=f"{prefix}extends",
                            code="invalid_reference",
                            message=f"Parent template '{template.extends}' not found"
                        ))
                    elif parent.status != "active":
                        errors.append(ValidationError(
                            field=f"{prefix}extends",
                            code="invalid_reference",
                            message=f"Parent template '{template.extends}' is {parent.status}, not active"
                        ))

            # Check fields
            for field in template.fields:
                # Terminology refs
                if field.terminology_ref:
                    try:
                        exists = await def_store.terminology_exists(
                            field.terminology_ref, namespace=namespace
                        )
                        if not exists:
                            errors.append(ValidationError(
                                field=f"{prefix}fields.{field.name}.terminology_ref",
                                code="invalid_reference",
                                message=f"Terminology '{field.terminology_ref}' not found or inactive"
                            ))
                    except DefStoreError as e:
                        warnings.append(ValidationWarning(
                            field=f"{prefix}fields.{field.name}.terminology_ref",
                            code="validation_failed",
                            message=f"Could not validate terminology: {e!s}"
                        ))

                if field.array_terminology_ref:
                    try:
                        exists = await def_store.terminology_exists(
                            field.array_terminology_ref, namespace=namespace
                        )
                        if not exists:
                            errors.append(ValidationError(
                                field=f"{prefix}fields.{field.name}.array_terminology_ref",
                                code="invalid_reference",
                                message=f"Terminology '{field.array_terminology_ref}' not found or inactive"
                            ))
                    except DefStoreError as e:
                        warnings.append(ValidationWarning(
                            field=f"{prefix}fields.{field.name}.array_terminology_ref",
                            code="validation_failed",
                            message=f"Could not validate terminology: {e!s}"
                        ))

                # Template refs
                if field.template_ref:
                    in_set = field.template_ref in set_ids or field.template_ref in set_values
                    if not in_set:
                        ref_tpl = await TemplateService._find_template_by_ref(
                            field.template_ref, namespace
                        )
                        if not ref_tpl:
                            errors.append(ValidationError(
                                field=f"{prefix}fields.{field.name}.template_ref",
                                code="invalid_reference",
                                message=f"Template '{field.template_ref}' not found"
                            ))
                        elif ref_tpl.status not in ("active",):
                            errors.append(ValidationError(
                                field=f"{prefix}fields.{field.name}.template_ref",
                                code="invalid_reference",
                                message=f"Template '{field.template_ref}' is {ref_tpl.status}, not active"
                            ))

                if field.array_template_ref:
                    in_set = field.array_template_ref in set_ids or field.array_template_ref in set_values
                    if not in_set:
                        ref_tpl = await TemplateService._find_template_by_ref(
                            field.array_template_ref, namespace
                        )
                        if not ref_tpl:
                            errors.append(ValidationError(
                                field=f"{prefix}fields.{field.name}.array_template_ref",
                                code="invalid_reference",
                                message=f"Template '{field.array_template_ref}' not found"
                            ))
                        elif ref_tpl.status not in ("active",):
                            errors.append(ValidationError(
                                field=f"{prefix}fields.{field.name}.array_template_ref",
                                code="invalid_reference",
                                message=f"Template '{field.array_template_ref}' is {ref_tpl.status}, not active"
                            ))

                # Reference type fields
                if field.type.value == "reference":
                    if not field.reference_type:
                        errors.append(ValidationError(
                            field=f"{prefix}fields.{field.name}.reference_type",
                            code="required",
                            message="reference_type is required for reference fields"
                        ))
                    else:
                        if field.reference_type.value == "document":
                            if not field.target_templates:
                                # target_templates is optional — warn only
                                warnings.append(ValidationWarning(
                                    field=f"{prefix}fields.{field.name}.target_templates",
                                    code="recommended",
                                    message="target_templates is recommended for document references"
                                ))
                            else:
                                for tpl_val in field.target_templates:
                                    in_set = tpl_val in set_values or tpl_val in set_ids
                                    if not in_set:
                                        ref_tpl = await TemplateService._find_template_by_ref(
                                            tpl_val, namespace
                                        )
                                        if not ref_tpl:
                                            errors.append(ValidationError(
                                                field=f"{prefix}fields.{field.name}.target_templates",
                                                code="invalid_reference",
                                                message=f"Template '{tpl_val}' not found"
                                            ))
                                        elif ref_tpl.status not in ("active",):
                                            errors.append(ValidationError(
                                                field=f"{prefix}fields.{field.name}.target_templates",
                                                code="invalid_reference",
                                                message=f"Template '{tpl_val}' is {ref_tpl.status}, not active"
                                            ))

                        elif field.reference_type.value == "term":
                            if not field.target_terminologies:
                                errors.append(ValidationError(
                                    field=f"{prefix}fields.{field.name}.target_terminologies",
                                    code="required",
                                    message="target_terminologies is required for term references"
                                ))
                            else:
                                for term_val in field.target_terminologies:
                                    try:
                                        exists = await def_store.terminology_exists(
                                            term_val, namespace=namespace
                                        )
                                        if not exists:
                                            errors.append(ValidationError(
                                                field=f"{prefix}fields.{field.name}.target_terminologies",
                                                code="invalid_reference",
                                                message=f"Terminology '{term_val}' not found or inactive"
                                            ))
                                    except DefStoreError as e:
                                        warnings.append(ValidationWarning(
                                            field=f"{prefix}fields.{field.name}.target_terminologies",
                                            code="validation_failed",
                                            message=f"Could not validate terminology: {e!s}"
                                        ))

        return errors, warnings

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
        results = await Template.find(query).sort([("version", -1)]).limit(1).to_list()
        template = results[0] if results else None
        if not template:
            raise ValueError(f"Template '{template_id}' not found")
        if template.status != "draft":
            raise ValueError(
                f"Template '{template_id}' is '{template.status}', not 'draft'. "
                f"Only draft templates can be activated."
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

        # Build known_templates from activation set for cross-resolution
        known_templates = {}
        for t in activation_set:
            known_templates[t.value] = t.template_id
            known_templates[t.template_id] = t.template_id

        # Normalize all references to canonical IDs (include reserved for activation set)
        activation_statuses = ["active", "reserved"]
        try:
            for t in activation_set:
                await TemplateService._normalize_field_references(
                    t.fields, namespace,
                    known_templates=known_templates,
                    include_statuses=activation_statuses,
                )
                # Also resolve extends (known_templates checked first, then Registry)
                if t.extends and t.extends in known_templates:
                    t.extends = known_templates[t.extends]
                elif t.extends:
                    t.extends = await resolve_entity_id(
                        t.extends, "template", namespace,
                        include_statuses=activation_statuses,
                    )
        except EntityNotFoundError as e:
            raise ValueError(str(e)) from e

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
            usage=t.usage,
            source_templates=t.source_templates,
            target_templates=t.target_templates,
            versioned=t.versioned,
            fields=t.fields,
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
    async def _find_template_by_ref(
        ref: str,
        namespace: str
    ) -> Template | None:
        """
        Find a template by reference (ID or value).

        Returns the latest version. Tries ID lookup first, then value lookup within namespace.

        Args:
            ref: Template ID or value
            namespace: Namespace for value lookups
        """
        # Try by template_id within namespace first (return latest version)
        results = await Template.find({"template_id": ref, "namespace": namespace}).sort([("version", -1)]).limit(1).to_list()
        if results:
            return results[0]
        # Fallback: try by template_id cross-namespace (for external refs)
        results = await Template.find({"template_id": ref}).sort([("version", -1)]).limit(1).to_list()
        if results:
            return results[0]
        # Try by value within namespace (return latest version)
        results = await Template.find({"namespace": namespace, "value": ref}).sort([("version", -1)]).limit(1).to_list()
        return results[0] if results else None

    @staticmethod
    async def _normalize_field_references(
        fields: list,
        namespace: str,
        known_templates: dict[str, str] | None = None,
        include_statuses: list[str] | None = None,
    ) -> None:
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
        if template_refs:
            resolved_templates = await resolve_entity_ids(
                list(template_refs), "template", namespace,
                include_statuses=include_statuses,
                bypass_cache=True,
            )
        if terminology_refs:
            resolved_terminologies = await resolve_entity_ids(
                list(terminology_refs), "terminology", namespace,
                include_statuses=include_statuses,
                bypass_cache=True,
            )

        # Merge known_templates into resolved map
        if known_templates:
            resolved_templates.update(known_templates)

        def _resolve_tpl(ref: str) -> str:
            return resolved_templates[ref]

        def _resolve_term(ref: str) -> str:
            return resolved_terminologies[ref]

        # Phase 3: Apply resolved IDs back to fields
        for field in fields:
            if field.target_templates:
                field.target_templates = [_resolve_tpl(r) for r in field.target_templates]
            if field.template_ref:
                field.template_ref = _resolve_tpl(field.template_ref)
            if field.array_template_ref:
                field.array_template_ref = _resolve_tpl(field.array_template_ref)
            if field.terminology_ref:
                field.terminology_ref = _resolve_term(field.terminology_ref)
            if field.array_terminology_ref:
                field.array_terminology_ref = _resolve_term(field.array_terminology_ref)
            if field.target_terminologies:
                field.target_terminologies = [_resolve_term(r) for r in field.target_terminologies]

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
