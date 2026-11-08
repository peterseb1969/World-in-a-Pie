"""CSV/XLSX file import service for bulk document creation."""

import csv
import io
import logging
import math
from datetime import date, datetime, time
from itertools import islice
from typing import Any

from wip_auth.csv_cells import unescape_cell

logger = logging.getLogger(__name__)


def _unescaped(row: dict[str, Any]) -> dict[str, Any]:
    """A CSV row with WIP's formula-escape undone (wip_auth.csv_cells), so a
    file WIP exported imports back as the values it held."""
    return {unescape_cell(k): unescape_cell(v) for k, v in row.items()}


SAMPLE_ROWS = 5

# Accepted spellings of a boolean cell, compared case-insensitively.
_TRUE = frozenset({"true", "yes", "1"})
_FALSE = frozenset({"false", "no", "0"})


class CellConversionError(ValueError):
    """A cell's value cannot be read as its field's type."""


def convert_cell(value: Any, field_type: str | None) -> Any:
    """A file cell as the value its template field takes.

    CSV cells are text and XLSX cells are whatever the sheet holds (numbers,
    booleans, dates, text). Number, integer, boolean, date and datetime fields
    get their own type; every other field gets the text as written. A value
    that cannot be read as its field's type raises CellConversionError, which
    fails the row, never the file.
    """
    if field_type == "integer":
        if isinstance(value, bool):
            raise CellConversionError(f"{value!r} is a boolean, not an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            raise CellConversionError(f"{value!r} is not a whole number")
        text = str(value).strip()
        try:
            return int(text)
        except ValueError:
            raise CellConversionError(f"'{text}' is not an integer") from None
    if field_type == "number":
        if isinstance(value, bool):
            raise CellConversionError(f"{value!r} is a boolean, not a number")
        if isinstance(value, int | float):
            number: int | float = value
        else:
            text = str(value).strip()
            try:
                number = int(text)
            except ValueError:
                try:
                    number = float(text)
                except ValueError:
                    raise CellConversionError(f"'{text}' is not a number") from None
        if isinstance(number, float) and not math.isfinite(number):
            raise CellConversionError(f"{value!r} is not a finite number")
        return number
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise CellConversionError(
            f"'{value}' is not a boolean (true/false, yes/no or 1/0)"
        )
    if field_type == "date":
        if isinstance(value, datetime):
            if value.time() != time(0, 0):
                raise CellConversionError(f"'{value}' has a time of day; the field is a date")
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value).strip()
    if field_type == "datetime":
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value).strip()
    return _as_text(value)


def _as_text(value: Any) -> str:
    """A cell as text: a sheet's whole-number float loses its '.0', a date
    reads as ISO."""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _blank(value: Any) -> bool:
    """An empty cell: not provided, so a mandatory field reports `required`."""
    return value is None or value == ""


class ImportService:
    """Handles parsing CSV/XLSX files and creating documents from rows."""

    @staticmethod
    def detect_format(filename: str) -> str:
        """Detect file format from extension. Returns 'csv' or 'xlsx'."""
        lower = filename.lower()
        if lower.endswith('.xlsx'):
            return 'xlsx'
        if lower.endswith('.xls'):
            return 'xls'
        return 'csv'

    @staticmethod
    def preview(file_content: bytes, filename: str) -> dict[str, Any]:
        """Parse file and return headers + sample rows.

        Builds only the rows it shows; the rest are counted, not kept.

        Returns:
            {format, headers, sample_rows (first 5), total_rows}
        """
        fmt = ImportService.detect_format(filename)

        if fmt == 'xlsx':
            try:
                import openpyxl
            except ImportError:
                raise ValueError("openpyxl not installed. Add it to requirements.txt.") from None

            wb = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
            try:
                rows = wb.active.iter_rows(values_only=True)
                first = next(rows, None)
                if first is None:
                    raise ValueError("Empty spreadsheet")
                headers = [str(h) if h is not None else f"column_{i}" for i, h in enumerate(first)]
                sample = [
                    {headers[i]: (_as_text(v) if v is not None else "")
                     for i, v in enumerate(row) if i < len(headers)}
                    for row in islice(rows, SAMPLE_ROWS)
                ]
                total = len(sample) + sum(1 for _ in rows)
            finally:
                wb.close()

            return {
                "format": "xlsx",
                "headers": headers,
                "sample_rows": sample,
                "total_rows": total,
            }

        elif fmt == 'xls':
            raise ValueError("Legacy .xls format not supported. Please save as .xlsx or .csv.")

        else:  # csv
            text = file_content.decode('utf-8-sig')  # Handle BOM
            reader = csv.DictReader(io.StringIO(text))
            headers = [unescape_cell(h) for h in reader.fieldnames or []]
            if not headers:
                raise ValueError("CSV file has no headers")

            sample = [_unescaped(row) for row in islice(reader, SAMPLE_ROWS)]
            total = len(sample) + sum(1 for _ in reader)

            return {
                "format": "csv",
                "headers": list(headers),
                "sample_rows": sample,
                "total_rows": total,
            }

    @staticmethod
    def parse_rows(file_content: bytes, filename: str) -> tuple[list[str], list[dict[str, Any]]]:
        """Parse all rows from file. Returns (headers, rows).

        CSV cells are text. XLSX cells keep the type the sheet gives them
        (number, boolean, date, text), so a number or date reaches its field
        unparsed; an empty cell is "".
        """
        fmt = ImportService.detect_format(filename)

        if fmt == 'xlsx':
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
            ws = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
            wb.close()

            if not all_rows:
                raise ValueError("Empty spreadsheet")

            headers = [str(h) if h is not None else f"column_{i}" for i, h in enumerate(all_rows[0])]
            data_rows = []
            for row in all_rows[1:]:
                data_rows.append({headers[i]: (v if v is not None else "") for i, v in enumerate(row) if i < len(headers)})
            return headers, data_rows

        else:  # csv
            text = file_content.decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text))
            headers = [unescape_cell(h) for h in reader.fieldnames or []]
            data_rows = [_unescaped(row) for row in reader]
            return headers, data_rows

    @staticmethod
    def build_documents(
        rows: list[dict[str, Any]],
        template_id: str,
        column_mapping: dict[str, str],
        namespace: str,
        field_types: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Convert parsed rows to document create requests using column mapping.

        Args:
            rows: List of {csv_column: value} dicts
            template_id: Template ID to create documents for
            column_mapping: {csv_column_name: template_field_name}
            namespace: Target namespace
            field_types: {template_field_name: field type}; each cell is
                converted to its field's type (convert_cell)

        Returns:
            One document create request dict per row. A row with a cell its
            field's type cannot read carries ``conversion_errors`` (one message
            per such cell) instead of a usable value for it; the caller fails
            that row.
        """
        types = field_types or {}
        documents = []
        for row in rows:
            data: dict[str, Any] = {}
            errors: list[str] = []
            for csv_col, field_name in column_mapping.items():
                value = row.get(csv_col)
                if _blank(value):
                    continue
                try:
                    data[field_name] = convert_cell(value, types.get(field_name))
                except CellConversionError as e:
                    errors.append(f"{field_name} (column '{csv_col}'): {e}")
                    data[field_name] = value

            document: dict[str, Any] = {
                "template_id": template_id,
                "namespace": namespace,
                "data": data,
            }
            if errors:
                document["conversion_errors"] = errors
            documents.append(document)

        return documents
