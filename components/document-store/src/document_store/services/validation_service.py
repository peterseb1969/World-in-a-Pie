"""Validation service for document validation against templates."""

import contextlib
import logging
import math
import re
import time
from datetime import date, datetime
from typing import Any, ClassVar, cast

from wip_auth import split_qualified_value
from wip_auth.document_identity import (
    build_referent_resolutions,
    is_referent_field,
    referent_identity_fields,
    substitute_resolved_referents,
)
from wip_auth.registry_client import AmbiguousIdentifierError, DependencyUnavailableError

from .def_store_client import DefStoreError, get_def_store_client
from .errors import dependency_unavailable
from .identity_service import IdentityService
from .inbound_gate import InboundGate, current_gate, gate_refuses, reset_gate, use_gate
from .template_store_client import TemplateStoreError, get_template_store_client
from .timing_stats import TimingStats

logger = logging.getLogger(__name__)

# Distinct (terminology, value) pairs per def-store request when a bulk
# write resolves its term values up front (prefetch_term_values).
TERM_PREFETCH_CHUNK = 1000


# An ISO 8601 UTC offset at the end of a datetime: Z or +HH:MM / -HH:MM.
_DATETIME_OFFSET = re.compile(r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$")
_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f")
_ELEMENT_SUFFIX = re.compile(r"\[\d+\]$")


def _is_iso_datetime(value: str) -> bool:
    """A datetime string: date, 'T', time with optional fraction, and an
    optional offset (Z or +HH:MM / -HH:MM). The value is stored as submitted;
    reporting stores datetimes with their time zone."""
    base = _DATETIME_OFFSET.sub("", value, count=1)
    for fmt in _DATETIME_FORMATS:
        try:
            datetime.strptime(base, fmt)
            return True
        except ValueError:
            continue
    return False


def _feeds_identity(field_path: str, template: dict[str, Any]) -> bool:
    """Whether a resolved term at ``field_path`` feeds the identity hash, so
    an ambiguous name there must be refused rather than resolved by storage
    order. That is an identity field itself, or an element (``tags[0]``) of a
    referent identity field, the same set the hash substitutes element-wise
    (``referent_identity_fields``)."""
    identity_fields = template.get("identity_fields") or []
    if field_path in identity_fields:
        return True
    base = _ELEMENT_SUFFIX.sub("", field_path)
    try:
        referent = referent_identity_fields(
            identity_fields, template.get("fields") or [],
            template.get("referent_identity_paths"),
        )
    except ValueError:
        return False  # the identity step refuses the write with the reason
    return base != field_path and base in referent


class ValidationResult:
    """Result of document validation."""

    def __init__(self):
        self.valid = True
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.identity_hash: str | None = None
        # Entity id -> namespace for referents whose resolver knows where
        # they live (templates, terminologies). The outbound reference gate
        # takes them as placements, so it does not depend on a Registry
        # lookup for them, which skips an entry that is not active (a
        # soft-deleted or merged-away entry).
        self.referent_namespaces: dict[str, str] = {}
        # The hash feed: resolved-referent rule applied — a term/reference
        # identity field carries its referent's canonical ID here, never the
        # submitted spelling (see wip_auth.document_identity).
        self.identity_values: dict[str, Any] = {}
        # The submitted spellings of the same fields — what the raw-values
        # Registry synonym is minted from, so human-readable business-key
        # references keep resolving while the hash converges on referents.
        self.submitted_identity_values: dict[str, Any] = {}
        self.identity_fields: list[str] = []
        # The identity fields whose value is a referent (term / reference
        # typed, nested paths included): the ones identity_values carries as
        # resolved canonical IDs rather than as submitted.
        self.referent_identity_fields: set[str] = set()
        self.template_version: int | None = None
        self.template_value: str | None = None
        # The template's OWN namespace — which may differ from the document's
        # (a document may be based on a shared or foreign template). The
        # isolation check needs this real value; passing the document's
        # namespace in its place makes the template branch constant-false.
        self.template_namespace: str | None = None
        # Array format for indexing: [{"field_path": "gender", "term_id": "019abc42-..."}, ...]
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
        field: str | None = None,
        details: dict[str, Any] | None = None
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


def _is_number(item: Any) -> bool:
    return isinstance(item, (int, float)) and not isinstance(item, bool)


def _is_integer(item: Any) -> bool:
    return isinstance(item, int) and not isinstance(item, bool)


def _is_string(item: Any) -> bool:
    return isinstance(item, str)


# Array item types checked by shape alone: (accepts the item, what the error
# says it must be). A file item is checked here only as a string; its
# existence is checked in the file stage, which collects only string items, so
# a non-string item must be refused here or it is never checked at all.
_ARRAY_ITEM_SHAPES: dict[str, tuple[Any, str]] = {
    "string": (_is_string, "a string"),
    "number": (_is_number, "a number"),
    "integer": (_is_integer, "an integer"),
    "file": (_is_string, "a string (file ID)"),
}

# Array item types checked by the validator of a field of that type.
_ARRAY_ITEM_FIELD_VALIDATORS: dict[str, str] = {
    "boolean": "_validate_boolean",
    "date": "_validate_date",
    "datetime": "_validate_datetime",
}


def _reference_item_shape_error(item: Any, reference_type: str | None) -> str | None:
    """What a reference item must be, when it is not; None when it passes.

    CASE-550: an array of references reuses the field's top-level
    reference_type slot (the collector reads it there and queues each item for
    Stage-5 existence resolution). This mirrors the single-reference Stage-3
    type check (_validate_reference): document refs accept a string or a
    composite dict, the others a string. reference_type is guaranteed present
    on templates authored after the template-store guard; a grandfathered
    template may carry None, and then the per-item type constraint is skipped
    (existing soft-link data keeps validating) — existence resolution still
    runs when a real reference_type is present.
    """
    if reference_type == "document":
        if not isinstance(item, (str, dict)):
            return "a string or object (document reference)"
        return None
    if reference_type in ("term", "terminology", "template") and not isinstance(item, str):
        return f"a string ({reference_type} reference)"
    return None


def _business_key_error(
    value: dict[str, Any],
    identities: dict[str, list[str]],
    matches: list[tuple[str, str]],
) -> dict[str, Any] | None:
    """The error of a business key, given each target template's identity
    fields and the documents it matched: it names no target template's
    identity, or it matches documents under several. None otherwise."""
    if not any(set(value) == set(fields) for fields in identities.values() if fields):
        return {
            "code": "invalid_reference_value",
            "message": (
                f"Business key {sorted(value)} does not name the identity fields "
                "of any target template"
            ),
            "details": {"value": value, "identity_fields": identities},
        }
    if len(matches) > 1:
        return {
            "code": "ambiguous_reference",
            "message": "Business key matches documents under several target templates",
            "details": {
                "value": value,
                "candidates": [{"document_id": d, "template_value": t} for d, t in matches],
            },
        }
    return None


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
    TYPE_VALIDATORS: ClassVar[dict[str, str]] = {
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
    SEMANTIC_VALIDATORS: ClassVar[dict[str, str]] = {
        "email": "_validate_semantic_email",
        "url": "_validate_semantic_url",
        "latitude": "_validate_semantic_latitude",
        "longitude": "_validate_semantic_longitude",
        "percentage": "_validate_semantic_percentage",
        "duration": "_validate_semantic_duration",
        "geo_point": "_validate_semantic_geo_point",
    }

    # Time units terminology value (system-provided)
    TIME_UNITS_TERMINOLOGY = "_TIME_UNITS"

    # Class-level timing statistics, in bounded memory (timing_stats).
    # Recorded for validations that complete; one that stops at an early
    # refusal records nothing, so validation_count counts completed ones.
    _timing: ClassVar[TimingStats] = TimingStats()

    @classmethod
    def get_timing_stats(cls) -> dict[str, Any]:
        """Get aggregated timing statistics across completed validations."""
        return {"validation_count": cls._timing.runs, "stages": cls._timing.stages()}

    @classmethod
    def reset_timing_stats(cls):
        """Reset timing statistics."""
        cls._timing = TimingStats()

    @classmethod
    def _record_timing(cls, timing: dict[str, float]):
        """Record timing from a validation run."""
        cls._timing.record(timing)

    async def validate(
        self,
        template_id: str,
        data: dict[str, Any],
        namespace: str,
        template_version: int | None = None,
        doc_ref_cache: dict | None = None,
        template_override: dict[str, Any] | None = None,
        inbound: InboundGate | None = None,
    ) -> ValidationResult:
        """
        Validate document data against a template.

        With `inbound`, a reference whose target lies in a closed namespace
        the writer cannot read is treated as not found, by each resolver's
        own not-found branch (see inbound_gate).

        Args:
            template_id: Template ID to validate against
            data: Document data to validate
            template_override: An inline template definition used INSTEAD of
                resolving template_id — the candidate dry-run path ("would
                these documents validate against this draft?"). Nothing is
                persisted or cached; every later validation stage consumes
                the dict exactly as a resolved template would be.

        Returns:
            ValidationResult with errors, warnings, identity hash, and timing
        """
        token = use_gate(inbound)
        try:
            return await self._validate(
                template_id, data, namespace, template_version,
                doc_ref_cache, template_override,
            )
        finally:
            reset_gate(token)

    async def _validate(
        self,
        template_id: str,
        data: dict[str, Any],
        namespace: str,
        template_version: int | None,
        doc_ref_cache: dict | None,
        template_override: dict[str, Any] | None,
    ) -> ValidationResult:
        result = ValidationResult()
        total_start = time.perf_counter()

        # Stage 1: Structural validation
        start = time.perf_counter()
        if not self._validate_structural(data, result):
            result.timing["1_structural"] = (time.perf_counter() - start) * 1000
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result
        result.timing["1_structural"] = (time.perf_counter() - start) * 1000

        # Stage 2: Template resolution (skipped for an inline candidate)
        start = time.perf_counter()
        template: dict[str, Any] | None
        if template_override is not None:
            # The candidate exists nowhere, so nothing worked out which of its
            # identity paths name a referent; a stored version carries that
            # answer from the template store. Derived here, by the same rule —
            # whatever the caller put in the field is not taken on trust.
            template = dict(template_override)
            template["referent_identity_paths"] = await self._candidate_referent_paths(
                template
            )
        else:
            template = await self._resolve_template(template_id, result, version=template_version)
        result.timing["2_template_resolution"] = (time.perf_counter() - start) * 1000
        if template is None:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        result.template_version = template.get("version", 1)
        result.template_value = template.get("value")
        result.template_namespace = template.get("namespace")

        # Stage 3: Field validation
        start = time.perf_counter()
        await self._validate_fields(data, template, result)
        result.timing["3_field_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 4: Term validation (batched for efficiency) - legacy term fields
        start = time.perf_counter()
        await self._validate_terms(data, template, result, namespace=namespace)
        result.timing["4_term_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 5: Reference validation - unified reference fields
        start = time.perf_counter()
        await self._validate_references(data, template, result, namespace=namespace, doc_ref_cache=doc_ref_cache)
        result.timing["5_reference_validation"] = (time.perf_counter() - start) * 1000
        if not result.valid:
            result.timing["total"] = (time.perf_counter() - total_start) * 1000
            return result

        # Stage 5b: File validation - validate file references
        start = time.perf_counter()
        await self._validate_files(
            data, template, result, template_id=template.get("template_id") or template_id,
        )
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

        # JSON parsing accepts NaN and ±Infinity, but no JSON response can
        # carry them: a stored NaN reads back as null, so the write would be
        # accepted and its value silently changed. NaN also passes every
        # minimum/maximum check, because every comparison with it is false.
        # Refused anywhere in the data, typed fields, untyped objects and
        # arrays alike.
        bad_paths = self._non_finite_paths(data, "")
        for path in bad_paths:
            result.add_error(
                code="non_finite_number",
                message=f"Field '{path}' is not a finite number (NaN and Infinity are refused)",
                field=path,
            )
        return not bad_paths

    @classmethod
    def _non_finite_paths(cls, value: Any, path: str) -> list[str]:
        """The paths of every non-finite float inside value."""
        if isinstance(value, float):
            return [] if math.isfinite(value) else [path]
        if isinstance(value, dict):
            return [
                p for k, v in value.items()
                for p in cls._non_finite_paths(v, f"{path}.{k}" if path else str(k))
            ]
        if isinstance(value, list):
            return [
                p for i, v in enumerate(value)
                for p in cls._non_finite_paths(v, f"{path}[{i}]")
            ]
        return []

    async def _resolve_template(
        self,
        template_id: str,
        result: ValidationResult,
        version: int | None = None
    ) -> dict[str, Any] | None:
        """
        Stage 2: Template resolution.

        Fetches the template and checks it's valid and active.
        """
        try:
            client = get_template_store_client()
            template = await client.get_template_resolved(template_id, version=version)
            if template is not None and await gate_refuses(
                template.get("namespace"), template.get("template_id") or template_id,
            ):
                template = None

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
            if e.unavailable:
                raise dependency_unavailable("template-store", f"template {template_id!r}", e) from e
            if e.code == "inheritance_unresolved":
                # The template exists but its inheritance chain is broken;
                # validating against its own fields alone would accept data
                # the full schema refuses.
                result.add_error(code="template_unresolvable", message=str(e))
                return None
            result.add_error(
                code="template_error",
                message=f"Failed to fetch template: {e!s}"
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

        # Reject unknown fields — if it's not in the template, it doesn't belong
        for field_name in data:
            if field_name not in field_map:
                result.add_error(
                    code="unknown_field",
                    message=f"Unknown field '{prefix}{field_name}' is not declared in the template",
                    field=f"{prefix}{field_name}"
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
        errors_before = len(result.errors)
        validator_name = self.TYPE_VALIDATORS.get(field_type)
        if validator_name:
            validator = getattr(self, validator_name)
            await validator(value, field, field_path, template, result, validation)

        # Semantic validation assumes the base type holds, so it is skipped
        # when THIS field failed its type check, not when any earlier field
        # did: every field's errors are reported.
        if len(result.errors) == errors_before:
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
            # The pattern must match the whole value: "[A-Z]{3}" means
            # "three capitals", not "starts with three capitals".
            if not re.fullmatch(pattern, value):
                result.add_error(
                    code="pattern",
                    message=f"Field '{field_path}' does not match required pattern",
                    field=field_path,
                    details={"pattern": pattern}
                )

        self._validate_enum(value, field_path, validation, result)

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
        # bool is an int subclass in Python; true/false is not a number.
        if not isinstance(value, (int, float)) or isinstance(value, bool):
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
        """Validate numeric min/max and enum constraints."""
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

        self._validate_enum(value, field_path, validation, result)

    @staticmethod
    def _validate_enum(
        value: Any, field_path: str, validation: dict[str, Any], result: ValidationResult
    ) -> None:
        """A declared enum holds for every scalar type, not only strings."""
        if validation.get("enum") and value not in validation["enum"]:
            result.add_error(
                code="invalid_enum",
                message=f"Field '{field_path}' must be one of {validation['enum']}",
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
            return
        self._validate_enum(value, field_path, validation, result)

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

        if isinstance(value, str) and _is_iso_datetime(value):
            return

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
                message=f"Field '{field_path}' must be a string (term value)",
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
            # Terminology refs accept string (value)
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string (terminology value)",
                    field=field_path
                )
        elif reference_type == "template":
            # Template refs accept string (value or ID)
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{field_path}' must be a string (template value)",
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
            max_files = file_config.get("max_files")
            if max_files is not None and len(value) > max_files:
                result.add_error(
                    code="max_files",
                    message=f"Field '{field_path}' holds at most {max_files} file(s), got {len(value)}",
                    field=field_path,
                    details={"max_files": max_files, "count": len(value)},
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

        # If template_ref is specified, validate against that template at its
        # PINNED version (CASE-493). template_ref_version is mandatory on the
        # template schema, so nested data is always validated against an exact
        # (template_id, version) pair — resolved via the permanent (immutable)
        # cache, never the 5s "latest" path. This is what stops a parent
        # document from stranding on PATCH when the nested template later ships
        # a new, incompatible version.
        template_ref = field.get("template_ref")
        if template_ref:
            template_ref_version = field.get("template_ref_version")
            nested_template = await self._sub_template(
                template_ref, template_ref_version, field_path, result,
            )
            if nested_template:
                await self._validate_fields(
                    value, nested_template, result, prefix=f"{field_path}."
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
        """Validate array field: a list, each item checked as its declared
        item type."""
        if not isinstance(value, list):
            result.add_error(
                code="invalid_type",
                message=f"Field '{field_path}' must be an array",
                field=field_path
            )
            return

        # An array that declares no item type has untyped items: they are not
        # checked one by one. (The key is always present, so a .get default
        # never applied; the field model documents no default type.)
        item_type = field.get("array_item_type")
        for i, item in enumerate(value):
            await self._validate_array_item(
                item, item_type, field, f"{field_path}[{i}]", template, result,
            )

    async def _validate_array_item(
        self,
        item: Any,
        item_type: str | None,
        field: dict[str, Any],
        item_path: str,
        template: dict[str, Any],
        result: ValidationResult,
    ) -> None:
        """Check one array item as its declared item type."""
        if item_type in _ARRAY_ITEM_SHAPES:
            accepts, what = _ARRAY_ITEM_SHAPES[item_type]
            if not accepts(item):
                result.add_error(
                    code="invalid_type",
                    message=f"Item at '{item_path}' must be {what}",
                    field=item_path
                )
        elif item_type in _ARRAY_ITEM_FIELD_VALIDATORS:
            # The field's own validator, run on the item.
            validator = getattr(self, _ARRAY_ITEM_FIELD_VALIDATORS[item_type])
            await validator(item, field, item_path, template, result, {})
        elif item_type == "term":
            # A term item is validated as a term field of the array's terminology.
            mock_field = {"type": "term", "terminology_ref": field.get("array_terminology_ref")}
            await self._validate_term(item, mock_field, item_path, template, result, {})
        elif item_type == "object":
            await self._validate_object_item(item, field, item_path, result)
        elif item_type == "reference":
            must_be = _reference_item_shape_error(item, field.get("reference_type"))
            if must_be:
                result.add_error(
                    code="invalid_type",
                    message=f"Item at '{item_path}' must be {must_be}",
                    field=item_path
                )

    async def _validate_object_item(
        self,
        item: Any,
        field: dict[str, Any],
        item_path: str,
        result: ValidationResult,
    ) -> None:
        """An object item: a dict, validated against the array's item template
        when it declares one.

        The item template is read at its PINNED version, same as the object
        axis (array_template_ref_version is mandatory on the schema). One the
        template store cannot answer refuses the write; one it does not find
        is an error on the item — the item is never left unchecked.
        """
        if not isinstance(item, dict):
            result.add_error(
                code="invalid_type",
                message=f"Item at '{item_path}' must be an object",
                field=item_path
            )
            return
        template_ref = field.get("array_template_ref")
        if not template_ref:
            return
        item_template = await self._sub_template(
            template_ref, field.get("array_template_ref_version"), item_path, result,
        )
        if item_template:
            await self._validate_fields(item, item_template, result, prefix=f"{item_path}.")

    async def _sub_template(
        self,
        template_ref: str,
        version: int | None,
        field_path: str,
        result: ValidationResult,
    ) -> dict[str, Any] | None:
        """The template a nested object or array item is validated against.

        A template the store cannot answer refuses the write
        (DependencyUnavailableError); one it does not find, or refuses to
        serve, is an error on the field. Either way the value is never
        accepted unchecked, as for the document's own template.
        """
        try:
            sub = await get_template_store_client().get_template_resolved(
                template_ref, version=version
            )
        except TemplateStoreError as e:
            if e.unavailable:
                raise dependency_unavailable(
                    "template-store", f"template {template_ref!r} for {field_path!r}", e,
                ) from e
            result.add_error(
                code="template_unresolvable",
                message=f"Template '{template_ref}' for field '{field_path}' cannot be served: {e!s}",
                field=field_path,
            )
            return None
        if sub is None:
            result.add_error(
                code="template_not_found",
                message=f"Template '{template_ref}' for field '{field_path}' not found",
                field=field_path,
            )
        return sub

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
        # fullmatch: re.match's "$" also matches before a trailing newline.
        if not re.fullmatch(email_pattern, value):
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
            # _TIME_UNITS is a system terminology living in the wip
            # namespace — resolve it there explicitly; the validating
            # document's own namespace would not find it
            validation_result = await client.validate_value(
                self.TIME_UNITS_TERMINOLOGY,
                unit_value,
                namespace="wip"
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
                f"Could not validate duration unit for '{field_path}': {e!s}"
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
        result: ValidationResult,
        namespace: str | None = None
    ):
        """
        Stage 4: Term validation.

        Collects all term fields and validates them in batch via Def-Store.
        Also collects term_references (resolved term IDs) for storage.
        The document's namespace is the resolution context for value-form
        terminology refs.
        """
        # Collect all term values to validate
        term_validations = await self._collect_term_values(data, template.get("fields", []), "")

        if not term_validations:
            return

        # Batch validate via Def-Store
        try:
            client = get_def_store_client()
            validation_results = await client.validate_values_bulk([
                {"terminology_ref": tv["terminology_ref"], "value": tv["value"]}
                for tv in term_validations
            ], namespace=namespace)

            # Process results and collect term references
            for i, validation_result in enumerate(validation_results):
                term_val = term_validations[i]
                field_path = term_val["field_path"]

                matched = validation_result.get("matched_term") or {}
                if await self._term_refused(validation_result, matched.get("term_id")):
                    # The terminology lies in a closed namespace the writer
                    # cannot read: every value is invalid, without a
                    # suggestion, whether the term exists or not.
                    validation_result = {"valid": False, "suggestion": None}

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
                    # Ambiguous name: several terms bear the submitted
                    # string, and the match is merely the first bearer in
                    # storage order. On an IDENTITY field that arbitrary
                    # pick would feed the identity hash — fail closed, the
                    # same rule as an unresolvable identity referent. On a
                    # non-identity field, keep the pick but say so loudly.
                    if validation_result.get("ambiguous"):
                        candidates = validation_result.get("candidates", [])
                        bearer_desc = ", ".join(
                            f"'{c.get('value')}' ({c.get('term_id')})"
                            for c in candidates
                        )
                        if _feeds_identity(field_path, template):
                            result.add_error(
                                code="ambiguous_term",
                                message=(
                                    f"Value '{term_val['value']}' is ambiguous in "
                                    f"terminology '{term_val['terminology_ref']}': "
                                    f"{len(candidates)} terms bear this name "
                                    f"({bearer_desc}). An identity field cannot "
                                    "resolve by storage order — submit the term's "
                                    "canonical value or an unambiguous name."
                                ),
                                field=field_path,
                                details={"candidates": candidates},
                            )
                            continue
                        result.add_warning(
                            f"Value '{term_val['value']}' in field '{field_path}' "
                            f"is ambiguous in terminology "
                            f"'{term_val['terminology_ref']}': {len(candidates)} "
                            f"terms bear this name ({bearer_desc}); resolved to "
                            "the first in storage order."
                        )

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
            result.add_warning(f"Could not validate terminology values: {e!s}")

    async def _typed_leaves(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str = ""
    ) -> list[tuple[str, dict[str, Any], Any]]:
        """Every present field of a document with its definition, nested ones included.

        Returns (path, field definition, value) for each field of `fields`
        that `data` carries with a non-null value, then descends into object
        fields with a template_ref and into the items of object arrays with
        an array_template_ref, so the term, reference and file stages see
        nested fields exactly as they see top-level ones. Nested templates
        are read at their pinned versions, the same ones Stage 3 validated
        against, from the client's permanent version cache. A nested
        template that cannot be fetched is skipped here; Stage 3 has already
        reported it. Paths follow Stage 3's: `field.sub`, `field[i].sub`.
        """
        leaves: list[tuple[str, dict[str, Any], Any]] = []

        for field in fields:
            field_name = field["name"]
            full_path = f"{prefix}{field_name}" if prefix else field_name

            if field_name not in data:
                continue

            value = data[field_name]
            if value is None:
                continue

            leaves.append((full_path, field, value))

            field_type = field.get("type", "string")
            if field_type == "object" and field.get("template_ref") and isinstance(value, dict):
                nested = await self._nested_template_fields(
                    field["template_ref"], field.get("template_ref_version")
                )
                if nested:
                    leaves.extend(await self._typed_leaves(value, nested, f"{full_path}."))
            elif (
                field_type == "array"
                and field.get("array_item_type") == "object"
                and field.get("array_template_ref")
                and isinstance(value, list)
            ):
                nested = await self._nested_template_fields(
                    field["array_template_ref"], field.get("array_template_ref_version")
                )
                if nested:
                    for i, item in enumerate(value):
                        if isinstance(item, dict):
                            leaves.extend(
                                await self._typed_leaves(item, nested, f"{full_path}[{i}].")
                            )

        return leaves

    async def _candidate_referent_paths(
        self, template: dict[str, Any]
    ) -> list[str] | None:
        """The identity paths of an inline candidate whose value is a referent.

        The template store's rule for a stored version, applied to a
        definition that was never stored: a path names a referent when its
        leaf is a term or reference field, following each object field into
        its nested template at the version the candidate's documents are
        validated against. None when a nested template on an identity path
        cannot be read — the identity stage then refuses the path rather than
        guess.
        """
        paths: list[str] = []
        for path in template.get("identity_fields") or []:
            fields: list[dict[str, Any]] | None = template.get("fields") or []
            segments = path.split(".")
            referent = False
            for depth, name in enumerate(segments):
                field = {f.get("name"): f for f in fields or []}.get(name)
                if field is None:
                    break
                if depth == len(segments) - 1:
                    referent = is_referent_field(field)
                    break
                if field.get("type") != "object" or not field.get("template_ref"):
                    break
                fields = await self._nested_template_fields(
                    field["template_ref"], field.get("template_ref_version")
                )
                if fields is None:
                    return None
            if referent:
                paths.append(path)
        return paths

    async def _nested_template_fields(
        self, template_ref: str, version: int | None
    ) -> list[dict[str, Any]] | None:
        """The fields of a nested template, or None when it cannot be fetched."""
        try:
            nested = await get_template_store_client().get_template_resolved(
                template_ref, version=version
            )
        except TemplateStoreError:
            return None
        return (nested or {}).get("fields") or None

    async def prefetch_term_values(
        self,
        documents: list[tuple[str, int | None, dict[str, Any]]],
        namespace: str | None,
    ) -> None:
        """Resolve a batch's term values before its documents are validated.

        ``documents`` holds (template_id, template_version, data) per
        document. Their term values are collected as stage 4 collects them,
        and the distinct (terminology, value) pairs are resolved with one
        def-store request per TERM_PREFETCH_CHUNK. Inside a
        term_resolution_batch the answers become the batch's shared
        resolutions, so each document's stage 4 finds its values there and
        asks def-store nothing. def-store still decides every value, at
        write time, by the same rule.

        Only a speed-up: a pair it misses is resolved by its document as
        before, and if it fails nothing is kept (a failed resolution is not
        shared) and each document resolves, and fails, on its own.
        """
        client = get_template_store_client()
        pairs: dict[tuple[str, str], None] = {}
        try:
            for template_id, version, data in documents:
                template = await client.get_template_resolved(template_id, version=version)
                if not template or not isinstance(data, dict):
                    continue
                for tv in await self._collect_term_values(data, template.get("fields", []), ""):
                    if isinstance(tv["value"], str):
                        pairs[(tv["terminology_ref"], tv["value"])] = None
            keys = list(pairs)
            for i in range(0, len(keys), TERM_PREFETCH_CHUNK):
                await get_def_store_client().validate_values_bulk(
                    [{"terminology_ref": ref, "value": value}
                     for ref, value in keys[i:i + TERM_PREFETCH_CHUNK]],
                    namespace=namespace,
                )
        except Exception as e:  # a skipped prefetch only costs speed
            logger.warning("Term prefetch skipped; documents resolve their own values: %s", e)

    async def _collect_term_values(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str
    ) -> list[dict[str, str]]:
        """Collect every term value for validation, nested templates included."""
        term_values = []

        for full_path, field, value in await self._typed_leaves(data, fields, prefix):
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
        result: ValidationResult,
        namespace: str,
        doc_ref_cache: dict | None = None
    ):
        """
        Validate and resolve reference fields.

        Handles document references by looking up the target document
        by document_id, identity_hash, or business key.
        """

        # Collect all reference values to validate
        ref_validations = await self._collect_reference_values(data, template.get("fields", []), "")

        # Cache for expanded target_templates (to avoid repeated lookups)
        expanded_templates_cache: dict[str, list[str]] = {}

        # An empty constraint list means "unconstrained" for a plain
        # reference field — a feature. For an edge type's endpoints it means
        # the projection from the declared endpoint lists was LOST (a reader
        # that did not derive it, a stripped row read raw), and accepting the
        # write would silently drop endpoint enforcement entirely. That is
        # the guardrail-that-works-sometimes shape, so it refuses instead.
        is_edge_type = template.get("usage") == "relationship"

        for ref_val in ref_validations:
            field_path = ref_val["field_path"]
            reference_type = ref_val["reference_type"]
            value = ref_val["value"]
            target_templates = ref_val.get("target_templates", [])
            include_subtypes = ref_val.get("include_subtypes", False)
            target_terminologies = ref_val.get("target_terminologies", [])
            version_strategy = ref_val.get("version_strategy", "latest")

            if (
                is_edge_type
                and field_path in ("source_ref", "target_ref")
                and not target_templates
            ):
                result.add_error(
                    code="missing_endpoint_constraint",
                    message=(
                        f"Edge type '{template.get('value')}' serves no "
                        f"endpoint constraint for '{field_path}' — the "
                        "declared endpoint lists did not reach validation, "
                        "and an unconstrained edge endpoint would silently "
                        "drop enforcement. Re-serve the template through "
                        "the template-store API (the constraint is derived "
                        "from the declaration there)."
                    ),
                    field=field_path,
                )
                continue

            # Expand target_templates with descendants when include_subtypes is set
            if include_subtypes and target_templates and reference_type == "document":
                target_templates = await self._expand_target_templates(
                    target_templates, expanded_templates_cache,
                    version_strategy=version_strategy
                )

            try:
                if reference_type == "document":
                    # Resolve document reference
                    resolved = await self._resolve_document_reference(
                        value, target_templates, result, field_path,
                        version_strategy=version_strategy,
                        namespace=namespace,
                        doc_ref_cache=doc_ref_cache
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
                            value, target_terminologies, result, field_path,
                            namespace=namespace
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
                                "terminology_ref": resolved.get("terminology_value"),
                                "matched_via": resolved.get("matched_via"),
                            })

                elif reference_type == "terminology":
                    # Resolve terminology reference
                    resolved = await self._resolve_terminology_reference(
                        value, result, field_path, namespace
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

            except DependencyUnavailableError:
                # The Registry, def-store or template store could not answer:
                # refuse the write rather than let the reference through
                # unchecked.
                raise
            except Exception as e:
                # Any other failure leaves the reference unchecked, and an
                # unchecked reference is never written: an error, not a
                # warning (the isolation gate sees only resolved references).
                logger.exception("Reference check failed for field %r", field_path)
                result.add_error(
                    code="reference_check_failed",
                    message=f"Could not check the reference in field '{field_path}': {e!s}",
                    field=field_path,
                )

    async def _validate_files(
        self,
        data: dict[str, Any],
        template: dict[str, Any],
        result: ValidationResult,
        *,
        template_id: str | None,
    ):
        """
        Stage 5b: File validation.

        Validates all file fields by checking:
        - File exists
        - File meets field constraints (allowed_types, max_size_mb)
        - The file's allowed_templates, if set, names the document's template
        - Builds file_references array for storage
        """
        from .file_service import FileServiceError, get_file_service
        from .file_storage_client import is_file_storage_enabled

        # Collect all file values to validate
        file_validations = await self._collect_file_values(data, template.get("fields", []), "")

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

            allowed_types = file_config.get("allowed_types") or ["*/*"]
            max_size_mb = file_config.get("max_size_mb") or 10.0

            try:
                is_valid, error_msg, file_ref = await file_service.validate_file_for_field(
                    file_id=file_id,
                    allowed_types=allowed_types,
                    max_size_mb=max_size_mb,
                    template_id=template_id,
                    is_refused=gate_refuses if current_gate() is not None else None,
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
                    message=f"Failed to validate file for field '{field_path}': {e!s}",
                    field=field_path
                )

    async def _collect_file_values(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str
    ) -> list[dict[str, Any]]:
        """Collect every file value for validation, nested templates included."""
        file_values = []

        for full_path, field, value in await self._typed_leaves(data, fields, prefix):
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
        cache: dict[str, list[str]],
        version_strategy: str = "latest"
    ) -> list[str]:
        """
        Expand target_templates to include descendant templates.

        When include_subtypes is set on a reference field, this method
        fetches all templates that inherit (directly or indirectly) from
        each target template and adds them to the allowed list.

        For pinned strategy, expands with template_ids.
        For latest strategy, expands with template values.

        Uses a cache to avoid repeated lookups for the same template.
        """
        expanded = set(target_templates)
        client = get_template_store_client()

        for tpl_id in target_templates:
            if tpl_id in cache:
                expanded.update(cache[tpl_id])
                continue

            try:
                template = await client.get_template(template_id=tpl_id)
                if not template:
                    cache[tpl_id] = []
                    continue

                descendants = await client.get_template_descendants(cast(str, template.get("template_id")))
                desc_refs: list[str]
                if version_strategy == "pinned":
                    desc_refs = [cast(str, d.get("template_id")) for d in descendants if d.get("template_id")]
                else:
                    desc_refs = [cast(str, d.get("value")) for d in descendants if d.get("value")]
                cache[tpl_id] = desc_refs
                expanded.update(desc_refs)
            except TemplateStoreError:
                cache[tpl_id] = []

        return list(expanded)

    async def _resolve_document_reference(
        self,
        value: Any,
        target_templates: list[str],
        result: ValidationResult,
        field_path: str,
        namespace: str,
        version_strategy: str = "latest",
        doc_ref_cache: dict | None = None
    ) -> dict[str, Any] | None:
        """Resolve a document reference by ID, hash, or business key.

        When doc_ref_cache is provided (bulk operations), resolved documents
        are cached by lookup key to avoid repeated MongoDB queries for the
        same reference across documents in a batch.
        """
        # Build a cache key for this lookup. The field's target_templates
        # narrow a bare value's candidates, so they are part of what the
        # lookup means.
        cache_key = None
        if doc_ref_cache is not None and isinstance(value, str):
            cache_key = f"doc:{value}|{','.join(sorted(target_templates))}"
            if cache_key in doc_ref_cache:
                return await self._accept_referenced_document(
                    doc_ref_cache[cache_key], value, target_templates, result,
                    field_path, version_strategy, namespace,
                )

        if isinstance(value, str):
            doc, refused = await self._lookup_string_reference(
                value, target_templates, result, field_path, namespace,
            )
            if refused:
                return None
        elif isinstance(value, dict):
            # Composite business key: the referenced document's identity,
            # resolved through the Registry like every other identifier.
            doc, error = await self._resolve_business_key_reference(
                value, target_templates, namespace, doc_ref_cache,
            )
            if error is not None:
                result.add_error(field=field_path, **error)
                return None
        else:
            result.add_error(
                code="invalid_reference_value",
                message=f"Invalid reference value for field '{field_path}'",
                field=field_path
            )
            return None

        # Cache the lookup result (including None for not-found)
        if cache_key is not None and doc_ref_cache is not None:
            doc_ref_cache[cache_key] = doc

        return await self._accept_referenced_document(
            doc, value, target_templates, result, field_path, version_strategy, namespace,
        )

    async def _accept_referenced_document(
        self,
        doc: Any,
        value: Any,
        target_templates: list[str],
        result: ValidationResult,
        field_path: str,
        version_strategy: str,
        namespace: str,
    ) -> dict[str, Any] | None:
        """Pass a looked-up (or cached) referent through the inbound gate and
        the template check; reference_not_found when there is none or the
        gate refuses it.

        The gate is asked on every use, cached referents included: it is per
        write, so a target refused in one item may be carried in another.
        """
        if doc is not None and await gate_refuses(doc.namespace, doc.document_id):
            doc = None
        if doc is None:
            result.add_error(
                code="reference_not_found",
                message=f"Referenced document not found for field '{field_path}'",
                field=field_path,
                details={"value": str(value), "target_templates": target_templates}
            )
            return None
        return await self._verify_ref_template_and_build_result(
            doc, target_templates, result, field_path, version_strategy,
            namespace=namespace,
        )

    async def _lookup_string_reference(
        self,
        value: str,
        target_templates: list[str],
        result: ValidationResult,
        field_path: str,
        namespace: str,
    ) -> tuple[Any, bool]:
        """Look a string reference up: (the document or None, refused).

        A UUID7 is a document_id, read active first and then in any status
        (for pinned references). Anything else resolves through the Registry;
        refused is True when several documents carry the value (the error is
        on ``result``).
        """
        from ..models.document import Document, DocumentStatus

        if self._is_uuid7(value):
            doc = await Document.find_one({"document_id": value, "status": DocumentStatus.ACTIVE})
            return doc or await Document.find_one({"document_id": value}), False

        # Registry lookup — resolve any identifier (synonym, composite key value, etc.)
        # Qualified form: explicit NS:VALUE resolves in the named
        # namespace (same syntax the resolve layer uses for schema
        # references); bare values never cross. The resolved reference
        # still passes the namespace-isolation check downstream, so a
        # qualified ref into an undeclared namespace surfaces as
        # reference_violation, not not_found.
        #
        # hash:<identity_hash> names a document by its identity hash,
        # in the caller's namespace. The Registry holds the bare digest
        # among a document's key values, so the prefix is removed
        # before the lookup; it is never read as a namespace. The same
        # digest under two templates (one identity under two schemas)
        # is narrowed by target_templates like any bare value, and is
        # refused as ambiguous when that leaves several.
        if value.startswith("hash:"):
            ref_ns, ref_value = None, value[len("hash:"):]
        else:
            ref_ns, ref_value = split_qualified_value(value)
        doc, candidates = await self._resolve_bare_document_reference(
            ref_value, ref_ns or namespace, target_templates,
        )
        if candidates:
            # Several documents carry the value and the field's
            # target_templates do not narrow them to one: refused, never
            # resolved to an arbitrary one. Not cached — the cache's None
            # means "not found".
            result.add_error(
                code="ambiguous_reference",
                message=(
                    f"Reference '{value}' in field '{field_path}' matches "
                    "several documents; use a document_id or a "
                    "qualified value"
                ),
                field=field_path,
                details={"value": str(value), "candidates": candidates},
            )
            return None, True

        if not doc:
            # CASE-435: string references resolve through the Registry only.
            # The service-local business-key fallback (a direct Mongo query on
            # data.<identity_field>) was removed: it could resolve a value the
            # Registry does not know, a guardrail that works only sometimes.
            # It was unreachable for correctly registered documents, whose
            # identity-values synonym puts the value into search_values. The
            # miss is logged so an under-registered target (a missing
            # Registry synonym) surfaces instead of being papered over.
            logger.warning(
                "string reference %r (field %r, ns %r) did not resolve via the "
                "Registry and is treated as unresolved; if it should resolve, "
                "the target is missing its Registry synonym (registration gap)",
                value, field_path, namespace,
            )
        return doc, False

    async def _verify_ref_template_and_build_result(
        self,
        doc: Any,
        target_templates: list[str],
        result: "ValidationResult",
        field_path: str,
        version_strategy: str = "latest",
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        """Verify referenced document's template and build the resolved result."""
        from ..models.document import DocumentStatus

        # Always look up the template so we can include template_value in
        # the resolved dict — Phase-2 relationship validation needs it.
        # A template store that cannot answer refuses the write: the endpoint
        # check below must never be skipped for an outage. A template it does
        # not find leaves only the display value unknown; the check runs on
        # the document's template_id either way.
        doc_template: dict[str, Any] | None = None
        try:
            client = get_template_store_client()
            doc_template = await client.get_template(doc.template_id)
        except TemplateStoreError as e:
            if e.unavailable:
                raise dependency_unavailable(
                    "template-store", f"template of the document referenced by {field_path!r}", e,
                ) from e

        # Verify template matches target_templates
        if target_templates and not await self._endpoint_allows(
            doc, doc_template, target_templates, result, field_path, version_strategy, namespace,
        ):
            return None

        # Check if document is inactive (warning only)
        if doc.status != DocumentStatus.ACTIVE:
            result.add_warning(f"Referenced document for field '{field_path}' is {doc.status.value}")

        return {
            "document_id": doc.document_id,
            "identity_hash": doc.identity_hash,
            "template_id": doc.template_id,
            "template_value": doc_template.get("value") if doc_template else None,
            "version": doc.version,
            "namespace": doc.namespace,
            "status": doc.status.value if hasattr(doc.status, "value") else doc.status,
        }

    async def _endpoint_allows(
        self,
        doc: Any,
        doc_template: dict[str, Any] | None,
        target_templates: list[str],
        result: "ValidationResult",
        field_path: str,
        version_strategy: str,
        namespace: str | None,
    ) -> bool:
        """Is the referenced document's template one of the field's
        target_templates? False after adding the error that says why not."""
        if version_strategy == "pinned":
            # Exact template_id match
            if doc.template_id not in target_templates:
                result.add_error(
                    code="invalid_reference_template",
                    message=f"Referenced document uses template '{doc.template_id}', expected one of {target_templates} (pinned)",
                    field=field_path
                )
                return False
        else:
            # latest: resolve every allowed endpoint to its canonical
            # template_id via the Registry, then match the referenced
            # doc's template_id by canonical id. value-form, UUID, and
            # any synonym resolve identically — References-Must-Resolve
            # (CASE-525; docs/design/synonym-resolution-gaps.md). The old
            # code called get_template(template_id=entry) namespace-free
            # and matched by .value, which 404s on a namespace-scoped
            # value-form endpoint → empty family → every edge rejected
            # (the add_edge_type_endpoints widen stores endpoints
            # value-form). Resolving here closes the gap regardless of the
            # stored form, instead of forcing the widen to avoid synonyms.
            try:
                allowed_ids = await self._allowed_template_ids(target_templates, namespace)
            except AmbiguousIdentifierError as e:
                result.add_error(
                    code="ambiguous_reference",
                    message=(
                        f"Target template '{e.value}' of field '{field_path}' names "
                        "several templates; declare it by id or as ns:VALUE"
                    ),
                    field=field_path,
                    details={"value": e.value, "candidates": e.candidates},
                )
                return False
            if doc.template_id not in allowed_ids:
                expected = (doc_template or {}).get("value") or doc.template_id
                # Report the DECLARED endpoints (human-readable, the form
                # the edge type declares) rather than resolved UUIDs.
                result.add_error(
                    code="invalid_reference_template",
                    message=f"Referenced document uses template '{expected}', expected family of {list(target_templates)}",
                    field=field_path
                )
                return False
        return True

    async def _resolve_business_key_reference(
        self,
        value: dict[str, Any],
        target_templates: list[str],
        namespace: str,
        doc_ref_cache: dict | None,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        """Resolve a dict reference to the one document whose identity it is.

        The dict must name exactly the identity fields of a target template.
        For each such template its values are resolved and hashed by the same
        stages a document write runs (term and reference identity fields feed
        their referent's canonical ID, so an alias or a renamed term finds the
        document), and the document is looked up by its Registry primary key
        (namespace, template_id, identity_hash) in the referencing document's
        namespace. Returns (document, None), (None, None) when nothing
        matches, or (None, error) for a key that names no target template's
        identity or that matches documents under several templates.
        """
        from ..models.document import Document, DocumentStatus

        if not target_templates:
            return None, {
                "code": "invalid_reference_value",
                "message": (
                    "A business-key reference needs the field's target_templates: "
                    "they say whose identity fields the key names"
                ),
                "details": {"value": value},
            }

        try:
            identities, matches = await self._match_business_key(
                value, target_templates, namespace, doc_ref_cache,
            )
        except TemplateStoreError as e:
            # The key's meaning depends on the target templates' identity
            # fields. A template store that cannot answer refuses the write
            # (an outage, never a miss); one that refuses to serve a target
            # template fails the reference.
            if e.unavailable:
                raise dependency_unavailable(
                    "template-store", "a target template of the business key", e,
                ) from e
            return None, {
                "code": "template_error",
                "message": f"Could not read a target template of the business key: {e!s}",
                "details": {"value": value},
            }
        error = _business_key_error(value, identities, matches)
        if error is not None or not matches:
            return None, error

        document_id = matches[0][0]
        doc = await Document.find_one(
            {"document_id": document_id, "status": DocumentStatus.ACTIVE}
        )
        return doc or await Document.find_one({"document_id": document_id}), None

    async def _match_business_key(
        self,
        value: dict[str, Any],
        target_templates: list[str],
        namespace: str,
        doc_ref_cache: dict | None,
    ) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
        """Try the key against each target template the namespace may use:
        (each template's identity fields by template value, the
        (document_id, template value) of every document it names).

        A template the template store does not find is skipped; one it cannot
        answer raises TemplateStoreError (the caller fails the reference with
        it, never reading it as "no such template"); one whose identity fields
        the key does not name exactly is recorded but not searched.
        """
        from .registry_client import get_registry_client

        client = get_template_store_client()
        registry = get_registry_client()
        identities: dict[str, list[str]] = {}
        matches: list[tuple[str, str]] = []
        for template_id in sorted(await self._allowed_template_ids(target_templates, namespace)):
            try:
                template = await client.get_template(template_id)
            except TemplateStoreError as e:
                raise TemplateStoreError(
                    f"target template '{template_id}': {e}", code=e.code, unavailable=e.unavailable,
                ) from e
            if not template:
                continue
            template_value = template.get("value") or template_id
            identity_fields = list(template.get("identity_fields") or [])
            identities[template_value] = identity_fields
            if not identity_fields or set(value) != set(identity_fields):
                continue
            document_id = await self._business_key_document_id(
                value, template_id, template, identity_fields, namespace,
                doc_ref_cache, registry,
            )
            if document_id:
                matches.append((document_id, template_value))
        return identities, matches

    async def _business_key_document_id(
        self,
        value: dict[str, Any],
        template_id: str,
        template: dict[str, Any],
        identity_fields: list[str],
        namespace: str,
        doc_ref_cache: dict | None,
        registry: Any,
    ) -> str | None:
        """The document under ``template_id`` whose identity the key is, or None.

        The key's values are resolved and hashed by the stages a document
        write runs, over the template's identity fields only (term and
        reference identity fields feed their referent's canonical ID); a
        value that does not resolve names no document here. The document is
        looked up by its Registry primary key (namespace, template_id,
        identity_hash).
        """
        from wip_auth.composite_key import compute_composite_key_hash

        identity_template = {
            **template,
            "fields": [f for f in template.get("fields", []) if f.get("name") in identity_fields],
        }
        scratch = ValidationResult()
        await self._validate_terms(value, identity_template, scratch, namespace=namespace)
        if scratch.valid:
            await self._validate_references(
                value, identity_template, scratch, namespace=namespace,
                doc_ref_cache=doc_ref_cache,
            )
        if scratch.valid:
            self._compute_identity(value, identity_template, scratch)
        if not scratch.valid or not scratch.identity_values:
            return None
        # A Registry that cannot answer raises RegistryUnavailableError, which
        # fails the write: an outage is never read as "no such document".
        document_id = await registry.lookup_document_by_primary_key(
            namespace, template_id, compute_composite_key_hash(scratch.identity_values),
        )
        return cast(str | None, document_id)

    async def _allowed_template_ids(
        self, target_templates: list[str], namespace: str | None,
    ) -> set[str]:
        """The canonical template_ids a field's target_templates name.

        Every entry resolves through the Registry, so value-form, UUID and
        any synonym count identically (References-Must-Resolve). An entry
        that does not resolve but is UUID-shaped is already canonical:
        honoured directly, never dropped. A Registry that does not answer
        raises RegistryUnavailableError, which RegistryError does not catch;
        an entry naming several templates raises AmbiguousIdentifierError,
        never dropped from the family (that would refuse a legal reference).
        """
        from .registry_client import RegistryError, get_registry_client

        registry = get_registry_client()
        allowed: set[str] = set()
        for entry in target_templates:
            try:
                rid = await registry.resolve_identifier(namespace, "templates", entry)
            except AmbiguousIdentifierError:
                raise
            except RegistryError:
                rid = None
            if rid:
                allowed.add(rid)
            elif self._is_uuid7(entry):
                allowed.add(entry)
        return allowed

    async def _resolve_bare_document_reference(
        self,
        value: str,
        namespace: str,
        target_templates: list[str],
    ) -> tuple[Any | None, list[dict[str, Any]]]:
        """Resolve a bare string reference to the one document it names.

        Returns (document, []) for a single match, (None, []) for none, and
        (None, candidates) when several documents carry the value and the
        field's target_templates leave more than one — or when the Registry
        reports more matches than it listed, so narrowing cannot be sure.
        A single candidate from another template is returned as the match;
        the template check that follows reports it.
        """
        from ..models.document import Document, DocumentStatus
        from .registry_client import RegistryError, get_registry_client

        try:
            entries, total = await get_registry_client().resolve_candidates(
                namespace, "documents", value,
            )
        except RegistryError:
            return None, []
        if not entries:
            return None, []

        ids = [e["entry_id"] for e in entries]
        rows = await Document.find({"document_id": {"$in": ids}}).to_list()
        latest: dict[str, Any] = {}
        for row in rows:
            held = latest.get(row.document_id)
            if held is None or (
                (row.status == DocumentStatus.ACTIVE, row.version)
                > (held.status == DocumentStatus.ACTIVE, held.version)
            ):
                latest[row.document_id] = row
        docs = [latest[i] for i in ids if i in latest]
        if total == 1 and len(docs) == 1:
            return docs[0], []

        survivors = docs
        if target_templates:
            allowed = await self._allowed_template_ids(target_templates, namespace)
            survivors = [d for d in docs if d.template_id in allowed]
        if len(survivors) == 1 and total == len(entries):
            return survivors[0], []

        by_id = {e["entry_id"]: e for e in entries}
        return None, [
            {
                "document_id": d.document_id,
                "template_value": d.template_value,
                "matched_composite_key": by_id[d.document_id].get("matched_composite_key"),
            }
            for d in (survivors or docs)
        ]

    async def _resolve_term_reference(
        self,
        value: str,
        target_terminologies: list[str],
        result: ValidationResult,
        field_path: str,
        namespace: str | None = None
    ) -> dict[str, Any] | None:
        """Resolve a term reference.

        The document's namespace is the resolution context for value-form
        terminology refs in target_terminologies.
        """
        client = get_def_store_client()

        # Try each terminology until we find a match
        for terminology_ref in target_terminologies:
            try:
                validation_result = await client.validate_value(
                    terminology_ref, value, namespace
                )
                if validation_result.get("valid"):
                    matched_term = validation_result.get("matched_term", {})
                    if await self._term_refused(validation_result, matched_term.get("term_id")):
                        continue
                    return {
                        "term_id": matched_term.get("term_id"),
                        "terminology_value": terminology_ref,
                        "matched_via": validation_result.get("matched_via")
                    }
            except DefStoreError as e:
                # An outage refuses the write; any other refusal of this
                # terminology is a miss here, and the next one is tried.
                if e.unavailable:
                    raise dependency_unavailable(
                        "def-store", f"term {value!r} for {field_path!r}", e,
                    ) from e
                continue

        result.add_error(
            code="invalid_term_reference",
            message=f"Term '{value}' not found in terminologies {target_terminologies}",
            field=field_path
        )
        return None

    async def _term_refused(self, validation_result: dict[str, Any], term_id: str | None) -> bool:
        """Is a term value's terminology in a closed namespace the writer
        cannot read? Decided by terminology, so an absent value and a present
        one are refused alike."""
        gate = current_gate()
        terminology_id = validation_result.get("terminology_id")
        if gate is None or not terminology_id:
            return False
        return await gate.refuses(await gate.namespace_of(terminology_id), term_id)

    async def _resolve_terminology_reference(
        self,
        value: str,
        result: ValidationResult,
        field_path: str,
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a terminology reference: a canonical id, a bare value in
        the document's own namespace, or 'ns:VALUE' in the named one. Asked
        of def-store on every write, so a terminology retired a moment ago
        is refused now."""
        client = get_def_store_client()

        try:
            terminology = await client.get_terminology(value, namespace)
            if (
                terminology
                and terminology.get("status") == "active"
                and not await gate_refuses(
                    terminology.get("namespace"), terminology.get("terminology_id"),
                )
            ):
                if terminology.get("namespace") and terminology.get("terminology_id"):
                    result.referent_namespaces[str(terminology["terminology_id"])] = (
                        str(terminology["namespace"])
                    )
                return {
                    "terminology_id": terminology.get("terminology_id"),
                    "terminology_value": terminology.get("value"),
                }
        except DefStoreError as e:
            if e.unavailable:
                raise dependency_unavailable(
                    "def-store", f"terminology {value!r} for {field_path!r}", e,
                ) from e

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
    ) -> dict[str, Any] | None:
        """Resolve a template reference."""
        client = get_template_store_client()

        try:
            template = await client.get_template(template_id=value)
            if not template:
                template = await client.get_template(template_value=value)

            if template and not await gate_refuses(
                template.get("namespace"), template.get("template_id"),
            ):
                if template.get("namespace") and template.get("template_id"):
                    result.referent_namespaces[str(template["template_id"])] = (
                        str(template["namespace"])
                    )
                return {
                    "template_id": template.get("template_id"),
                    "template_value": template.get("value"),
                    "version": template.get("version")
                }
        except TemplateStoreError as e:
            if e.unavailable:
                raise dependency_unavailable(
                    "template-store", f"template {value!r} for {field_path!r}", e,
                ) from e

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
        return bool(re.fullmatch(uuid_pattern, value.lower()))

    async def _collect_reference_values(
        self,
        data: dict[str, Any],
        fields: list[dict[str, Any]],
        prefix: str
    ) -> list[dict[str, Any]]:
        """Collect every reference value for resolution, nested templates included."""
        ref_values = []

        for full_path, field, value in await self._typed_leaves(data, fields, prefix):
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

            elif (
                field_type == "array"
                and field.get("array_item_type") == "reference"
                and isinstance(value, list)
            ):
                # Array of references
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
        rules = template.get("rules") or []
        evaluators = {
            "conditional_required": self._evaluate_conditional_required,
            "conditional_value": self._evaluate_conditional_value,
            "mutual_exclusion": self._evaluate_mutual_exclusion,
            "dependency": self._evaluate_dependency,
            "pattern": self._evaluate_pattern,
            "range": self._evaluate_range,
        }

        # A rule that cannot be evaluated is refused, never skipped: a
        # constraint the template declares and the platform quietly ignores
        # is a guardrail that holds only sometimes. Template-store refuses
        # these shapes at write time; a template stored before that check
        # can still carry one, and its writes fail here, per document,
        # instead of crashing the request.
        for index, rule in enumerate(rules):
            rule_type = rule.get("type")
            evaluator = evaluators.get(rule_type)
            if evaluator is None:
                self._refuse_rule(result, index, rule, f"unknown rule type {rule_type!r}")
                continue
            try:
                evaluator(data, rule, result)
            except (TypeError, AttributeError, KeyError, re.error) as e:
                self._refuse_rule(result, index, rule, f"cannot be evaluated: {e}")

    @staticmethod
    def _refuse_rule(
        result: ValidationResult, index: int, rule: dict[str, Any], reason: str
    ) -> None:
        result.add_error(
            code="invalid_rule",
            message=(
                f"Template rule {index} ({rule.get('type')}) {reason}; "
                "fix the template's rules"
            ),
            details={"rule_index": index, "rule_type": rule.get("type")},
        )

    def _evaluate_pattern(
        self,
        data: dict[str, Any],
        rule: dict[str, Any],
        result: ValidationResult
    ):
        """Evaluate a pattern rule: when its conditions hold (none = always),
        a present target value must be a string the pattern matches whole,
        the same reading as a field's validation.pattern."""
        if not self._check_conditions(data, rule.get("conditions") or []):
            return
        target_field = rule["target_field"]
        target_value = IdentityService._get_nested_value(data, target_field)
        if target_value is None:
            return
        pattern = rule["pattern"]
        if not isinstance(target_value, str) or not re.fullmatch(pattern, target_value):
            result.add_error(
                code="pattern",
                message=rule.get("error_message") or f"Field '{target_field}' does not match required pattern",
                field=target_field,
                details={"pattern": pattern},
            )

    @staticmethod
    def _within(value: Any, minimum: float | None, maximum: float | None) -> bool:
        """A number (not a bool) inside the inclusive bounds; None = unbounded."""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)

    def _evaluate_range(
        self,
        data: dict[str, Any],
        rule: dict[str, Any],
        result: ValidationResult
    ):
        """Evaluate a range rule: when its conditions hold (none = always),
        a present target value must be a number within minimum..maximum,
        either bound optional, both inclusive."""
        if not self._check_conditions(data, rule.get("conditions") or []):
            return
        target_field = rule["target_field"]
        target_value = IdentityService._get_nested_value(data, target_field)
        if target_value is None:
            return
        minimum, maximum = rule.get("minimum"), rule.get("maximum")
        if not self._within(target_value, minimum, maximum):
            result.add_error(
                code="range",
                message=rule.get("error_message") or (
                    f"Field '{target_field}' must be a number between "
                    f"{minimum if minimum is not None else '-∞'} and "
                    f"{maximum if maximum is not None else '∞'}"
                ),
                field=target_field,
                details={"minimum": minimum, "maximum": maximum},
            )

    @staticmethod
    def _coerce_for_comparison(actual: Any, expected: Any) -> tuple[Any, Any]:
        """Coerce values to compatible types for comparison.

        Handles mismatches between numeric strings and numbers that occur
        when rule conditions store values as strings but document data is numeric.
        """
        if actual is None or expected is None:
            return actual, expected
        # If one is numeric and the other is a string, try to coerce the string
        if isinstance(actual, (int, float)) and isinstance(expected, str):
            with contextlib.suppress(ValueError, TypeError):
                expected = type(actual)(expected)
        elif isinstance(expected, (int, float)) and isinstance(actual, str):
            with contextlib.suppress(ValueError, TypeError):
                actual = type(expected)(actual)
        return actual, expected

    def _check_condition(
        self,
        data: dict[str, Any],
        condition: dict[str, Any]
    ) -> bool:
        """Check if a condition is met."""
        field = condition.get("field")
        operator = condition.get("operator")
        expected_value = condition.get("value")

        actual_value = IdentityService._get_nested_value(data, cast(str, field))

        if operator == "equals":
            a, e = self._coerce_for_comparison(actual_value, expected_value)
            return cast(bool, a == e)
        elif operator == "not_equals":
            a, e = self._coerce_for_comparison(actual_value, expected_value)
            return cast(bool, a != e)
        elif operator in ("in", "not_in"):
            # A list of values; anything else cannot be evaluated (a string
            # would be read as a substring test). The rule is refused.
            if not isinstance(expected_value, list):
                raise TypeError(
                    f"operator {operator!r} needs a list value, got "
                    f"{type(expected_value).__name__}"
                )
            found = actual_value in expected_value
            return found if operator == "in" else not found
        elif operator == "exists":
            return actual_value is not None
        elif operator == "not_exists":
            return actual_value is None

        raise TypeError(f"unknown condition operator {operator!r}")

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
        conditions = rule.get("conditions") or []
        target_field = rule.get("target_field")
        # Template-store serves every optional rule key, unset ones as null,
        # so `required` is unset (null or absent) on most rules. Unset means
        # required: that is what the rule type exists for. Only an explicit
        # false turns it off.
        is_required = rule.get("required") is not False

        if not self._check_conditions(data, conditions):
            return

        target_value = IdentityService._get_nested_value(data, cast(str, target_field))

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
        conditions = rule.get("conditions") or []
        target_field = rule.get("target_field")
        allowed_values = rule.get("allowed_values") or []

        if not self._check_conditions(data, conditions):
            return

        target_value = IdentityService._get_nested_value(data, cast(str, target_field))

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
        target_fields = rule.get("target_fields") or []

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
        conditions = rule.get("conditions") or []
        target_field = rule.get("target_field")

        target_value = IdentityService._get_nested_value(data, cast(str, target_field))

        # If target field has value, check that dependency conditions are met
        if target_value is not None and not self._check_conditions(data, conditions):
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
        Stage 7: Identity extraction.

        Extracts identity field values from document data, then applies the
        resolved-referent feed rule: a term- or reference-typed identity
        field contributes its referent's canonical ID (resolved by stages
        4/5 into term_references / references), never the submitted
        spelling — so every alias of one referent yields one identity. The
        Registry computes the actual identity_hash from these values.

        Fail-closed: a referent identity field with no resolution (for
        example a Def-Store outage, which degrades non-identity term fields
        to a warning) rejects the write — hashing the spelling instead
        would fork the document on a later, resolvable write.
        """
        identity_fields = template.get("identity_fields", [])
        result.identity_fields = identity_fields

        if not identity_fields:
            result.add_warning(
                "Template has no identity fields. Document will not support upsert logic."
            )
            result.identity_values = {}
            return

        try:
            submitted = IdentityService.extract_identity_values(
                data, identity_fields
            )
        except ValueError as e:
            result.add_error(
                code="identity_error",
                message=str(e)
            )
            return

        result.submitted_identity_values = submitted
        try:
            referent_fields = referent_identity_fields(
                identity_fields, template.get("fields", []),
                template.get("referent_identity_paths"),
            )
        except ValueError as e:
            result.add_error(code="identity_resolution_failed", message=str(e))
            return
        result.referent_identity_fields = referent_fields
        if not referent_fields:
            result.identity_values = submitted
            return

        try:
            result.identity_values = substitute_resolved_referents(
                submitted,
                referent_fields,
                build_referent_resolutions(
                    result.term_references, result.references
                ),
            )
        except ValueError as e:
            result.add_error(
                code="identity_resolution_failed",
                message=(
                    f"{e!s}. Identity is resolved-referent based: the write "
                    "is rejected when an identity field's term or reference "
                    "cannot be resolved (resolution service unavailable or "
                    "resolution missing), because hashing the submitted "
                    "spelling would fork the document."
                ),
            )
