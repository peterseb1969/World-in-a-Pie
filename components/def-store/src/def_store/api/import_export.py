"""API endpoints for import/export operations."""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Body, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from ..models.api_models import ImportTerminologyRequest, ExportFormat
from ..services.import_export import ImportExportService
from .auth import require_api_key

router = APIRouter(prefix="/import-export", tags=["Import/Export"])


@router.get(
    "/export/{terminology_id}",
    summary="Export a terminology"
)
async def export_terminology(
    terminology_id: str,
    format: str = Query("json", description="Export format: json, csv"),
    include_metadata: bool = Query(True, description="Include metadata"),
    include_inactive: bool = Query(False, description="Include inactive terms"),
    languages: Optional[str] = Query(None, description="Comma-separated language codes"),
    api_key: str = Depends(require_api_key)
):
    """
    Export a terminology with all its terms.

    Supports JSON and CSV formats.
    """
    try:
        language_list = languages.split(",") if languages else None

        result = await ImportExportService.export_terminology(
            terminology_id=terminology_id,
            format=format,
            include_metadata=include_metadata,
            include_inactive=include_inactive,
            languages=language_list
        )

        if format == "csv":
            return PlainTextResponse(
                content=result["csv_content"],
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename={result['terminology']['code']}.csv"
                }
            )

        return JSONResponse(content=result)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/export",
    summary="Export all terminologies"
)
async def export_all_terminologies(
    format: str = Query("json", description="Export format: json"),
    include_inactive: bool = Query(False, description="Include inactive terminologies"),
    api_key: str = Depends(require_api_key)
):
    """Export all terminologies with their terms."""
    results = await ImportExportService.export_all_terminologies(
        format=format,
        include_inactive=include_inactive
    )
    return JSONResponse(content={"terminologies": results, "count": len(results)})


@router.post(
    "/import",
    summary="Import a terminology"
)
async def import_terminology(
    data: dict[str, Any] = Body(...),
    format: str = Query("json", description="Import format: json, csv"),
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    created_by: Optional[str] = Query(None, description="User performing import"),
    batch_size: int = Query(
        1000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100,
        description="Number of terms per registry HTTP call (default 100). "
        "Reduce if experiencing timeouts on large imports."
    ),
    api_key: str = Depends(require_api_key)
):
    """
    Import a terminology with terms.

    JSON format expected:
    ```json
    {
      "terminology": {
        "code": "DOC_STATUS",
        "name": "Document Status",
        "description": "...",
        "case_sensitive": false
      },
      "terms": [
        {"code": "DRAFT", "value": "draft", "label": "Draft"},
        {"code": "APPROVED", "value": "approved", "label": "Approved"}
      ]
    }
    ```

    CSV format requires terminology_code and terminology_name in the data,
    plus csv_content with columns: code, value, label, description, sort_order

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)

    If you experience timeouts, try reducing `registry_batch_size` to 50 or lower.
    """
    try:
        options = {
            "skip_duplicates": skip_duplicates,
            "update_existing": update_existing,
            "created_by": created_by,
            "batch_size": batch_size,
            "registry_batch_size": registry_batch_size,
        }

        result = await ImportExportService.import_terminology(
            data=data,
            format=format,
            options=options
        )

        return JSONResponse(content=result)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@router.post(
    "/import/url",
    summary="Import from URL"
)
async def import_from_url(
    url: str = Query(..., description="URL to fetch terminology from"),
    format: str = Query("json", description="Expected format: json, csv"),
    terminology_code: Optional[str] = Query(None, description="Code for CSV import"),
    terminology_name: Optional[str] = Query(None, description="Name for CSV import"),
    skip_duplicates: bool = Query(True, description="Skip existing terms"),
    update_existing: bool = Query(False, description="Update existing terms"),
    created_by: Optional[str] = Query(None, description="User performing import"),
    batch_size: int = Query(
        1000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100,
        description="Number of terms per registry HTTP call (default 100). "
        "Reduce if experiencing timeouts on large imports."
    ),
    api_key: str = Depends(require_api_key)
):
    """
    Import a terminology from a URL.

    Fetches the data from the URL and imports it.

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)
    """
    try:
        options = {
            "skip_duplicates": skip_duplicates,
            "update_existing": update_existing,
            "created_by": created_by,
            "terminology_code": terminology_code,
            "terminology_name": terminology_name,
            "batch_size": batch_size,
            "registry_batch_size": registry_batch_size,
        }

        result = await ImportExportService.import_from_url(
            url=url,
            format=format,
            options=options
        )

        return JSONResponse(content=result)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
