"""
Template-driven document generator.

Generates valid documents by reading template definitions and terminology values,
then automatically satisfying validation rules.

Usage:
    generator = DocumentGenerator()
    doc = generator.generate("PERSON", index=0)
"""
from __future__ import annotations

import random
import string
import re
from datetime import datetime, timedelta, date
from typing import Any

try:
    from faker import Faker
except ImportError:
    raise ImportError("Faker is required. Install with: pip install faker")

from . import terminologies as term_module
from . import templates as tmpl_module


# Initialize Faker
fake = Faker(['en_US', 'en_GB', 'de_DE', 'fr_FR'])
Faker.seed(42)


class TerminologyCache:
    """Cache for terminology definitions and term values."""

    def __init__(self):
        self._terminologies: dict[str, dict] = {}
        self._load_all()

    def _load_all(self):
        """Load all terminologies from the terminologies module."""
        for terminology in term_module.get_terminology_definitions():
            self._terminologies[terminology["code"]] = terminology

    def get_terminology(self, code: str) -> dict | None:
        """Get a terminology by code."""
        return self._terminologies.get(code)

    def get_random_term_value(self, terminology_code: str, use_alias: bool = False) -> str | None:
        """Get a random term value from a terminology."""
        terminology = self.get_terminology(terminology_code)
        if not terminology or not terminology.get("terms"):
            return None

        term = random.choice(terminology["terms"])

        if use_alias and term.get("aliases") and random.random() < 0.3:
            return random.choice(term["aliases"])

        return term["value"]

    def get_term_values(self, terminology_code: str) -> list[str]:
        """Get all term values for a terminology."""
        terminology = self.get_terminology(terminology_code)
        if not terminology or not terminology.get("terms"):
            return []
        return [t["value"] for t in terminology["terms"]]

    def get_random_term_from_list(self, terminology_code: str, allowed_values: list[str]) -> str | None:
        """Get a random term value from an allowed list."""
        terminology = self.get_terminology(terminology_code)
        if not terminology:
            return allowed_values[0] if allowed_values else None

        valid_terms = [t["value"] for t in terminology["terms"] if t["value"] in allowed_values]
        if valid_terms:
            return random.choice(valid_terms)
        return allowed_values[0] if allowed_values else None

    def get_random_term_excluding(self, terminology_code: str, exclude_values: list[str]) -> str | None:
        """Get a random term value excluding specific values."""
        terminology = self.get_terminology(terminology_code)
        if not terminology or not terminology.get("terms"):
            return None

        valid_terms = [t for t in terminology["terms"] if t["value"] not in exclude_values]
        if valid_terms:
            return random.choice(valid_terms)["value"]
        return terminology["terms"][0]["value"]


class TemplateCache:
    """Cache for template definitions."""

    def __init__(self):
        self._templates: dict[str, dict] = {}
        self._load_all()

    def _load_all(self):
        """Load all templates from the templates module."""
        for template in tmpl_module.get_template_definitions():
            self._templates[template["code"]] = template

    def get_template(self, code: str) -> dict | None:
        """Get a template by code."""
        return self._templates.get(code)

    def get_resolved_template(self, code: str) -> dict | None:
        """Get a template with inheritance resolved (fields merged from parents)."""
        template = self.get_template(code)
        if not template:
            return None

        if "extends" not in template or not template["extends"]:
            return template

        # Resolve parent first
        parent = self.get_resolved_template(template["extends"])
        if not parent:
            return template

        # Merge fields (child fields override parent)
        parent_fields = {f["name"]: f for f in parent.get("fields", [])}
        child_fields = {f["name"]: f for f in template.get("fields", [])}

        # Parent fields first, then child overrides
        merged_fields = list(parent_fields.values())
        for name, field in child_fields.items():
            if name in parent_fields:
                # Replace parent field with child field
                merged_fields = [f if f["name"] != name else field for f in merged_fields]
            else:
                merged_fields.append(field)

        # Merge rules (child rules added to parent rules)
        merged_rules = parent.get("rules", []) + template.get("rules", [])

        return {
            **template,
            "fields": merged_fields,
            "rules": merged_rules,
        }


class DocumentGenerator:
    """
    Template-driven document generator.

    Reads template definitions and terminology values to generate valid documents
    that automatically satisfy all validation rules.
    """

    def __init__(self):
        self.terminologies = TerminologyCache()
        self.templates = TemplateCache()

        # Field name patterns for realistic data generation
        self._realistic_generators = {
            # Names
            "first_name": lambda: fake.first_name(),
            "last_name": lambda: fake.last_name(),
            "middle_name": lambda: fake.first_name(),
            "name": lambda: fake.name(),
            "patient_name": lambda: fake.name(),
            "provider_name": lambda: f"Dr. {fake.last_name()}",
            "billing_name": lambda: fake.company() if random.random() < 0.5 else fake.name(),
            "company_name": lambda: fake.company(),

            # Contact
            "email": lambda: fake.email(),
            "customer_email": lambda: fake.email(),
            "reporter_email": lambda: fake.email(),
            "assignee_email": lambda: fake.company_email(),
            "phone": lambda: fake.phone_number(),
            "mobile": lambda: fake.phone_number(),

            # Address parts
            "street": lambda: fake.street_address(),
            "street2": lambda: f"Apt {random.randint(1, 999)}",
            "city": lambda: fake.city(),
            "state": lambda: fake.state(),
            "postal_code": lambda: fake.postcode(),

            # Job
            "job_title": lambda: fake.job(),
            "school": lambda: f"{fake.city()} University",
            "facility": lambda: f"{fake.city()} Medical Center",

            # Descriptions
            "description": lambda: fake.paragraph(nb_sentences=3),
            "notes": lambda: fake.paragraph(nb_sentences=2),
            "chief_complaint": lambda: fake.sentence(),
            "diagnosis": lambda: fake.sentence(),
            "treatment_plan": lambda: fake.paragraph(),
            "resolution_notes": lambda: fake.paragraph(),

            # Titles
            "title": lambda: fake.sentence(nb_words=6)[:200],
            "product_name": lambda: fake.catch_phrase(),
        }

    def generate(self, template_code: str, index: int = 0) -> dict[str, Any]:
        """
        Generate a valid document for a template.

        Args:
            template_code: The template code (e.g., "PERSON", "EMPLOYEE")
            index: Index for unique ID generation

        Returns:
            A document dict that satisfies all template validation rules
        """
        template = self.templates.get_resolved_template(template_code)
        if not template:
            raise ValueError(f"Template '{template_code}' not found")

        # Generate base document from fields
        doc = self._generate_fields(template["fields"], index, template_code)

        # Apply rules to ensure validity
        rules = template.get("rules", [])
        self._apply_rules(doc, rules, template["fields"])

        return doc

    def _generate_fields(
        self,
        fields: list[dict],
        index: int,
        template_code: str,
        prefix: str = ""
    ) -> dict[str, Any]:
        """Generate values for all fields in a template."""
        doc = {}

        for field in fields:
            name = field["name"]
            mandatory = field.get("mandatory", False)

            # Always generate mandatory fields, sometimes generate optional
            if mandatory or random.random() < 0.7:
                value = self._generate_field_value(field, index, template_code, prefix)
                if value is not None:
                    doc[name] = value

        return doc

    def _generate_field_value(
        self,
        field: dict,
        index: int,
        template_code: str,
        prefix: str = ""
    ) -> Any:
        """Generate a value for a single field based on its type."""
        field_type = field.get("type", "string")
        field_name = field["name"]
        validation = field.get("validation", {})

        # Check for realistic generator first
        if field_name in self._realistic_generators:
            value = self._realistic_generators[field_name]()
            # Apply validation constraints if needed
            if field_type == "string" and validation:
                value = self._apply_string_validation(value, validation)
            return value

        # Generate by type
        if field_type == "string":
            return self._generate_string(field, index, template_code)
        elif field_type == "number":
            return self._generate_number(field, validation)
        elif field_type == "integer":
            return self._generate_integer(field, validation)
        elif field_type == "boolean":
            return random.choice([True, False])
        elif field_type == "date":
            return self._generate_date(field)
        elif field_type == "datetime":
            return self._generate_datetime(field)
        elif field_type == "term":
            return self._generate_term(field)
        elif field_type == "object":
            return self._generate_object(field, index)
        elif field_type == "array":
            return self._generate_array(field, index)
        else:
            return None

    def _generate_string(self, field: dict, index: int, template_code: str) -> str:
        """Generate a string value."""
        field_name = field["name"]
        validation = field.get("validation", {})

        # Check for ID patterns
        if "pattern" in validation:
            pattern = validation["pattern"]
            return self._generate_from_pattern(pattern, field_name, index, template_code)

        # Check for enum
        if "enum" in validation:
            return random.choice(validation["enum"])

        # Default string generation
        max_length = validation.get("max_length", 100)
        min_length = validation.get("min_length", 1)

        # Generate based on field name hints
        if "id" in field_name.lower():
            return f"{field_name.upper()}-{index:06d}"
        elif "number" in field_name.lower():
            return f"{field_name.upper()[:3]}-{index:06d}"
        elif "sku" in field_name.lower():
            return f"SKU-{random.randint(10000, 99999)}"
        else:
            # Generate a sentence and truncate
            text = fake.sentence(nb_words=5)
            if len(text) > max_length:
                text = text[:max_length]
            if len(text) < min_length:
                text = text + "x" * (min_length - len(text))
            return text

    def _generate_from_pattern(self, pattern: str, field_name: str, index: int, template_code: str) -> str:
        """Generate a string matching a regex pattern."""
        # Common patterns and their generators
        pattern_generators = {
            r"^EMP-\d{6}$": lambda: f"EMP-{100000 + index:06d}",
            r"^CON-\d{6}$": lambda: f"CON-{100000 + index:06d}",
            r"^CUST-\d{6,10}$": lambda: f"CUST-{100000 + index:06d}",
            r"^PAT-\d{8}$": lambda: f"PAT-{10000000 + index:08d}",
            r"^TKT-\d{6}$": lambda: f"TKT-{100000 + index:06d}",
            r"^ORD-\d{8,12}$": lambda: f"ORD-{datetime.now().strftime('%Y%m%d')}{index:04d}",
            r"^INV-\d{4}-\d{6}$": lambda: f"INV-{datetime.now().year}-{index:06d}",
            r"^[A-Z0-9\-]{3,20}$": lambda: f"SKU-{random.randint(10000, 99999)}",
            r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$": lambda: fake.email(),
            r"^\+?[\d\s\-\(\)]{7,20}$": lambda: fake.phone_number(),
        }

        for pat, gen in pattern_generators.items():
            if pattern == pat or pattern.replace("\\", "") == pat.replace("\\", ""):
                return gen()

        # For email-like patterns
        if "email" in field_name.lower() or "@" in pattern:
            return fake.email()

        # Default: generate based on field name
        return f"{field_name.upper()[:4]}-{index:06d}"

    def _apply_string_validation(self, value: str, validation: dict) -> str:
        """Apply string validation constraints."""
        max_length = validation.get("max_length")
        min_length = validation.get("min_length")

        if max_length and len(value) > max_length:
            value = value[:max_length]
        if min_length and len(value) < min_length:
            value = value + "x" * (min_length - len(value))

        return value

    def _generate_number(self, field: dict, validation: dict) -> float:
        """Generate a number value."""
        minimum = validation.get("minimum", 0)
        maximum = validation.get("maximum", 10000)
        return round(random.uniform(minimum, maximum), 2)

    def _generate_integer(self, field: dict, validation: dict) -> int:
        """Generate an integer value."""
        minimum = validation.get("minimum", 0)
        maximum = validation.get("maximum", 1000)
        return random.randint(int(minimum), int(maximum))

    def _generate_date(self, field: dict) -> str:
        """Generate a date value."""
        field_name = field["name"]

        if "birth" in field_name.lower():
            return fake.date_of_birth(minimum_age=18, maximum_age=80).isoformat()
        elif "hire" in field_name.lower() or "start" in field_name.lower():
            return fake.date_between(start_date="-10y", end_date="today").isoformat()
        elif "end" in field_name.lower() or "termination" in field_name.lower():
            return fake.date_between(start_date="today", end_date="+1y").isoformat()
        elif "due" in field_name.lower():
            return fake.date_between(start_date="today", end_date="+60d").isoformat()
        elif "graduation" in field_name.lower():
            return fake.date_between(start_date="today", end_date="+4y").isoformat()
        elif "follow" in field_name.lower():
            return fake.date_between(start_date="today", end_date="+30d").isoformat()
        else:
            return fake.date_between(start_date="-1y", end_date="today").isoformat()

    def _generate_datetime(self, field: dict) -> str:
        """Generate a datetime value."""
        field_name = field["name"]

        if "created" in field_name.lower():
            return fake.date_time_between(start_date="-1y", end_date="now").isoformat()
        elif "updated" in field_name.lower():
            return fake.date_time_between(start_date="-30d", end_date="now").isoformat()
        elif "resolved" in field_name.lower():
            return fake.date_time_between(start_date="-30d", end_date="now").isoformat()
        else:
            return datetime.now().isoformat()

    def _generate_term(self, field: dict) -> str | None:
        """Generate a term value from the referenced terminology."""
        terminology_ref = field.get("terminology_ref")
        if not terminology_ref:
            return None

        return self.terminologies.get_random_term_value(terminology_ref, use_alias=True)

    def _generate_object(self, field: dict, index: int) -> dict | None:
        """Generate an object value."""
        template_ref = field.get("template_ref")

        if template_ref:
            # Use referenced template
            ref_template = self.templates.get_resolved_template(template_ref)
            if ref_template:
                return self._generate_fields(ref_template["fields"], index, template_ref)

        # Free-form object based on field name
        field_name = field["name"]
        if "dimensions" in field_name.lower():
            return {
                "length": round(random.uniform(1, 100), 1),
                "width": round(random.uniform(1, 100), 1),
                "height": round(random.uniform(1, 50), 1)
            }
        elif "vital" in field_name.lower():
            return {
                "blood_pressure": f"{random.randint(100, 140)}/{random.randint(60, 90)}",
                "heart_rate": random.randint(60, 100),
                "temperature": round(random.uniform(97.0, 99.5), 1),
            }
        elif "level" in field_name.lower():
            return {
                "name": fake.word(),
                "value": random.randint(1, 100)
            }

        return {"value": fake.word()}

    def _generate_array(self, field: dict, index: int) -> list:
        """Generate an array value."""
        item_type = field.get("array_item_type", "string")
        count = random.randint(1, 5)

        if item_type == "term":
            terminology_ref = field.get("array_terminology_ref")
            if terminology_ref:
                values = [self.terminologies.get_random_term_value(terminology_ref)
                         for _ in range(count)]
                return list(set(v for v in values if v))  # Remove duplicates and None

        elif item_type == "object":
            template_ref = field.get("array_template_ref")
            if template_ref:
                ref_template = self.templates.get_resolved_template(template_ref)
                if ref_template:
                    return [self._generate_fields(ref_template["fields"], index + i, template_ref)
                            for i in range(count)]
            # Free-form objects
            return [{"value": fake.word()} for _ in range(count)]

        elif item_type == "string":
            return [fake.word() for _ in range(count)]

        elif item_type == "number":
            return [round(random.uniform(0, 100), 2) for _ in range(count)]

        elif item_type == "integer":
            return [random.randint(0, 100) for _ in range(count)]

        elif item_type == "boolean":
            return [random.choice([True, False]) for _ in range(count)]

        return []

    def _apply_rules(self, doc: dict, rules: list[dict], fields: list[dict]):
        """Apply validation rules to ensure document validity."""
        # Build field lookup
        field_map = {f["name"]: f for f in fields}

        for rule in rules:
            rule_type = rule.get("type")

            if rule_type == "conditional_required":
                self._apply_conditional_required(doc, rule, field_map)
            elif rule_type == "conditional_value":
                self._apply_conditional_value(doc, rule, field_map)
            elif rule_type == "mutual_exclusion":
                self._apply_mutual_exclusion(doc, rule, field_map)
            elif rule_type == "dependency":
                self._apply_dependency(doc, rule, field_map)

    def _check_conditions(self, doc: dict, conditions: list[dict]) -> bool:
        """Check if all conditions are met."""
        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            expected = condition.get("value")
            actual = doc.get(field)

            if operator == "equals":
                if actual != expected:
                    return False
            elif operator == "not_equals":
                if actual == expected:
                    return False
            elif operator == "in":
                if actual not in (expected or []):
                    return False
            elif operator == "not_in":
                if actual in (expected or []):
                    return False
            elif operator == "exists":
                if actual is None:
                    return False
            elif operator == "not_exists":
                if actual is not None:
                    return False

        return True

    def _apply_conditional_required(self, doc: dict, rule: dict, field_map: dict):
        """Apply conditional_required rule: if conditions met, target field must exist."""
        conditions = rule.get("conditions", [])
        target_field = rule.get("target_field")

        if self._check_conditions(doc, conditions):
            # Conditions met, target field is required
            if target_field not in doc or doc[target_field] is None:
                # Generate the missing field
                if target_field in field_map:
                    field = field_map[target_field]
                    doc[target_field] = self._generate_field_value(field, 0, "")

    def _apply_conditional_value(self, doc: dict, rule: dict, field_map: dict):
        """Apply conditional_value rule: if conditions met, target must be in allowed values."""
        conditions = rule.get("conditions", [])
        target_field = rule.get("target_field")
        allowed_values = rule.get("allowed_values", [])

        if not allowed_values:
            return

        if self._check_conditions(doc, conditions):
            # Conditions met - ensure target field exists and has valid value
            current_value = doc.get(target_field)

            if current_value is None or current_value not in allowed_values:
                # Set to an allowed value
                field = field_map.get(target_field, {})
                terminology_ref = field.get("terminology_ref")

                if terminology_ref:
                    doc[target_field] = self.terminologies.get_random_term_from_list(
                        terminology_ref, allowed_values
                    )
                else:
                    doc[target_field] = random.choice(allowed_values)

    def _apply_mutual_exclusion(self, doc: dict, rule: dict, field_map: dict):
        """Apply mutual_exclusion rule: ensure exactly one (or none) of fields is set."""
        target_fields = rule.get("target_fields", [])
        require_one = rule.get("require_one", False)

        present_fields = [f for f in target_fields if f in doc and doc[f] is not None]

        if require_one and len(present_fields) == 0:
            # Need at least one - generate the first field
            if target_fields and target_fields[0] in field_map:
                field = field_map[target_fields[0]]
                doc[target_fields[0]] = self._generate_field_value(field, 0, "")

        elif len(present_fields) > 1:
            # Too many - remove extras
            for f in present_fields[1:]:
                del doc[f]

    def _apply_dependency(self, doc: dict, rule: dict, field_map: dict):
        """Apply dependency rule: if target field is set, required field must also be set."""
        target_field = rule.get("target_field")
        conditions = rule.get("conditions", [])

        # If target field is set, check conditions
        if target_field in doc and doc[target_field] is not None:
            if not self._check_conditions(doc, conditions):
                # Conditions not met - either add required fields or remove target
                # Try to add required fields first
                for condition in conditions:
                    if condition.get("operator") == "exists":
                        req_field = condition.get("field")
                        if req_field not in doc and req_field in field_map:
                            doc[req_field] = self._generate_field_value(
                                field_map[req_field], 0, ""
                            )


# Singleton instance
_generator: DocumentGenerator | None = None


def get_generator() -> DocumentGenerator:
    """Get the singleton document generator instance."""
    global _generator
    if _generator is None:
        _generator = DocumentGenerator()
    return _generator


def generate_document(template_code: str, index: int = 0) -> dict[str, Any]:
    """Generate a document for a template (convenience function)."""
    return get_generator().generate(template_code, index)
