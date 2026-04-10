"""Template API endpoints."""

import math

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    check_namespace_permission,
    get_current_identity,
    resolve_bulk_ids,
    resolve_namespace_filter,
    resolve_or_404,
)

from ..models.api_models import (
    ActivateTemplateResponse,
    BulkResponse,
    BulkResultItem,
    CascadeResponse,
    CreateTemplateRequest,
    DeleteItem,
    TemplateListResponse,
    TemplateResponse,
    UpdateTemplateItem,
    ValidateTemplateRequest,
    ValidateTemplateResponse,
)
from ..services.dependency_service import DependencyService, TemplateDependencies
from ..services.inheritance_service import InheritanceService
from ..services.registry_client import RegistryError
from ..services.template_service import TemplateService
from .auth import require_api_key

router = APIRouter(
    prefix="/templates",
    tags=["Templates"],
    dependencies=[Depends(require_api_key)]
)


@router.post("", response_model=BulkResponse)
async def create_templates(
    items: list[CreateTemplateRequest] = Body(...),
    on_conflict: str = Query(
        default="error",
        description=(
            "How to handle a value collision with an existing template in the same "
            "namespace. 'error' (default): treat as error (existing behavior). "
            "'validate': identical schema returns 'unchanged'; compatible (added "
            "optional fields only) bumps to version N+1; incompatible returns an "
            "error item with error_code='incompatible_schema' and a structured diff."
        ),
    ),
):
    """
    Create one or more templates.

    Each template is registered with the Registry service to get a unique ID.
    Namespace is specified per item (default: "wip").
    For single items, uses direct creation. For multiple items, uses batch path.
    """
    if on_conflict not in ("error", "validate"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid on_conflict value: {on_conflict!r}. Must be 'error' or 'validate'.",
        )

    identity = get_current_identity()
    namespaces = {item.namespace for item in items}
    for ns in namespaces:
        await check_namespace_permission(identity, ns, "write")

    if on_conflict == "validate":
        # Per-item dispatch with conflict policy.
        try:
            results = await TemplateService.create_templates_with_conflict_policy(
                items=items,
                on_conflict=on_conflict,
            )
        except RegistryError as e:
            raise HTTPException(status_code=502, detail=f"Registry error: {e!s}") from e
    elif len(items) == 1:
        # Single-item fast path (preserves existing behavior).
        try:
            result = await TemplateService.create_template(items[0], namespace=items[0].namespace)
            results = [BulkResultItem(index=0, status="created", id=result.template_id, value=items[0].value, version=result.version)]
        except ValueError as e:
            results = [BulkResultItem(index=0, status="error", value=items[0].value, error=str(e))]
        except RegistryError as e:
            results = [BulkResultItem(index=0, status="error", value=items[0].value, error=f"Registry error: {e!s}")]
    else:
        # Multi-item bulk path (preserves existing behavior — one Registry batch call).
        try:
            results = await TemplateService.create_templates_bulk(
                templates=items,
                namespace=items[0].namespace,
            )
        except RegistryError as e:
            raise HTTPException(status_code=502, detail=f"Registry error: {e!s}") from e

    succeeded = sum(1 for r in results if r.status != "error")
    failed = sum(1 for r in results if r.status == "error")
    return BulkResponse(results=results, total=len(results), succeeded=succeeded, failed=failed)


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    namespace: str | None = Query(default=None, description="Namespace to query (omit for all)"),
    status: str | None = Query(None, description="Filter by status"),
    extends: str | None = Query(None, description="Filter by parent template"),
    value: str | None = Query(None, description="Filter by template value (shows all versions)"),
    latest_only: bool = Query(False, description="Only return latest version of each template"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page")
):
    """
    List templates with pagination.

    Supports filtering by status, parent template, and value.
    Use latest_only=true to only show the most recent version of each template.
    """
    identity = get_current_identity()
    ns_filter = await resolve_namespace_filter(identity, namespace)

    # Resolve extends synonym if provided
    if extends:
        extends = await resolve_or_404(extends, "template", namespace, param_name="extends")

    templates, total = await TemplateService.list_templates(
        status=status,
        extends=extends,
        value=value,
        latest_only=latest_only,
        page=page,
        page_size=page_size,
        ns_filter=ns_filter.query,
    )
    return TemplateListResponse(
        items=templates,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    version: int | None = Query(None, description="Specific version (default: latest)"),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
):
    """
    Get a template by ID or synonym value.

    Accepts canonical UUID or human-readable value (e.g., "PATIENT").
    Template IDs are stable across versions. Without a version parameter,
    returns the latest version. Returns the template with inheritance resolved
    (all fields from parent templates merged).
    """
    # Resolve synonym if not canonical UUID (e.g., "PATIENT" → UUID)
    resolved_id = await resolve_or_404(template_id, "template", namespace, param_name="template_id")

    template = await TemplateService.get_template(template_id=resolved_id, version=version)
    if not template and namespace:
        # Fallback: try as value (requires namespace)
        template = await TemplateService.get_template(value=template_id, version=version, namespace=namespace)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/{template_id}/raw", response_model=TemplateResponse)
async def get_template_raw(
    template_id: str,
    version: int | None = Query(None, description="Specific version (default: latest)"),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
):
    """
    Get a template by ID without inheritance resolution.

    Accepts canonical UUID or human-readable value (e.g., "PATIENT").
    Returns the template as stored in the database, without merging
    fields from parent templates.
    """
    resolved_id = await resolve_or_404(template_id, "template", namespace, param_name="template_id")

    template = await TemplateService.get_template(
        template_id=resolved_id, version=version, resolve_inheritance=False
    )
    if not template and namespace:
        template = await TemplateService.get_template(
            value=template_id, version=version, resolve_inheritance=False, namespace=namespace
        )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/by-value/{value}", response_model=TemplateResponse)
async def get_template_by_value(
    value: str,
    namespace: str | None = Query(default=None, description="Namespace to search in (omit for all)")
):
    """
    Get the latest version of a template by value.

    Returns the template with inheritance resolved.
    To get a specific version, use /by-value/{value}/versions/{version}.
    """
    if namespace:
        identity = get_current_identity()
        await check_namespace_permission(identity, namespace, "read")

    versions = await TemplateService.get_template_versions(value, namespace=namespace)
    if not versions:
        raise HTTPException(status_code=404, detail="Template not found")
    # Return the first one (highest version since sorted descending)
    return versions[0]


@router.get("/by-value/{value}/raw", response_model=TemplateResponse)
async def get_template_by_value_raw(
    value: str,
    namespace: str = Query(..., description="Namespace to search in"),
):
    """
    Get the latest version of a template by value without inheritance resolution.
    """
    template = await TemplateService.get_template_raw(value=value, namespace=namespace)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/by-value/{value}/versions", response_model=TemplateListResponse)
async def get_template_versions(
    value: str,
    namespace: str | None = Query(default=None, description="Namespace to search in (omit for all)"),
):
    """
    Get all versions of a template by value.

    Returns all versions sorted by version number (newest first).
    This allows viewing the full version history of a template.
    """
    if namespace:
        identity = get_current_identity()
        await check_namespace_permission(identity, namespace, "read")

    versions = await TemplateService.get_template_versions(value, namespace=namespace)
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


@router.put("", response_model=BulkResponse)
async def update_templates(
    items: list[UpdateTemplateItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
):
    """
    Update one or more templates by creating new versions.

    The template_id is stable across versions — updates create a new version
    document with the same template_id but incremented version number.
    """
    await resolve_bulk_ids(items, "template_id", "template", namespace=namespace)

    results = []
    for i, item in enumerate(items):
        try:
            result = await TemplateService.update_template(item.template_id, item)
            if not result:
                results.append(BulkResultItem(index=i, status="error", id=item.template_id, error="Template not found"))
            else:
                results.append(BulkResultItem(
                    index=i, status="updated", id=result.template_id,
                    value=result.value, version=result.version,
                    is_new_version=result.is_new_version,
                ))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.template_id, error=str(e)))
        except RegistryError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.template_id, error=f"Registry error: {e!s}"))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.get("/{template_id}/dependencies", response_model=TemplateDependencies)
async def get_template_dependencies(template_id: str):
    """
    Get dependencies of a template.

    Returns information about what depends on this template:
    - Templates that extend it
    - Documents that use it

    Use this to check before deactivating a template.
    """
    template_id = await resolve_or_404(template_id, "template", namespace=None, param_name="template_id")

    try:
        return await DependencyService.check_template_dependencies(template_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("", response_model=BulkResponse)
async def delete_templates(
    items: list[DeleteItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
):
    """
    Soft-delete one or more templates.

    Sets the template status to 'inactive'. Cannot delete templates that
    have other templates extending them.
    Set force=true per item to delete even if documents exist.
    """
    await resolve_bulk_ids(items, "id", "template", namespace=namespace)

    results = []
    for i, item in enumerate(items):
        try:
            # Check dependencies
            deps = await DependencyService.check_template_dependencies(item.id)

            # Block if child templates exist
            if deps.child_template_count > 0:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.id,
                    error=f"Cannot deactivate: {deps.child_template_count} template(s) extend this template"
                ))
                continue

            # Warn if documents exist (unless force)
            if deps.document_count > 0 and not item.force:
                results.append(BulkResultItem(
                    index=i, status="error", id=item.id,
                    error=f"Template has {deps.document_count} dependent document(s). Use force=true to deactivate anyway."
                ))
                continue

            deleted = await TemplateService.delete_template(
                item.id, item.updated_by, version=item.version,
                hard_delete=item.hard_delete,
            )
            if not deleted:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Template not found"))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error=str(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


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
    template_id = await resolve_or_404(template_id, "template", namespace=None, param_name="template_id")

    return await TemplateService.validate_template(
        template_id=template_id,
        check_terminologies=request.check_terminologies,
        check_templates=request.check_templates
    )


@router.post("/{template_id}/activate", response_model=ActivateTemplateResponse)
async def activate_template(
    template_id: str,
    namespace: str = Query(..., description="Namespace for the template"),
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
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    template_id = await resolve_or_404(template_id, "template", namespace, param_name="template_id")

    try:
        return await TemplateService.activate_template(
            template_id=template_id,
            namespace=namespace,
            dry_run=dry_run
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{template_id}/cascade", response_model=CascadeResponse)
async def cascade_template(template_id: str):
    """
    Cascade a parent template update to all child templates.

    After updating a parent template (which creates a new version), child
    templates still extend the old version. This endpoint creates new versions
    of all direct children that extend the new parent version.

    Only the `extends` pointer is updated — child-specific fields are preserved.
    """
    template_id = await resolve_or_404(template_id, "template", namespace=None, param_name="template_id")

    try:
        return await TemplateService.cascade_to_children(template_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=f"Registry error: {e!s}") from e


@router.get("/{template_id}/children", response_model=TemplateListResponse)
async def get_template_children(template_id: str):
    """
    Get templates that directly extend this template.
    """
    template_id = await resolve_or_404(template_id, "template", namespace=None, param_name="template_id")
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
    template_id = await resolve_or_404(template_id, "template", namespace=None, param_name="template_id")
    descendants = await InheritanceService.get_descendants(template_id)
    templates = [TemplateService._to_template_response(t) for t in descendants]
    return TemplateListResponse(
        items=templates,
        total=len(templates),
        page=1,
        page_size=len(templates)
    )
