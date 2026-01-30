"""Validation service for document validation against templates."""

import re
from datetime import datetime, date
from typing import Any, Optional

from .template_store_client import get_template_store_client, TemplateStoreError
from .def_store_client import get_def_store_client, DefStoreError
from .identity_service import IdentityService


class ValidationResult:
    """Result of document validation."""

    def __init__(self):
        self.valid = True
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.identity_hash: Optional[str] = None
        self.template_version: Optional[int] = None
        self.term_references: dict[str, Any] = {}  # field_path -> term_id

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
            "term_references": self.term_references
        }


class ValidationService:
    """
    Service for validating documents against templates.

    Implements a 6-stage validation pipeline:
    1. Structural - Valid dict, required envelope fields
    2. Template Resolution - Fetch template, check active, resolve inheritance
    3. Field Validation - Mandatory fields, type checking, field-level constraints
    4. Term Validation - Validate term values via Def-Store
    5. Rule Evaluation - Cross-field rules
    6. Identity Computation - Extract identity fields, compute hash
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
        "object": "_validate_object",
        "array": "_validate_array",
    }

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
            ValidationResult with errors, warnings, and identity hash
        """
        result = ValidationResult()

        # Stage 1: Structural validation
        if not self._validate_structural(data, result):
            return result

        # Stage 2: Template resolution
        template = await self._resolve_template(template_id, result)
        if template is None:
            return result

        result.template_version = template.get("version", 1)

        # Stage 3: Field validation
        await self._validate_fields(data, template, result)
        if not result.valid:
            return result

        # Stage 4: Term validation (batched for efficiency)
        await self._validate_terms(data, template, result)
        if not result.valid:
            return result

        # Stage 5: Rule evaluation
        self._evaluate_rules(data, template, result)
        if not result.valid:
            return result

        # Stage 6: Identity computation
        self._compute_identity(data, template, result)

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
        validation = field.get("validation", {})

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
                    # Extract term_id from matched_term and store in term_references
                    matched_term = validation_result.get("matched_term")
                    if matched_term and matched_term.get("term_id"):
                        term_id = matched_term["term_id"]

                        # Handle array fields - store as list
                        if "[" in field_path:
                            # Extract base path (e.g., "languages" from "languages[0]")
                            base_path = field_path.split("[")[0]
                            if base_path not in result.term_references:
                                result.term_references[base_path] = []
                            # Ensure list is long enough
                            index = int(field_path.split("[")[1].rstrip("]"))
                            while len(result.term_references[base_path]) <= index:
                                result.term_references[base_path].append(None)
                            result.term_references[base_path][index] = term_id
                        else:
                            result.term_references[field_path] = term_id

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
