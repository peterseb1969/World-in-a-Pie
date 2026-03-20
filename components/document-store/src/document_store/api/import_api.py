"""CSV/XLSX import API for bulk document creation."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from wip_auth import check_namespace_permission, get_current_identity, require_api_key

from ..models.api_models import DocumentCreateRequest
from ..services.document_service import DocumentService
from ..services.import_service import ImportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["Import"])


@router.post("/preview")
async def preview_import(
    file: UploadFile = File(..., description="CSV or XLSX file to preview"),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Preview a CSV/XLSX file: returns headers, sample rows, and detected format.

    Upload a file to see its structure before importing. Use the headers
    to build a column_mapping for the import endpoint.
    """
    content = await file.read()
    if not content:
        return {"error": "Empty file"}

    try:
        result = ImportService.preview(content, file.filename or "data.csv")
        return result
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Preview failed: {e}")
        return {"error": f"Failed to parse file: {e}"}


@router.post("")
async def import_documents(
    file: UploadFile = File(..., description="CSV or XLSX file to import"),
    template_id: str = Form(..., description="Template ID for the documents"),
    column_mapping: str = Form(..., description="JSON object mapping CSV columns to template fields, e.g. {\"Name\": \"name\", \"Email\": \"email\"}"),
    namespace: str = Form("wip", description="Target namespace"),
    skip_errors: bool = Form(False, description="Skip rows that fail validation instead of stopping"),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Import documents from a CSV/XLSX file.

    Upload a file with a column mapping to create documents in bulk.
    Use the /preview endpoint first to see the file structure.

    The column_mapping maps CSV column names to template field names:
    {"CSV Column Name": "template_field_name", ...}

    Term fields: use human-readable values in the CSV (e.g., "United Kingdom").
    WIP resolves them to term_ids automatically.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    content = await file.read()
    if not content:
        return {"error": "Empty file"}

    # Parse column mapping
    try:
        mapping = json.loads(column_mapping)
        if not isinstance(mapping, dict):
            return {"error": "column_mapping must be a JSON object"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid column_mapping JSON: {e}"}

    # Parse file
    try:
        headers, rows = ImportService.parse_rows(content, file.filename or "data.csv")
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to parse file: {e}"}

    # Validate mapping references valid columns
    invalid_cols = [col for col in mapping.keys() if col not in headers]
    if invalid_cols:
        return {
            "error": f"Column mapping references columns not found in file: {invalid_cols}",
            "available_columns": headers,
        }

    # Build documents
    documents = ImportService.build_documents(rows, template_id, mapping, namespace)

    if not documents:
        return {"error": "No documents to import (file may be empty)"}

    # Create documents using existing bulk create
    doc_service = DocumentService()

    total = len(documents)
    succeeded = 0
    failed = 0
    skipped = 0
    errors = []
    created_ids = []

    # Process in batches to avoid overwhelming the system
    batch_size = 50
    for batch_start in range(0, total, batch_size):
        batch = documents[batch_start:batch_start + batch_size]

        for i, doc in enumerate(batch):
            row_num = batch_start + i + 2  # +2 for 1-indexed + header row
            try:
                req = DocumentCreateRequest(**doc)
                result, error_msg = await doc_service.create_document(
                    req, namespace=namespace
                )
                if result is None:
                    raise ValueError(error_msg or "Validation failed")
                succeeded += 1
                created_ids.append({
                    "row": row_num,
                    "document_id": result.document_id,
                    "version": result.version,
                    "is_new": result.is_new,
                })
            except Exception as e:
                failed += 1
                error_info = {"row": row_num, "error": str(e)}
                # Include the data that failed for debugging
                if doc.get("data"):
                    error_info["data"] = {k: str(v)[:100] for k, v in doc["data"].items()}
                errors.append(error_info)

                if not skip_errors:
                    # Stop on first error
                    skipped = total - (succeeded + failed)
                    break

        if not skip_errors and errors:
            break

    return {
        "total_rows": total,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": created_ids[:100],  # Cap to avoid huge responses
        "errors": errors[:50],  # Cap error list
    }
