"""Template service for business logic."""

from datetime import datetime, timezone
from typing import Optional

from ..models.template import Template, TemplateMetadata
from ..models.api_models import (
    CreateTemplateRequest,
    UpdateTemplateRequest,
    TemplateResponse,
    TemplateUpdateResponse,
    BulkOperationResult,
    ValidateTemplateResponse,
    ValidationError,
    ValidationWarning,
    CascadeResult,
    CascadeResponse,
)
from .registry_client import get_registry_client, RegistryError
from .def_store_client import get_def_store_client, DefStoreError
from .inheritance_service import InheritanceService, InheritanceError
from .nats_client import publish_template_event, EventType
from .reference_validator import get_reference_validator, ReferenceValidationError

# Import identity helper from wip-auth
# This returns the authenticated identity, not the client-provided value
from ..api.auth import get_identity_string


class TemplateService:
    """Service for managing templates."""

    # =========================================================================
    # TEMPLATE CRUD OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_template(
        request: CreateTemplateRequest,
        namespace: str = "wip"
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

        # Check if value already exists within namespace
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
        # Normalization implicitly validates (raises ValueError for invalid refs),
        # so a separate validation pass is not needed.
        if not is_draft:
            await TemplateService._normalize_field_references(request.fields, namespace)

        # Validate cross-namespace references (isolation mode check) — skip for drafts
        if not is_draft and parent_namespace:
            try:
                validator = get_reference_validator()
                await validator.validate_template_references(
                    template_namespace=namespace,
                    extends_template_namespace=parent_namespace,
                )
            except ReferenceValidationError as e:
                raise ValueError(f"Cross-namespace reference violation: {e.violations}")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register with Registry to get ID (empty composite key — always fresh)
        client = get_registry_client()
        template_id = await client.register_template(
            created_by=actor,
            namespace=namespace
        )

        # Create template document
        template = Template(
            namespace=namespace,
            template_id=template_id,
            value=request.value,
            label=request.label,
            description=request.description,
            extends=request.extends,
            extends_version=request.extends_version,
            identity_fields=request.identity_fields,
            fields=request.fields,
            rules=request.rules,
            metadata=request.metadata or TemplateMetadata(),
            reporting=request.reporting,
            status=request.status or "active",
            created_by=actor,
        )
        await template.insert()

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
        template_id: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[int] = None,
        resolve_inheritance: bool = True,
        namespace: Optional[str] = None
    ) -> Optional[TemplateResponse]:
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
            # Value lookups require namespace (defaults to wip)
            ns = namespace or "wip"
            query = {"namespace": ns, "value": value}
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
            try:
                template = await InheritanceService.resolve_template(template)
            except InheritanceError:
                # Return unresolved if inheritance fails
                pass

        return TemplateService._to_template_response(template)

    @staticmethod
    async def get_template_raw(
        template_id: Optional[str] = None,
        value: Optional[str] = None
    ) -> Optional[TemplateResponse]:
        """
        Get a template by ID or value without inheritance resolution.

        Args:
            template_id: Template ID
            value: Template value

        Returns:
            Template as stored, without inheritance resolution
        """
        return await TemplateService.get_template(
            template_id=template_id,
            value=value,
            resolve_inheritance=False
        )

    @staticmethod
    async def list_templates(
        status: Optional[str] = None,
        extends: Optional[str] = None,
        value: Optional[str] = None,
        latest_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        namespace: Optional[str] = None
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
            namespace: Namespace to query (None returns all)

        Returns:
            Tuple of (templates, total_count)
        """
        query: dict = {}
        if namespace:
            query["namespace"] = namespace
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
            count_pipeline = pipeline + [{"$count": "total"}]
            count_result = await Template.aggregate(count_pipeline).to_list()
            total = count_result[0]["total"] if count_result else 0

            # Get paginated results
            paginated_pipeline = pipeline + [
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size}
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
        namespace: Optional[str] = None
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
    ) -> Optional[TemplateResponse]:
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
            try:
                template = await InheritanceService.resolve_template(template)
            except InheritanceError:
                pass

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
        if request.identity_fields is not None:
            if request.identity_fields != original.identity_fields:
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
    async def update_template(
        template_id: str,
        request: UpdateTemplateRequest
    ) -> Optional[TemplateUpdateResponse]:
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
                "value": {"$ne": original.value}  # Different template family
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

        # Stable ID: reuse original template_id (no Registry call for updates)
        # Create new template document for this version
        new_template = Template(
            template_id=original.template_id,
            value=new_value,
            label=request.label if request.label is not None else original.label,
            description=request.description if request.description is not None else original.description,
            version=new_version,
            extends=extends_value if extends_value else None,
            extends_version=request.extends_version if request.extends_version is not None else original.extends_version,
            identity_fields=request.identity_fields if request.identity_fields is not None else original.identity_fields,
            fields=request.fields if request.fields is not None else original.fields,
            rules=request.rules if request.rules is not None else original.rules,
            metadata=request.metadata if request.metadata is not None else original.metadata,
            reporting=request.reporting if request.reporting is not None else original.reporting,
            status="active",
            created_at=datetime.now(timezone.utc),
            created_by=actor,
            updated_at=datetime.now(timezone.utc),
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
        updated_by: Optional[str] = None,  # Deprecated: uses authenticated identity
        version: Optional[int] = None
    ) -> bool:
        """
        Soft-delete a template version (set status to inactive).

        Args:
            template_id: Template to delete
            updated_by: Deprecated - uses authenticated identity
            version: Specific version to deactivate (None = latest)

        Returns:
            True if deleted, False if not found
        """
        # Find the specific version or latest
        if version is not None:
            template = await Template.find_one({"template_id": template_id, "version": version})
        else:
            results = await Template.find({"template_id": template_id}).sort([("version", -1)]).limit(1).to_list()
            template = results[0] if results else None
        if not template:
            return False

        if template.status == "inactive":
            return True  # Already inactive

        # Check if any templates extend this one
        children = await InheritanceService.get_children(template_id)
        if children:
            raise ValueError(
                f"Cannot delete template: {len(children)} template(s) extend it"
            )

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Deactivate template
        template.status = "inactive"
        template.updated_at = datetime.now(timezone.utc)
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
    async def create_templates_bulk(
        templates: list[CreateTemplateRequest],
        created_by: Optional[str] = None,  # Deprecated: uses authenticated identity
        namespace: str = "wip",
    ) -> list[BulkOperationResult]:
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

        for i, (template_req, reg_result) in enumerate(zip(templates, registry_results)):
            if reg_result["status"] == "error":
                results.append(BulkOperationResult(
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
                results.append(BulkOperationResult(
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

            # Normalize field references to canonical IDs — skip for drafts
            if not is_draft:
                try:
                    await TemplateService._normalize_field_references(
                        template_req.fields, namespace
                    )
                except ValueError as e:
                    results.append(BulkOperationResult(
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
                fields=template_req.fields,
                rules=template_req.rules,
                metadata=template_req.metadata or TemplateMetadata(),
                reporting=template_req.reporting,
                status=req_status,
                created_by=actor,
            )
            await template.insert()

            # Publish event — skip for drafts
            if not is_draft:
                await publish_template_event(
                    EventType.TEMPLATE_CREATED,
                    TemplateService._template_to_event_payload(template),
                    changed_by=actor
                )

            results.append(BulkOperationResult(
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
                            message=f"Could not validate terminology: {str(e)}"
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
                            message=f"Could not validate terminology: {str(e)}"
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
                                errors.append(ValidationError(
                                    field=f"fields.{field.name}.target_templates",
                                    code="required",
                                    message="target_templates is required for document references"
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
                                            message=f"Could not validate terminology: {str(e)}"
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
                # Skip if already extending the target parent
                if child.extends == template_id:
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
                    created_at=datetime.now(timezone.utc),
                    created_by=actor,
                    updated_at=datetime.now(timezone.utc),
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
                            message=f"Could not validate terminology: {str(e)}"
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
                            message=f"Could not validate terminology: {str(e)}"
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
                                errors.append(ValidationError(
                                    field=f"{prefix}fields.{field.name}.target_templates",
                                    code="required",
                                    message="target_templates is required for document references"
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
                                            message=f"Could not validate terminology: {str(e)}"
                                        ))

        return errors, warnings

    @staticmethod
    async def activate_template(
        template_id: str,
        namespace: str = "wip",
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

        results = await Template.find({"template_id": template_id}).sort([("version", -1)]).limit(1).to_list()
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

        # Normalize all references to canonical IDs
        for t in activation_set:
            await TemplateService._normalize_field_references(
                t.fields, namespace, known_templates=known_templates
            )
            # Also normalize extends if it's a value (not already an ID)
            if t.extends:
                resolved = await TemplateService._resolve_to_template_id(
                    t.extends, namespace, known_templates=known_templates
                )
                if resolved:
                    t.extends = resolved

        # Activate all templates in the set
        actor = get_identity_string()
        now = datetime.now(timezone.utc)
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
    ) -> Optional[Template]:
        """
        Find a template by reference (ID or value).

        Returns the latest version. Tries ID lookup first, then value lookup within namespace.

        Args:
            ref: Template ID or value
            namespace: Namespace for value lookups
        """
        # Try by template_id first (return latest version)
        results = await Template.find({"template_id": ref}).sort([("version", -1)]).limit(1).to_list()
        if results:
            return results[0]
        # Try by value within namespace (return latest version)
        results = await Template.find({"namespace": namespace, "value": ref}).sort([("version", -1)]).limit(1).to_list()
        return results[0] if results else None

    @staticmethod
    async def _resolve_to_template_id(
        ref: str,
        namespace: str,
        known_templates: dict[str, str] | None = None
    ) -> str:
        """
        Resolve a template reference to a canonical template_id.

        Accepts either a template_id or a value.
        Returns the canonical template_id.

        Args:
            ref: Template ID or value
            namespace: Namespace to search in for value lookups
            known_templates: Optional dict {value->template_id, template_id->template_id}
                for resolving within an activation set
        """
        # Check known_templates first (for activation set cross-references)
        if known_templates and ref in known_templates:
            return known_templates[ref]

        # Try by template_id (stable ID — check if any version exists)
        exists = await Template.find({"template_id": ref}).limit(1).to_list()
        if exists:
            return ref

        # It's a value — resolve to template_id of the latest active version
        results = await Template.find(
            {"namespace": namespace, "value": ref, "status": "active"}
        ).sort([("version", -1)]).limit(1).to_list()
        if not results:
            raise ValueError(
                f"No active template with value '{ref}' found in namespace '{namespace}'"
            )
        return results[0].template_id

    @staticmethod
    async def _resolve_to_terminology_id(
        ref: str,
        namespace: str = "wip"
    ) -> str:
        """
        Resolve a terminology reference to a canonical terminology_id.

        Accepts either a terminology_id or a value.
        Returns the canonical terminology_id.

        Args:
            ref: Terminology ID or value
            namespace: Namespace to search in for value lookups
        """
        def_store = get_def_store_client()

        # Try as ID first
        terminology = await def_store.get_terminology(terminology_id=ref, namespace=namespace)
        if terminology:
            if terminology.get("status") != "active":
                raise ValueError(f"Terminology '{ref}' is {terminology.get('status')}, not active")
            return terminology["terminology_id"]

        # Try as value
        terminology = await def_store.get_terminology(terminology_value=ref, namespace=namespace)
        if not terminology:
            raise ValueError(f"No terminology with value '{ref}' found")
        if terminology.get("status") != "active":
            raise ValueError(f"Terminology '{ref}' is {terminology.get('status')}, not active")
        return terminology["terminology_id"]

    @staticmethod
    async def _normalize_field_references(
        fields: list,
        namespace: str,
        known_templates: dict[str, str] | None = None
    ) -> None:
        """
        Normalize all reference fields to canonical IDs.

        Resolves template values to template_ids and terminology values to
        terminology_ids. Mutates fields in-place.

        Args:
            fields: List of FieldDefinition objects
            namespace: Namespace for lookups
            known_templates: Optional dict for activation set cross-references
        """
        for field in fields:
            # Template references
            if field.target_templates:
                field.target_templates = [
                    await TemplateService._resolve_to_template_id(
                        ref, namespace, known_templates
                    )
                    for ref in field.target_templates
                ]

            if field.template_ref:
                field.template_ref = await TemplateService._resolve_to_template_id(
                    field.template_ref, namespace, known_templates
                )

            if field.array_template_ref:
                field.array_template_ref = await TemplateService._resolve_to_template_id(
                    field.array_template_ref, namespace, known_templates
                )

            # Terminology references
            if field.terminology_ref:
                field.terminology_ref = await TemplateService._resolve_to_terminology_id(
                    field.terminology_ref, namespace=namespace
                )

            if field.array_terminology_ref:
                field.array_terminology_ref = await TemplateService._resolve_to_terminology_id(
                    field.array_terminology_ref, namespace=namespace
                )

            if field.target_terminologies:
                field.target_terminologies = [
                    await TemplateService._resolve_to_terminology_id(
                        ref, namespace=namespace
                    )
                    for ref in field.target_terminologies
                ]

    @staticmethod
    async def _validate_field_references(fields: list, namespace: str = "wip") -> list[str]:
        """
        Validate terminology_ref and template_ref values in fields.

        Args:
            fields: List of field definitions
            namespace: Namespace for cross-service lookups

        Returns:
            List of error messages for invalid references
        """
        errors = []
        def_store = get_def_store_client()

        for field in fields:
            field_name = field.name if hasattr(field, 'name') else field.get('name', 'unknown')
            field_type = field.type if hasattr(field, 'type') else field.get('type')

            # Check terminology_ref for term fields
            if field_type == 'term':
                term_ref = field.terminology_ref if hasattr(field, 'terminology_ref') else field.get('terminology_ref')
                if term_ref:
                    try:
                        exists = await def_store.terminology_exists(
                            term_ref, namespace=namespace
                        )
                        if not exists:
                            errors.append(f"Field '{field_name}': terminology '{term_ref}' not found or inactive")
                    except DefStoreError as e:
                        errors.append(f"Field '{field_name}': could not validate terminology '{term_ref}': {e}")

            # Check template_ref for object fields
            if field_type == 'object':
                tpl_ref = field.template_ref if hasattr(field, 'template_ref') else field.get('template_ref')
                if tpl_ref:
                    referenced = await TemplateService._find_template_by_ref(tpl_ref, namespace)
                    if referenced is None:
                        errors.append(f"Field '{field_name}': template '{tpl_ref}' not found")
                    elif referenced.status != 'active':
                        errors.append(f"Field '{field_name}': template '{tpl_ref}' is {referenced.status}")

            # Check array item references
            if field_type == 'array':
                array_item_type = field.array_item_type if hasattr(field, 'array_item_type') else field.get('array_item_type')

                if array_item_type == 'term':
                    array_term_ref = field.array_terminology_ref if hasattr(field, 'array_terminology_ref') else field.get('array_terminology_ref')
                    if array_term_ref:
                        try:
                            exists = await def_store.terminology_exists(
                                array_term_ref, namespace=namespace
                            )
                            if not exists:
                                errors.append(f"Field '{field_name}[]': terminology '{array_term_ref}' not found or inactive")
                        except DefStoreError as e:
                            errors.append(f"Field '{field_name}[]': could not validate terminology '{array_term_ref}': {e}")

                if array_item_type == 'object':
                    array_tpl_ref = field.array_template_ref if hasattr(field, 'array_template_ref') else field.get('array_template_ref')
                    if array_tpl_ref:
                        referenced = await TemplateService._find_template_by_ref(
                            array_tpl_ref, namespace
                        )
                        if referenced is None:
                            errors.append(f"Field '{field_name}[]': template '{array_tpl_ref}' not found")
                        elif referenced.status != 'active':
                            errors.append(f"Field '{field_name}[]': template '{array_tpl_ref}' is {referenced.status}")

        return errors
