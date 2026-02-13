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
        pool_id: str = "wip-templates"
    ) -> TemplateResponse:
        """
        Create a new template.

        1. Validate extends reference if provided
        2. Register with Registry to get ID
        3. Create template document in MongoDB

        Args:
            request: Creation request
            pool_id: Pool ID for the template (default: wip-templates)

        Returns:
            Created template

        Raises:
            ValueError: If code already exists or extends invalid
            RegistryError: If Registry communication fails
        """
        # Check if code already exists within pool
        existing = await Template.find_one({"pool_id": pool_id, "code": request.code})
        if existing:
            raise ValueError(f"Template with code '{request.code}' already exists in pool '{pool_id}'")

        # Validate extends if provided
        parent_pool_id: str | None = None
        if request.extends:
            parent = await Template.find_one({"template_id": request.extends})
            if not parent:
                # Try by code within same pool
                parent = await Template.find_one({"pool_id": pool_id, "code": request.extends})
                if parent:
                    request.extends = parent.template_id
                else:
                    raise ValueError(f"Parent template '{request.extends}' not found")
            parent_pool_id = parent.pool_id

        # Validate references if requested (default: True)
        if request.validate_references:
            validation_errors = await TemplateService._validate_field_references(
                request.fields
            )
            if validation_errors:
                raise ValueError(
                    f"Invalid references: {'; '.join(validation_errors)}"
                )

        # Validate cross-namespace references (isolation mode check)
        # Only validate extends reference - terminology refs are resolved by code at runtime
        if parent_pool_id:
            try:
                validator = get_reference_validator()
                await validator.validate_template_references(
                    template_pool_id=pool_id,
                    extends_template_pool_id=parent_pool_id,
                    terminology_pool_ids=None,  # Terminology refs are by code, validated at doc creation
                )
            except ReferenceValidationError as e:
                raise ValueError(f"Cross-namespace reference violation: {e.violations}")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register with Registry to get ID
        client = get_registry_client()
        template_id = await client.register_template(
            code=request.code,
            name=request.name,
            created_by=actor,
            pool_id=pool_id
        )

        # Create template document
        template = Template(
            pool_id=pool_id,
            template_id=template_id,
            code=request.code,
            name=request.name,
            description=request.description,
            extends=request.extends,
            identity_fields=request.identity_fields,
            fields=request.fields,
            rules=request.rules,
            metadata=request.metadata or TemplateMetadata(),
            reporting=request.reporting,
            created_by=actor,
        )
        await template.insert()

        # Publish template created event
        await publish_template_event(
            EventType.TEMPLATE_CREATED,
            TemplateService._template_to_event_payload(template),
            changed_by=actor
        )

        return TemplateService._to_template_response(template)

    @staticmethod
    async def get_template(
        template_id: Optional[str] = None,
        code: Optional[str] = None,
        resolve_inheritance: bool = True,
        pool_id: Optional[str] = None
    ) -> Optional[TemplateResponse]:
        """
        Get a template by ID or code.

        Args:
            template_id: Template ID (e.g., 'TPL-000001')
            code: Template code (e.g., 'PERSON')
            resolve_inheritance: Whether to resolve inheritance
            pool_id: Pool ID to search in (if None, searches globally by ID)

        Returns:
            Template if found, None otherwise
        """
        if template_id:
            # ID lookups can be global (for cross-namespace refs in open mode)
            query = {"template_id": template_id}
            if pool_id:
                query["pool_id"] = pool_id
            template = await Template.find_one(query)
        elif code:
            # Code lookups require pool_id (defaults to wip-templates)
            ns = pool_id or "wip-templates"
            template = await Template.find_one({"pool_id": ns, "code": code})
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
        code: Optional[str] = None
    ) -> Optional[TemplateResponse]:
        """
        Get a template by ID or code without inheritance resolution.

        Args:
            template_id: Template ID
            code: Template code

        Returns:
            Template as stored, without inheritance resolution
        """
        return await TemplateService.get_template(
            template_id=template_id,
            code=code,
            resolve_inheritance=False
        )

    @staticmethod
    async def list_templates(
        status: Optional[str] = None,
        extends: Optional[str] = None,
        code: Optional[str] = None,
        latest_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        pool_id: str = "wip-templates"
    ) -> tuple[list[TemplateResponse], int]:
        """
        List templates with pagination.

        Args:
            status: Filter by status (active, deprecated, inactive)
            extends: Filter by parent template ID
            code: Filter by template code (shows all versions of that code)
            latest_only: If True, only return the latest version of each template
            page: Page number (1-indexed)
            page_size: Items per page
            pool_id: Pool ID to query (default: wip-templates)

        Returns:
            Tuple of (templates, total_count)
        """
        query = {"pool_id": pool_id}
        if status:
            query["status"] = status
        if extends:
            query["extends"] = extends
        if code:
            query["code"] = code

        if latest_only and not code:
            # Use aggregation to get only the latest version of each code
            pipeline = [
                {"$match": query} if query else {"$match": {}},
                {"$sort": {"code": 1, "version": -1}},
                {"$group": {
                    "_id": "$code",
                    "doc": {"$first": "$$ROOT"}
                }},
                {"$replaceRoot": {"newRoot": "$doc"}},
                {"$sort": {"name": 1}}
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
                .sort([("code", 1), ("version", -1)]) \
                .skip(skip) \
                .limit(page_size) \
                .to_list()

        return (
            [TemplateService._to_template_response(t) for t in templates],
            total
        )

    @staticmethod
    async def get_template_versions(
        code: str,
        pool_id: str = "wip-templates"
    ) -> list[TemplateResponse]:
        """
        Get all versions of a template by code.

        Args:
            code: Template code
            pool_id: Pool ID to search in (default: wip-templates)

        Returns:
            List of all versions, sorted by version descending (newest first)
        """
        templates = await Template.find({"pool_id": pool_id, "code": code}) \
            .sort([("version", -1)]) \
            .to_list()

        return [TemplateService._to_template_response(t) for t in templates]

    @staticmethod
    async def get_template_by_code_and_version(
        code: str,
        version: int,
        resolve_inheritance: bool = True
    ) -> Optional[TemplateResponse]:
        """
        Get a specific version of a template by code and version number.

        Args:
            code: Template code
            version: Version number
            resolve_inheritance: Whether to resolve inheritance

        Returns:
            Template if found, None otherwise
        """
        template = await Template.find_one({"code": code, "version": version})
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
        if request.code is not None and request.code != original.code:
            return True
        if request.name is not None and request.name != original.name:
            return True
        if request.description is not None and request.description != original.description:
            return True
        if request.extends is not None and request.extends != original.extends:
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
        original = await Template.find_one({"template_id": template_id})
        if not original:
            return None

        # Check if anything has actually changed
        if not TemplateService._template_has_changed(original, request):
            # No changes - return current template info
            return TemplateUpdateResponse(
                template_id=original.template_id,
                code=original.code,
                version=original.version,
                is_new_version=False,
                previous_version=None
            )

        # Calculate new version number (max version for this code + 1)
        max_version_template = await Template.find(
            {"code": original.code}
        ).sort([("version", -1)]).limit(1).to_list()
        new_version = max_version_template[0].version + 1 if max_version_template else 1

        # Determine the code for the new version
        new_code = request.code if request.code is not None else original.code

        # If code is changing, check it doesn't conflict with another template family
        if new_code != original.code:
            existing_other = await Template.find_one({
                "code": new_code,
                "code": {"$ne": original.code}  # Different template family
            })
            if existing_other:
                raise ValueError(f"Template with code '{new_code}' already exists")

        # Validate extends if changing
        extends_value = request.extends if request.extends is not None else original.extends
        if extends_value and extends_value != original.extends:
            parent = await Template.find_one({"template_id": extends_value})
            if not parent:
                parent = await Template.find_one({"code": extends_value})
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

        # Register new version with Registry to get a new template_id
        client = get_registry_client()
        new_template_id = await client.register_template(
            code=new_code,
            name=request.name if request.name is not None else original.name,
            version=new_version,
            created_by=actor
        )

        # Create new template document for this version
        new_template = Template(
            template_id=new_template_id,
            code=new_code,
            name=request.name if request.name is not None else original.name,
            description=request.description if request.description is not None else original.description,
            version=new_version,
            extends=extends_value if extends_value else None,
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
            template_id=new_template_id,
            code=new_code,
            version=new_version,
            is_new_version=True,
            previous_version=original.version
        )

    @staticmethod
    async def delete_template(
        template_id: str,
        updated_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> bool:
        """
        Soft-delete a template (set status to inactive).

        Args:
            template_id: Template to delete
            updated_by: Deprecated - uses authenticated identity

        Returns:
            True if deleted, False if not found
        """
        template = await Template.find_one({"template_id": template_id})
        if not template:
            return False

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
        created_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> list[BulkOperationResult]:
        """
        Create multiple templates.

        Args:
            templates: Templates to create
            created_by: Deprecated - uses authenticated identity

        Returns:
            List of operation results
        """
        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register all templates with Registry
        client = get_registry_client()
        registry_results = await client.register_templates_bulk(
            templates=[{"code": t.code, "name": t.name} for t in templates],
            created_by=actor
        )

        results = []

        for i, (template_req, reg_result) in enumerate(zip(templates, registry_results)):
            if reg_result["status"] == "error":
                results.append(BulkOperationResult(
                    index=i,
                    status="error",
                    code=template_req.code,
                    error=reg_result.get("error")
                ))
                continue

            template_id = reg_result["registry_id"]

            # Check if template already exists in our DB
            existing = await Template.find_one({"template_id": template_id})
            if existing:
                results.append(BulkOperationResult(
                    index=i,
                    status="skipped",
                    id=template_id,
                    code=template_req.code,
                    error="Already exists"
                ))
                continue

            # Create template document
            template = Template(
                template_id=template_id,
                code=template_req.code,
                name=template_req.name,
                description=template_req.description,
                extends=template_req.extends,
                identity_fields=template_req.identity_fields,
                fields=template_req.fields,
                rules=template_req.rules,
                metadata=template_req.metadata or TemplateMetadata(),
                reporting=template_req.reporting,
                created_by=actor,
            )
            await template.insert()

            results.append(BulkOperationResult(
                index=i,
                status="created",
                id=template_id,
                code=template_req.code
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
        template = await Template.find_one({"template_id": template_id})
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
                        exists = await def_store.terminology_exists(field.terminology_ref)
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
                        exists = await def_store.terminology_exists(field.array_terminology_ref)
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
                    ref_template = await Template.find_one({"template_id": field.template_ref})
                    if not ref_template:
                        ref_template = await Template.find_one({"code": field.template_ref})
                    if not ref_template:
                        errors.append(ValidationError(
                            field=f"fields.{field.name}.template_ref",
                            code="invalid_reference",
                            message=f"Template '{field.template_ref}' not found"
                        ))

                if field.array_template_ref:
                    ref_template = await Template.find_one({"template_id": field.array_template_ref})
                    if not ref_template:
                        ref_template = await Template.find_one({"code": field.array_template_ref})
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
                                for tpl_code in field.target_templates:
                                    ref_tpl = await Template.find_one({"code": tpl_code})
                                    if not ref_tpl:
                                        errors.append(ValidationError(
                                            field=f"fields.{field.name}.target_templates",
                                            code="invalid_reference",
                                            message=f"Template '{tpl_code}' not found"
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
                                for term_code in field.target_terminologies:
                                    try:
                                        exists = await def_store.terminology_exists(term_code)
                                        if not exists:
                                            errors.append(ValidationError(
                                                field=f"fields.{field.name}.target_terminologies",
                                                code="invalid_reference",
                                                message=f"Terminology '{term_code}' not found or inactive"
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
        # Find the target parent template
        parent = await Template.find_one({"template_id": template_id})
        if not parent:
            raise ValueError(f"Template '{template_id}' not found")

        # Find all versions of this template's code to get ALL possible old template_ids
        all_parent_versions = await Template.find({"code": parent.code}).to_list()
        all_parent_ids = [t.template_id for t in all_parent_versions]

        # Find all active children extending ANY version of this parent
        children = await Template.find({
            "extends": {"$in": all_parent_ids},
            "status": "active"
        }).to_list()

        if not children:
            return CascadeResponse(
                parent_template_id=parent.template_id,
                parent_code=parent.code,
                parent_version=parent.version,
                total=0,
                updated=0,
                unchanged=0,
                failed=0,
                results=[]
            )

        # Get authenticated identity
        actor = get_identity_string()
        client = get_registry_client()

        results = []
        updated = 0
        unchanged = 0
        failed = 0

        # Group children by code (only cascade latest version of each child)
        children_by_code: dict[str, Template] = {}
        for child in children:
            existing = children_by_code.get(child.code)
            if not existing or child.version > existing.version:
                children_by_code[child.code] = child

        for child in children_by_code.values():
            try:
                # Skip if already extending the target parent
                if child.extends == template_id:
                    unchanged += 1
                    results.append(CascadeResult(
                        code=child.code,
                        old_template_id=child.template_id,
                        status="unchanged"
                    ))
                    continue

                # Calculate new version for this child
                max_ver = await Template.find(
                    {"code": child.code}
                ).sort([("version", -1)]).limit(1).to_list()
                new_version = max_ver[0].version + 1 if max_ver else 1

                # Register new version with Registry
                new_child_id = await client.register_template(
                    code=child.code,
                    name=child.name,
                    version=new_version,
                    created_by=actor
                )

                # Create new child version with updated extends pointer
                new_child = Template(
                    template_id=new_child_id,
                    code=child.code,
                    name=child.name,
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
                    code=child.code,
                    old_template_id=child.template_id,
                    new_template_id=new_child_id,
                    new_version=new_version,
                    status="updated"
                ))

            except Exception as e:
                failed += 1
                results.append(CascadeResult(
                    code=child.code,
                    old_template_id=child.template_id,
                    status="error",
                    error=str(e)
                ))

        return CascadeResponse(
            parent_template_id=parent.template_id,
            parent_code=parent.code,
            parent_version=parent.version,
            total=len(results),
            updated=updated,
            unchanged=unchanged,
            failed=failed,
            results=results
        )

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _template_to_event_payload(t: Template) -> dict:
        """Convert Template document to event payload for NATS publishing."""
        return {
            "template_id": t.template_id,
            "code": t.code,
            "name": t.name,
            "description": t.description,
            "version": t.version,
            "extends": t.extends,
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
            code=t.code,
            name=t.name,
            description=t.description,
            version=t.version,
            extends=t.extends,
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
    async def _validate_field_references(fields: list) -> list[str]:
        """
        Validate terminology_ref and template_ref values in fields.

        Args:
            fields: List of field definitions

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
                        terminology = await def_store.get_terminology(term_ref)
                        if terminology is None:
                            errors.append(f"Field '{field_name}': terminology '{term_ref}' not found")
                        elif terminology.get('status') != 'active':
                            errors.append(f"Field '{field_name}': terminology '{term_ref}' is {terminology.get('status')}")
                    except DefStoreError as e:
                        errors.append(f"Field '{field_name}': could not validate terminology '{term_ref}': {e}")

            # Check template_ref for object fields
            if field_type == 'object':
                tpl_ref = field.template_ref if hasattr(field, 'template_ref') else field.get('template_ref')
                if tpl_ref:
                    # Look up by code (template_ref stores code, not ID)
                    referenced = await Template.find_one({"code": tpl_ref})
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
                            terminology = await def_store.get_terminology(array_term_ref)
                            if terminology is None:
                                errors.append(f"Field '{field_name}[]': terminology '{array_term_ref}' not found")
                            elif terminology.get('status') != 'active':
                                errors.append(f"Field '{field_name}[]': terminology '{array_term_ref}' is {terminology.get('status')}")
                        except DefStoreError as e:
                            errors.append(f"Field '{field_name}[]': could not validate terminology '{array_term_ref}': {e}")

                if array_item_type == 'object':
                    array_tpl_ref = field.array_template_ref if hasattr(field, 'array_template_ref') else field.get('array_template_ref')
                    if array_tpl_ref:
                        referenced = await Template.find_one({"code": array_tpl_ref})
                        if referenced is None:
                            errors.append(f"Field '{field_name}[]': template '{array_tpl_ref}' not found")
                        elif referenced.status != 'active':
                            errors.append(f"Field '{field_name}[]': template '{array_tpl_ref}' is {referenced.status}")

        return errors
