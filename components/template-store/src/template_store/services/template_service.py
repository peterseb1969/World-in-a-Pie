"""Template service for business logic."""

from datetime import datetime, timezone
from typing import Optional

from ..models.template import Template, TemplateMetadata
from ..models.api_models import (
    CreateTemplateRequest,
    UpdateTemplateRequest,
    TemplateResponse,
    BulkOperationResult,
    ValidateTemplateResponse,
    ValidationError,
    ValidationWarning,
)
from .registry_client import get_registry_client, RegistryError
from .def_store_client import get_def_store_client, DefStoreError
from .inheritance_service import InheritanceService, InheritanceError
from .nats_client import publish_template_event, EventType


class TemplateService:
    """Service for managing templates."""

    # =========================================================================
    # TEMPLATE CRUD OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_template(
        request: CreateTemplateRequest
    ) -> TemplateResponse:
        """
        Create a new template.

        1. Validate extends reference if provided
        2. Register with Registry to get ID
        3. Create template document in MongoDB

        Args:
            request: Creation request

        Returns:
            Created template

        Raises:
            ValueError: If code already exists or extends invalid
            RegistryError: If Registry communication fails
        """
        # Check if code already exists
        existing = await Template.find_one({"code": request.code})
        if existing:
            raise ValueError(f"Template with code '{request.code}' already exists")

        # Validate extends if provided
        if request.extends:
            parent = await Template.find_one({"template_id": request.extends})
            if not parent:
                # Try by code
                parent = await Template.find_one({"code": request.extends})
                if parent:
                    request.extends = parent.template_id
                else:
                    raise ValueError(f"Parent template '{request.extends}' not found")

        # Register with Registry to get ID
        client = get_registry_client()
        template_id = await client.register_template(
            code=request.code,
            name=request.name,
            created_by=request.created_by
        )

        # Create template document
        template = Template(
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
            created_by=request.created_by,
        )
        await template.insert()

        # Publish template created event
        await publish_template_event(
            EventType.TEMPLATE_CREATED,
            TemplateService._template_to_event_payload(template),
            changed_by=request.created_by
        )

        return TemplateService._to_template_response(template)

    @staticmethod
    async def get_template(
        template_id: Optional[str] = None,
        code: Optional[str] = None,
        resolve_inheritance: bool = True
    ) -> Optional[TemplateResponse]:
        """
        Get a template by ID or code.

        Args:
            template_id: Template ID (e.g., 'TPL-000001')
            code: Template code (e.g., 'PERSON')
            resolve_inheritance: Whether to resolve inheritance

        Returns:
            Template if found, None otherwise
        """
        if template_id:
            template = await Template.find_one({"template_id": template_id})
        elif code:
            template = await Template.find_one({"code": code})
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
        page_size: int = 50
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

        Returns:
            Tuple of (templates, total_count)
        """
        query = {}
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
        code: str
    ) -> list[TemplateResponse]:
        """
        Get all versions of a template by code.

        Args:
            code: Template code

        Returns:
            List of all versions, sorted by version descending (newest first)
        """
        templates = await Template.find({"code": code}) \
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
    async def update_template(
        template_id: str,
        request: UpdateTemplateRequest
    ) -> Optional[TemplateResponse]:
        """
        Update a template by creating a new version.

        Creates a NEW template document with incremented version, rather than
        modifying in-place. This allows multiple versions to exist simultaneously,
        supporting gradual migration of documents to new template versions.

        Args:
            template_id: Template to update (any version)
            request: Update request

        Returns:
            New template version, or None if original not found
        """
        original = await Template.find_one({"template_id": template_id})
        if not original:
            return None

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

        # Register new version with Registry to get a new template_id
        client = get_registry_client()
        new_template_id = await client.register_template(
            code=new_code,
            name=request.name if request.name is not None else original.name,
            version=new_version,
            created_by=request.updated_by
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
            created_by=request.updated_by,
            updated_at=datetime.now(timezone.utc),
            updated_by=request.updated_by,
        )
        await new_template.insert()

        # Publish template updated event
        await publish_template_event(
            EventType.TEMPLATE_UPDATED,
            TemplateService._template_to_event_payload(new_template),
            changed_by=request.updated_by
        )

        return TemplateService._to_template_response(new_template)

    @staticmethod
    async def delete_template(
        template_id: str,
        updated_by: Optional[str] = None
    ) -> bool:
        """
        Soft-delete a template (set status to inactive).

        Args:
            template_id: Template to delete
            updated_by: User performing the deletion

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

        # Deactivate template
        template.status = "inactive"
        template.updated_at = datetime.now(timezone.utc)
        template.updated_by = updated_by
        await template.save()

        # Publish template deleted event
        await publish_template_event(
            EventType.TEMPLATE_DELETED,
            TemplateService._template_to_event_payload(template),
            changed_by=updated_by
        )

        return True

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_templates_bulk(
        templates: list[CreateTemplateRequest],
        created_by: Optional[str] = None
    ) -> list[BulkOperationResult]:
        """
        Create multiple templates.

        Args:
            templates: Templates to create
            created_by: User creating the templates

        Returns:
            List of operation results
        """
        # Register all templates with Registry
        client = get_registry_client()
        registry_results = await client.register_templates_bulk(
            templates=[{"code": t.code, "name": t.name} for t in templates],
            created_by=created_by
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
                created_by=created_by,
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

        return ValidateTemplateResponse(
            valid=len(errors) == 0,
            template_id=template_id,
            errors=errors,
            warnings=warnings
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
