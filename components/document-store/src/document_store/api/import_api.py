"""CSV/XLSX import API for bulk document creation."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from wip_auth import UserIdentity, check_namespace_permission, require_api_key, resolve_or_404
from wip_auth.term_resolution import term_resolution_batch

from ..models.api_models import DocumentCreateRequest
from ..services.document_service import DocumentService
from ..services.import_service import ImportService
from ..services.nats_client import get_throttle_delay
from ..services.template_store_client import get_template_store_client
from .uploads import read_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["Import"])


@router.post("/preview")
async def preview_import(
    file: UploadFile = File(..., description="CSV or XLSX file to preview"),
    identity: UserIdentity = Depends(require_api_key),
) -> dict[str, Any]:
    """Preview a CSV/XLSX file: returns headers, sample rows, and detected format.

    Upload a file to see its structure before importing. Use the headers
    to build a column_mapping for the import endpoint.
    """
    content = await read_upload(file)
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


# The most created rows and failed rows a response lists; counts stay complete.
_MAX_LISTED_RESULTS = 100
_MAX_LISTED_ERRORS = 50


def _parse_mapping(column_mapping: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(the column mapping, None), or (None, the error response refusing it)."""
    try:
        mapping = json.loads(column_mapping)
    except json.JSONDecodeError as e:
        return None, {"error": f"Invalid column_mapping JSON: {e}"}
    if not isinstance(mapping, dict):
        return None, {"error": "column_mapping must be a JSON object"}
    return mapping, None


def _parse_file(
    content: bytes, filename: str,
) -> tuple[list[str], list[dict[str, Any]]] | dict[str, Any]:
    """The file's (headers, rows), or the error response when it cannot be read.

    A parser refusal (ValueError) is answered with its own message; any other
    failure is reported as a parse failure.
    """
    try:
        return ImportService.parse_rows(content, filename)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to parse file: {e}"}


async def _field_types(template_id: str) -> dict[str, str]:
    """Each field's type in the template's latest version, as validation will
    use it: a cell is converted to its field's type before its row is validated."""
    template = await get_template_store_client().get_template(template_id=template_id)
    return {
        str(f["name"]): str(f["type"])
        for f in (template or {}).get("fields") or []
        if isinstance(f, dict) and f.get("name") and f.get("type")
    }


# Rows written between two back-off checks.
_IMPORT_BATCH_SIZE = 50


@dataclass
class _ImportOutcome:
    total: int
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    created: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


async def _import_row(
    doc_service: DocumentService, doc: dict[str, Any], row_num: int, namespace: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Create one row's document: (the created entry, None) or (None, the error entry).

    A cell that could not be converted to its field's type fails the row like
    a validation failure. The error entry carries the row's data, each value
    cut to 100 characters, for debugging.
    """
    try:
        conversion_errors = doc.pop("conversion_errors", None)
        if conversion_errors:
            raise ValueError("; ".join(conversion_errors))
        req = DocumentCreateRequest(**doc)
        result, error = await doc_service.create_document(req, namespace=namespace)
        if result is None:
            raise ValueError((error or {}).get("error") or "Validation failed")
        return {
            "row": row_num,
            "document_id": result.document_id,
            "version": result.version,
            "is_new": result.is_new,
        }, None
    except Exception as e:
        error_info: dict[str, Any] = {"row": row_num, "error": str(e)}
        if doc.get("data"):
            error_info["data"] = {k: str(v)[:100] for k, v in doc["data"].items()}
        return None, error_info


async def _import_rows(
    documents: list[dict[str, Any]], namespace: str, skip_errors: bool,
) -> _ImportOutcome:
    """Create every row's document in order, each through the single-item
    create path (its own Registry call and write), so a failing row is
    reported by its row number.

    Without skip_errors the first failing row stops the import and the rows
    after it are counted as skipped.
    """
    doc_service = DocumentService()
    outcome = _ImportOutcome(total=len(documents))

    # One write batch for the whole file: rows repeating a term value
    # resolve it once (see wip_auth.term_resolution).
    with term_resolution_batch():
        for batch_start in range(0, outcome.total, _IMPORT_BATCH_SIZE):
            batch = documents[batch_start:batch_start + _IMPORT_BATCH_SIZE]
            for i, doc in enumerate(batch):
                row_num = batch_start + i + 2  # +2 for 1-indexed + header row
                created, error = await _import_row(doc_service, doc, row_num, namespace)
                if created is not None:
                    outcome.succeeded += 1
                    outcome.created.append(created)
                    continue
                outcome.failed += 1
                outcome.errors.append(error or {})
                if not skip_errors:
                    outcome.skipped = outcome.total - (outcome.succeeded + outcome.failed)
                    return outcome
            # Write endpoints back off while the reporting consumer lags
            # (nats_client.get_throttle_delay); an import is the largest of them.
            await asyncio.sleep(get_throttle_delay())
    return outcome


def _import_response(outcome: _ImportOutcome) -> dict[str, Any]:
    """The import's report: complete counts, the listed rows cut to their caps."""
    return {
        "total_rows": outcome.total,
        "succeeded": outcome.succeeded,
        "failed": outcome.failed,
        "skipped": outcome.skipped,
        "results": outcome.created[:_MAX_LISTED_RESULTS],
        "results_truncated": len(outcome.created) > _MAX_LISTED_RESULTS,
        "errors": outcome.errors[:_MAX_LISTED_ERRORS],
        "errors_truncated": len(outcome.errors) > _MAX_LISTED_ERRORS,
    }


@router.post("")
async def import_documents(
    file: UploadFile = File(..., description="CSV or XLSX file to import"),
    template_id: str = Form(..., description="Template ID for the documents"),
    column_mapping: str = Form(..., description="JSON object mapping CSV columns to template fields, e.g. {\"Name\": \"name\", \"Email\": \"email\"}"),
    namespace: str = Form(..., description="Target namespace"),
    skip_errors: bool = Form(
        True,
        description="Report a row that fails and go on with the next (the default); "
                    "false stops at the first failing row and reports the rest skipped",
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> dict[str, Any]:
    """Import documents from a CSV/XLSX file.

    Upload a file with a column mapping to create documents in bulk.
    Use the /preview endpoint first to see the file structure.

    The column_mapping maps CSV column names to template field names:
    {"CSV Column Name": "template_field_name", ...}

    Term fields: use human-readable values in the CSV (e.g., "United Kingdom").
    WIP resolves them to term_ids automatically.

    Cells are converted to their field's type before validation: number and
    integer fields take numerals, boolean fields true/false, yes/no or 1/0
    (any case), date fields YYYY-MM-DD (an XLSX date cell works as is),
    datetime fields ISO 8601. A cell that cannot be read as its field's type
    fails its row, reported in ``errors`` like a validation failure.

    Every row is attempted and reported (``skip_errors`` true, the default)
    unless ``skip_errors`` is false. ``results`` lists at most the first 100
    created rows and ``errors`` the first 50 failed ones; ``results_truncated``
    and ``errors_truncated`` say when a list was cut, and ``succeeded`` /
    ``failed`` always count every row.
    """
    await check_namespace_permission(identity, namespace, "write")

    # Resolve template_id synonym. bypass_cache: write door — the import
    # creates documents against whatever template the Registry names now.
    template_id = await resolve_or_404(
        template_id, "template", namespace, param_name="template_id",
        bypass_cache=True,
    )

    content = await read_upload(file)
    if not content:
        return {"error": "Empty file"}

    mapping, mapping_error = _parse_mapping(column_mapping)
    if mapping is None:
        return mapping_error or {}

    parsed = _parse_file(content, file.filename or "data.csv")
    if isinstance(parsed, dict):
        return parsed
    headers, rows = parsed

    # Validate mapping references valid columns
    invalid_cols = [col for col in mapping if col not in headers]
    if invalid_cols:
        return {
            "error": f"Column mapping references columns not found in file: {invalid_cols}",
            "available_columns": headers,
        }

    documents = ImportService.build_documents(
        rows, template_id, mapping, namespace,
        field_types=await _field_types(template_id),
    )
    if not documents:
        return {"error": "No documents to import (file may be empty)"}

    outcome = await _import_rows(documents, namespace, skip_errors)
    return _import_response(outcome)
