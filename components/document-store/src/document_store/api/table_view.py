"""Table View API endpoint for per-template flattened document views.

Provides a "template as table" view where each document is one or more rows,
with array fields optionally flattened into multiple rows.

Use cases:
- Transactional apps treating templates as database tables
- CSV exports for downstream processing
- Reporting and data analysis
"""

from enum import Enum
from itertools import product
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from wip_auth import check_namespace_permission, get_current_identity, resolve_or_404

from ..models.document import Document, DocumentStatus
from ..services.template_store_client import get_template_store_client
from .auth import require_api_key

router = APIRouter(prefix="/table", tags=["Table View"])


# ============================================================================
# Models
# ============================================================================

class TableFormat(str, Enum):
    """Output format for table view."""
    JSON = "json"
    CSV = "csv"


class TableColumn(BaseModel):
    """Metadata about a column in the table view."""
    name: str = Field(..., description="Column name (field path)")
    label: str = Field(..., description="Human-readable label")
    type: str = Field(..., description="Field type (string, number, etc.)")
    is_array: bool = Field(default=False, description="Whether this is an array field")
    is_flattened: bool = Field(default=False, description="Whether arrays were flattened")


class TableViewResponse(BaseModel):
    """Response from table view endpoint."""
    template_id: str
    template_value: str
    template_label: str
    columns: list[TableColumn] = Field(
        ...,
        description="Column definitions for the table"
    )
    rows: list[dict[str, Any]] = Field(
        ...,
        description="Flattened document rows"
    )
    total_documents: int = Field(
        ...,
        description="Total number of source documents (before flattening)"
    )
    total_rows: int = Field(
        ...,
        description="Total rows after flattening"
    )
    page: int
    page_size: int
    pages: int
    array_handling: str = Field(
        ...,
        description="How arrays were handled: 'flattened' or 'json'"
    )


# ============================================================================
# Helper Functions
# ============================================================================

def _get_field_value(data: dict[str, Any], field_path: str) -> Any:
    """Get a nested field value from document data."""
    parts = field_path.split(".")
    value = data
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _flatten_field_path(prefix: str, field_name: str) -> str:
    """Create a flattened field path."""
    if prefix:
        return f"{prefix}.{field_name}"
    return field_name


def _extract_columns_from_template(
    template: dict[str, Any],
    prefix: str = ""
) -> list[TableColumn]:
    """Extract column definitions from template fields."""
    columns = []

    for field in template.get("fields", []):
        field_name = field["name"]
        field_path = _flatten_field_path(prefix, field_name)
        field_type = field["type"]
        label = field.get("label", field_name)

        if field_type == "object" and field.get("template_ref"):
            # Nested object - we don't resolve template refs for now
            # Just include it as a JSON column
            columns.append(TableColumn(
                name=field_path,
                label=label,
                type="object",
                is_array=False,
                is_flattened=False
            ))
        elif field_type == "array":
            columns.append(TableColumn(
                name=field_path,
                label=label,
                type=field.get("array_item_type", "unknown"),
                is_array=True,
                is_flattened=False  # Updated during processing
            ))
        else:
            columns.append(TableColumn(
                name=field_path,
                label=label,
                type=field_type,
                is_array=False,
                is_flattened=False
            ))

    return columns


def _count_array_cross_product(data: dict[str, Any], array_fields: list[str]) -> int:
    """Count the cross-product size of multiple arrays."""
    sizes = []
    for field_path in array_fields:
        value = _get_field_value(data, field_path)
        if isinstance(value, list):
            sizes.append(max(len(value), 1))
        else:
            sizes.append(1)

    result = 1
    for size in sizes:
        result *= size
    return result


def _flatten_document(
    doc: Document,
    columns: list[TableColumn],
    array_fields: list[str],
    max_cross_product: int = 1000
) -> tuple[list[dict[str, Any]], str]:
    """
    Flatten a document into one or more rows.

    Returns:
        Tuple of (rows, array_handling) where array_handling is 'flattened' or 'json'
    """
    data = doc.data

    # Base row with non-array fields and metadata
    base_row = {
        "_document_id": doc.document_id,
        "_version": doc.version,
        "_identity_hash": doc.identity_hash,
        "_status": doc.status.value,
        "_created_at": doc.created_at.isoformat() if doc.created_at else None,
        "_updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }

    # Extract non-array values
    for col in columns:
        if not col.is_array:
            value = _get_field_value(data, col.name)
            if isinstance(value, dict):
                # Keep objects as JSON
                import json
                base_row[col.name] = json.dumps(value)
            else:
                base_row[col.name] = value

    # Handle arrays
    if not array_fields:
        return [base_row], "none"

    # Check cross-product size
    cross_product_size = _count_array_cross_product(data, array_fields)

    if cross_product_size > max_cross_product:
        # Too large - keep arrays as JSON
        import json
        for field_path in array_fields:
            value = _get_field_value(data, field_path)
            base_row[field_path] = json.dumps(value) if value else None
        return [base_row], "json"

    # Flatten arrays via cross-product
    array_values = []
    for field_path in array_fields:
        value = _get_field_value(data, field_path)
        if isinstance(value, list) and len(value) > 0:
            array_values.append([(field_path, v) for v in value])
        else:
            array_values.append([(field_path, value)])

    # Generate cross-product
    rows = []
    if array_values:
        for combo in product(*array_values):
            row = base_row.copy()
            for field_path, value in combo:
                row[field_path] = value
            rows.append(row)
    else:
        rows.append(base_row)

    return rows, "flattened"


# ============================================================================
# Endpoint
# ============================================================================

@router.get(
    "/{template_id}",
    response_model=TableViewResponse,
    summary="Get flattened table view of documents",
    description="""
Get documents for a template in a flattened table format.

This endpoint treats the template as a database table, returning documents as rows.
Array fields can be flattened into multiple rows (cross-product) or kept as JSON.

**Array Handling:**
- 0 arrays: 1 row per document
- 1 array: Flatten into multiple rows
- 2+ arrays, cross-product ≤1000 rows: Cross-product (flatten all)
- 2+ arrays, cross-product >1000 rows: Keep arrays as JSON fields

**Metadata Columns:**
All rows include system columns prefixed with underscore:
- `_document_id`: Document ID
- `_version`: Document version
- `_identity_hash`: Identity hash
- `_status`: Document status
- `_created_at`: Creation timestamp
- `_updated_at`: Last update timestamp
    """
)
async def get_table_view(
    template_id: str,
    status: DocumentStatus | None = Query(
        DocumentStatus.ACTIVE,
        description="Filter by document status"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=1, le=1000, description="Rows per page"),
    max_cross_product: int = Query(
        1000,
        ge=1,
        le=10000,
        description="Max cross-product rows before falling back to JSON arrays"
    ),
    _: str = Depends(require_api_key)
):
    """Get flattened table view for a template."""

    # Fetch template (resolve synonym first)
    template_client = get_template_store_client()
    template = await template_client.get_template_resolved(template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Use canonical template_id from resolved template
    canonical_template_id = template.get("template_id", template_id)

    # Check namespace permission
    ns = template.get("namespace", "wip")
    identity = get_current_identity()
    await check_namespace_permission(identity, ns, "read")

    # Extract column definitions and identify array fields
    columns = _extract_columns_from_template(template)
    array_fields = [col.name for col in columns if col.is_array]

    # Build query filter using canonical ID
    query_filter = {"template_id": canonical_template_id}
    if status:
        query_filter["status"] = status.value

    # Count total documents
    total_documents = await Document.find(query_filter).count()

    if total_documents == 0:
        return TableViewResponse(
            template_id=canonical_template_id,
            template_value=template.get("value", ""),
            template_label=template.get("label", ""),
            columns=columns,
            rows=[],
            total_documents=0,
            total_rows=0,
            page=page,
            page_size=page_size,
            pages=0,
            array_handling="none"
        )

    # Fetch documents and flatten
    # Note: Pagination is complex with flattening. We paginate by source documents,
    # then the actual row count may exceed page_size due to array expansion.
    skip = (page - 1) * page_size
    documents = await Document.find(query_filter).skip(skip).limit(page_size).to_list()

    all_rows = []
    array_handling = "none"

    for doc in documents:
        rows, handling = _flatten_document(doc, columns, array_fields, max_cross_product)
        all_rows.extend(rows)
        if handling != "none":
            array_handling = handling

    # Update column metadata based on actual handling
    if array_handling == "flattened":
        for col in columns:
            if col.is_array:
                col.is_flattened = True

    # Calculate pagination
    pages = (total_documents + page_size - 1) // page_size

    return TableViewResponse(
        template_id=canonical_template_id,
        template_value=template.get("value", ""),
        template_label=template.get("label", ""),
        columns=columns,
        rows=all_rows,
        total_documents=total_documents,
        total_rows=len(all_rows),
        page=page,
        page_size=page_size,
        pages=pages,
        array_handling=array_handling
    )


@router.get(
    "/{template_id}/csv",
    summary="Export table view as CSV",
    description="""
Export documents for a template as a CSV file.

Same flattening logic as the JSON endpoint applies.
    """
)
async def export_table_csv(
    template_id: str,
    status: DocumentStatus | None = Query(
        DocumentStatus.ACTIVE,
        description="Filter by document status"
    ),
    max_cross_product: int = Query(
        1000,
        ge=1,
        le=10000,
        description="Max cross-product rows before falling back to JSON arrays"
    ),
    include_metadata: bool = Query(
        True,
        description="Include _document_id, _version, etc. columns"
    ),
    _: str = Depends(require_api_key)
):
    """Export table view as CSV."""
    import csv
    import io

    # Fetch template (resolve synonym via template-store client)
    template_client = get_template_store_client()
    template = await template_client.get_template_resolved(template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    canonical_template_id = template.get("template_id", template_id)

    # Check namespace permission
    ns = template.get("namespace", "wip")
    identity = get_current_identity()
    await check_namespace_permission(identity, ns, "read")

    # Extract column definitions and identify array fields
    columns = _extract_columns_from_template(template)
    array_fields = [col.name for col in columns if col.is_array]

    # Build query filter using canonical ID
    query_filter = {"template_id": canonical_template_id}
    if status:
        query_filter["status"] = status.value

    # Fetch all documents (no pagination for CSV export)
    documents = await Document.find(query_filter).to_list()

    if not documents:
        return Response(
            content="",
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{template.get("value", template_id)}.csv"'
            }
        )

    # Flatten all documents
    all_rows = []
    for doc in documents:
        rows, _ = _flatten_document(doc, columns, array_fields, max_cross_product)
        all_rows.extend(rows)

    # Build CSV
    output = io.StringIO()

    # Determine column order
    metadata_cols = ["_document_id", "_version", "_identity_hash", "_status", "_created_at", "_updated_at"]
    data_cols = [col.name for col in columns]

    if include_metadata:
        csv_columns = metadata_cols + data_cols
    else:
        csv_columns = data_cols

    writer = csv.DictWriter(output, fieldnames=csv_columns, extrasaction='ignore')
    writer.writeheader()

    for row in all_rows:
        # Filter to only include requested columns
        filtered_row = {k: v for k, v in row.items() if k in csv_columns}
        writer.writerow(filtered_row)

    csv_content = output.getvalue()

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{template.get("value", template_id)}.csv"'
        }
    )
