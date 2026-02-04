"""Import/Export service for terminologies and terms."""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Optional

from ..models.terminology import Terminology, TerminologyMetadata
from ..models.term import Term, TermTranslation
from ..models.api_models import (
    CreateTerminologyRequest,
    CreateTermRequest,
    TerminologyResponse,
    TermResponse,
    ExportTerminologyResponse,
    BulkOperationResult,
)
from .terminology_service import TerminologyService


class ImportExportService:
    """Service for importing and exporting terminologies."""

    # =========================================================================
    # EXPORT
    # =========================================================================

    @staticmethod
    async def export_terminology(
        terminology_id: Optional[str] = None,
        terminology_code: Optional[str] = None,
        format: str = "json",
        include_metadata: bool = True,
        include_inactive: bool = False,
        languages: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """
        Export a terminology with all its terms.

        Args:
            terminology_id: Terminology ID
            terminology_code: Terminology code (alternative)
            format: Export format (json, csv)
            include_metadata: Include metadata in export
            include_inactive: Include inactive/deprecated terms
            languages: Languages to include for translations

        Returns:
            Export data in requested format
        """
        # Get terminology
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif terminology_code:
            terminology = await Terminology.find_one({"code": terminology_code})
        else:
            raise ValueError("Must provide terminology_id or terminology_code")

        if not terminology:
            raise ValueError("Terminology not found")

        # Get terms
        query = {"terminology_id": terminology.terminology_id}
        if not include_inactive:
            query["status"] = "active"

        terms = await Term.find(query).sort("sort_order").to_list()

        # Filter translations if languages specified
        if languages:
            for term in terms:
                term.translations = [
                    t for t in term.translations
                    if t.language in languages
                ]

        if format == "csv":
            return ImportExportService._export_csv(terminology, terms, include_metadata)
        else:
            return ImportExportService._export_json(terminology, terms, include_metadata)

    @staticmethod
    def _export_json(
        terminology: Terminology,
        terms: list[Term],
        include_metadata: bool
    ) -> dict[str, Any]:
        """Export as JSON."""
        term_data = []
        for t in terms:
            term_dict = {
                "code": t.code,
                "value": t.value,
                "label": t.label,
                "description": t.description,
                "sort_order": t.sort_order,
                "status": t.status,
            }
            if t.parent_term_id:
                term_dict["parent_term_id"] = t.parent_term_id
            if t.translations:
                term_dict["translations"] = [
                    {"language": tr.language, "label": tr.label, "description": tr.description}
                    for tr in t.translations
                ]
            if include_metadata and t.metadata:
                term_dict["metadata"] = t.metadata
            if t.status == "deprecated":
                term_dict["deprecated_reason"] = t.deprecated_reason
                term_dict["replaced_by_term_id"] = t.replaced_by_term_id

            term_data.append(term_dict)

        result = {
            "terminology": {
                "code": terminology.code,
                "name": terminology.name,
                "description": terminology.description,
                "case_sensitive": terminology.case_sensitive,
                "allow_multiple": terminology.allow_multiple,
                "extensible": terminology.extensible,
            },
            "terms": term_data,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "format": "json",
            "version": "1.0"
        }

        if include_metadata:
            result["terminology"]["metadata"] = {
                "source": terminology.metadata.source,
                "source_url": terminology.metadata.source_url,
                "version": terminology.metadata.version,
                "language": terminology.metadata.language,
                "custom": terminology.metadata.custom
            }

        return result

    @staticmethod
    def _export_csv(
        terminology: Terminology,
        terms: list[Term],
        include_metadata: bool
    ) -> dict[str, Any]:
        """Export as CSV."""
        output = io.StringIO()

        # Define columns
        columns = ["code", "value", "label", "description", "sort_order", "status"]
        if include_metadata:
            columns.append("metadata")

        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()

        for t in terms:
            row = {
                "code": t.code,
                "value": t.value,
                "label": t.label,
                "description": t.description or "",
                "sort_order": t.sort_order,
                "status": t.status,
            }
            if include_metadata:
                row["metadata"] = json.dumps(t.metadata) if t.metadata else ""

            writer.writerow(row)

        return {
            "terminology": {
                "code": terminology.code,
                "name": terminology.name,
            },
            "csv_content": output.getvalue(),
            "export_date": datetime.now(timezone.utc).isoformat(),
            "format": "csv"
        }

    @staticmethod
    async def export_all_terminologies(
        format: str = "json",
        include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        """Export all terminologies."""
        query = {} if include_inactive else {"status": "active"}
        terminologies = await Terminology.find(query).to_list()

        results = []
        for t in terminologies:
            export = await ImportExportService.export_terminology(
                terminology_id=t.terminology_id,
                format=format,
                include_inactive=include_inactive
            )
            results.append(export)

        return results

    # =========================================================================
    # IMPORT
    # =========================================================================

    @staticmethod
    async def import_terminology(
        data: dict[str, Any],
        format: str = "json",
        options: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Import a terminology with terms.

        Args:
            data: Import data
            format: Data format (json, csv)
            options: Import options
                - skip_duplicates: Skip terms that already exist
                - update_existing: Update existing terms
                - created_by: User performing import

        Returns:
            Import results
        """
        options = options or {}
        skip_duplicates = options.get("skip_duplicates", True)
        update_existing = options.get("update_existing", False)
        created_by = options.get("created_by")

        if format == "csv":
            return await ImportExportService._import_csv(data, options)

        # JSON import
        terminology_data = data.get("terminology")
        if not terminology_data:
            raise ValueError("Missing 'terminology' field in import data")

        if not terminology_data.get("code"):
            raise ValueError("Missing 'terminology.code' field in import data")

        if not terminology_data.get("name"):
            raise ValueError("Missing 'terminology.name' field in import data")

        terms_data = data.get("terms", [])

        # Check if terminology exists
        existing_terminology = await Terminology.find_one({"code": terminology_data.get("code")})

        if existing_terminology:
            if not update_existing:
                terminology_id = existing_terminology.terminology_id
                terminology_status = "exists"
            else:
                # Update existing terminology
                # TODO: Implement update logic
                terminology_id = existing_terminology.terminology_id
                terminology_status = "updated"
        else:
            # Create new terminology
            metadata = terminology_data.get("metadata", {})
            create_req = CreateTerminologyRequest(
                code=terminology_data["code"],
                name=terminology_data["name"],
                description=terminology_data.get("description"),
                case_sensitive=terminology_data.get("case_sensitive", False),
                allow_multiple=terminology_data.get("allow_multiple", False),
                extensible=terminology_data.get("extensible", False),
                metadata=TerminologyMetadata(**metadata) if metadata else None,
                created_by=created_by
            )
            terminology_response = await TerminologyService.create_terminology(create_req)
            terminology_id = terminology_response.terminology_id
            terminology_status = "created"

        # Build CreateTermRequest objects for batch operation
        term_requests = []
        for i, term_data in enumerate(terms_data):
            translations = [
                TermTranslation(**tr)
                for tr in term_data.get("translations", [])
            ]
            term_requests.append(CreateTermRequest(
                code=term_data["code"],
                value=term_data["value"],
                aliases=term_data.get("aliases", []),
                label=term_data.get("label", term_data["value"]),
                description=term_data.get("description"),
                sort_order=term_data.get("sort_order", i),
                parent_term_id=term_data.get("parent_term_id"),
                translations=translations,
                metadata=term_data.get("metadata", {}),
                created_by=created_by
            ))

        # Delegate to batch method for efficient bulk import
        term_results = await TerminologyService.create_terms_bulk(
            terminology_id=terminology_id,
            terms=term_requests,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
        )

        created_count = sum(1 for r in term_results if r.status == "created")
        skipped_count = sum(1 for r in term_results if r.status == "skipped")
        error_count = sum(1 for r in term_results if r.status == "error")

        # Get terminology name (from existing or from import data)
        terminology_name = (
            existing_terminology.name if existing_terminology
            else terminology_data.get("name")
        )

        return {
            "terminology": {
                "terminology_id": terminology_id,
                "code": terminology_data.get("code"),
                "name": terminology_name,
                "status": terminology_status
            },
            "terms_result": {
                "results": [r.model_dump() for r in term_results],
                "total": len(terms_data),
                "succeeded": created_count,
                "skipped": skipped_count,
                "failed": error_count
            }
        }

    @staticmethod
    async def _import_csv(
        data: dict[str, Any],
        options: dict[str, Any]
    ) -> dict[str, Any]:
        """Import from CSV format."""
        terminology_code = data.get("terminology_code")
        terminology_name = data.get("terminology_name", terminology_code)
        csv_content = data.get("csv_content", "")
        created_by = options.get("created_by")

        if not terminology_code:
            raise ValueError("terminology_code is required for CSV import")

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_content))
        terms_data = []

        for row in reader:
            term = {
                "code": row.get("code", "").strip(),
                "value": row.get("value", "").strip(),
                "label": row.get("label", row.get("value", "")).strip(),
                "description": row.get("description", "").strip() or None,
                "sort_order": int(row.get("sort_order", 0) or 0),
            }
            if row.get("metadata"):
                try:
                    term["metadata"] = json.loads(row["metadata"])
                except json.JSONDecodeError:
                    pass

            if term["code"] and term["value"]:
                terms_data.append(term)

        # Convert to JSON format and use JSON import
        json_data = {
            "terminology": {
                "code": terminology_code,
                "name": terminology_name,
            },
            "terms": terms_data
        }

        return await ImportExportService.import_terminology(json_data, "json", options)

    @staticmethod
    async def import_from_url(
        url: str,
        format: str = "json",
        options: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Import terminology from a URL.

        Args:
            url: URL to fetch data from
            format: Expected format (json, csv)
            options: Import options

        Returns:
            Import results
        """
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()

            if format == "json":
                data = response.json()
            else:
                # CSV - wrap in expected structure
                data = {
                    "csv_content": response.text,
                    "terminology_code": options.get("terminology_code") if options else None,
                    "terminology_name": options.get("terminology_name") if options else None,
                }

        return await ImportExportService.import_terminology(data, format, options)
