"""
Template definitions for comprehensive testing.

Contains 20+ templates covering:
- Base templates (ADDRESS, CONTACT_INFO, MONEY)
- Domain templates (PERSON, EMPLOYEE, PRODUCT, ORDER, etc.)
- Inheritance testing (EMPLOYEE -> PERSON, MANAGER -> EMPLOYEE)
- Edge cases (MINIMAL, ALL_TYPES, DEEP_NEST, LARGE_FIELDS, COMPLEX_RULES)
"""
from __future__ import annotations

from typing import Any


def get_template_definitions() -> list[dict[str, Any]]:
    """Return all template definitions in dependency order."""
    return [
        # Base templates (no dependencies)
        ADDRESS,
        CONTACT_INFO,
        MONEY,
        # Domain templates - no inheritance
        PERSON,
        PRODUCT,
        ORDER_LINE,
        ORDER,
        CUSTOMER,
        INVOICE,
        MEDICAL_RECORD,
        ISSUE_TICKET,
        # Inheritance templates
        EMPLOYEE,
        CONTRACTOR,
        MANAGER,
        INTERN,
        BILLING_ADDRESS,
        PHYSICAL_PRODUCT,
        DIGITAL_PRODUCT,
        # Edge case templates
        MINIMAL,
        ALL_TYPES,
        DEEP_NEST,
        LARGE_FIELDS,
        COMPLEX_RULES,
        ARRAY_HEAVY,
    ]


def get_template_by_value(value: str) -> dict[str, Any] | None:
    """Get a specific template by value."""
    for template in get_template_definitions():
        if template["value"] == value:
            return template
    return None


def get_base_templates() -> list[dict[str, Any]]:
    """Return templates that don't extend other templates."""
    return [t for t in get_template_definitions() if "extends" not in t]


def get_inheritance_templates() -> list[dict[str, Any]]:
    """Return templates that extend other templates."""
    return [t for t in get_template_definitions() if "extends" in t]


# =============================================================================
# BASE TEMPLATES (No inheritance)
# =============================================================================

ADDRESS = {
    "value": "ADDRESS",
    "label": "Address",
    "description": "Standard postal address structure for reuse as nested object",
    "identity_fields": [],  # Not used standalone
    "fields": [
        {"name": "street", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 200}},
        {"name": "street2", "type": "string", "mandatory": False, "validation": {"max_length": 200}},
        {"name": "city", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 100}},
        {"name": "state", "type": "string", "mandatory": False, "validation": {"max_length": 100}},
        {"name": "postal_code", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 20}},
        {"name": "country", "type": "term", "terminology_ref": "COUNTRY", "mandatory": True},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "country", "operator": "in", "value": ["USA", "CAN", "AUS"]}],
            "target_field": "state",
            "error_message": "State is required for USA, Canada, and Australia"
        }
    ]
}

CONTACT_INFO = {
    "value": "CONTACT_INFO",
    "label": "Contact Information",
    "description": "Email and phone contact details",
    "identity_fields": [],  # Not used standalone
    "fields": [
        {
            "name": "email",
            "type": "string",
            "mandatory": True,
            "validation": {
                "pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$",
                "max_length": 255
            }
        },
        {
            "name": "phone",
            "type": "string",
            "mandatory": False,
            "validation": {
                "pattern": r"^\+?[\d\s\-\(\)]{7,20}$",
                "max_length": 25
            }
        },
        {
            "name": "mobile",
            "type": "string",
            "mandatory": False,
            "validation": {
                "pattern": r"^\+?[\d\s\-\(\)]{7,20}$",
                "max_length": 25
            }
        },
        {"name": "preferred_contact", "type": "string", "mandatory": False, "validation": {"enum": ["email", "phone", "mobile"]}},
    ],
    "rules": [
        {
            "type": "mutual_exclusion",
            "target_fields": ["phone", "mobile"],
            "require_one": True,
            "error_message": "At least one phone number (phone or mobile) is required"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "preferred_contact", "operator": "equals", "value": "mobile"}],
            "target_field": "mobile",
            "error_message": "Mobile is required when it's the preferred contact method"
        }
    ]
}

MONEY = {
    "value": "MONEY",
    "label": "Money Amount",
    "description": "Currency and amount pair",
    "identity_fields": [],  # Not used standalone
    "fields": [
        {"name": "currency", "type": "term", "terminology_ref": "CURRENCY", "mandatory": True},
        {"name": "amount", "type": "number", "mandatory": True, "validation": {"minimum": 0}},
    ],
    "rules": [
        {
            "type": "range",
            "target_field": "amount",
            "minimum": 0,
            "maximum": 999999999.99,
            "error_message": "Amount must be between 0 and 999,999,999.99"
        }
    ]
}


# =============================================================================
# DOMAIN TEMPLATES - Base entities
# =============================================================================

PERSON = {
    "value": "PERSON",
    "label": "Person",
    "description": "Base person template with all common field types",
    "identity_fields": ["email"],
    "fields": [
        {"name": "salutation", "type": "term", "terminology_ref": "SALUTATION", "mandatory": False},
        {"name": "first_name", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 100}},
        {"name": "middle_name", "type": "string", "mandatory": False, "validation": {"max_length": 100}},
        {"name": "last_name", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 100}},
        {
            "name": "email",
            "type": "string",
            "mandatory": True,
            "validation": {
                "pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$",
                "max_length": 255
            }
        },
        {"name": "birth_date", "type": "date", "mandatory": False},
        {"name": "age", "type": "integer", "mandatory": False, "validation": {"minimum": 0, "maximum": 150}},
        {"name": "gender", "type": "term", "terminology_ref": "GENDER", "mandatory": False},
        {"name": "nationality", "type": "term", "terminology_ref": "COUNTRY", "mandatory": False},
        {"name": "languages", "type": "array", "array_item_type": "term", "array_terminology_ref": "LANGUAGE", "mandatory": False},
        {"name": "address", "type": "object", "template_ref": "ADDRESS", "mandatory": False},
        {"name": "active", "type": "boolean", "mandatory": False, "default_value": True},
        {"name": "notes", "type": "string", "mandatory": False, "validation": {"max_length": 5000}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "age", "operator": "not_exists"}],
            "target_field": "birth_date",
            "error_message": "Birth date is required if age is not provided"
        },
        {
            "type": "pattern",
            "target_field": "email",
            "pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$",
            "error_message": "Email must be in valid format (e.g., user@example.com)"
        },
        {
            "type": "range",
            "target_field": "age",
            "minimum": 0,
            "maximum": 150,
            "error_message": "Age must be between 0 and 150"
        }
    ]
}

PRODUCT = {
    "value": "PRODUCT",
    "label": "Product",
    "description": "E-commerce product template",
    "identity_fields": ["sku"],
    "fields": [
        {"name": "sku", "type": "string", "mandatory": True, "validation": {"pattern": r"^[A-Z0-9\-]{3,20}$", "min_length": 3, "max_length": 20}},
        {"name": "name", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 200}},
        {"name": "description", "type": "string", "mandatory": False, "validation": {"max_length": 5000}},
        {"name": "category", "type": "term", "terminology_ref": "PRODUCT_CATEGORY", "mandatory": True},
        {"name": "price", "type": "number", "mandatory": True, "validation": {"minimum": 0.01, "maximum": 999999.99}},
        {"name": "currency", "type": "term", "terminology_ref": "CURRENCY", "mandatory": True},
        {"name": "unit", "type": "term", "terminology_ref": "UNIT_OF_MEASURE", "mandatory": False},
        {"name": "in_stock", "type": "boolean", "mandatory": False, "default_value": True},
        {"name": "stock_quantity", "type": "integer", "mandatory": False, "validation": {"minimum": 0}},
        {"name": "tags", "type": "array", "array_item_type": "string", "mandatory": False},
    ],
    "rules": [
        {
            "type": "range",
            "target_field": "price",
            "minimum": 0.01,
            "maximum": 999999.99,
            "error_message": "Price must be between 0.01 and 999,999.99"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "in_stock", "operator": "equals", "value": True}],
            "target_field": "stock_quantity",
            "error_message": "Stock quantity is required when product is in stock"
        }
    ]
}

ORDER_LINE = {
    "value": "ORDER_LINE",
    "label": "Order Line Item",
    "description": "Single line item within an order",
    "identity_fields": [],  # Used as nested object in ORDER
    "fields": [
        {"name": "product_sku", "type": "string", "mandatory": True, "validation": {"pattern": r"^[A-Z0-9\-]{3,20}$"}},
        {"name": "product_name", "type": "string", "mandatory": True, "validation": {"max_length": 200}},
        {"name": "quantity", "type": "integer", "mandatory": True, "validation": {"minimum": 1, "maximum": 9999}},
        {"name": "unit_price", "type": "number", "mandatory": True, "validation": {"minimum": 0}},
        {"name": "discount_percent", "type": "number", "mandatory": False, "validation": {"minimum": 0, "maximum": 100}},
        {"name": "line_total", "type": "number", "mandatory": False},
    ],
    "rules": [
        {
            "type": "range",
            "target_field": "quantity",
            "minimum": 1,
            "maximum": 9999,
            "error_message": "Quantity must be between 1 and 9999"
        },
        {
            "type": "range",
            "target_field": "discount_percent",
            "minimum": 0,
            "maximum": 100,
            "error_message": "Discount must be between 0% and 100%"
        }
    ]
}

ORDER = {
    "value": "ORDER",
    "label": "Order",
    "description": "Customer order with line items",
    "identity_fields": ["order_number"],
    "fields": [
        {"name": "order_number", "type": "string", "mandatory": True, "validation": {"pattern": r"^ORD-\d{8,12}$"}},
        {"name": "customer_email", "type": "string", "mandatory": True, "validation": {"pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$"}},
        {"name": "status", "type": "term", "terminology_ref": "DOC_STATUS", "mandatory": True},
        {"name": "order_date", "type": "datetime", "mandatory": True},
        {"name": "shipping_address", "type": "object", "template_ref": "ADDRESS", "mandatory": True},
        {"name": "billing_address", "type": "object", "template_ref": "ADDRESS", "mandatory": False},
        {"name": "lines", "type": "array", "array_item_type": "object", "array_template_ref": "ORDER_LINE", "mandatory": True},
        {"name": "subtotal", "type": "number", "mandatory": False, "validation": {"minimum": 0}},
        {"name": "tax_amount", "type": "number", "mandatory": False, "validation": {"minimum": 0}},
        {"name": "shipping_cost", "type": "number", "mandatory": False, "validation": {"minimum": 0}},
        {"name": "total", "type": "number", "mandatory": False, "validation": {"minimum": 0}},
        {"name": "currency", "type": "term", "terminology_ref": "CURRENCY", "mandatory": True},
        {"name": "payment_method", "type": "term", "terminology_ref": "PAYMENT_METHOD", "mandatory": False},
        {"name": "tracking_number", "type": "string", "mandatory": False, "validation": {"max_length": 100}},
        {"name": "notes", "type": "string", "mandatory": False, "validation": {"max_length": 2000}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "status", "operator": "in", "value": ["Approved"]}],
            "target_field": "payment_method",
            "error_message": "Payment method is required for approved orders"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "status", "operator": "in", "value": ["Approved", "Archived"]}],
            "target_field": "tracking_number",
            "error_message": "Tracking number is required for shipped orders"
        },
        {
            "type": "dependency",
            "target_field": "billing_address",
            "conditions": [{"field": "shipping_address", "operator": "exists"}],
            "error_message": "Shipping address must be set before billing address"
        }
    ]
}

CUSTOMER = {
    "value": "CUSTOMER",
    "label": "Customer",
    "description": "CRM customer record",
    "identity_fields": ["customer_number"],
    "fields": [
        {"name": "customer_number", "type": "string", "mandatory": True, "validation": {"pattern": r"^CUST-\d{6,10}$"}},
        {"name": "company_name", "type": "string", "mandatory": False, "validation": {"max_length": 200}},
        {"name": "first_name", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 100}},
        {"name": "last_name", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 100}},
        {"name": "email", "type": "string", "mandatory": True, "validation": {"pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$"}},
        {"name": "phone", "type": "string", "mandatory": False, "validation": {"max_length": 25}},
        {"name": "billing_address", "type": "object", "template_ref": "ADDRESS", "mandatory": True},
        {"name": "shipping_address", "type": "object", "template_ref": "ADDRESS", "mandatory": False},
        {"name": "preferred_currency", "type": "term", "terminology_ref": "CURRENCY", "mandatory": False},
        {"name": "preferred_language", "type": "term", "terminology_ref": "LANGUAGE", "mandatory": False},
        {"name": "tax_id", "type": "string", "mandatory": False, "validation": {"max_length": 50}},
        {"name": "active", "type": "boolean", "mandatory": False, "default_value": True},
        {"name": "created_date", "type": "datetime", "mandatory": False},
        {"name": "notes", "type": "string", "mandatory": False, "validation": {"max_length": 5000}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "company_name", "operator": "exists"}],
            "target_field": "tax_id",
            "error_message": "Tax ID is required for business customers"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "active", "operator": "equals", "value": True}],
            "target_field": "email",
            "error_message": "Email is required for active customers"
        },
        {
            "type": "pattern",
            "target_field": "email",
            "pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$",
            "error_message": "Email must be in valid format"
        }
    ]
}

INVOICE = {
    "value": "INVOICE",
    "label": "Invoice",
    "description": "Financial invoice document",
    "identity_fields": ["invoice_number"],
    "fields": [
        {"name": "invoice_number", "type": "string", "mandatory": True, "validation": {"pattern": r"^INV-\d{4}-\d{6}$"}},
        {"name": "customer_number", "type": "string", "mandatory": True, "validation": {"pattern": r"^CUST-\d{6,10}$"}},
        {"name": "order_number", "type": "string", "mandatory": False, "validation": {"pattern": r"^ORD-\d{8,12}$"}},
        {"name": "status", "type": "term", "terminology_ref": "DOC_STATUS", "mandatory": True},
        {"name": "issue_date", "type": "date", "mandatory": True},
        {"name": "due_date", "type": "date", "mandatory": True},
        {"name": "paid_date", "type": "date", "mandatory": False},
        {"name": "lines", "type": "array", "array_item_type": "object", "array_template_ref": "ORDER_LINE", "mandatory": True},
        {"name": "subtotal", "type": "number", "mandatory": True, "validation": {"minimum": 0}},
        {"name": "tax_rate", "type": "number", "mandatory": False, "validation": {"minimum": 0, "maximum": 100}},
        {"name": "tax_amount", "type": "number", "mandatory": False, "validation": {"minimum": 0}},
        {"name": "total", "type": "number", "mandatory": True, "validation": {"minimum": 0}},
        {"name": "currency", "type": "term", "terminology_ref": "CURRENCY", "mandatory": True},
        {"name": "payment_method", "type": "term", "terminology_ref": "PAYMENT_METHOD", "mandatory": False},
        {"name": "payment_reference", "type": "string", "mandatory": False, "validation": {"max_length": 100}},
        {"name": "notes", "type": "string", "mandatory": False, "validation": {"max_length": 2000}},
    ],
    "rules": [
        {
            "type": "dependency",
            "target_field": "paid_date",
            "conditions": [{"field": "payment_method", "operator": "exists"}],
            "error_message": "Payment method is required when paid date is set"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "status", "operator": "equals", "value": "Approved"}],
            "target_field": "paid_date",
            "error_message": "Paid date is required for approved (paid) invoices"
        },
        {
            "type": "conditional_value",
            "conditions": [{"field": "status", "operator": "equals", "value": "Approved"}],
            "target_field": "payment_method",
            "allowed_values": ["Credit Card", "Bank Transfer", "PayPal"],
            "error_message": "Payment method must be Card, Bank, or PayPal for paid invoices"
        },
        {
            "type": "range",
            "target_field": "tax_rate",
            "minimum": 0,
            "maximum": 100,
            "error_message": "Tax rate must be between 0% and 100%"
        }
    ]
}

MEDICAL_RECORD = {
    "value": "MEDICAL_RECORD",
    "label": "Medical Record",
    "description": "Healthcare patient medical record",
    "identity_fields": ["patient_id", "record_date"],
    "fields": [
        {"name": "patient_id", "type": "string", "mandatory": True, "validation": {"pattern": r"^PAT-\d{8}$"}},
        {"name": "record_date", "type": "datetime", "mandatory": True},
        {"name": "record_type", "type": "string", "mandatory": True, "validation": {"enum": ["visit", "lab", "imaging", "procedure", "prescription"]}},
        {"name": "patient_name", "type": "string", "mandatory": True, "validation": {"max_length": 200}},
        {"name": "date_of_birth", "type": "date", "mandatory": True},
        {"name": "gender", "type": "term", "terminology_ref": "GENDER", "mandatory": True},
        {"name": "blood_type", "type": "term", "terminology_ref": "BLOOD_TYPE", "mandatory": False},
        {"name": "allergies", "type": "array", "array_item_type": "string", "mandatory": False},
        {"name": "chief_complaint", "type": "string", "mandatory": False, "validation": {"max_length": 1000}},
        {"name": "diagnosis", "type": "string", "mandatory": False, "validation": {"max_length": 2000}},
        {"name": "treatment_plan", "type": "string", "mandatory": False, "validation": {"max_length": 5000}},
        {"name": "medications", "type": "array", "array_item_type": "string", "mandatory": False},
        {"name": "vital_signs", "type": "object", "mandatory": False},  # Free-form object
        {"name": "lab_results", "type": "array", "array_item_type": "object", "mandatory": False},
        {"name": "provider_name", "type": "string", "mandatory": True, "validation": {"max_length": 200}},
        {"name": "facility", "type": "string", "mandatory": False, "validation": {"max_length": 200}},
        {"name": "priority", "type": "term", "terminology_ref": "PRIORITY", "mandatory": False},
        {"name": "follow_up_date", "type": "date", "mandatory": False},
        {"name": "confidential_notes", "type": "string", "mandatory": False, "validation": {"max_length": 5000}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "record_type", "operator": "equals", "value": "visit"}],
            "target_field": "chief_complaint",
            "error_message": "Chief complaint is required for visit records"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "record_type", "operator": "equals", "value": "lab"}],
            "target_field": "lab_results",
            "error_message": "Lab results are required for lab records"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "record_type", "operator": "equals", "value": "prescription"}],
            "target_field": "medications",
            "error_message": "Medications list is required for prescription records"
        },
        {
            "type": "dependency",
            "target_field": "follow_up_date",
            "conditions": [{"field": "diagnosis", "operator": "exists"}],
            "error_message": "Diagnosis must be set before scheduling follow-up"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "priority", "operator": "in", "value": ["Critical", "High"]}],
            "target_field": "follow_up_date",
            "error_message": "Follow-up date is required for critical/high priority cases"
        }
    ]
}

ISSUE_TICKET = {
    "value": "ISSUE_TICKET",
    "label": "Issue Ticket",
    "description": "Support system issue/bug ticket",
    "identity_fields": ["ticket_number"],
    "fields": [
        {"name": "ticket_number", "type": "string", "mandatory": True, "validation": {"pattern": r"^TKT-\d{6}$"}},
        {"name": "title", "type": "string", "mandatory": True, "validation": {"min_length": 5, "max_length": 200}},
        {"name": "description", "type": "string", "mandatory": True, "validation": {"min_length": 10, "max_length": 10000}},
        {"name": "status", "type": "term", "terminology_ref": "DOC_STATUS", "mandatory": True},
        {"name": "priority", "type": "term", "terminology_ref": "PRIORITY", "mandatory": True},
        {"name": "severity", "type": "term", "terminology_ref": "SEVERITY", "mandatory": False},
        {"name": "reporter_email", "type": "string", "mandatory": True, "validation": {"pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$"}},
        {"name": "assignee_email", "type": "string", "mandatory": False, "validation": {"pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$"}},
        {"name": "department", "type": "term", "terminology_ref": "DEPARTMENT", "mandatory": False},
        {"name": "tags", "type": "array", "array_item_type": "string", "mandatory": False},
        {"name": "created_at", "type": "datetime", "mandatory": True},
        {"name": "updated_at", "type": "datetime", "mandatory": False},
        {"name": "resolved_at", "type": "datetime", "mandatory": False},
        {"name": "resolution_notes", "type": "string", "mandatory": False, "validation": {"max_length": 5000}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "status", "operator": "in", "value": ["Approved"]}],
            "target_field": "resolved_at",
            "error_message": "Resolution timestamp is required for resolved tickets"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "status", "operator": "in", "value": ["Approved"]}],
            "target_field": "resolution_notes",
            "error_message": "Resolution notes are required for resolved tickets"
        },
        {
            "type": "dependency",
            "target_field": "resolved_at",
            "conditions": [{"field": "assignee_email", "operator": "exists"}],
            "error_message": "Ticket must be assigned before it can be resolved"
        }
    ]
}


# =============================================================================
# INHERITANCE TEMPLATES
# =============================================================================

EMPLOYEE = {
    "value": "EMPLOYEE",
    "label": "Employee",
    "description": "Employee extending Person with work-related fields",
    "extends": "PERSON",  # Inherits all fields from PERSON
    "identity_fields": ["employee_id"],  # Override parent identity
    "fields": [
        {"name": "employee_id", "type": "string", "mandatory": True, "validation": {"pattern": r"^EMP-\d{6}$"}},
        {"name": "hire_date", "type": "date", "mandatory": True},
        {"name": "termination_date", "type": "date", "mandatory": False},
        {"name": "department", "type": "term", "terminology_ref": "DEPARTMENT", "mandatory": True},
        {"name": "job_title", "type": "string", "mandatory": True, "validation": {"max_length": 100}},
        {"name": "employment_type", "type": "term", "terminology_ref": "EMPLOYMENT_TYPE", "mandatory": True},
        {"name": "manager_id", "type": "string", "mandatory": False, "validation": {"pattern": r"^EMP-\d{6}$"}},
        {"name": "salary", "type": "object", "template_ref": "MONEY", "mandatory": False},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "department", "operator": "not_in", "value": ["Human Resources", "Legal"]}],
            "target_field": "manager_id",
            "error_message": "Manager ID is required for non-HR/Legal departments"
        },
        {
            "type": "dependency",
            "target_field": "termination_date",
            "conditions": [{"field": "hire_date", "operator": "exists"}],
            "error_message": "Hire date must be set before termination date"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "employment_type", "operator": "equals", "value": "Full-time"}],
            "target_field": "salary",
            "error_message": "Salary is required for full-time employees"
        },
        {
            "type": "pattern",
            "target_field": "employee_id",
            "pattern": r"^EMP-\d{6}$",
            "error_message": "Employee ID must be in format EMP-NNNNNN"
        }
    ]
}

CONTRACTOR = {
    "value": "CONTRACTOR",
    "label": "Contractor",
    "description": "Independent contractor extending Person",
    "extends": "PERSON",
    "identity_fields": ["contractor_id"],
    "fields": [
        {"name": "contractor_id", "type": "string", "mandatory": True, "validation": {"pattern": r"^CON-\d{6}$"}},
        {"name": "company_name", "type": "string", "mandatory": False, "validation": {"max_length": 200}},
        {"name": "contract_start", "type": "date", "mandatory": True},
        {"name": "contract_end", "type": "date", "mandatory": False},
        {"name": "hourly_rate", "type": "object", "template_ref": "MONEY", "mandatory": True},
        {"name": "department", "type": "term", "terminology_ref": "DEPARTMENT", "mandatory": True},
    ],
    "rules": [
        {
            "type": "dependency",
            "target_field": "contract_end",
            "conditions": [{"field": "contract_start", "operator": "exists"}],
            "error_message": "Contract start date must be set before end date"
        }
    ]
}

MANAGER = {
    "value": "MANAGER",
    "label": "Manager",
    "description": "Manager extending Employee with leadership fields (3-level inheritance)",
    "extends": "EMPLOYEE",  # MANAGER -> EMPLOYEE -> PERSON
    "identity_fields": ["employee_id"],  # Same as EMPLOYEE
    "fields": [
        {"name": "direct_reports", "type": "array", "array_item_type": "string", "mandatory": False},
        {"name": "budget_authority", "type": "object", "template_ref": "MONEY", "mandatory": False},
        {"name": "management_level", "type": "string", "mandatory": True, "validation": {"enum": ["team_lead", "manager", "director", "vp", "c_level"]}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "management_level", "operator": "in", "value": ["director", "vp", "c_level"]}],
            "target_field": "budget_authority",
            "error_message": "Budget authority is required for director level and above"
        }
    ]
}

INTERN = {
    "value": "INTERN",
    "label": "Intern",
    "description": "Intern extending Employee with internship fields",
    "extends": "EMPLOYEE",
    "identity_fields": ["employee_id"],
    "fields": [
        {"name": "school", "type": "string", "mandatory": True, "validation": {"max_length": 200}},
        {"name": "graduation_date", "type": "date", "mandatory": False},
        {"name": "mentor_id", "type": "string", "mandatory": True, "validation": {"pattern": r"^EMP-\d{6}$"}},
        {"name": "program_name", "type": "string", "mandatory": False, "validation": {"max_length": 100}},
    ],
    "rules": [
        {
            "type": "pattern",
            "target_field": "mentor_id",
            "pattern": r"^EMP-\d{6}$",
            "error_message": "Mentor ID must be a valid employee ID (EMP-NNNNNN)"
        }
    ]
}

BILLING_ADDRESS = {
    "value": "BILLING_ADDRESS",
    "label": "Billing Address",
    "description": "Billing address extending Address with billing-specific fields",
    "extends": "ADDRESS",
    "identity_fields": [],  # Used as nested object
    "fields": [
        {"name": "billing_name", "type": "string", "mandatory": True, "validation": {"max_length": 200}},
        {"name": "tax_id", "type": "string", "mandatory": False, "validation": {"max_length": 50}},
        {"name": "attention_to", "type": "string", "mandatory": False, "validation": {"max_length": 100}},
        # Override country field to require specific values for billing
        {
            "name": "country",
            "type": "term",
            "terminology_ref": "COUNTRY",
            "mandatory": True,
            "validation": {"enum": ["USA", "GBR", "DEU", "FRA", "CAN", "AUS", "JPN"]}  # Limited countries
        },
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "country", "operator": "in", "value": ["DEU", "FRA"]}],
            "target_field": "tax_id",
            "error_message": "Tax ID (VAT number) is required for EU billing addresses"
        }
    ]
}

PHYSICAL_PRODUCT = {
    "value": "PHYSICAL_PRODUCT",
    "label": "Physical Product",
    "description": "Physical product extending Product with shipping fields",
    "extends": "PRODUCT",
    "identity_fields": ["sku"],
    "fields": [
        {"name": "weight", "type": "number", "mandatory": True, "validation": {"minimum": 0.001}},
        {"name": "weight_unit", "type": "term", "terminology_ref": "UNIT_OF_MEASURE", "mandatory": True},
        {"name": "dimensions", "type": "object", "mandatory": False},  # Free-form {length, width, height}
        {"name": "dimension_unit", "type": "term", "terminology_ref": "UNIT_OF_MEASURE", "mandatory": False},
        {"name": "requires_shipping", "type": "boolean", "mandatory": False, "default_value": True},
        {"name": "shipping_class", "type": "string", "mandatory": False, "validation": {"enum": ["standard", "oversized", "fragile", "hazardous"]}},
    ],
    "rules": [
        {
            "type": "dependency",
            "target_field": "dimension_unit",
            "conditions": [{"field": "dimensions", "operator": "exists"}],
            "error_message": "Dimensions must be set before dimension unit"
        },
        {
            "type": "conditional_required",
            "conditions": [{"field": "shipping_class", "operator": "in", "value": ["oversized", "hazardous"]}],
            "target_field": "weight",
            "error_message": "Weight is required for oversized or hazardous items"
        }
    ]
}

DIGITAL_PRODUCT = {
    "value": "DIGITAL_PRODUCT",
    "label": "Digital Product",
    "description": "Digital product extending Product with download fields",
    "extends": "PRODUCT",
    "identity_fields": ["sku"],
    "fields": [
        {"name": "file_size_mb", "type": "number", "mandatory": True, "validation": {"minimum": 0.001}},
        {"name": "file_format", "type": "string", "mandatory": True, "validation": {"enum": ["pdf", "epub", "mp3", "mp4", "zip", "exe", "dmg"]}},
        {"name": "download_url", "type": "string", "mandatory": False, "validation": {"max_length": 500}},
        {"name": "license_type", "type": "string", "mandatory": True, "validation": {"enum": ["single", "multi", "enterprise", "subscription"]}},
        {"name": "max_downloads", "type": "integer", "mandatory": False, "validation": {"minimum": 1}},
    ],
    "rules": [
        {
            "type": "conditional_required",
            "conditions": [{"field": "license_type", "operator": "equals", "value": "single"}],
            "target_field": "max_downloads",
            "error_message": "Max downloads is required for single-user licenses"
        }
    ]
}


# =============================================================================
# EDGE CASE TEMPLATES
# =============================================================================

MINIMAL = {
    "value": "MINIMAL",
    "label": "Minimal",
    "description": "Minimal template with single field for edge case testing",
    "identity_fields": ["id"],
    "fields": [
        {"name": "id", "type": "string", "mandatory": True},
    ],
    "rules": []
}

ALL_TYPES = {
    "value": "ALL_TYPES",
    "label": "All Field Types",
    "description": "Template with one of each field type for comprehensive type testing",
    "identity_fields": ["string_field"],
    "fields": [
        {"name": "string_field", "type": "string", "mandatory": True, "validation": {"min_length": 1, "max_length": 100}},
        {"name": "number_field", "type": "number", "mandatory": False, "validation": {"minimum": -999999.99, "maximum": 999999.99}},
        {"name": "integer_field", "type": "integer", "mandatory": False, "validation": {"minimum": -2147483648, "maximum": 2147483647}},
        {"name": "boolean_field", "type": "boolean", "mandatory": False},
        {"name": "date_field", "type": "date", "mandatory": False},
        {"name": "datetime_field", "type": "datetime", "mandatory": False},
        {"name": "term_field", "type": "term", "terminology_ref": "GENDER", "mandatory": False},
        {"name": "object_field", "type": "object", "template_ref": "ADDRESS", "mandatory": False},
        {"name": "string_array", "type": "array", "array_item_type": "string", "mandatory": False},
        {"name": "number_array", "type": "array", "array_item_type": "number", "mandatory": False},
        {"name": "term_array", "type": "array", "array_item_type": "term", "array_terminology_ref": "LANGUAGE", "mandatory": False},
        {"name": "object_array", "type": "array", "array_item_type": "object", "array_template_ref": "MONEY", "mandatory": False},
    ],
    "rules": []
}

DEEP_NEST = {
    "value": "DEEP_NEST",
    "label": "Deep Nesting",
    "description": "Template with 4 levels of object nesting for stress testing",
    "identity_fields": ["root_id"],
    "fields": [
        {"name": "root_id", "type": "string", "mandatory": True},
        {"name": "level1", "type": "object", "mandatory": False},  # Free-form for nested testing
    ],
    "rules": []
}

# Generate LARGE_FIELDS programmatically
def _generate_large_fields() -> dict[str, Any]:
    """Generate a template with 50+ fields for performance testing."""
    fields = [
        {"name": "id", "type": "string", "mandatory": True},
    ]

    # Add 10 string fields
    for i in range(1, 11):
        fields.append({
            "name": f"string_field_{i}",
            "type": "string",
            "mandatory": False,
            "validation": {"max_length": 200}
        })

    # Add 10 number fields
    for i in range(1, 11):
        fields.append({
            "name": f"number_field_{i}",
            "type": "number",
            "mandatory": False
        })

    # Add 10 integer fields
    for i in range(1, 11):
        fields.append({
            "name": f"integer_field_{i}",
            "type": "integer",
            "mandatory": False
        })

    # Add 5 boolean fields
    for i in range(1, 6):
        fields.append({
            "name": f"boolean_field_{i}",
            "type": "boolean",
            "mandatory": False
        })

    # Add 5 date fields
    for i in range(1, 6):
        fields.append({
            "name": f"date_field_{i}",
            "type": "date",
            "mandatory": False
        })

    # Add 5 datetime fields
    for i in range(1, 6):
        fields.append({
            "name": f"datetime_field_{i}",
            "type": "datetime",
            "mandatory": False
        })

    # Add 5 term fields
    term_refs = ["GENDER", "COUNTRY", "CURRENCY", "LANGUAGE", "PRIORITY"]
    for i, ref in enumerate(term_refs, 1):
        fields.append({
            "name": f"term_field_{i}",
            "type": "term",
            "terminology_ref": ref,
            "mandatory": False
        })

    return {
        "value": "LARGE_FIELDS",
        "label": "Large Template",
        "description": "Template with 50+ fields for performance testing",
        "identity_fields": ["id"],
        "fields": fields,
        "rules": []
    }

LARGE_FIELDS = _generate_large_fields()

COMPLEX_RULES = {
    "value": "COMPLEX_RULES",
    "label": "Complex Rules",
    "description": "Template demonstrating all 6 rule types",
    "identity_fields": ["id"],
    "fields": [
        {"name": "id", "type": "string", "mandatory": True},
        {"name": "status", "type": "term", "terminology_ref": "DOC_STATUS", "mandatory": True},
        {"name": "priority", "type": "term", "terminology_ref": "PRIORITY", "mandatory": False},
        {"name": "phone", "type": "string", "mandatory": False},
        {"name": "mobile", "type": "string", "mandatory": False},
        {"name": "email", "type": "string", "mandatory": False},
        {"name": "start_date", "type": "date", "mandatory": False},
        {"name": "end_date", "type": "date", "mandatory": False},
        {"name": "amount", "type": "number", "mandatory": False},
        {"name": "quantity", "type": "integer", "mandatory": False},
    ],
    "rules": [
        # 1. conditional_required
        {
            "type": "conditional_required",
            "conditions": [{"field": "status", "operator": "equals", "value": "Approved"}],
            "target_field": "end_date",
            "error_message": "End date is required for approved status"
        },
        # 2. conditional_value
        {
            "type": "conditional_value",
            "conditions": [{"field": "priority", "operator": "in", "value": ["Critical", "High"]}],
            "target_field": "status",
            "allowed_values": ["Draft", "Pending Review"],
            "error_message": "Critical/High priority items cannot be approved immediately"
        },
        # 3. mutual_exclusion
        {
            "type": "mutual_exclusion",
            "target_fields": ["phone", "mobile"],
            "require_one": True,
            "error_message": "Exactly one contact method (phone or mobile) is required"
        },
        # 4. dependency
        {
            "type": "dependency",
            "target_field": "end_date",
            "conditions": [{"field": "start_date", "operator": "exists"}],
            "error_message": "Start date must be set before end date"
        },
        # 5. pattern
        {
            "type": "pattern",
            "target_field": "email",
            "pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w{2,}$",
            "error_message": "Email must be in valid format"
        },
        # 6. range
        {
            "type": "range",
            "target_field": "amount",
            "minimum": 0,
            "maximum": 1000000,
            "error_message": "Amount must be between 0 and 1,000,000"
        }
    ]
}

ARRAY_HEAVY = {
    "value": "ARRAY_HEAVY",
    "label": "Array Heavy",
    "description": "Template with multiple array fields for array handling testing",
    "identity_fields": ["id"],
    "fields": [
        {"name": "id", "type": "string", "mandatory": True},
        {"name": "tags", "type": "array", "array_item_type": "string", "mandatory": False},
        {"name": "scores", "type": "array", "array_item_type": "number", "mandatory": False},
        {"name": "counts", "type": "array", "array_item_type": "integer", "mandatory": False},
        {"name": "flags", "type": "array", "array_item_type": "boolean", "mandatory": False},
        {"name": "languages", "type": "array", "array_item_type": "term", "array_terminology_ref": "LANGUAGE", "mandatory": False},
        {"name": "countries", "type": "array", "array_item_type": "term", "array_terminology_ref": "COUNTRY", "mandatory": False},
        {"name": "addresses", "type": "array", "array_item_type": "object", "array_template_ref": "ADDRESS", "mandatory": False},
        {"name": "money_amounts", "type": "array", "array_item_type": "object", "array_template_ref": "MONEY", "mandatory": False},
    ],
    "rules": []
}
