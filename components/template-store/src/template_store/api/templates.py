"""Template API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models.api_models import (
    CreateTemplateRequest,
    UpdateTemplateRequest,
    TemplateResponse,
    TemplateUpdateResponse,
    TemplateListResponse,
    BulkCreateTemplateRequest,
    BulkOperationResponse,
    ValidateTemplateRequest,
    ValidateTemplateResponse,
    CascadeResponse,
    ActivateTemplateResponse,
)
from ..services.template_service import TemplateService
from ..services.registry_client import RegistryError
from ..services.inheritance_service import InheritanceService, InheritanceError
from ..services.dependency_service import DependencyService, TemplateDependencies
from .auth import require_api_key


router = APIRouter(
    prefix="/templates",
    tags=["Templates"],
    dependencies=[Depends(require_api_key)]
)


@router.post("", response_model=TemplateResponse)
async def create_template(
    request: CreateTemplateRequest,
):
    """
    Create a new template.

    The template is registered with the Registry service to get a unique ID.
    Namespace is specified in the request body (default: "wip").
    """
    try:
        return await TemplateService.create_template(request, namespace=request.namespace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    namespace: Optional[str] = Query(default=None, description="Namespace to query (omit for all)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    extends: Optional[str] = Query(None, description="Filter by parent template"),
    value: Optional[str] = Query(None, description="Filter by template value (shows all versions)"),
    latest_only: bool = Query(False, description="Only return latest version of each template"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page")
):
    """
    List templates with pagination.

    Supports filtering by status, parent template, and value.
    Use latest_only=true to only show the most recent version of each template.
    """
    templates, total = await TemplateService.list_templates(
        status=status,
        extends=extends,
        value=value,
        latest_only=latest_only,
        page=page,
        page_size=page_size,
        namespace=namespace
    )
    return TemplateListResponse(
        items=templates,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: str):
    """
    Get a template by ID.

    Returns the template with inheritance resolved (all fields from parent
    templates merged).
    """
    template = await TemplateService.get_template(template_id=template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/{template_id}/raw", response_model=TemplateResponse)
async def get_template_raw(template_id: str):
    """
    Get a template by ID without inheritance resolution.

    Returns the template as stored in the database, without merging
    fields from parent templates.
    """
    template = await TemplateService.get_template_raw(template_id=template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/by-value/{value}", response_model=TemplateResponse)
async def get_template_by_value(
    value: str,
    namespace: Optional[str] = Query(default=None, description="Namespace to search in (omit for all)")
):
    """
    Get the latest version of a template by value.

    Returns the template with inheritance resolved.
    To get a specific version, use /by-value/{value}/versions/{version}.
    """
    versions = await TemplateService.get_template_versions(value, namespace=namespace)
    if not versions:
        raise HTTPException(status_code=404, detail="Template not found")
    # Return the first one (highest version since sorted descending)
    return versions[0]


@router.get("/by-value/{value}/raw", response_model=TemplateResponse)
async def get_template_by_value_raw(value: str):
    """
    Get the latest version of a template by value without inheritance resolution.
    """
    template = await TemplateService.get_template_raw(value=value)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/by-value/{value}/versions", response_model=TemplateListResponse)
async def get_template_versions(value: str):
    """
    Get all versions of a template by value.

    Returns all versions sorted by version number (newest first).
    This allows viewing the full version history of a template.
    """
    versions = await TemplateService.get_template_versions(value)
    if not versions:
        raise HTTPException(status_code=404, detail="Template not found")
    return TemplateListResponse(
        items=versions,
        total=len(versions),
        page=1,
        page_size=len(versions)
    )


@router.get("/by-value/{value}/versions/{version}", response_model=TemplateResponse)
async def get_template_by_value_and_version(value: str, version: int):
    """
    Get a specific version of a template.

    Args:
        value: Template value
        version: Version number

    Returns the template with inheritance resolved.
    """
    template = await TemplateService.get_template_by_value_and_version(value, version)
    if not template:
        raise HTTPException(status_code=404, detail="Template version not found")
    return template


@router.put("/{template_id}", response_model=TemplateUpdateResponse)
async def update_template(template_id: str, request: UpdateTemplateRequest):
    """
    Update a template by creating a new version.

    This creates a NEW template document with a new template_id and incremented
    version number. The original version remains unchanged, allowing documents
    to continue referencing it. This supports gradual migration scenarios where
    different systems may use different template versions.

    If no changes are detected, returns the current template info without
    creating a new version.

    Returns:
        TemplateUpdateResponse with:
        - template_id: The ID of the template (new if changed, existing if unchanged)
        - value: The template value
        - version: The version number
        - is_new_version: True if a new version was created
        - previous_version: Previous version number if created, None if unchanged
    """
    try:
        result = await TemplateService.update_template(template_id, request)
        if not result:
            raise HTTPException(status_code=404, detail="Template not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.get("/{template_id}/dependencies", response_model=TemplateDependencies)
async def get_template_dependencies(template_id: str):
    """
    Get dependencies of a template.

    Returns information about what depends on this template:
    - Templates that extend it
    - Documents that use it

    Use this to check before deactivating a template.
    """
    try:
        return await DependencyService.check_template_dependencies(template_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    updated_by: Optional[str] = Query(None, description="User performing deletion"),
    force: bool = Query(False, description="Force deletion even if documents exist")
):
    """
    Soft-delete a template.

    Sets the template status to 'inactive'. Cannot delete templates that
    have other templates extending them.

    If documents exist that use this template, returns a warning unless force=true.
    Use GET /{template_id}/dependencies to check dependencies first.
    """
    try:
        # Check dependencies first
        deps = await DependencyService.check_template_dependencies(template_id)

        # Block if child templates exist
        if deps.child_template_count > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Cannot deactivate: template has children",
                    "message": deps.warning_message,
                    "child_count": deps.child_template_count,
                    "children": deps.child_templates
                }
            )

        # Warn if documents exist (unless force)
        if deps.document_count > 0 and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Template has dependent documents",
                    "message": deps.warning_message,
                    "document_count": deps.document_count,
                    "hint": "Use force=true to deactivate anyway"
                }
            )

        deleted = await TemplateService.delete_template(template_id, updated_by)
        if not deleted:
            raise HTTPException(status_code=404, detail="Template not found")

        response = {"status": "deleted", "template_id": template_id}
        if deps.document_count > 0:
            response["warning"] = f"{deps.document_count} documents still reference this template"

        return response
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{template_id}/validate", response_model=ValidateTemplateResponse)
async def validate_template(
    template_id: str,
    request: ValidateTemplateRequest = ValidateTemplateRequest()
):
    """
    Validate a template's references.

    Checks that:
    - terminology_ref fields point to existing terminologies in Def-Store
    - template_ref fields point to existing templates
    - extends points to an existing template
    """
    return await TemplateService.validate_template(
        template_id=template_id,
        check_terminologies=request.check_terminologies,
        check_templates=request.check_templates
    )


@router.post("/{template_id}/activate", response_model=ActivateTemplateResponse)
async def activate_template(
    template_id: str,
    namespace: str = Query(default="wip", description="Namespace for the template"),
    dry_run: bool = Query(default=False, description="Preview activation without making changes")
):
    """
    Activate a draft template.

    Validates all references and transitions the template from draft to active.
    If the template references other draft templates, those are activated too
    (cascading activation). All-or-nothing: if any template in the set fails
    validation, none are activated.

    Use dry_run=true to preview what would be activated without making changes.
    """
    try:
        return await TemplateService.activate_template(
            template_id=template_id,
            namespace=namespace,
            dry_run=dry_run
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bulk", response_model=BulkOperationResponse)
async def create_templates_bulk(
    request: BulkCreateTemplateRequest,
):
    """
    Create multiple templates at once.

    Each template is registered with the Registry service.
    Namespace is read from each template's namespace field (default: "wip").
    """
    try:
        # Determine namespace: use the first template's namespace (all should share the same)
        namespace = request.templates[0].namespace if request.templates else "wip"
        results = await TemplateService.create_templates_bulk(
            templates=request.templates,
            created_by=request.created_by,
            namespace=namespace,
        )
        succeeded = sum(1 for r in results if r.status == "created")
        failed = sum(1 for r in results if r.status == "error")
        return BulkOperationResponse(
            results=results,
            total=len(results),
            succeeded=succeeded,
            failed=failed
        )
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.post("/{template_id}/cascade", response_model=CascadeResponse)
async def cascade_template(template_id: str):
    """
    Cascade a parent template update to all child templates.

    After updating a parent template (which creates a new version), child
    templates still extend the old version. This endpoint creates new versions
    of all direct children that extend the new parent version.

    Only the `extends` pointer is updated — child-specific fields are preserved.
    """
    try:
        return await TemplateService.cascade_to_children(template_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.get("/{template_id}/children", response_model=TemplateListResponse)
async def get_template_children(template_id: str):
    """
    Get templates that directly extend this template.
    """
    children = await InheritanceService.get_children(template_id)
    templates = [TemplateService._to_template_response(t) for t in children]
    return TemplateListResponse(
        items=templates,
        total=len(templates),
        page=1,
        page_size=len(templates)
    )


@router.get("/{template_id}/descendants", response_model=TemplateListResponse)
async def get_template_descendants(template_id: str):
    """
    Get all templates that extend this template (directly or indirectly).
    """
    descendants = await InheritanceService.get_descendants(template_id)
    templates = [TemplateService._to_template_response(t) for t in descendants]
    return TemplateListResponse(
        items=templates,
        total=len(templates),
        page=1,
        page_size=len(templates)
    )
