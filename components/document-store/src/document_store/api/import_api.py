"""CSV/XLSX import API for bulk document creation."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from wip_auth import UserIdentity, check_namespace_permission, require_api_key, resolve_or_404
from wip_auth.term_resolution import term_resolution_batch

from ..models.api_models import DocumentCreateRequest
from ..services.document_service import DocumentService
from ..services.errors import dependency_unavailable
from ..services.import_service import ImportService
from ..services.nats_client import get_throttle_delay
from ..services.template_store_client import TemplateStoreError, get_template_store_client
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
        raise _unusable("Empty file")

    try:
        return ImportService.preview(content, file.filename or "data.csv")
    except Exception as e:
        logger.info(f"Preview refused an unreadable file: {e}")
        raise _parse_failure(e) from None


# The most created rows and failed rows a response lists; counts stay complete.
_MAX_LISTED_RESULTS = 100
_MAX_LISTED_ERRORS = 50


def _unusable(message: str, **extra: Any) -> HTTPException:
    """The answer to an upload that cannot be imported: 422, never a 200 that
    a caller would read as an (empty) result."""
    return HTTPException(status_code=422, detail={"message": message, **extra} if extra else message)


def _parse_mapping(column_mapping: str) -> dict[str, Any]:
    """The column mapping; 422 when it is not a JSON object."""
    try:
        mapping = json.loads(column_mapping)
    except json.JSONDecodeError as e:
        raise _unusable(f"Invalid column_mapping JSON: {e}") from None
    if not isinstance(mapping, dict):
        raise _unusable("column_mapping must be a JSON object")
    return mapping


def _parse_failure(e: Exception) -> HTTPException:
    """A file the parser cannot read is the upload's fault: 422. A parser
    refusal (ValueError) keeps its own message; anything else raised while
    reading the bytes (a corrupt XLSX archive, say) is a parse failure too."""
    return _unusable(str(e) if isinstance(e, ValueError) else f"Failed to parse file: {e}")


async def _field_types(template_id: str) -> dict[str, str]:
    """Each field's type in the template's latest version, as validation will
    use it: a cell is converted to its field's type before its row is validated.

    A template-store that cannot answer is an outage (503,
    dependency_unavailable); a template it refuses to serve (e.g. a parent it
    cannot resolve) makes the whole file unusable (422, its code), one answer
    for one cause rather than the same error on every row."""
    try:
        template = await get_template_store_client().get_template(template_id=template_id)
    except TemplateStoreError as e:
        if e.unavailable:
            raise dependency_unavailable("template-store", f"template {template_id!r}", e) from e
        raise _unusable(
            f"Template {template_id!r} cannot be used: {e}",
            error_code=e.code or "template_error",
        ) from e
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


# The import side of the CSV formula escape (wip_auth.csv_cells).
_UNESCAPE_NOTE = (
    "In a CSV file (not XLSX), a text cell that starts with an apostrophe "
    "followed by =, +, -, @, tab, carriage return or another apostrophe loses "
    "that one apostrophe: it is the "
    "escape WIP's CSV exports write so a spreadsheet does not run the cell as a "
    "formula. To import such a value literally, double the apostrophe."
)


@router.post("")
async def import_documents(
    file: UploadFile = File(..., description=f"CSV or XLSX file to import. {_UNESCAPE_NOTE}"),
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
        raise _unusable("Empty file")

    mapping = _parse_mapping(column_mapping)

    try:
        headers, rows = ImportService.parse_rows(content, file.filename or "data.csv")
    except Exception as e:
        raise _parse_failure(e) from None

    # Validate mapping references valid columns
    invalid_cols = [col for col in mapping if col not in headers]
    if invalid_cols:
        raise _unusable(
            f"Column mapping references columns not found in file: {invalid_cols}",
            available_columns=headers,
        )

    documents = ImportService.build_documents(
        rows, template_id, mapping, namespace,
        field_types=await _field_types(template_id),
    )
    if not documents:
        raise _unusable("No documents to import (file may be empty)")

    outcome = await _import_rows(documents, namespace, skip_errors)
    return _import_response(outcome)
