"""Table View API endpoint for per-template flattened document views.

Provides a "template as table" view where each document is one or more rows,
with array fields optionally flattened into multiple rows.

Use cases:
- Transactional apps treating templates as database tables
- CSV exports for downstream processing
- Reporting and data analysis
"""

from collections.abc import AsyncIterator
from enum import StrEnum
from itertools import product
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from wip_auth import UserIdentity, check_namespace_permission, resolve_namespace_filter
from wip_auth.csv_cells import escape_cell

from ..models.document import Document, DocumentStatus
from ..services.document_service import DocumentService
from ..services.template_store_client import get_template_store_client
from .auth import require_api_key

router = APIRouter(prefix="/table", tags=["Table View"])


# ============================================================================
# Models
# ============================================================================

class TableFormat(StrEnum):
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
        description=(
            "Number of documents (their latest versions) matching the filter, "
            "across all pages, before flattening"
        )
    )
    total_rows: int = Field(
        ...,
        description="Rows on this page after flattening"
    )
    page: int
    page_size: int
    pages: int
    array_handling: Literal["flattened", "json", "none"] = Field(
        ...,
        description="How arrays were handled: 'flattened', 'json', or 'none' (no arrays in the result set)"
    )


# ============================================================================
# Helper Functions
# ============================================================================

# The most flattened rows one table-view response carries. Arrays are
# expanded per document up to max_cross_product, so a page could otherwise
# hold page_size x max_cross_product rows (10 million at the limits).
MAX_TABLE_ROWS = 10_000

# The CSV export sends the rows in chunks of about this many characters.
CSV_CHUNK_CHARS = 64 * 1024


async def _documents_query(
    template_id: str,
    status: DocumentStatus | None,
    identity: UserIdentity,
    namespace: str | None,
) -> dict[str, Any]:
    """The documents a table shows: each document's latest version on this
    template, with ``status`` judged on that version, in the namespaces the
    caller can read (or the one named).

    A document's namespace is independent of its template's, so read on the
    template's namespace says nothing about the documents on it. And a
    document can hold several version rows at once (a merge restore with
    older_versions: keep leaves several active), so a status filter alone
    would list one document several times.
    """
    base: dict[str, Any] = {"template_id": template_id}
    base.update((await resolve_namespace_filter(identity, namespace)).query)
    return await DocumentService._latest_versions_query(base, status)


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
) -> tuple[list[dict[str, Any]], Literal["flattened", "json", "none"]]:
    """
    Flatten a document into one or more rows.

    Returns:
        Tuple of (rows, array_handling) where array_handling is 'flattened',
        'json', or 'none' (document contains no arrays)
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
- 2+ arrays, cross-product ≤ max_cross_product rows (default 1000, tunable up to 10000): Cross-product (flatten all)
- 2+ arrays, cross-product > max_cross_product rows: Keep arrays as JSON fields

**Metadata Columns:**
All rows include system columns prefixed with underscore:
- `_document_id`: Document ID
- `_version`: Document version
- `_identity_hash`: Identity hash
- `_status`: Document status
- `_created_at`: Creation timestamp
- `_updated_at`: Last update timestamp

**Access:** the caller needs read on the namespace the template lives in —
the table's columns are the template's full declared layout (inherited
fields included), and a template's schema is shown only to readers of its
namespace, as the template store's own reads do. Without it the answer is
404, even when the caller can read documents on the template in another
namespace; read those through `GET /documents?template_id=…&namespace=…`.
The rows are then limited to the namespaces the caller can read.
    """
)
async def get_table_view(
    template_id: str,
    status: DocumentStatus | None = Query(
        DocumentStatus.ACTIVE,
        description="Filter by document status"
    ),
    namespace: str | None = Query(
        None,
        description=(
            "Only documents in this namespace. Without it, the documents of "
            "every namespace the caller can read."
        ),
    ),
    page: int = Query(1, ge=1, description="Page number"),
    # Default 100 (not the platform-wide 50) is deliberate: table view serves a
    # spreadsheet-like surface of cheap flat row projections, and the MCP
    # get_table_view tool mirrors the same default.
    page_size: int = Query(100, ge=1, le=1000, description="Rows per page"),
    max_cross_product: int = Query(
        1000,
        ge=1,
        le=10000,
        description="Max cross-product rows before falling back to JSON arrays"
    ),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get flattened table view for a template."""

    # Fetch template (resolve synonym first)
    template_client = get_template_store_client()
    template = await template_client.get_template_resolved(template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Use canonical template_id from resolved template
    canonical_template_id = template.get("template_id", template_id)

    # The columns are the template's whole resolved layout: only readers of
    # the template's namespace see it (the template store gates its own reads
    # the same way). Reading documents on it elsewhere is not enough.
    ns = template.get("namespace")
    if not ns:
        raise HTTPException(status_code=500, detail="Template response missing namespace")
    await check_namespace_permission(identity, ns, "read")

    # Extract column definitions and identify array fields
    columns = _extract_columns_from_template(template)
    array_fields = [col.name for col in columns if col.is_array]

    query_filter = await _documents_query(canonical_template_id, status, identity, namespace)

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
    array_handling: Literal["flattened", "json", "none"] = "none"

    for doc in documents:
        rows, handling = _flatten_document(doc, columns, array_fields, max_cross_product)
        all_rows.extend(rows)
        if len(all_rows) > MAX_TABLE_ROWS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"This page flattens to more than {MAX_TABLE_ROWS} rows. Lower "
                    "page_size, or lower max_cross_product so wide arrays stay JSON "
                    "cells; the CSV export streams any size."
                ),
            )
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

**Access:** the caller needs read on the namespace the template lives in —
the table's columns are the template's full declared layout (inherited
fields included), and a template's schema is shown only to readers of its
namespace, as the template store's own reads do. Without it the answer is
404, even when the caller can read documents on the template in another
namespace; read those through `GET /documents?template_id=…&namespace=…`.
The rows are then limited to the namespaces the caller can read.
    """
)
async def export_table_csv(
    template_id: str,
    status: DocumentStatus | None = Query(
        DocumentStatus.ACTIVE,
        description="Filter by document status"
    ),
    namespace: str | None = Query(
        None,
        description=(
            "Only documents in this namespace. Without it, the documents of "
            "every namespace the caller can read."
        ),
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
    identity: UserIdentity = Depends(require_api_key)
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

    # The columns are the template's whole resolved layout: only readers of
    # the template's namespace see it (the template store gates its own reads
    # the same way). Reading documents on it elsewhere is not enough.
    ns = template.get("namespace")
    if not ns:
        raise HTTPException(status_code=500, detail="Template response missing namespace")
    await check_namespace_permission(identity, ns, "read")

    # Extract column definitions and identify array fields
    columns = _extract_columns_from_template(template)
    array_fields = [col.name for col in columns if col.is_array]

    query_filter = await _documents_query(canonical_template_id, status, identity, namespace)

    metadata_cols = ["_document_id", "_version", "_identity_hash", "_status", "_created_at", "_updated_at"]
    data_cols = [col.name for col in columns]
    csv_columns = metadata_cols + data_cols if include_metadata else data_cols
    disposition = {
        "Content-Disposition": f'attachment; filename="{template.get("value", template_id)}.csv"'
    }

    # No documents: an empty body, as before (no header line either).
    if await Document.find(query_filter).first_or_none() is None:
        return Response(content="", media_type="text/csv", headers=disposition)

    async def rows() -> AsyncIterator[str]:
        """The CSV, one document at a time from a cursor, so an export of
        any size holds one document's rows in memory, not the whole table.
        Header and cells are formula-escaped (wip_auth.csv_cells): document
        values and field names would otherwise run as spreadsheet formulas."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=csv_columns, extrasaction='ignore')
        writer.writerow({c: escape_cell(c) for c in csv_columns})
        async for doc in Document.find(query_filter):
            flat, _ = _flatten_document(doc, columns, array_fields, max_cross_product)
            for row in flat:
                writer.writerow({k: escape_cell(v) for k, v in row.items() if k in csv_columns})
            if buf.tell() >= CSV_CHUNK_CHARS:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        if buf.tell():
            yield buf.getvalue()

    return StreamingResponse(rows(), media_type="text/csv", headers=disposition)
