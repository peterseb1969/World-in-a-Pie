"""Template API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models.api_models import (
    CreateTemplateRequest,
    UpdateTemplateRequest,
    TemplateResponse,
    TemplateListResponse,
    BulkCreateTemplateRequest,
    BulkOperationResponse,
    ValidateTemplateRequest,
    ValidateTemplateResponse,
)
from ..services.template_service import TemplateService
from ..services.registry_client import RegistryError
from ..services.inheritance_service import InheritanceService, InheritanceError
from .auth import require_api_key


router = APIRouter(
    prefix="/templates",
    tags=["Templates"],
    dependencies=[Depends(require_api_key)]
)


@router.post("", response_model=TemplateResponse)
async def create_template(request: CreateTemplateRequest):
    """
    Create a new template.

    The template is registered with the Registry service to get a unique ID
    (TPL-XXXXXX format).
    """
    try:
        return await TemplateService.create_template(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    status: Optional[str] = Query(None, description="Filter by status"),
    extends: Optional[str] = Query(None, description="Filter by parent template"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page")
):
    """
    List templates with pagination.

    Supports filtering by status and parent template.
    """
    templates, total = await TemplateService.list_templates(
        status=status,
        extends=extends,
        page=page,
        page_size=page_size
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
async def get_template_by_code(code: str):
    """
    Get a template by code.

    Returns the template with inheritance resolved.
    """
    template = await TemplateService.get_template(code=code)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/by-code/{code}/raw", response_model=TemplateResponse)
async def get_template_by_code_raw(code: str):
    """
    Get a template by code without inheritance resolution.
    """
    template = await TemplateService.get_template_raw(code=code)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(template_id: str, request: UpdateTemplateRequest):
    """
    Update a template.

    Creates a new version of the template. If the code changes, a synonym
    is added in the Registry so both old and new codes resolve to the same ID.
    """
    try:
        template = await TemplateService.update_template(template_id, request)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=503, detail=f"Registry error: {str(e)}")


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    updated_by: Optional[str] = Query(None, description="User performing deletion")
):
    """
    Soft-delete a template.

    Sets the template status to 'inactive'. Cannot delete templates that
    have other templates extending them.
    """
    try:
        deleted = await TemplateService.delete_template(template_id, updated_by)
        if not deleted:
            raise HTTPException(status_code=404, detail="Template not found")
        return {"status": "deleted", "template_id": template_id}
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
