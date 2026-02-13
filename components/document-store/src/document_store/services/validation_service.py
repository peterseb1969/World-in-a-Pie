"""Validation service for document validation against templates."""

import re
import time
import logging
from datetime import datetime, date
from typing import Any, Optional

from .template_store_client import get_template_store_client, TemplateStoreError
from .def_store_client import get_def_store_client, DefStoreError
from .identity_service import IdentityService


logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of document validation."""

    def __init__(self):
        self.valid = True
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.identity_hash: Optional[str] = None
        self.template_version: Optional[int] = None
        self.template_code: Optional[str] = None
        # Array format for indexing: [{"field_path": "gender", "term_id": "T-001"}, ...]
        self.term_references: list[dict[str, Any]] = []
        # Array format: [{"field_path": "supervisor", "reference_type": "document", "resolved": {...}}, ...]
        self.references: list[dict[str, Any]] = []
        # Array format: [{"field_path": "scan_image", "file_id": "FILE-000001", "filename": "...", ...}, ...]
        self.file_references: list[dict[str, Any]] = []
        self.timing: dict[str, float] = {}  # stage -> milliseconds

    def add_error(
        self,
        code: str,
        message: str,
        field: Optional[str] = None,
        details: Optional[dict[str, Any]] = None
    ):
        """Add a validation error."""
        self.valid = False
        self.errors.append({
            "field": field,
            "code": code,
            "message": message,
            "details": details
        })

    def add_warning(self, message: str):
        """Add a validation warning."""
        self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "identity_hash": self.identity_hash,
            "template_version": self.template_version,
            "term_references": self.term_references,
            "references": self.references,
            "file_references": self.file_references,
            "timing": self.timing
        }


class ValidationService:
    """
    Service for validating documents against templates.

    Implements a 7-stage validation pipeline:
    1. Structural - Valid dict, required envelope fields
    2. Template Resolution - Fetch template, check active, resolve inheritance
    3. Field Validation - Mandatory fields, type checking, field-level constraints
    4. Term Validation - Validate term values via Def-Store (legacy term fields)
    5. Reference Validation - Validate and resolve reference fields
    6. Rule Evaluation - Cross-field rules
    7. Identity Computation - Extract identity fields, compute hash

    Includes timing instrumentation for performance analysis.
    """

    # Type validators mapping field type to validation function
    TYPE_VALIDATORS = {
        "string": "_validate_string",
        "number": "_validate_number",
        "integer": "_validate_integer",
        "boolean": "_validate_boolean",
        "date": "_validate_date",
        "datetime": "_validate_datetime",
        "term": "_validate_term",
        "reference": "_validate_reference",
        "file": "_validate_file",
        "object": "_validate_object",
        "array": "_validate_array",
    }

    # Semantic type validators (called after base type validation)
    SEMANTIC_VALIDATORS = {
        "email": "_validate_semantic_email",
        "url": "_validate_semantic_url",
        "latitude": "_validate_semantic_latitude",
        "longitude": "_validate_semantic_longitude",
        "percentage": "_validate_semantic_percentage",
        "duration": "_validate_semantic_duration",
        "geo_point": "_validate_semantic_geo_point",
    }

    # Time units terminology code (system-provided)
    TIME_UNITS_TERMINOLOGY = "_TIME_UNITS"

    # Class-level timing statistics
    _timing_stats: dict[str, list[float]] = {}
    _validation_count: int = 0

    @classmethod
    def get_timing_stats(cls) -> dict[str, Any]:
        """Get aggregated timing statistics across all validations."""
        if cls._validation_count == 0:
            return {"validation_count": 0, "stages": {}}

        stats = {
            "validation_count": cls._validation_count,
            "stages": {}
        }

        for stage, times in cls._timing_stats.items():
            if times:
                sorted_times = sorted(times)
                n = len(sorted_times)
                stats["stages"][stage] = {
                    "count": n,
                    "avg_ms": sum(times) / n,
                    "min_ms": sorted_times[0],
                    "max_ms": sorted_times[-1],
                    "p50_ms": sorted_times[n // 2],
                    "p95_ms": sorted_times[int(n * 0.95)] if n >= 20 else sorted_times[-1],
                    "p99_ms": sorted_times[int(n * 0.99)] if n >= 100 else sorted_times[-1],
                }

        return stats

    @classmethod
    def reset_timing_stats(cls):
        """Reset timing statistics."""
        cls._timing_stats = {}
        cls._validation_count = 0

    @classmethod
    def _record_timing(cls, timing: dict[str, float]):
        """Record timing from a validation run."""
        cls._validation_count += 1
        for stage, ms in timing.items():
            if stage not in cls._timing_stats:
                cls._timing_stats[stage] = []
            cls._timing_stats[stage].append(ms)

    async def validate(
        self,
        template_id: str,
        data: dict[str, Any]
    ) -> ValidationResult:
        """
        Validate document data against a template.

        Args:
            template_id: Template ID to validate against
            data: Document data to validate

        Returns:
            ValidationResult with errors, warnings, identity hash, and timing
        """
        result = ValidationResult()
        total_start = time.perf_counter()

        # Stage 1: Structural validation
        start = time.perf_counter()
        if not self._validate_structural(data, result):
            result.timing["1_structural"] = (time.perf_counter() - start) * 1000
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result
        result.timing["1_structural"] = (time.perf_counter() - start) * 1000

        # Stage 2: Template resolution
        start = time.perf_counter()
        template = await self._resolve_template(template_id, result)
        result.timing["2_template_resolution"] = (time.perf_counter() - start) * 1000
        if template is None:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        result.template_version = template.get("version", 1)
        result.template_code = template.get("code")

        # Stage 3: Field validation
        start = time.perf_counter()
        await self._validate_fields(data, template, result)
        result.timing["3_field_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 4: Term validation (batched for efficiency) - legacy term fields
        start = time.perf_counter()
        await self._validate_terms(data, template, result)
        result.timing["4_term_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 5: Reference validation - unified reference fields
        start = time.perf_counter()
        await self._validate_references(data, template, result)
        result.timing["5_reference_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 5b: File validation - validate file references
        start = time.perf_counter()
        await self._validate_files(data, template, result)
        result.timing["5b_file_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 6: Rule evaluation
        start = time.perf_counter()
        self._evaluate_rules(data, template, result)
        result.timing["6_rule_evaluation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 7: Identity computation
        start = time.perf_counter()
        self._compute_identity(data, template, result)
        result.timing["7_identity_computation"] = (time.perf_counter() - start) * 1000

        result.timing["total"] = (time.perf_counter() - total_start) * 1000

        # Record timing for aggregated stats
        self._record_timing(result.timing)

        return result

    def _validate_structural(
        self,
        data: dict[str, Any],
        result: ValidationResult
    ) -> bool:
        """
        Stage 1: Structural validation.

        Ensures data is a valid dict.
        """
        if not isinstance(data, dict):
            result.add_error(
                code="invalid_structure",
                message="Document data must be a dictionary"
            )
            return False

        return True

    async def _resolve_template(
        self,
        template_id: str,
        result: ValidationResult
    ) -> Optional[dict[str, Any]]:
        """
        Stage 2: Template resolution.

        Fetches the template and checks it's valid and active.
        """
        try:
            client = get_template_store_client()
            template = await client.get_template_resolved(template_id)

            if template is None:
                result.add_error(
                    code="template_not_found",
                    message=f"Template '{template_id}' not found"
                )
                return None

            if template.get("status") != "active":
                result.add_error(
                    code="template_inactive",
                    message=f"Template '{template_id}' is not active"
                )
                return None

            return template

        except TemplateStoreError as e:
            result.add_error(
                code="template_error",
                message=f"Failed to fetch template: {str(e)}"
            )
            return None

    async def _validate_fields(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult,
        prefix: str = ""
    ):
        """
        Stage 3: Field validation.

        Validates field presence, types, and constraints.
        """
        fields = template.get("fields", [])
        field_map = {f["name"]: f for f in fields}

        # Check mandatory fields
        for field in fields:
            field_name = field["name"]
            full_path = f"{prefix}{field_name}" if prefix else field_name
            is_mandatory = field.get("mandatory", False)

            if field_name not in data:
                if is_mandatory:
                    result.add_error(
                        code="required",
                        message=f"Field '{full_path}' is required",
                        field=full_path
                    )
                continue

            # Validate field value
            value = data[field_name]
            await self._validate_field_value(
                value, field, full_path, template, result
            )

        # Check for unknown fields
        for field_name in data:
            if field_name not in field_map:
                result.add_warning(
                    f"Unknown field '{prefix}{field_name}' will be stored but not validated"
                )

    async def _validate_field_value(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult
    ):
        """Validate a single field value."""
        field_type = field.get("type", "string")
        validation = field.get("validation") or {}

        # Handle null values
        if value is None:
            if field.get("mandatory", False):
                result.add_error(
                    code="null_value",
                    message=f"Field '{field_path}' cannot be null",
                    field=field_path
                )
            return

        # Get type validator
        validator_name = self.TYPE_VALIDATORS.get(field_type)
        if validator_name:
            validator = getattr(self, validator_name)
            await validator(value, field, field_path, template, result, validation)

        # If base type validation passed, run semantic type validation
        if result.valid:
            semantic_type = field.get("semantic_type")
            if semantic_type:
                semantic_validator_name = self.SEMANTIC_VALIDATORS.get(semantic_type)
                if semantic_validator_name:
                    semantic_validator = getattr(self, semantic_validator_name)
                    await semantic_validator(value, field, field_path, result)

    async def _validate_string(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate string field."""
        if not isinstance(value, str):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be a string",
                field=field_path,
                details={"expected": "string", "got": type(value).__name__}
            )
            return

        # Check length constraints
        if validation.get("min_length") and len(value) < validation["min_length"]:
            result.add_error(
                code="min_length",
                message=f"Field '{field_path}' must be at least {validation['min_length']} characters",
                field=field_path
            )

        if validation.get("max_length") and len(value) > validation["max_length"]:
            result.add_error(
                code="max_length",
                message=f"Field '{field_path}' must be at most {validation['max_length']} characters",
                field=field_path
            )

        # Check pattern
        if validation.get("pattern"):
            pattern = validation["pattern"]
            if not re.match(pattern, value):
                result.add_error(
                    code="pattern",
                    message=f"Field '{field_path}' does not match required pattern",
                    field=field_path,
                    details={"pattern": pattern}
                )

        # Check enum
        if validation.get("enum") and value not in validation["enum"]:
            result.add_error(
                code="invalid_enum",
                message=f"Field '{field_path}' must be one of {validation['enum']}",
                field=field_path
            )

    async def _validate_number(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate number field."""
        if not isinstance(value, (int, float)):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be a number",
                field=field_path
            )
            return

        self._validate_numeric_constraints(value, field_path, validation, result)

    async def _validate_integer(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate integer field."""
        if not isinstance(value, int) or isinstance(value, bool):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be an integer",
                field=field_path
            )
            return

        self._validate_numeric_constraints(value, field_path, validation, result)

    def _validate_numeric_constraints(
        self,
        value: float,
        field_path: str,
        validation: dict[str, Any],
        result: ValidationResult
    ):
        """Validate numeric min/max constraints."""
        if validation.get("minimum") is not None and value < validation["minimum"]:
            result.add_error(
                code="minimum",
                message=f"Field '{field_path}' must be at least {validation['minimum']}",
                field=field_path
            )

        if validation.get("maximum") is not None and value > validation["maximum"]:
            result.add_error(
                code="maximum",
                message=f"Field '{field_path}' must be at most {validation['maximum']}",
                field=field_path
            )

    async def _validate_boolean(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate boolean field."""
        if not isinstance(value, bool):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be a boolean",
                field=field_path
            )

    async def _validate_date(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate date field."""
        if isinstance(value, date) and not isinstance(value, datetime):
            return  # Already a date object

        if isinstance(value, str):
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return
            except ValueError:
                pass

        result.add_error(
            code="invalid_type",
            message=f"Field '{field_path}' must be a valid date (YYYY-MM-DD)",
            field=field_path
        )

    async def _validate_datetime(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate datetime field."""
        if isinstance(value, datetime):
            return  # Already a datetime object

        if isinstance(value, str):
            # Try ISO format
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%fZ"]:
                try:
                    datetime.strptime(value.replace("+00:00", "Z").rstrip("Z") + ("" if "." in value else ""), fmt.rstrip("Z"))
                    return
                except ValueError:
                    continue

        result.add_error(
            code="invalid_type",
            message=f"Field '{field_path}' must be a valid datetime (ISO 8601)",
            field=field_path
        )

    async def _validate_term(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate term field (will be batch validated later)."""
        # Just check it's a string - actual term validation happens in _validate_terms
        if not isinstance(value, str):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be a string (term code)",
                field=field_path
            )

    async def _validate_reference(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """
        Validate reference field (will be batch resolved later).

        Reference values can be:
        - document_id (UUID7 format) - direct lookup
        - identity_hash (prefixed with 'hash:') - lookup by hash
        - business key (string or dict for composite) - resolve via identity fields
        """
        reference_type = field.get("reference_type")

        if reference_type == "document":
            # Document refs accept string (id, hash, or business key) or dict (composite business key)
            if not isinstance(value, (str, dict)):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string or object (document reference)",
                    field=field_path
                )
        elif reference_type == "term":
            # Term refs accept string (term code/value/alias)
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string (term reference)",
                    field=field_path
                )
        elif reference_type == "terminology":
            # Terminology refs accept string (code)
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string (terminology code)",
                    field=field_path
                )
        elif reference_type == "template":
            # Template refs accept string (code or ID)
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string (template code)",
                    field=field_path
                )
        else:
            result.add_error(
                code="invalid_reference_type",
                message=f"Field '{field_path}' has invalid reference_type '{reference_type}'",
                field=field_path
            )

    async def _validate_file(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """
        Validate file field (basic type check - actual validation happens in _validate_files).

        File values must be:
        - A string (single file_id like FILE-000001)
        - A list of strings if multiple=true in file_config
        """
        file_config = field.get("file_config") or {}
        multiple = file_config.get("multiple", False)

        if multiple:
            # Array of file IDs
            if not isinstance(value, list):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be an array of file IDs",
                    field=field_path
                )
                return
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    result.add_error(
                        code="invalid_type",
                        message=f"Item at '{field_path}[{i}]' must be a string (file ID)",
                        field=f"{field_path}[{i}]"
                    )
        else:
            # Single file ID
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string (file ID)",
                    field=field_path
                )

    async def _validate_object(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate nested object field."""
        if not isinstance(value, dict):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be an object",
                field=field_path
            )
            return

        # If template_ref is specified, fetch and validate against that template
        template_ref = field.get("template_ref")
        if template_ref:
            try:
                client = get_template_store_client()
                nested_template = await client.get_template_resolved(template_ref)
                if nested_template:
                    await self._validate_fields(
                        value, nested_template, result, prefix=f"{field_path}."
                    )
            except TemplateStoreError:
                result.add_warning(
                    f"Could not validate nested template '{template_ref}' for field '{field_path}'"
                )

    async def _validate_array(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        template: dict[str, Any],
        result: ValidationResult,
        validation: dict[str, Any]
    ):
        """Validate array field."""
        if not isinstance(value, list):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be an array",
                field=field_path
            )
            return

        # Validate each item
        item_type = field.get("array_item_type", "string")
        for i, item in enumerate(value):
            item_path = f"{field_path}[{i}]"

            if item_type == "term":
                # Create a mock field for term validation
                mock_field = {
                    "type": "term",
                    "terminology_ref": field.get("array_terminology_ref")
                }
                await self._validate_term(item, mock_field, item_path, template, result, {})

            elif item_type == "object":
                if not isinstance(item, dict):
                    result.add_error(
                        code="invalid_type",
                        message=f"Item at '{item_path}' must be an object",
                        field=item_path
                    )
                else:
                    template_ref = field.get("array_template_ref")
                    if template_ref:
                        try:
                            client = get_template_store_client()
                            item_template = await client.get_template_resolved(template_ref)
                            if item_template:
                                await self._validate_fields(
                                    item, item_template, result, prefix=f"{item_path}."
                                )
                        except TemplateStoreError:
                            pass

            elif item_type == "string":
                if not isinstance(item, str):
                    result.add_error(
                        code="invalid_type",
                        message=f"Item at '{item_path}' must be a string",
                        field=item_path
                    )

            elif item_type == "number":
                if not isinstance(item, (int, float)):
                    result.add_error(
                        code="invalid_type",
                        message=f"Item at '{item_path}' must be a number",
                        field=item_path
                    )

            elif item_type == "integer":
                if not isinstance(item, int) or isinstance(item, bool):
                    result.add_error(
                        code="invalid_type",
                        message=f"Item at '{item_path}' must be an integer",
                        field=item_path
                    )

    # ========================================================================
    # Semantic Type Validators
    # ========================================================================

    async def _validate_semantic_email(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """Validate email semantic type (RFC 5322 pattern)."""
        if not isinstance(value, str):
            return  # Base type validation will catch this

        # RFC 5322 simplified pattern
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, value):
            result.add_error(
                code="invalid_email",
                message=f"Field '{field_path}' must be a valid email address",
                field=field_path,
                details={"value": value}
            )

    async def _validate_semantic_url(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """Validate URL semantic type (valid HTTP/HTTPS URL)."""
        if not isinstance(value, str):
            return

        from urllib.parse import urlparse

        try:
            parsed = urlparse(value)
            if parsed.scheme not in ('http', 'https'):
                result.add_error(
                    code="invalid_url",
                    message=f"Field '{field_path}' must be a valid HTTP(S) URL",
                    field=field_path,
                    details={"value": value, "reason": "scheme must be http or https"}
                )
            elif not parsed.netloc:
                result.add_error(
                    code="invalid_url",
                    message=f"Field '{field_path}' must be a valid HTTP(S) URL",
                    field=field_path,
                    details={"value": value, "reason": "missing host"}
                )
        except Exception:
            result.add_error(
                code="invalid_url",
                message=f"Field '{field_path}' must be a valid HTTP(S) URL",
                field=field_path,
                details={"value": value}
            )

    async def _validate_semantic_latitude(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """Validate latitude semantic type (-90 to 90)."""
        if not isinstance(value, (int, float)):
            return

        if value < -90 or value > 90:
            result.add_error(
                code="invalid_latitude",
                message=f"Field '{field_path}' must be a valid latitude (-90 to 90)",
                field=field_path,
                details={"value": value, "min": -90, "max": 90}
            )

    async def _validate_semantic_longitude(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """Validate longitude semantic type (-180 to 180)."""
        if not isinstance(value, (int, float)):
            return

        if value < -180 or value > 180:
            result.add_error(
                code="invalid_longitude",
                message=f"Field '{field_path}' must be a valid longitude (-180 to 180)",
                field=field_path,
                details={"value": value, "min": -180, "max": 180}
            )

    async def _validate_semantic_percentage(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """Validate percentage semantic type (0 to 100)."""
        if not isinstance(value, (int, float)):
            return

        if value < 0 or value > 100:
            result.add_error(
                code="invalid_percentage",
                message=f"Field '{field_path}' must be a valid percentage (0 to 100)",
                field=field_path,
                details={"value": value, "min": 0, "max": 100}
            )

    async def _validate_semantic_duration(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """
        Validate duration semantic type.

        Expected structure: {"value": number, "unit": string}
        Unit is validated against _TIME_UNITS terminology.
        """
        if not isinstance(value, dict):
            result.add_error(
                code="invalid_duration",
                message=f"Field '{field_path}' must be an object with 'value' and 'unit'",
                field=field_path,
                details={"expected_structure": {"value": "number", "unit": "string"}}
            )
            return

        # Check required keys
        if "value" not in value:
            result.add_error(
                code="invalid_duration",
                message=f"Field '{field_path}' is missing required key 'value'",
                field=field_path
            )
            return

        if "unit" not in value:
            result.add_error(
                code="invalid_duration",
                message=f"Field '{field_path}' is missing required key 'unit'",
                field=field_path
            )
            return

        # Validate value is a number
        duration_value = value.get("value")
        if not isinstance(duration_value, (int, float)):
            result.add_error(
                code="invalid_duration",
                message=f"Field '{field_path}.value' must be a number",
                field=f"{field_path}.value",
                details={"got": type(duration_value).__name__}
            )
            return

        # Validate unit against _TIME_UNITS terminology
        unit_value = value.get("unit")
        if not isinstance(unit_value, str):
            result.add_error(
                code="invalid_duration",
                message=f"Field '{field_path}.unit' must be a string",
                field=f"{field_path}.unit"
            )
            return

        # Validate unit via Def-Store
        try:
            client = get_def_store_client()
            validation_result = await client.validate_value(
                self.TIME_UNITS_TERMINOLOGY,
                unit_value
            )

            if not validation_result.get("valid", False):
                result.add_error(
                    code="invalid_duration_unit",
                    message=f"Field '{field_path}.unit' value '{unit_value}' is not a valid time unit",
                    field=f"{field_path}.unit",
                    details={
                        "terminology": self.TIME_UNITS_TERMINOLOGY,
                        "suggestion": validation_result.get("suggestion"),
                        "valid_units": ["seconds", "minutes", "hours", "days", "weeks"]
                    }
                )
            else:
                # Store the unit's term_id in term_references
                matched_term = validation_result.get("matched_term")
                if matched_term and matched_term.get("term_id"):
                    result.term_references.append({
                        "field_path": f"{field_path}.unit",
                        "term_id": matched_term["term_id"],
                        "terminology_ref": self.TIME_UNITS_TERMINOLOGY,
                        "matched_via": validation_result.get("matched_via"),
                    })

        except DefStoreError as e:
            result.add_warning(
                f"Could not validate duration unit for '{field_path}': {str(e)}"
            )

    async def _validate_semantic_geo_point(
        self,
        value: Any,
        field: dict[str, Any],
        field_path: str,
        result: ValidationResult
    ):
        """
        Validate geo_point semantic type.

        Expected structure: {"latitude": number, "longitude": number}
        """
        if not isinstance(value, dict):
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}' must be an object with 'latitude' and 'longitude'",
                field=field_path,
                details={"expected_structure": {"latitude": "number", "longitude": "number"}}
            )
            return

        # Check required keys
        if "latitude" not in value:
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}' is missing required key 'latitude'",
                field=field_path
            )
            return

        if "longitude" not in value:
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}' is missing required key 'longitude'",
                field=field_path
            )
            return

        # Validate latitude
        lat = value.get("latitude")
        if not isinstance(lat, (int, float)):
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}.latitude' must be a number",
                field=f"{field_path}.latitude"
            )
        elif lat < -90 or lat > 90:
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}.latitude' must be between -90 and 90",
                field=f"{field_path}.latitude",
                details={"value": lat, "min": -90, "max": 90}
            )

        # Validate longitude
        lon = value.get("longitude")
        if not isinstance(lon, (int, float)):
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}.longitude' must be a number",
                field=f"{field_path}.longitude"
            )
        elif lon < -180 or lon > 180:
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{field_path}.longitude' must be between -180 and 180",
                field=f"{field_path}.longitude",
                details={"value": lon, "min": -180, "max": 180}
            )

    # ========================================================================
    # Term Validation (Stage 4)
    # ========================================================================

    async def _validate_terms(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult
    ):
        """
        Stage 4: Term validation.

        Collects all term fields and validates them in batch via Def-Store.
        Also collects term_references (resolved term IDs) for storage.
        """
        # Collect all term values to validate
        term_validations = self._collect_term_values(data, template.get("fields", []), "")

        if not term_validations:
            return

        # Batch validate via Def-Store
        try:
            client = get_def_store_client()
            validation_results = await client.validate_values_bulk([
                {"terminology_ref": tv["terminology_ref"], "value": tv["value"]}
                for tv in term_validations
            ])

            # Process results and collect term references
            for i, validation_result in enumerate(validation_results):
                term_val = term_validations[i]
                field_path = term_val["field_path"]

                if not validation_result.get("valid", False):
                    result.add_error(
                        code="invalid_term",
                        message=f"Value '{term_val['value']}' is not valid for terminology '{term_val['terminology_ref']}'",
                        field=field_path,
                        details={
                            "terminology": term_val["terminology_ref"],
                            "suggestion": validation_result.get("suggestion")
                        }
                    )
                else:
                    # Extract term_id from matched_term and store in term_references (array format)
                    matched_term = validation_result.get("matched_term")
                    if matched_term and matched_term.get("term_id"):
                        result.term_references.append({
                            "field_path": field_path,
                            "term_id": matched_term["term_id"],
                            "terminology_ref": term_val["terminology_ref"],
                            "matched_via": validation_result.get("matched_via"),
                        })

        except DefStoreError as e:
            # If Def-Store is unavailable, add a warning but don't fail
            result.add_warning(f"Could not validate terminology values: {str(e)}")

    def _collect_term_values(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str
    ) -> list[dict[str, str]]:
        """Recursively collect term field values for validation."""
        term_values = []

        for field in fields:
            field_name = field["name"]
            full_path = f"{prefix}{field_name}" if prefix else field_name

            if field_name not in data:
                continue

            value = data[field_name]
            if value is None:
                continue

            field_type = field.get("type", "string")

            if field_type == "term" and field.get("terminology_ref"):
                term_values.append({
                    "field_path": full_path,
                    "terminology_ref": field["terminology_ref"],
                    "value": value
                })

            elif field_type == "array" and field.get("array_item_type") == "term":
                terminology_ref = field.get("array_terminology_ref")
                if terminology_ref and isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, str):
                            term_values.append({
                                "field_path": f"{full_path}[{i}]",
                                "terminology_ref": terminology_ref,
                                "value": item
                            })

        return term_values

    async def _validate_references(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult
    ):
        """
        Validate and resolve reference fields.

        Handles document references by looking up the target document
        by document_id, identity_hash, or business key.
        """
        from .document_service import DocumentService  # Import here to avoid circular import
        from ..models.document import Document

        # Collect all reference values to validate
        ref_validations = self._collect_reference_values(data, template.get("fields", []), "")

        # Cache for expanded target_templates (to avoid repeated lookups)
        expanded_templates_cache: dict[str, list[str]] = {}

        for ref_val in ref_validations:
            field_path = ref_val["field_path"]
            reference_type = ref_val["reference_type"]
            value = ref_val["value"]
            target_templates = ref_val.get("target_templates", [])
            include_subtypes = ref_val.get("include_subtypes", False)
            target_terminologies = ref_val.get("target_terminologies", [])
            version_strategy = ref_val.get("version_strategy", "latest")

            # Expand target_templates with descendants when include_subtypes is set
            if include_subtypes and target_templates and reference_type == "document":
                target_templates = await self._expand_target_templates(
                    target_templates, expanded_templates_cache
                )

            try:
                if reference_type == "document":
                    # Resolve document reference
                    resolved = await self._resolve_document_reference(
                        value, target_templates, result, field_path
                    )
                    if resolved:
                        result.references.append({
                            "field_path": field_path,
                            "reference_type": "document",
                            "lookup_value": value if isinstance(value, str) else str(value),
                            "version_strategy": version_strategy,
                            "resolved": resolved,
                        })

                elif reference_type == "term":
                    # Resolve term reference (similar to legacy term validation)
                    if target_terminologies:
                        resolved = await self._resolve_term_reference(
                            value, target_terminologies, result, field_path
                        )
                        if resolved:
                            result.references.append({
                                "field_path": field_path,
                                "reference_type": "term",
                                "lookup_value": value,
                                "version_strategy": version_strategy,
                                "resolved": resolved,
                            })
                            # Also populate term_references for backward compatibility
                            result.term_references.append({
                                "field_path": field_path,
                                "term_id": resolved.get("term_id"),
                                "terminology_ref": resolved.get("terminology_code"),
                                "matched_via": resolved.get("matched_via"),
                            })

                elif reference_type == "terminology":
                    # Resolve terminology reference
                    resolved = await self._resolve_terminology_reference(
                        value, result, field_path
                    )
                    if resolved:
                        result.references.append({
                            "field_path": field_path,
                            "reference_type": "terminology",
                            "lookup_value": value,
                            "version_strategy": version_strategy,
                            "resolved": resolved,
                        })

                elif reference_type == "template":
                    # Resolve template reference
                    resolved = await self._resolve_template_reference(
                        value, result, field_path
                    )
                    if resolved:
                        result.references.append({
                            "field_path": field_path,
                            "reference_type": "template",
                            "lookup_value": value,
                            "version_strategy": version_strategy,
                            "resolved": resolved,
                        })

            except Exception as e:
                result.add_warning(f"Could not resolve reference for field '{field_path}': {str(e)}")

    async def _validate_files(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult
    ):
        """
        Stage 5b: File validation.

        Validates all file fields by checking:
        - File exists
        - File meets field constraints (allowed_types, max_size_mb)
        - Builds file_references array for storage
        """
        from .file_service import get_file_service, FileServiceError
        from .file_storage_client import is_file_storage_enabled

        # Collect all file values to validate
        file_validations = self._collect_file_values(data, template.get("fields", []), "")

        if not file_validations:
            return

        # Skip file validation if file storage is not enabled
        if not is_file_storage_enabled():
            result.add_warning("File storage not enabled. File references not validated.")
            return

        # Validate each file
        file_service = get_file_service()

        for file_val in file_validations:
            field_path = file_val["field_path"]
            file_id = file_val["file_id"]
            file_config = file_val["file_config"]

            allowed_types = file_config.get("allowed_types", ["*/*"])
            max_size_mb = file_config.get("max_size_mb", 10.0)

            try:
                is_valid, error_msg, file_ref = await file_service.validate_file_for_field(
                    file_id=file_id,
                    allowed_types=allowed_types,
                    max_size_mb=max_size_mb,
                )

                if not is_valid:
                    result.add_error(
                        code="invalid_file",
                        message=error_msg or f"File validation failed for field '{field_path}'",
                        field=field_path,
                        details={"file_id": file_id}
                    )
                elif file_ref:
                    # Add to file_references with field_path set
                    result.file_references.append({
                        "field_path": field_path,
                        "file_id": file_ref.file_id,
                        "filename": file_ref.filename,
                        "content_type": file_ref.content_type,
                        "size_bytes": file_ref.size_bytes,
                        "description": file_ref.description,
                    })

            except FileServiceError as e:
                result.add_error(
                    code="file_validation_error",
                    message=f"Failed to validate file for field '{field_path}': {str(e)}",
                    field=field_path
                )

    def _collect_file_values(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str
    ) -> list[dict[str, Any]]:
        """Recursively collect file field values for validation."""
        file_values = []

        for field in fields:
            field_name = field["name"]
            full_path = f"{prefix}{field_name}" if prefix else field_name

            if field_name not in data:
                continue

            value = data[field_name]
            if value is None:
                continue

            field_type = field.get("type", "string")
            file_config = field.get("file_config") or {}

            if field_type == "file":
                multiple = file_config.get("multiple", False)

                if multiple and isinstance(value, list):
                    # Array of file IDs
                    for i, file_id in enumerate(value):
                        if isinstance(file_id, str):
                            file_values.append({
                                "field_path": f"{full_path}[{i}]",
                                "file_id": file_id,
                                "file_config": file_config
                            })
                elif isinstance(value, str):
                    # Single file ID
                    file_values.append({
                        "field_path": full_path,
                        "file_id": value,
                        "file_config": file_config
                    })

            elif field_type == "array" and field.get("array_item_type") == "file":
                # Array of file items
                array_file_config = field.get("array_file_config") or {}
                if isinstance(value, list):
                    for i, file_id in enumerate(value):
                        if isinstance(file_id, str):
                            file_values.append({
                                "field_path": f"{full_path}[{i}]",
                                "file_id": file_id,
                                "file_config": array_file_config
                            })

        return file_values

    async def _expand_target_templates(
        self,
        target_templates: list[str],
        cache: dict[str, list[str]]
    ) -> list[str]:
        """
        Expand target_templates to include descendant template codes.

        When include_subtypes is set on a reference field, this method
        fetches all templates that inherit (directly or indirectly) from
        each target template and adds their codes to the allowed list.

        Uses a cache to avoid repeated lookups for the same template code.
        """
        expanded = set(target_templates)
        client = get_template_store_client()

        for code in target_templates:
            if code in cache:
                expanded.update(cache[code])
                continue

            try:
                # Fetch template by code to get template_id
                template = await client.get_template(template_code=code)
                if not template:
                    cache[code] = []
                    continue

                template_id = template.get("template_id")
                # Fetch descendants using template-store API
                descendants = await client.get_template_descendants(template_id)
                descendant_codes = [d.get("code") for d in descendants if d.get("code")]
                cache[code] = descendant_codes
                expanded.update(descendant_codes)
            except TemplateStoreError:
                cache[code] = []

        return list(expanded)

    async def _resolve_document_reference(
        self,
        value: Any,
        target_templates: list[str],
        result: ValidationResult,
        field_path: str
    ) -> Optional[dict[str, Any]]:
        """Resolve a document reference by ID, hash, or business key."""
        from ..models.document import Document, DocumentStatus

        # Determine lookup method based on value format
        if isinstance(value, str):
            if self._is_uuid7(value):
                # Direct document_id lookup
                doc = await Document.find_one({
                    "document_id": value,
                    "status": DocumentStatus.ACTIVE
                })
                if not doc:
                    # Try inactive too for pinned references
                    doc = await Document.find_one({"document_id": value})
            elif value.startswith("hash:"):
                # Identity hash lookup
                identity_hash = value[5:]  # Remove "hash:" prefix
                doc = await Document.find_one({
                    "identity_hash": identity_hash,
                    "status": DocumentStatus.ACTIVE
                })
            else:
                # Registry lookup — resolve any identifier (synonym, composite key value, etc.)
                doc = await self._resolve_via_registry(value, "wip-documents")

                if not doc:
                    # Fallback: Business key lookup
                    doc = await self._lookup_by_business_key(value, target_templates)
        elif isinstance(value, dict):
            # Composite business key
            doc = await self._lookup_by_business_key(value, target_templates)
        else:
            result.add_error(
                code="invalid_reference_value",
                message=f"Invalid reference value for field '{field_path}'",
                field=field_path
            )
            return None

        if not doc:
            result.add_error(
                code="reference_not_found",
                message=f"Referenced document not found for field '{field_path}'",
                field=field_path,
                details={"value": str(value), "target_templates": target_templates}
            )
            return None

        # Verify template matches target_templates
        # Look up the template code for this document
        try:
            client = get_template_store_client()
            doc_template = await client.get_template(doc.template_id)
            if doc_template and target_templates:
                if doc_template.get("code") not in target_templates:
                    result.add_error(
                        code="invalid_reference_template",
                        message=f"Referenced document is '{doc_template.get('code')}', expected one of {target_templates}",
                        field=field_path
                    )
                    return None
        except TemplateStoreError:
            result.add_warning(f"Could not verify template for referenced document in field '{field_path}'")

        # Check if document is inactive (warning only)
        if doc.status != DocumentStatus.ACTIVE:
            result.add_warning(f"Referenced document for field '{field_path}' is {doc.status.value}")

        return {
            "document_id": doc.document_id,
            "identity_hash": doc.identity_hash,
            "template_id": doc.template_id,
            "version": doc.version
        }

    async def _lookup_by_business_key(
        self,
        value: Any,
        target_templates: list[str]
    ) -> Optional[Any]:
        """Look up a document by business key value(s)."""
        from ..models.document import Document, DocumentStatus

        # For each target template, try to find a matching document
        client = get_template_store_client()

        for tpl_code in target_templates:
            try:
                template = await client.get_template(template_code=tpl_code)
                if not template:
                    continue

                identity_fields = template.get("identity_fields", [])
                if not identity_fields:
                    continue

                # Build query based on identity fields
                if isinstance(value, str) and len(identity_fields) == 1:
                    # Single identity field - value is the direct value
                    query = {
                        f"data.{identity_fields[0]}": value,
                        "status": DocumentStatus.ACTIVE
                    }
                elif isinstance(value, dict):
                    # Composite identity - value is a dict
                    query = {"status": DocumentStatus.ACTIVE}
                    for field_name in identity_fields:
                        if field_name in value:
                            query[f"data.{field_name}"] = value[field_name]
                else:
                    continue

                doc = await Document.find_one(query)
                if doc:
                    return doc

            except TemplateStoreError:
                continue

        return None

    async def _resolve_via_registry(
        self,
        value: str,
        pool_id: str
    ) -> Optional[Any]:
        """
        Resolve a reference value via the Registry's extended lookup.

        If the resolved entry points to an inactive document, follows the
        identity_hash chain to find the latest active version.
        """
        from ..models.document import Document, DocumentStatus
        from .registry_client import get_registry_client, RegistryError

        try:
            registry = get_registry_client()
            resolved_id = await registry.resolve_identifier(pool_id, value)
            if not resolved_id:
                return None

            # Fetch the document by resolved ID
            doc = await Document.find_one({
                "document_id": resolved_id,
                "status": DocumentStatus.ACTIVE
            })
            if doc:
                return doc

            # If inactive, follow identity_hash chain to latest active version
            inactive_doc = await Document.find_one({"document_id": resolved_id})
            if inactive_doc:
                active_doc = await Document.find_one({
                    "identity_hash": inactive_doc.identity_hash,
                    "status": DocumentStatus.ACTIVE
                })
                if active_doc:
                    return active_doc
                # Return the inactive doc if no active version exists
                return inactive_doc

            return None
        except RegistryError:
            return None

    async def _resolve_term_reference(
        self,
        value: str,
        target_terminologies: list[str],
        result: ValidationResult,
        field_path: str
    ) -> Optional[dict[str, Any]]:
        """Resolve a term reference."""
        client = get_def_store_client()

        # Try each terminology until we find a match
        for terminology_code in target_terminologies:
            try:
                validation_result = await client.validate_value(terminology_code, value)
                if validation_result.get("valid"):
                    matched_term = validation_result.get("matched_term", {})
                    return {
                        "term_id": matched_term.get("term_id"),
                        "terminology_code": terminology_code,
                        "matched_via": validation_result.get("matched_via")
                    }
            except DefStoreError:
                continue

        result.add_error(
            code="invalid_term_reference",
            message=f"Term '{value}' not found in terminologies {target_terminologies}",
            field=field_path
        )
        return None

    async def _resolve_terminology_reference(
        self,
        value: str,
        result: ValidationResult,
        field_path: str
    ) -> Optional[dict[str, Any]]:
        """Resolve a terminology reference."""
        client = get_def_store_client()

        try:
            exists = await client.terminology_exists(value)
            if exists:
                terminology = await client.get_terminology(value)
                if terminology:
                    return {
                        "terminology_id": terminology.get("terminology_id"),
                        "terminology_code": terminology.get("code"),
                    }
        except DefStoreError:
            pass

        result.add_error(
            code="invalid_terminology_reference",
            message=f"Terminology '{value}' not found",
            field=field_path
        )
        return None

    async def _resolve_template_reference(
        self,
        value: str,
        result: ValidationResult,
        field_path: str
    ) -> Optional[dict[str, Any]]:
        """Resolve a template reference."""
        client = get_template_store_client()

        try:
            template = await client.get_template(template_id=value)
            if not template:
                template = await client.get_template(template_code=value)

            if template:
                return {
                    "template_id": template.get("template_id"),
                    "template_code": template.get("code"),
                    "version": template.get("version")
                }
        except TemplateStoreError:
            pass

        result.add_error(
            code="invalid_template_reference",
            message=f"Template '{value}' not found",
            field=field_path
        )
        return None

    def _is_uuid7(self, value: str) -> bool:
        """Check if a value looks like a UUID7."""
        import re
        # UUID format: 8-4-4-4-12 hex digits
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        return bool(re.match(uuid_pattern, value.lower()))

    def _collect_reference_values(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str
    ) -> list[dict[str, Any]]:
        """Recursively collect reference field values for resolution."""
        ref_values = []

        for field in fields:
            field_name = field["name"]
            full_path = f"{prefix}{field_name}" if prefix else field_name

            if field_name not in data:
                continue

            value = data[field_name]
            if value is None:
                continue

            field_type = field.get("type", "string")

            if field_type == "reference":
                ref_values.append({
                    "field_path": full_path,
                    "reference_type": field.get("reference_type"),
                    "target_templates": field.get("target_templates", []),
                    "include_subtypes": field.get("include_subtypes", False),
                    "target_terminologies": field.get("target_terminologies", []),
                    "version_strategy": field.get("version_strategy", "latest"),
                    "value": value
                })

            elif field_type == "array" and field.get("array_item_type") == "reference":
                # Array of references
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        ref_values.append({
                            "field_path": f"{full_path}[{i}]",
                            "reference_type": field.get("reference_type"),
                            "target_templates": field.get("target_templates", []),
                            "include_subtypes": field.get("include_subtypes", False),
                            "target_terminologies": field.get("target_terminologies", []),
                            "version_strategy": field.get("version_strategy", "latest"),
                            "value": item
                        })

        return ref_values

    def _evaluate_rules(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult
    ):
        """
        Stage 5: Rule evaluation.

        Evaluates cross-field validation rules.
        """
        rules = template.get("rules", [])

        for rule in rules:
            rule_type = rule.get("type")

            if rule_type == "conditional_required":
                self._evaluate_conditional_required(data, rule, result)
            elif rule_type == "conditional_value":
                self._evaluate_conditional_value(data, rule, result)
            elif rule_type == "mutual_exclusion":
                self._evaluate_mutual_exclusion(data, rule, result)
            elif rule_type == "dependency":
                self._evaluate_dependency(data, rule, result)

    def _check_condition(
        self,
        data: dict[str, Any],
        condition: dict[str, Any]
    ) -> bool:
        """Check if a condition is met."""
        field = condition.get("field")
        operator = condition.get("operator")
        expected_value = condition.get("value")

        actual_value = IdentityService._get_nested_value(data, field)

        if operator == "equals":
            return actual_value == expected_value
        elif operator == "not_equals":
            return actual_value != expected_value
        elif operator == "in":
            return actual_value in expected_value if expected_value else False
        elif operator == "not_in":
            return actual_value not in expected_value if expected_value else True
        elif operator == "exists":
            return actual_value is not None
        elif operator == "not_exists":
            return actual_value is None

        return False

    def _check_conditions(
        self,
        data: dict[str, Any],
        conditions: list[dict[str, Any]]
    ) -> bool:
        """Check if all conditions are met (AND logic)."""
        return all(self._check_condition(data, c) for c in conditions)

    def _evaluate_conditional_required(
        self,
        data: dict[str, Any],
        rule: dict[str, Any],
        result: ValidationResult
    ):
        """Evaluate conditional_required rule."""
        conditions = rule.get("conditions", [])
        target_field = rule.get("target_field")
        is_required = rule.get("required", True)

        if not self._check_conditions(data, conditions):
            return

        target_value = IdentityService._get_nested_value(data, target_field)

        if is_required and target_value is None:
            result.add_error(
                code="conditional_required",
                message=rule.get("error_message") or f"Field '{target_field}' is required based on conditions",
                field=target_field
            )

    def _evaluate_conditional_value(
        self,
        data: dict[str, Any],
        rule: dict[str, Any],
        result: ValidationResult
    ):
        """Evaluate conditional_value rule."""
        conditions = rule.get("conditions", [])
        target_field = rule.get("target_field")
        allowed_values = rule.get("allowed_values", [])

        if not self._check_conditions(data, conditions):
            return

        target_value = IdentityService._get_nested_value(data, target_field)

        if target_value is not None and target_value not in allowed_values:
            result.add_error(
                code="conditional_value",
                message=rule.get("error_message") or f"Field '{target_field}' must be one of {allowed_values}",
                field=target_field
            )

    def _evaluate_mutual_exclusion(
        self,
        data: dict[str, Any],
        rule: dict[str, Any],
        result: ValidationResult
    ):
        """Evaluate mutual_exclusion rule."""
        target_fields = rule.get("target_fields", [])

        fields_with_values = [
            f for f in target_fields
            if IdentityService._get_nested_value(data, f) is not None
        ]

        if len(fields_with_values) > 1:
            result.add_error(
                code="mutual_exclusion",
                message=rule.get("error_message") or f"Only one of {target_fields} can have a value",
                field=fields_with_values[0]
            )

    def _evaluate_dependency(
        self,
        data: dict[str, Any],
        rule: dict[str, Any],
        result: ValidationResult
    ):
        """Evaluate dependency rule."""
        conditions = rule.get("conditions", [])
        target_field = rule.get("target_field")

        target_value = IdentityService._get_nested_value(data, target_field)

        # If target field has value, check that dependency conditions are met
        if target_value is not None:
            if not self._check_conditions(data, conditions):
                # Find missing dependency
                for condition in conditions:
                    if not self._check_condition(data, condition):
                        result.add_error(
                            code="dependency",
                            message=rule.get("error_message") or f"Field '{target_field}' requires '{condition['field']}' to be set",
                            field=target_field
                        )
                        break

    def _compute_identity(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult
    ):
        """
        Stage 6: Identity computation.

        Computes the identity hash from identity fields.
        """
        identity_fields = template.get("identity_fields", [])

        if not identity_fields:
            result.add_warning(
                "Template has no identity fields. Document will not support upsert logic."
            )
            # Generate a hash from all data
            result.identity_hash = IdentityService.compute_hash(data)
            return

        try:
            result.identity_hash = IdentityService.compute_identity_hash(
                data, identity_fields
            )
        except ValueError as e:
            result.add_error(
                code="identity_error",
                message=str(e)
            )
