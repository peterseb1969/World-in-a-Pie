"""
Transformer - Converts documents to flat PostgreSQL rows.

Responsibilities:
- Flatten nested objects into prefixed columns
- Handle arrays (expand to multiple rows or store as JSON)
- Map term fields to value + term_id columns
- Handle semantic types (duration → _seconds, geo_point → _lat/_lon)
- Preserve original JSON for complex queries
"""

import contextlib
import json
import logging
import re
from datetime import date, datetime
from typing import Any, ClassVar

from .models import ReportingConfig, SemanticType

logger = logging.getLogger(__name__)


# Markdown preprocessing for full-text indexing. Runs in Python at sync
# time before the GENERATED tsvector column tokenises. Goal: feed
# to_tsvector plain prose, not link/heading/bullet syntax. **Code-block
# contents are preserved** — fences are stripped but the code itself
# stays so a search for `tsvector` finds it inside code samples (this is
# a deliberate v1 design call from the FTS fireside).
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_MD_CODE_FENCE = re.compile(r"^[ \t]{0,3}```[^\n]*\n", re.MULTILINE)
_MD_CODE_FENCE_CLOSE = re.compile(r"\n[ \t]{0,3}```[ \t]*$", re.MULTILINE)
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_BOLD_UNDER = re.compile(r"__([^_\n]+)__")
_MD_ITALIC_UNDER = re.compile(r"(?<!_)_([^_\n]+)_(?!_)")
_MD_BULLET = re.compile(r"^[ \t]{0,3}[-*+][ \t]+", re.MULTILINE)
_MD_NUMBERED = re.compile(r"^[ \t]{0,3}\d+\.[ \t]+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_MD_HR = re.compile(r"^[ \t]{0,3}(-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)


def _strip_md(text: str | None) -> str | None:
    """Strip common markdown syntax for full-text-search preprocessing.

    Preserves code-block contents (fence delimiters removed, code kept).
    Replaces links/images with their visible text. Removes inline
    formatting markers (bold, italic, headings, bullets, blockquotes).

    Returns the original input verbatim if not a string. None passes
    through as None.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    if not text:
        return text

    s = text
    # Code fences: drop the fence lines but keep the code between them.
    s = _MD_CODE_FENCE.sub("", s)
    s = _MD_CODE_FENCE_CLOSE.sub("", s)
    # Images first (so the !\[ doesn't get caught by the link rule).
    s = _MD_IMAGE.sub(r"\1", s)
    # Links: keep visible text, drop URL.
    s = _MD_LINK.sub(r"\1", s)
    # Inline code: keep contents.
    s = _MD_INLINE_CODE.sub(r"\1", s)
    # Emphasis markers.
    s = _MD_BOLD.sub(r"\1", s)
    s = _MD_BOLD_UNDER.sub(r"\1", s)
    s = _MD_ITALIC.sub(r"\1", s)
    s = _MD_ITALIC_UNDER.sub(r"\1", s)
    # Block-level prefixes.
    s = _MD_HEADING.sub("", s)
    s = _MD_BULLET.sub("", s)
    s = _MD_NUMBERED.sub("", s)
    s = _MD_BLOCKQUOTE.sub("", s)
    s = _MD_HR.sub("", s)
    return s


# Time unit factors for computing duration seconds
# These match the _TIME_UNITS terminology in Def-Store
TIME_UNIT_FACTORS = {
    "seconds": 1,
    "second": 1,
    "sec": 1,
    "s": 1,
    "minutes": 60,
    "minute": 60,
    "min": 60,
    "m": 60,
    "hours": 3600,
    "hour": 3600,
    "hr": 3600,
    "h": 3600,
    "days": 86400,
    "day": 86400,
    "d": 86400,
    "weeks": 604800,
    "week": 604800,
    "wk": 604800,
    "w": 604800,
}


def _parse_datetime(value: Any) -> datetime | None:
    """Parse ISO datetime string to datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # Handle ISO format with timezone
            if value.endswith('Z'):
                value = value[:-1] + '+00:00'
            return datetime.fromisoformat(value)
        except ValueError:
            logger.warning(f"Failed to parse datetime: {value}")
            return None
    return None


def _parse_date(value: Any) -> date | None:
    """Parse ISO date string to date object."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            logger.warning(f"Failed to parse date: {value}")
            return None
    return None


class DocumentTransformer:
    """Transforms documents into flat row(s) for PostgreSQL."""

    # System column names that data fields should not conflict with
    SYSTEM_COLUMNS: ClassVar[set[str]] = {
        "document_id", "namespace", "template_id",
        "template_version", "version", "status", "identity_hash",
        "created_at", "created_by", "updated_at", "updated_by",
        "data_json", "term_references_json", "file_references_json",
    }

    def __init__(self, config: ReportingConfig | None = None):
        self.config = config or ReportingConfig()

    def _safe_column_name(self, name: str) -> str:
        """Return safe PostgreSQL column name.

        Dots become underscores (dots are table.column separators in SQL).
        System column names get a 'data_' prefix to avoid conflicts.
        """
        name = name.replace(".", "_")
        if name in self.SYSTEM_COLUMNS:
            return f"data_{name}"
        return name

    def _flatten_object(
        self,
        obj: dict[str, Any],
        prefix: str = "",
        term_references: dict[str, Any] | None = None,
        flatten_nested: bool = False,
        field_types: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Process object into columns, optionally flattening nested objects.

        By default (flatten_nested=False), nested objects are stored as JSONB
        to match the schema_manager behavior. Set flatten_nested=True to get
        prefixed columns like address_street, address_city.

        Example (flatten_nested=False):
            {"address": {"street": "Main St", "city": "NYC"}}
            →
            {"address": '{"street": "Main St", "city": "NYC"}'}

        Example (flatten_nested=True):
            {"address": {"street": "Main St", "city": "NYC"}}
            →
            {"address_street": "Main St", "address_city": "NYC"}
        """
        result = {}
        term_refs = term_references or {}

        for key, value in obj.items():
            full_key = f"{prefix}{key}" if prefix else key
            # Use safe column name to avoid conflicts with system columns
            safe_key = self._safe_column_name(full_key)

            if isinstance(value, dict):
                if flatten_nested:
                    # Recursively flatten nested objects
                    nested = self._flatten_object(
                        value, f"{full_key}_", term_refs.get(key, {}), flatten_nested=True,
                        field_types=field_types,
                    )
                    result.update(nested)
                else:
                    # Store nested objects as JSON (matches schema_manager JSONB columns)
                    result[safe_key] = json.dumps(value)
            elif isinstance(value, list):
                # Store arrays as JSON (row expansion handled separately)
                result[safe_key] = json.dumps(value)
            else:
                # Convert date/datetime strings using declared field types
                if isinstance(value, str) and field_types:
                    ft = field_types.get(key)
                    if ft == "date":
                        parsed_date = _parse_date(value)
                        if parsed_date:
                            value = parsed_date
                    elif ft == "datetime":
                        parsed_dt = _parse_datetime(value)
                        if parsed_dt:
                            value = parsed_dt

                result[safe_key] = value

                # Check for term reference
                if key in term_refs:
                    result[f"{safe_key}_term_id"] = term_refs[key]
                elif full_key in term_refs:
                    result[f"{safe_key}_term_id"] = term_refs[full_key]

        return result

    def _expand_arrays(
        self,
        base_row: dict[str, Any],
        data: dict[str, Any],
        term_references: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Expand arrays into multiple rows (cross product).

        If flatten_arrays is True and arrays are present, each combination
        of array elements produces a separate row.

        NOTE: Currently disabled by default because the schema_manager stores
        arrays as JSONB columns, not as separate rows. The full data is always
        available in data_json and term_references_json columns.
        """
        # Currently we don't expand arrays because:
        # 1. Schema stores arrays as JSONB, not individual columns per element
        # 2. Array term_id columns don't exist in the schema
        # 3. Full data is preserved in data_json and term_references_json
        # To enable array expansion, the schema_manager needs to be updated to
        # create the necessary columns (which requires knowing array structure ahead of time)
        return [base_row]

    def _process_semantic_types(
        self,
        row: dict[str, Any],
        data: dict[str, Any],
        term_references: dict[str, Any],
        semantic_types: dict[str, "SemanticType"],
    ) -> None:
        """
        Process semantic types and populate additional columns.

        For duration fields:
        - Computes {name}_seconds from value * factor
        - Sets {name}_unit_term_id from term_references

        For geo_point fields:
        - Extracts {name}_latitude and {name}_longitude
        """
        for field_name, semantic_type in semantic_types.items():
            value = data.get(field_name)
            if value is None:
                continue

            safe_name = self._safe_column_name(field_name)

            if semantic_type == SemanticType.DURATION:
                self._process_duration(row, safe_name, value, term_references)

            elif semantic_type == SemanticType.GEO_POINT:
                self._process_geo_point(row, safe_name, value)

    def _process_duration(
        self,
        row: dict[str, Any],
        col_name: str,
        value: dict[str, Any],
        term_references: dict[str, Any],
    ) -> None:
        """Process duration semantic type."""
        if not isinstance(value, dict):
            return

        duration_value = value.get("value")
        unit = value.get("unit")

        # The base column is already set as JSONB by _flatten_object
        # Just need to add _seconds and _unit_term_id columns

        # Compute normalized seconds
        if duration_value is not None and unit:
            # Look up unit factor (case-insensitive)
            factor = TIME_UNIT_FACTORS.get(str(unit).lower())
            if factor:
                row[f"{col_name}_seconds"] = duration_value * factor
            else:
                # Unknown unit - try to compute anyway using original value
                logger.warning(f"Unknown duration unit: {unit}")
                row[f"{col_name}_seconds"] = None
        else:
            row[f"{col_name}_seconds"] = None

        # Get unit term_id from term_references
        # The term reference path for duration unit is "{field_name}.unit"
        unit_ref_path = f"{col_name}.unit"
        unit_term_id = term_references.get(unit_ref_path)
        row[f"{col_name}_unit_term_id"] = unit_term_id

    def _process_geo_point(
        self,
        row: dict[str, Any],
        col_name: str,
        value: dict[str, Any],
    ) -> None:
        """Process geo_point semantic type."""
        if not isinstance(value, dict):
            return

        # The base column is already set as JSONB by _flatten_object
        # Just need to add _latitude and _longitude columns

        lat = value.get("latitude")
        lon = value.get("longitude")

        row[f"{col_name}_latitude"] = lat if isinstance(lat, (int, float)) else None
        row[f"{col_name}_longitude"] = lon if isinstance(lon, (int, float)) else None

    def transform(
        self,
        document: dict[str, Any],
        template: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Transform a document into one or more flat rows.

        Args:
            document: Full document from Document Store including:
                - document_id
                - template_id
                - version
                - status
                - identity_hash
                - data (the actual document content)
                - term_references
                - file_references
                - created_at, created_by, updated_at, updated_by
            template: Template definition (required for field type resolution)

        Returns:
            List of flat row dictionaries ready for PostgreSQL insert/upsert
        """
        data = document.get("data", {})
        term_references_list = document.get("term_references", [])

        # Build field type map and semantic type map from template
        field_types: dict[str, str] = {}
        semantic_types: dict[str, SemanticType] = {}
        for field in template.get("fields", []):
            field_types[field["name"]] = field.get("type", "string")
            if field.get("semantic_type"):
                with contextlib.suppress(ValueError):
                    semantic_types[field["name"]] = SemanticType(field["semantic_type"])
        file_references_list = document.get("file_references", [])

        # Convert array format to dict for compatibility with existing flattening logic
        # Array format: [{"field_path": "gender", "term_id": "019abc42-..."}, ...]
        # Dict format: {"gender": "019abc42-...", ...}
        term_references = {}
        for ref in term_references_list:
            field_path = ref.get("field_path", "")
            term_id = ref.get("term_id", "")
            if field_path and term_id:
                # Handle array indices in field path (e.g., "languages[0]")
                if "[" in field_path:
                    base_path = field_path.split("[")[0]
                    if base_path not in term_references:
                        term_references[base_path] = []
                    term_references[base_path].append(term_id)
                else:
                    term_references[field_path] = term_id

        # Convert file_references to dict for column mapping
        # Array format: [{"field_path": "photo", "file_id": "FILE-001", "filename": "...", "content_type": "..."}, ...]
        file_references = {}
        for ref in file_references_list:
            field_path = ref.get("field_path", "")
            if field_path:
                # Handle array indices in field path (e.g., "attachments[0]")
                if "[" in field_path:
                    base_path = field_path.split("[")[0]
                    if base_path not in file_references:
                        file_references[base_path] = []
                    file_references[base_path].append(ref)
                else:
                    file_references[field_path] = ref

        # Base row with system columns
        base_row = {
            "document_id": document["document_id"],
            "namespace": document["namespace"],
            "template_id": document["template_id"],
            "template_version": document.get("template_version", 1),
            "version": document.get("version", 1),
            "status": document.get("status", "active"),
            "identity_hash": document.get("identity_hash", ""),
        }

        # Metadata columns
        if self.config.include_metadata:
            base_row.update({
                "created_at": _parse_datetime(document.get("created_at")),
                "created_by": document.get("created_by"),
                "updated_at": _parse_datetime(document.get("updated_at")),
                "updated_by": document.get("updated_by"),
            })

        # Phase 6/7 — relationship templates carry extra resolution
        # metadata in event-payload data (source_ref_resolved,
        # target_ref_resolved, source_template_value, target_template_value).
        # These are NOT template fields — they're event enrichment — so
        # exclude them from the flatten step or the row will end up with
        # columns the table doesn't have.
        is_relationship = template.get("usage") == "relationship"
        flatten_input = data
        if is_relationship:
            flatten_input = {
                k: v for k, v in data.items()
                if k not in (
                    "source_ref_resolved", "target_ref_resolved",
                    "source_template_value", "target_template_value",
                )
            }

        # Flatten the data
        flattened_data = self._flatten_object(
            flatten_input, "", term_references, field_types=field_types,
        )
        base_row.update(flattened_data)

        # Process semantic types if template is provided
        if semantic_types:
            self._process_semantic_types(base_row, data, term_references, semantic_types)

        # Add term_id columns for top-level term references
        for field_path, term_id in term_references.items():
            if not isinstance(term_id, (list, dict)):
                safe_field = self._safe_column_name(field_path)
                col_name = f"{safe_field}_term_id"
                if col_name not in base_row:
                    base_row[col_name] = term_id

        # Add file columns for file references
        for field_path, file_ref in file_references.items():
            safe_field = self._safe_column_name(field_path)
            if isinstance(file_ref, list):
                # Multiple files - store as JSON
                base_row[safe_field] = json.dumps(file_ref)
            else:
                # Single file - separate columns
                base_row[f"{safe_field}_file_id"] = file_ref.get("file_id")
                base_row[f"{safe_field}_filename"] = file_ref.get("filename")
                base_row[f"{safe_field}_content_type"] = file_ref.get("content_type")

        # Full-text-indexed fields: populate <field>_search with the
        # markdown-stripped value. The schema_manager creates a
        # GENERATED tsvector column from this — we don't write the
        # tsvector itself. Only top-level string fields are eligible
        # (validator at template-creation enforces type=string).
        for f in template.get("fields", []):
            if not f.get("full_text_indexed"):
                continue
            if f.get("type") != "string":
                continue
            field_name = f["name"]
            raw = data.get(field_name)
            search_col = f"{self._safe_column_name(field_name)}_search"
            base_row[search_col] = _strip_md(raw) if isinstance(raw, str) else None

        # Store original JSON
        base_row["data_json"] = json.dumps(data)
        base_row["term_references_json"] = json.dumps(term_references_list)
        base_row["file_references_json"] = json.dumps(file_references_list)

        # Phase 7 — relationship templates carry source_ref_id and
        # target_ref_id columns populated from the Phase-6 enriched
        # event payload (data.source_ref_resolved / data.target_ref_resolved).
        # Fall back to the raw refs for resilience: if the producer was
        # pre-Phase-6, the resolved keys won't exist, but the columns
        # are still useful for JOINs against documents whose document_id
        # equals the source_ref / target_ref value.
        if is_relationship:
            base_row["source_ref_id"] = (
                data.get("source_ref_resolved") or data.get("source_ref")
            )
            base_row["target_ref_id"] = (
                data.get("target_ref_resolved") or data.get("target_ref")
            )

        # Expand arrays if configured
        rows = self._expand_arrays(base_row, data, term_references)

        return rows

    def generate_upsert_sql(
        self,
        table_name: str,
        row: dict[str, Any],
        strategy: str = "latest_only",
    ) -> tuple[str, list[Any]]:
        """
        Generate an UPSERT SQL statement for a row.

        Args:
            table_name: Target PostgreSQL table
            row: Flattened row dictionary
            strategy: "latest_only" (upsert) or "all_versions" (insert)

        Returns:
            Tuple of (sql_string, parameter_values)
        """
        columns = list(row.keys())
        placeholders = [f"${i+1}" for i in range(len(columns))]
        values = list(row.values())

        # Quote column names to handle reserved words
        quoted_columns = [f'"{col}"' for col in columns]

        if strategy == "latest_only":
            # UPSERT with version check
            update_cols = [
                f'"{col}" = EXCLUDED."{col}"'
                for col in columns
                if col != "document_id"
            ]
            update_clause = ", ".join(update_cols)

            sql = f"""
                INSERT INTO "{table_name}" ({', '.join(quoted_columns)})
                VALUES ({', '.join(placeholders)})
                ON CONFLICT (document_id)
                DO UPDATE SET {update_clause}
                WHERE "{table_name}".version < EXCLUDED.version
            """
        else:
            # INSERT for all_versions strategy — composite PK (document_id, version)
            sql = f"""
                INSERT INTO "{table_name}" ({', '.join(quoted_columns)})
                VALUES ({', '.join(placeholders)})
                ON CONFLICT (document_id, version) DO NOTHING
            """

        return sql.strip(), values
