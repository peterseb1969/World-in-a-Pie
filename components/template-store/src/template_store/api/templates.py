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
    pool_id: str = Query(default="wip-templates", description="Pool ID for the template")
):
    """
    Create a new template.

    The template is registered with the Registry service to get a unique ID
    (TPL-XXXXXX format).
    """
    try:
        return await TemplateService.create_template(request, pool_id=pool_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    pool_id: str = Query(default="wip-templates", description="Pool ID to query"),
    status: Optional[str] = Query(None, description="Filter by status"),
    extends: Optional[str] = Query(None, description="Filter by parent template"),
    code: Optional[str] = Query(None, description="Filter by template code (shows all versions)"),
    latest_only: bool = Query(False, description="Only return latest version of each template"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page")
):
    """
    List templates with pagination.

    Supports filtering by status, parent template, and code.
    Use latest_only=true to only show the most recent version of each template.
    """
    templates, total = await TemplateService.list_templates(
        status=status,
        extends=extends,
        code=code,
        latest_only=latest_only,
        page=page,
        page_size=page_size,
        pool_id=pool_id
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


@router.get("/by-code/{code}", response_model=TemplateResponse)
async def get_template_by_code(
    code: str,
    pool_id: str = Query(default="wip-templates", description="Pool ID to search in")
):
    """
    Get the latest version of a template by code.

    Returns the template with inheritance resolved.
    To get a specific version, use /by-code/{code}/versions/{version}.
    """
    versions = await TemplateService.get_template_versions(code, pool_id=pool_id)
    if not versions:
        raise HTTPException(status_code=404, detail="Template not found")
    # Return the first one (highest version since sorted descending)
    return versions[0]


@router.get("/by-code/{code}/raw", response_model=TemplateResponse)
async def get_template_by_code_raw(code: str):
    """
    Get the latest version of a template by code without inheritance resolution.
    """
    template = await TemplateService.get_template_raw(code=code)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/by-code/{code}/versions", response_model=TemplateListResponse)
async def get_template_versions(code: str):
    """
    Get all versions of a template by code.

    Returns all versions sorted by version number (newest first).
    This allows viewing the full version history of a template.
    """
    versions = await TemplateService.get_template_versions(code)
    if not versions:
        raise HTTPException(status_code=404, detail="Template not found")
    return TemplateListResponse(
        items=versions,
        total=len(versions),
        page=1,
        page_size=len(versions)
    )


@router.get("/by-code/{code}/versions/{version}", response_model=TemplateResponse)
async def get_template_by_code_and_version(code: str, version: int):
    """
    Get a specific version of a template.

    Args:
        code: Template code
        version: Version number

    Returns the template with inheritance resolved.
    """
    template = await TemplateService.get_template_by_code_and_version(code, version)
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
        - code: The template code
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


@router.post("/bulk", response_model=BulkOperationResponse)
async def create_templates_bulk(request: BulkCreateTemplateRequest):
    """
    Create multiple templates at once.

    Each template is registered with the Registry service.
    """
    try:
        results = await TemplateService.create_templates_bulk(
            templates=request.templates,
            created_by=request.created_by
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
