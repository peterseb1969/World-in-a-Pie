"""Validate a document against the archive's definitions, offline.

A port of document-store's `ValidationService`
(`components/document-store/src/document_store/services/validation_service.py`)
that reads templates and vocabularies from an archive instead of calling
template-store, def-store and the Registry. It must agree with the live
validator: a pre-upload gate that disagrees with the server is worse than none.
So it reproduces the live behaviour exactly, including its quirks (CASE-1009):
stages stop at the first one with errors, `pattern` anchors at the start only,
array items of type boolean, date, datetime and file are not checked, a datetime
accepts only a `Z` or `+00:00` offset, an earlier error suppresses later
semantic checks, and `ambiguous_term` fails only an exact identity-field path.
Error codes, field paths and messages are the live ones. Comparison tests
against the live validator hold the two together.

Stages run: structural, fields (types, constraints, semantic types), terms,
rules. Not run here: reference resolution and file checks (live stages 5 and
5b) — they need the target documents and file rows, which a later stage of the
archive check covers. A result is therefore valid for the stages it ran.

What the archive cannot answer is reported, never guessed: a terminology or
nested template the archive does not carry is listed in `unverifiable`, and the
value it governs is not judged.

Two live behaviours it cannot reproduce, both without effect on validity:
the closed-namespace refusal of term values depends on the caller's
permissions, not the archive; and when a name is ambiguous on a non-identity
field, live picks the first-created bearer, while archives are sorted by value,
so the bearer recorded in `term_references` can differ (both warn).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast
from urllib.parse import urlparse

from wip_archive.definitions import DefinitionIndex, UnresolvableTemplate, term_names
from wip_archive.identity import _get_nested_value

TIME_UNITS_TERMINOLOGY = "_TIME_UNITS"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_EMAIL = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
# The live TYPE_VALIDATORS and SEMANTIC_VALIDATORS keys; anything else is not checked.
_FIELD_TYPES = frozenset({
    "string", "number", "integer", "boolean", "date", "datetime", "term",
    "reference", "file", "object", "array",
})
_SEMANTIC_TYPES = frozenset({
    "email", "url", "latitude", "longitude", "percentage", "duration", "geo_point",
})
_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%fZ",
)


@dataclass
class ValidationOutcome:
    """The live `ValidationResult`'s shape, plus what could not be checked."""

    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    term_references: list[dict[str, Any]] = field(default_factory=list)
    # {"kind": "terminology" | "template", "ref": ..., "field": ...}
    unverifiable: list[dict[str, Any]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def add_error(
        self, code: str, message: str, field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.errors.append({"field": field, "code": code, "message": message, "details": details})

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_unverifiable(self, kind: str, ref: str, field_path: str) -> None:
        entry = {"kind": kind, "ref": ref, "field": field_path}
        if entry not in self.unverifiable:
            self.unverifiable.append(entry)


@dataclass(frozen=True)
class TermResolution:
    """One value resolved against one terminology — def-store's answer shape."""

    valid: bool
    matched_term: dict[str, Any] | None = None
    matched_via: str | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    suggestion: dict[str, Any] | None = None

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


class _Unknown:
    """The archive cannot say: the thing lies outside it."""


UNKNOWN = _Unknown()


def _term_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {"term_id": row.get("term_id"), "value": row.get("value")}


class OfflineValidator:
    """Validate documents against an archive's definitions."""

    def __init__(self, definitions: DefinitionIndex) -> None:
        self.definitions = definitions
        self._carried_namespaces = {
            row.get("namespace") for row in definitions.templates.values()
        } | {row.get("namespace") for row in definitions.terminologies.values()}

    # ------------------------------------------------------------------
    # The pipeline
    # ------------------------------------------------------------------

    def validate(
        self, template: dict[str, Any], data: Any, namespace: str
    ) -> ValidationOutcome:
        """Validate `data` against a resolved template dict.

        `template` is what the live validator receives from template-store:
        a resolved template (see `ResolvedTemplate.as_template_dict`).
        `namespace` is the document's, the context bare terminology values
        resolve in. Template status is not checked here; the caller decides
        which version a document is judged against.
        """
        result = ValidationOutcome()
        if not isinstance(data, dict):
            result.add_error(code="invalid_structure", message="Document data must be a dictionary")
            return result
        template_ns = template.get("namespace") or namespace

        self._validate_fields(data, template, result, template_ns)
        if not result.valid:
            return result
        self._validate_terms(data, template, result, namespace, template_ns)
        if not result.valid:
            return result
        self._evaluate_rules(data, template, result)
        return result

    # ------------------------------------------------------------------
    # Stage 3: fields
    # ------------------------------------------------------------------

    def _validate_fields(
        self, data: dict[str, Any], template: dict[str, Any], result: ValidationOutcome,
        template_ns: str, prefix: str = "",
    ) -> None:
        fields = template.get("fields", [])
        field_map = {f["name"]: f for f in fields}

        for f in fields:
            name = f["name"]
            full_path = f"{prefix}{name}" if prefix else name
            if name not in data:
                if f.get("mandatory", False):
                    result.add_error(
                        code="required", message=f"Field '{full_path}' is required", field=full_path,
                    )
                continue
            self._validate_field_value(data[name], f, full_path, result, template_ns)

        for name in data:
            if name not in field_map:
                result.add_error(
                    code="unknown_field",
                    message=f"Unknown field '{prefix}{name}' is not declared in the template",
                    field=f"{prefix}{name}",
                )

    def _validate_field_value(
        self, value: Any, f: dict[str, Any], path: str, result: ValidationOutcome,
        template_ns: str,
    ) -> None:
        field_type = f.get("type", "string")
        validation = f.get("validation") or {}

        if value is None:
            if f.get("mandatory", False):
                result.add_error(
                    code="null_value", message=f"Field '{path}' cannot be null", field=path,
                )
            return

        if field_type in _FIELD_TYPES:
            getattr(self, f"_validate_{field_type}")(value, f, path, result, validation, template_ns)

        # Live runs semantic checks only while the whole result is still valid.
        semantic = f.get("semantic_type")
        if result.valid and semantic in _SEMANTIC_TYPES:
            getattr(self, f"_semantic_{semantic}")(value, path, result)

    def _validate_string(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, str):
            result.add_error(
                code="invalid_type", message=f"Field '{path}' must be a string", field=path,
                details={"expected": "string", "got": type(value).__name__},
            )
            return
        if validation.get("min_length") and len(value) < validation["min_length"]:
            result.add_error(
                code="min_length",
                message=f"Field '{path}' must be at least {validation['min_length']} characters",
                field=path,
            )
        if validation.get("max_length") and len(value) > validation["max_length"]:
            result.add_error(
                code="max_length",
                message=f"Field '{path}' must be at most {validation['max_length']} characters",
                field=path,
            )
        if validation.get("pattern"):
            pattern = validation["pattern"]
            if not re.match(pattern, value):
                result.add_error(
                    code="pattern", message=f"Field '{path}' does not match required pattern",
                    field=path, details={"pattern": pattern},
                )
        if validation.get("enum") and value not in validation["enum"]:
            result.add_error(
                code="invalid_enum", message=f"Field '{path}' must be one of {validation['enum']}",
                field=path,
            )

    def _validate_number(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, (int, float)):
            result.add_error(code="invalid_type", message=f"Field '{path}' must be a number", field=path)
            return
        self._numeric_constraints(value, path, validation, result)

    def _validate_integer(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            result.add_error(code="invalid_type", message=f"Field '{path}' must be an integer", field=path)
            return
        self._numeric_constraints(value, path, validation, result)

    @staticmethod
    def _numeric_constraints(value, path, validation, result) -> None:
        if validation.get("minimum") is not None and value < validation["minimum"]:
            result.add_error(
                code="minimum", message=f"Field '{path}' must be at least {validation['minimum']}",
                field=path,
            )
        if validation.get("maximum") is not None and value > validation["maximum"]:
            result.add_error(
                code="maximum", message=f"Field '{path}' must be at most {validation['maximum']}",
                field=path,
            )

    def _validate_boolean(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, bool):
            result.add_error(code="invalid_type", message=f"Field '{path}' must be a boolean", field=path)

    def _validate_date(self, value, f, path, result, validation, template_ns) -> None:
        if isinstance(value, date) and not isinstance(value, datetime):
            return
        if isinstance(value, str):
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return
            except ValueError:
                pass
        result.add_error(
            code="invalid_type", message=f"Field '{path}' must be a valid date (YYYY-MM-DD)", field=path,
        )

    def _validate_datetime(self, value, f, path, result, validation, template_ns) -> None:
        if isinstance(value, datetime):
            return
        if isinstance(value, str):
            for fmt in _DATETIME_FORMATS:
                try:
                    datetime.strptime(value.replace("+00:00", "Z").rstrip("Z"), fmt.rstrip("Z"))
                    return
                except ValueError:
                    continue
        result.add_error(
            code="invalid_type", message=f"Field '{path}' must be a valid datetime (ISO 8601)", field=path,
        )

    def _validate_term(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, str):
            result.add_error(
                code="invalid_type", message=f"Field '{path}' must be a string (term value)", field=path,
            )

    def _validate_reference(self, value, f, path, result, validation, template_ns) -> None:
        reference_type = f.get("reference_type")
        if reference_type == "document":
            if not isinstance(value, (str, dict)):
                result.add_error(
                    code="invalid_type",
                    message=f"Field '{path}' must be a string or object (document reference)", field=path,
                )
        elif reference_type == "term":
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type", message=f"Field '{path}' must be a string (term reference)",
                    field=path,
                )
        elif reference_type == "terminology":
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type", message=f"Field '{path}' must be a string (terminology value)",
                    field=path,
                )
        elif reference_type == "template":
            if not isinstance(value, str):
                result.add_error(
                    code="invalid_type", message=f"Field '{path}' must be a string (template value)",
                    field=path,
                )
        else:
            result.add_error(
                code="invalid_reference_type",
                message=f"Field '{path}' has invalid reference_type '{reference_type}'", field=path,
            )

    def _validate_file(self, value, f, path, result, validation, template_ns) -> None:
        if (f.get("file_config") or {}).get("multiple", False):
            if not isinstance(value, list):
                result.add_error(
                    code="invalid_type", message=f"Field '{path}' must be an array of file IDs", field=path,
                )
                return
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    result.add_error(
                        code="invalid_type", message=f"Item at '{path}[{i}]' must be a string (file ID)",
                        field=f"{path}[{i}]",
                    )
        elif not isinstance(value, str):
            result.add_error(
                code="invalid_type", message=f"Field '{path}' must be a string (file ID)", field=path,
            )

    def _validate_object(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, dict):
            result.add_error(code="invalid_type", message=f"Field '{path}' must be an object", field=path)
            return
        template_ref = f.get("template_ref")
        if template_ref:
            nested = self._nested_template(
                template_ref, f.get("template_ref_version"), template_ns, path, result,
            )
            if nested is not None:
                self._validate_fields(
                    value, nested, result, nested.get("namespace") or template_ns, prefix=f"{path}.",
                )

    def _validate_array(self, value, f, path, result, validation, template_ns) -> None:
        if not isinstance(value, list):
            result.add_error(code="invalid_type", message=f"Field '{path}' must be an array", field=path)
            return
        item_type = f.get("array_item_type", "string")
        for i, item in enumerate(value):
            item_path = f"{path}[{i}]"
            if item_type == "term":
                self._validate_term(item, {}, item_path, result, {}, template_ns)
            elif item_type == "object":
                if not isinstance(item, dict):
                    result.add_error(
                        code="invalid_type", message=f"Item at '{item_path}' must be an object",
                        field=item_path,
                    )
                elif f.get("array_template_ref"):
                    nested = self._nested_template(
                        f["array_template_ref"], f.get("array_template_ref_version"),
                        template_ns, item_path, result,
                    )
                    if nested is not None:
                        self._validate_fields(
                            item, nested, result, nested.get("namespace") or template_ns,
                            prefix=f"{item_path}.",
                        )
            elif item_type == "string":
                if not isinstance(item, str):
                    result.add_error(
                        code="invalid_type", message=f"Item at '{item_path}' must be a string",
                        field=item_path,
                    )
            elif item_type == "number":
                if not isinstance(item, (int, float)):
                    result.add_error(
                        code="invalid_type", message=f"Item at '{item_path}' must be a number",
                        field=item_path,
                    )
            elif item_type == "integer":
                if not isinstance(item, int) or isinstance(item, bool):
                    result.add_error(
                        code="invalid_type", message=f"Item at '{item_path}' must be an integer",
                        field=item_path,
                    )
            elif item_type == "reference":
                reference_type = f.get("reference_type")
                if reference_type == "document":
                    if not isinstance(item, (str, dict)):
                        result.add_error(
                            code="invalid_type",
                            message=f"Item at '{item_path}' must be a string or object (document reference)",
                            field=item_path,
                        )
                elif reference_type in ("term", "terminology", "template") and not isinstance(item, str):
                    result.add_error(
                        code="invalid_type",
                        message=f"Item at '{item_path}' must be a string ({reference_type} reference)",
                        field=item_path,
                    )
            # boolean, date, datetime and file items: not checked, as live (CASE-1009).

    # ------------------------------------------------------------------
    # Semantic types
    # ------------------------------------------------------------------

    def _semantic_email(self, value, path, result) -> None:
        if isinstance(value, str) and not re.match(_EMAIL, value):
            result.add_error(
                code="invalid_email", message=f"Field '{path}' must be a valid email address",
                field=path, details={"value": value},
            )

    def _semantic_url(self, value, path, result) -> None:
        if not isinstance(value, str):
            return
        message = f"Field '{path}' must be a valid HTTP(S) URL"
        try:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https"):
                result.add_error(
                    code="invalid_url", message=message, field=path,
                    details={"value": value, "reason": "scheme must be http or https"},
                )
            elif not parsed.netloc:
                result.add_error(
                    code="invalid_url", message=message, field=path,
                    details={"value": value, "reason": "missing host"},
                )
        except Exception:
            result.add_error(code="invalid_url", message=message, field=path, details={"value": value})

    def _bounded(self, value, path, result, code, label, low, high) -> None:
        if isinstance(value, (int, float)) and (value < low or value > high):
            result.add_error(
                code=code, message=f"Field '{path}' must be a valid {label} ({low} to {high})",
                field=path, details={"value": value, "min": low, "max": high},
            )

    def _semantic_latitude(self, value, path, result) -> None:
        self._bounded(value, path, result, "invalid_latitude", "latitude", -90, 90)

    def _semantic_longitude(self, value, path, result) -> None:
        self._bounded(value, path, result, "invalid_longitude", "longitude", -180, 180)

    def _semantic_percentage(self, value, path, result) -> None:
        self._bounded(value, path, result, "invalid_percentage", "percentage", 0, 100)

    def _semantic_duration(self, value, path, result) -> None:
        if not isinstance(value, dict):
            result.add_error(
                code="invalid_duration",
                message=f"Field '{path}' must be an object with 'value' and 'unit'", field=path,
                details={"expected_structure": {"value": "number", "unit": "string"}},
            )
            return
        for key in ("value", "unit"):
            if key not in value:
                result.add_error(
                    code="invalid_duration", message=f"Field '{path}' is missing required key '{key}'",
                    field=path,
                )
                return
        if not isinstance(value.get("value"), (int, float)):
            result.add_error(
                code="invalid_duration", message=f"Field '{path}.value' must be a number",
                field=f"{path}.value", details={"got": type(value.get("value")).__name__},
            )
            return
        unit = value.get("unit")
        if not isinstance(unit, str):
            result.add_error(
                code="invalid_duration", message=f"Field '{path}.unit' must be a string",
                field=f"{path}.unit",
            )
            return
        # _TIME_UNITS is a system terminology in the `wip` namespace, which
        # most archives do not carry.
        terminology = self._terminology_by_value(TIME_UNITS_TERMINOLOGY, "wip")
        if not isinstance(terminology, dict):
            result.add_unverifiable("terminology", f"wip:{TIME_UNITS_TERMINOLOGY}", f"{path}.unit")
            return
        resolution = self.resolve_value(terminology, unit)
        if not resolution.valid:
            result.add_error(
                code="invalid_duration_unit",
                message=f"Field '{path}.unit' value '{unit}' is not a valid time unit",
                field=f"{path}.unit",
                details={
                    "terminology": TIME_UNITS_TERMINOLOGY,
                    "suggestion": resolution.suggestion,
                    "valid_units": ["seconds", "minutes", "hours", "days", "weeks"],
                },
            )
        elif resolution.matched_term:
            result.term_references.append({
                "field_path": f"{path}.unit",
                "term_id": resolution.matched_term["term_id"],
                "terminology_ref": TIME_UNITS_TERMINOLOGY,
                "matched_via": resolution.matched_via,
            })

    def _semantic_geo_point(self, value, path, result) -> None:
        if not isinstance(value, dict):
            result.add_error(
                code="invalid_geo_point",
                message=f"Field '{path}' must be an object with 'latitude' and 'longitude'", field=path,
                details={"expected_structure": {"latitude": "number", "longitude": "number"}},
            )
            return
        for key in ("latitude", "longitude"):
            if key not in value:
                result.add_error(
                    code="invalid_geo_point", message=f"Field '{path}' is missing required key '{key}'",
                    field=path,
                )
                return
        for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            number = value.get(key)
            if not isinstance(number, (int, float)):
                result.add_error(
                    code="invalid_geo_point", message=f"Field '{path}.{key}' must be a number",
                    field=f"{path}.{key}",
                )
            elif number < low or number > high:
                result.add_error(
                    code="invalid_geo_point",
                    message=f"Field '{path}.{key}' must be between {low} and {high}",
                    field=f"{path}.{key}", details={"value": number, "min": low, "max": high},
                )

    # ------------------------------------------------------------------
    # Stage 4: terms
    # ------------------------------------------------------------------

    def _validate_terms(
        self, data: dict[str, Any], template: dict[str, Any], result: ValidationOutcome,
        namespace: str, template_ns: str,
    ) -> None:
        identity_fields = template.get("identity_fields") or []
        for path, terminology_ref, value in self._collect_term_values(
            data, template.get("fields", []), template_ns, result,
        ):
            terminology = self.resolve_terminology(terminology_ref, namespace)
            if isinstance(terminology, _Unknown):
                result.add_unverifiable("terminology", terminology_ref, path)
                continue
            resolution = (
                TermResolution(valid=False) if terminology is None
                else self.resolve_value(terminology, value)
            )
            if not resolution.valid:
                result.add_error(
                    code="invalid_term",
                    message=f"Value '{value}' is not valid for terminology '{terminology_ref}'",
                    field=path,
                    details={"terminology": terminology_ref, "suggestion": resolution.suggestion},
                )
                continue
            if resolution.ambiguous:
                candidates = list(resolution.candidates)
                bearer_desc = ", ".join(f"'{c.get('value')}' ({c.get('term_id')})" for c in candidates)
                if path in identity_fields:
                    result.add_error(
                        code="ambiguous_term",
                        message=(
                            f"Value '{value}' is ambiguous in terminology '{terminology_ref}': "
                            f"{len(candidates)} terms bear this name ({bearer_desc}). An identity "
                            "field cannot resolve by storage order — submit the term's canonical "
                            "value or an unambiguous name."
                        ),
                        field=path, details={"candidates": candidates},
                    )
                    continue
                result.add_warning(
                    f"Value '{value}' in field '{path}' is ambiguous in terminology "
                    f"'{terminology_ref}': {len(candidates)} terms bear this name ({bearer_desc}); "
                    "resolved to the first in storage order."
                )
            if resolution.matched_term:
                result.term_references.append({
                    "field_path": path,
                    "term_id": resolution.matched_term["term_id"],
                    "terminology_ref": terminology_ref,
                    "matched_via": resolution.matched_via,
                })

    def _typed_leaves(
        self, data: dict[str, Any], fields: list[dict[str, Any]], template_ns: str,
        result: ValidationOutcome, prefix: str = "",
    ) -> list[tuple[str, dict[str, Any], Any, str]]:
        """Every present, non-null field with its definition, nested ones included."""
        leaves: list[tuple[str, dict[str, Any], Any, str]] = []
        for f in fields:
            name = f["name"]
            full_path = f"{prefix}{name}" if prefix else name
            if name not in data or data[name] is None:
                continue
            value = data[name]
            leaves.append((full_path, f, value, template_ns))
            field_type = f.get("type", "string")
            if field_type == "object" and f.get("template_ref") and isinstance(value, dict):
                nested = self._nested_template(
                    f["template_ref"], f.get("template_ref_version"), template_ns, full_path, result,
                )
                if nested and nested.get("fields"):
                    leaves.extend(self._typed_leaves(
                        value, nested["fields"], nested.get("namespace") or template_ns,
                        result, f"{full_path}.",
                    ))
            elif (
                field_type == "array" and f.get("array_item_type") == "object"
                and f.get("array_template_ref") and isinstance(value, list)
            ):
                nested = self._nested_template(
                    f["array_template_ref"], f.get("array_template_ref_version"),
                    template_ns, full_path, result,
                )
                if nested and nested.get("fields"):
                    for i, item in enumerate(value):
                        if isinstance(item, dict):
                            leaves.extend(self._typed_leaves(
                                item, nested["fields"], nested.get("namespace") or template_ns,
                                result, f"{full_path}[{i}].",
                            ))
        return leaves

    def _collect_term_values(
        self, data: dict[str, Any], fields: list[dict[str, Any]], template_ns: str,
        result: ValidationOutcome,
    ) -> list[tuple[str, str, str]]:
        values: list[tuple[str, str, str]] = []
        for path, f, value, _ns in self._typed_leaves(data, fields, template_ns, result):
            field_type = f.get("type", "string")
            if field_type == "term" and f.get("terminology_ref"):
                values.append((path, f["terminology_ref"], value))
            elif field_type == "array" and f.get("array_item_type") == "term":
                terminology_ref = f.get("array_terminology_ref")
                if terminology_ref and isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, str):
                            values.append((f"{path}[{i}]", terminology_ref, item))
        return values

    # ------------------------------------------------------------------
    # Resolution against the archive
    # ------------------------------------------------------------------

    def resolve_terminology(self, ref: str, namespace: str) -> dict[str, Any] | _Unknown | None:
        """A terminology ref as live resolves it: canonical id, bare value in
        `namespace`, or `ns:VALUE`. None when the archive proves it absent
        (its namespace is carried), UNKNOWN when the archive cannot say."""
        if _UUID.match(ref):
            return self.definitions.terminologies.get(ref, UNKNOWN)
        # A qualified ref splits on its first colon, as split_qualified_value does.
        ns, sep, value = ref.partition(":")
        return self._terminology_by_value(value, ns) if sep else self._terminology_by_value(ref, namespace)

    def _terminology_by_value(self, value: str, ns: str) -> dict[str, Any] | _Unknown | None:
        for row in self.definitions.terminologies.values():
            if row.get("namespace") == ns and row.get("value") == value:
                return row
        return None if ns in self._carried_namespaces else UNKNOWN

    def resolve_value(self, terminology: dict[str, Any], value: str) -> TermResolution:
        """def-store's resolver (`TerminologyService.resolve_values`) over the
        archive's terms: active terms only, every value before any name,
        case per the terminology, every bearer of a name listed."""
        case_sensitive = bool(terminology.get("case_sensitive", True))

        def cmp(s: str) -> str:
            return s if case_sensitive else s.lower()

        terms = [
            t for t in self.definitions.terms_by_terminology.get(
                terminology.get("terminology_id", ""), ()
            )
            if t.get("status", "active") == "active"
            and t.get("namespace", terminology.get("namespace")) == terminology.get("namespace")
        ]
        # Live orders by creation (MongoDB _id); the archive carries created_at.
        terms.sort(key=lambda t: (str(t.get("created_at") or ""), str(t.get("term_id") or "")))
        target = cmp(value)

        match = next((t for t in terms if cmp(t.get("value", "") or "") == target), None)
        if match is not None:
            return TermResolution(True, _term_ref(match), "value")
        bearers = [t for t in terms if any(cmp(n) == target for n in term_names(t))]
        if bearers:
            return TermResolution(
                True, _term_ref(bearers[0]), "alias",
                candidates=tuple(_term_ref(t) for t in bearers) if len(bearers) > 1 else (),
            )
        suggestion = None
        if len(value) >= 2:
            prefix = value if case_sensitive else value.lower()
            hit = next((t for t in terms if cmp(t.get("value", "") or "").startswith(prefix)), None)
            suggestion = _term_ref(hit) if hit else None
        return TermResolution(False, suggestion=suggestion)

    def _nested_template(
        self, ref: str, version: int | None, template_ns: str, path: str,
        result: ValidationOutcome,
    ) -> dict[str, Any] | None:
        """A nested template, resolved, or None when the archive cannot supply it."""
        template_id = self._template_id(ref, template_ns)
        if template_id is None:
            result.add_unverifiable("template", ref, path)
            return None
        if version is None:
            versions = [v for (tid, v) in self.definitions.templates if tid == template_id]
            version = max(versions) if versions else None
        if version is None:
            result.add_unverifiable("template", ref, path)
            return None
        try:
            return self.definitions.resolve_template(template_id, int(version)).as_template_dict()
        except UnresolvableTemplate:
            result.add_unverifiable("template", ref, path)
            return None

    def _template_id(self, ref: str, template_ns: str) -> str | None:
        if any(tid == ref for (tid, _v) in self.definitions.templates):
            return ref
        ns, sep, value = ref.partition(":")
        if not sep:
            ns, value = template_ns, ref
        for (tid, _v), row in self.definitions.templates.items():
            if row.get("namespace") == ns and row.get("value") == value:
                return tid
        return None

    # ------------------------------------------------------------------
    # Stage 6: rules
    # ------------------------------------------------------------------

    def _evaluate_rules(
        self, data: dict[str, Any], template: dict[str, Any], result: ValidationOutcome,
    ) -> None:
        for rule in template.get("rules") or []:
            rule_type = rule.get("type")
            if rule_type == "conditional_required":
                self._conditional_required(data, rule, result)
            elif rule_type == "conditional_value":
                self._conditional_value(data, rule, result)
            elif rule_type == "mutual_exclusion":
                self._mutual_exclusion(data, rule, result)
            elif rule_type == "dependency":
                self._dependency(data, rule, result)

    @staticmethod
    def _coerce(actual: Any, expected: Any) -> tuple[Any, Any]:
        if actual is None or expected is None:
            return actual, expected
        if isinstance(actual, (int, float)) and isinstance(expected, str):
            with contextlib.suppress(ValueError, TypeError):
                expected = type(actual)(expected)
        elif isinstance(expected, (int, float)) and isinstance(actual, str):
            with contextlib.suppress(ValueError, TypeError):
                actual = type(expected)(actual)
        return actual, expected

    def _condition(self, data: dict[str, Any], condition: dict[str, Any]) -> bool:
        operator = condition.get("operator")
        expected = condition.get("value")
        actual = _get_nested_value(data, cast(str, condition.get("field")))
        if operator == "equals":
            a, e = self._coerce(actual, expected)
            return bool(a == e)
        if operator == "not_equals":
            a, e = self._coerce(actual, expected)
            return bool(a != e)
        if operator == "in":
            return bool(actual in expected) if expected else False
        if operator == "not_in":
            return bool(actual not in expected) if expected else True
        if operator == "exists":
            return actual is not None
        if operator == "not_exists":
            return actual is None
        return False

    def _conditions(self, data: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
        return all(self._condition(data, c) for c in conditions)

    def _conditional_required(self, data, rule, result) -> None:
        target = rule.get("target_field")
        if not self._conditions(data, rule.get("conditions") or []):
            return
        if rule.get("required") is not False and _get_nested_value(data, target) is None:
            result.add_error(
                code="conditional_required",
                message=rule.get("error_message") or f"Field '{target}' is required based on conditions",
                field=target,
            )

    def _conditional_value(self, data, rule, result) -> None:
        target = rule.get("target_field")
        allowed = rule.get("allowed_values") or []
        if not self._conditions(data, rule.get("conditions") or []):
            return
        value = _get_nested_value(data, target)
        if value is not None and value not in allowed:
            result.add_error(
                code="conditional_value",
                message=rule.get("error_message") or f"Field '{target}' must be one of {allowed}",
                field=target,
            )

    def _mutual_exclusion(self, data, rule, result) -> None:
        targets = rule.get("target_fields") or []
        present = [f for f in targets if _get_nested_value(data, f) is not None]
        if len(present) > 1:
            result.add_error(
                code="mutual_exclusion",
                message=rule.get("error_message") or f"Only one of {targets} can have a value",
                field=present[0],
            )

    def _dependency(self, data, rule, result) -> None:
        target = rule.get("target_field")
        conditions = rule.get("conditions") or []
        if _get_nested_value(data, target) is not None and not self._conditions(data, conditions):
            for condition in conditions:
                if not self._condition(data, condition):
                    result.add_error(
                        code="dependency",
                        message=rule.get("error_message")
                        or f"Field '{target}' requires '{condition['field']}' to be set",
                        field=target,
                    )
                    break
