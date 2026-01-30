"""
Document generation configurations and helpers.

Defines document counts per template for each profile and provides
helpers for generating batches of documents.
"""

from typing import Any
from . import generators


# Document counts per template for each profile
DOCUMENT_PROFILES = {
    "minimal": {
        # Domain templates
        "PERSON": 10,
        "EMPLOYEE": 5,
        "PRODUCT": 10,
        "ORDER": 5,
        "CUSTOMER": 5,
        # Edge case templates
        "MINIMAL": 5,
        "ALL_TYPES": 5,
        "ARRAY_HEAVY": 5,
    },
    "standard": {
        # Domain templates
        "PERSON": 50,
        "EMPLOYEE": 30,
        "CONTRACTOR": 10,
        "MANAGER": 10,
        "INTERN": 5,
        "PRODUCT": 100,
        "ORDER": 50,
        "CUSTOMER": 30,
        "INVOICE": 30,
        "MEDICAL_RECORD": 20,
        "ISSUE_TICKET": 50,
        # Inheritance templates
        "PHYSICAL_PRODUCT": 20,
        "DIGITAL_PRODUCT": 20,
        "BILLING_ADDRESS": 10,
        # Edge case templates
        "MINIMAL": 10,
        "ALL_TYPES": 10,
        "ARRAY_HEAVY": 10,
        "DEEP_NEST": 10,
        "LARGE_FIELDS": 10,
        "COMPLEX_RULES": 20,
    },
    "full": {
        # Domain templates
        "PERSON": 200,
        "EMPLOYEE": 100,
        "CONTRACTOR": 30,
        "MANAGER": 30,
        "INTERN": 20,
        "PRODUCT": 500,
        "ORDER": 200,
        "CUSTOMER": 100,
        "INVOICE": 100,
        "MEDICAL_RECORD": 50,
        "ISSUE_TICKET": 200,
        # Inheritance templates
        "PHYSICAL_PRODUCT": 100,
        "DIGITAL_PRODUCT": 100,
        "BILLING_ADDRESS": 50,
        # Edge case templates
        "MINIMAL": 50,
        "ALL_TYPES": 50,
        "ARRAY_HEAVY": 50,
        "DEEP_NEST": 50,
        "LARGE_FIELDS": 50,
        "COMPLEX_RULES": 100,
    },
    "performance": {
        # Domain templates
        "PERSON": 5000,
        "EMPLOYEE": 2000,
        "CONTRACTOR": 500,
        "MANAGER": 200,
        "INTERN": 200,
        "PRODUCT": 10000,
        "ORDER": 5000,
        "CUSTOMER": 2000,
        "INVOICE": 2000,
        "MEDICAL_RECORD": 1000,
        "ISSUE_TICKET": 5000,
        # Inheritance templates
        "PHYSICAL_PRODUCT": 2000,
        "DIGITAL_PRODUCT": 2000,
        "BILLING_ADDRESS": 500,
        # Edge case templates
        "MINIMAL": 10000,
        "ALL_TYPES": 1000,
        "ARRAY_HEAVY": 1000,
        "DEEP_NEST": 500,
        "LARGE_FIELDS": 500,
        "COMPLEX_RULES": 2000,
    }
}


def get_document_counts(profile: str) -> dict[str, int]:
    """Get document counts per template for a profile."""
    return DOCUMENT_PROFILES.get(profile, DOCUMENT_PROFILES["standard"])


def get_total_documents(profile: str) -> int:
    """Get total document count for a profile."""
    counts = get_document_counts(profile)
    return sum(counts.values())


def generate_documents_for_template(
    template_code: str,
    count: int,
    start_index: int = 0
) -> list[dict[str, Any]]:
    """Generate multiple documents for a template."""
    generator = generators.get_generator(template_code)
    if not generator:
        return []

    documents = []
    for i in range(count):
        doc = generator(start_index + i)
        if doc:
            documents.append(doc)

    return documents


def generate_all_documents(profile: str = "standard") -> dict[str, list[dict[str, Any]]]:
    """Generate all documents for a profile, grouped by template code."""
    counts = get_document_counts(profile)
    all_documents = {}

    for template_code, count in counts.items():
        documents = generate_documents_for_template(template_code, count)
        if documents:
            all_documents[template_code] = documents

    return all_documents


def generate_versioning_test_documents() -> list[dict[str, Any]]:
    """
    Generate documents designed to test versioning/upsert behavior.

    Creates multiple documents with the same identity that should result
    in version updates rather than new documents.
    """
    documents = []

    # Create base person
    person1 = generators.generate_person(0)
    person1["email"] = "version.test@example.com"
    person1["first_name"] = "Version"
    person1["last_name"] = "Test"
    person1["notes"] = "Version 1 - Initial"
    documents.append(("PERSON", person1, "create"))

    # Create update to same person (same email = same identity)
    person2 = person1.copy()
    person2["notes"] = "Version 2 - Updated"
    person2["address"] = generators.generate_address()
    documents.append(("PERSON", person2, "update"))

    # Another update
    person3 = person2.copy()
    person3["notes"] = "Version 3 - Final"
    person3["active"] = False
    documents.append(("PERSON", person3, "update"))

    # Create employee with versioning
    emp1 = generators.generate_employee(0)
    emp1["employee_id"] = "EMP-999999"
    emp1["notes"] = "Employee Version 1"
    documents.append(("EMPLOYEE", emp1, "create"))

    # Update employee
    emp2 = emp1.copy()
    emp2["notes"] = "Employee Version 2 - Promoted"
    emp2["job_title"] = "Senior Engineer"
    documents.append(("EMPLOYEE", emp2, "update"))

    return documents


def generate_validation_edge_cases() -> list[tuple[str, dict[str, Any], str]]:
    """
    Generate documents designed to test validation edge cases.

    Returns tuples of (template_code, document_data, expected_result)
    where expected_result is 'valid', 'invalid', or 'warning'.
    """
    cases = []

    # Valid: Minimal valid person
    cases.append(("PERSON", {
        "first_name": "Min",
        "last_name": "Valid",
        "email": "min.valid@example.com",
        "birth_date": "2000-01-01"
    }, "valid"))

    # Valid: Using alias for salutation
    cases.append(("PERSON", {
        "salutation": "Mr.",  # Alias for "Mr"
        "first_name": "Alias",
        "last_name": "Test",
        "email": "alias.test@example.com",
        "birth_date": "1990-05-15"
    }, "valid"))

    # Valid: Using age instead of birth_date (conditional_required)
    cases.append(("PERSON", {
        "first_name": "Age",
        "last_name": "Only",
        "email": "age.only@example.com",
        "age": 35
    }, "valid"))

    # Invalid: Missing required field
    cases.append(("PERSON", {
        "first_name": "Missing",
        # Missing last_name and email
    }, "invalid"))

    # Invalid: Invalid email format (pattern validation)
    cases.append(("PERSON", {
        "first_name": "Bad",
        "last_name": "Email",
        "email": "not-an-email",
        "birth_date": "1990-01-01"
    }, "invalid"))

    # Invalid: Age out of range
    cases.append(("PERSON", {
        "first_name": "Old",
        "last_name": "Person",
        "email": "old@example.com",
        "age": 200  # Max is 150
    }, "invalid"))

    # Invalid: Invalid term value
    cases.append(("PERSON", {
        "first_name": "Bad",
        "last_name": "Gender",
        "email": "bad.gender@example.com",
        "birth_date": "1990-01-01",
        "gender": "InvalidValue"
    }, "invalid"))

    # Valid: Employee with required manager (non-HR department)
    cases.append(("EMPLOYEE", {
        "employee_id": "EMP-888888",
        "first_name": "Valid",
        "last_name": "Employee",
        "email": "valid.emp@example.com",
        "birth_date": "1985-06-15",
        "hire_date": "2020-01-15",
        "department": "Engineering",
        "job_title": "Developer",
        "employment_type": "Full-time",
        "manager_id": "EMP-000001",
        "salary": {"currency": "US Dollar", "amount": 100000}
    }, "valid"))

    # Invalid: Employee missing manager for Engineering dept
    cases.append(("EMPLOYEE", {
        "employee_id": "EMP-777777",
        "first_name": "No",
        "last_name": "Manager",
        "email": "no.manager@example.com",
        "birth_date": "1985-06-15",
        "hire_date": "2020-01-15",
        "department": "Engineering",  # Requires manager
        "job_title": "Developer",
        "employment_type": "Full-time"
        # Missing manager_id
    }, "invalid"))

    # Valid: Employee in HR (manager not required)
    cases.append(("EMPLOYEE", {
        "employee_id": "EMP-666666",
        "first_name": "HR",
        "last_name": "Person",
        "email": "hr.person@example.com",
        "birth_date": "1985-06-15",
        "hire_date": "2020-01-15",
        "department": "Human Resources",  # No manager required
        "job_title": "HR Specialist",
        "employment_type": "Part-time"
        # No manager_id needed
    }, "valid"))

    # Invalid: Full-time employee without salary
    cases.append(("EMPLOYEE", {
        "employee_id": "EMP-555555",
        "first_name": "No",
        "last_name": "Salary",
        "email": "no.salary@example.com",
        "birth_date": "1985-06-15",
        "hire_date": "2020-01-15",
        "department": "Human Resources",
        "job_title": "HR Manager",
        "employment_type": "Full-time"
        # Missing salary (required for full-time)
    }, "invalid"))

    # Valid: Product in stock with quantity
    cases.append(("PRODUCT", {
        "sku": "VAL-12345",
        "name": "Valid Product",
        "category": "Electronics",
        "price": 99.99,
        "currency": "US Dollar",
        "in_stock": True,
        "stock_quantity": 50
    }, "valid"))

    # Invalid: Product in stock without quantity
    cases.append(("PRODUCT", {
        "sku": "INV-12345",
        "name": "Invalid Product",
        "category": "Electronics",
        "price": 99.99,
        "currency": "US Dollar",
        "in_stock": True
        # Missing stock_quantity
    }, "invalid"))

    # Valid: Order with tracking (approved status)
    cases.append(("ORDER", {
        "order_number": "ORD-20240130001",
        "customer_email": "order@example.com",
        "status": "Approved",
        "order_date": "2024-01-30T10:00:00",
        "shipping_address": {
            "street": "123 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "United States"
        },
        "lines": [
            {"product_sku": "SKU-001", "product_name": "Item 1", "quantity": 2, "unit_price": 25.00}
        ],
        "currency": "US Dollar",
        "payment_method": "Credit Card",
        "tracking_number": "TRK123456789"
    }, "valid"))

    return cases


def generate_special_character_documents() -> list[tuple[str, dict[str, Any]]]:
    """Generate documents with special characters for encoding testing."""
    documents = []

    # Unicode names
    documents.append(("PERSON", {
        "first_name": "Jose",
        "last_name": "Garcia",
        "email": "jose.garcia@example.com",
        "birth_date": "1990-01-01",
        "notes": "Spanish name with accents"
    }))

    # German umlauts
    documents.append(("PERSON", {
        "first_name": "Muller",
        "last_name": "Grosse",
        "email": "muller.grosse@example.com",
        "birth_date": "1985-06-15",
        "notes": "German name with umlauts"
    }))

    # Chinese characters
    documents.append(("PERSON", {
        "first_name": "Wei",
        "last_name": "Zhang",
        "email": "wei.zhang@example.com",
        "birth_date": "1988-03-20",
        "notes": "Chinese name"
    }))

    # Emoji in notes
    documents.append(("PERSON", {
        "first_name": "Emoji",
        "last_name": "Test",
        "email": "emoji.test@example.com",
        "birth_date": "1995-12-25",
        "notes": "Testing emoji support"
    }))

    # Long text
    documents.append(("PERSON", {
        "first_name": "Long",
        "last_name": "Notes",
        "email": "long.notes@example.com",
        "birth_date": "1980-01-01",
        "notes": "A" * 4999  # Near max length
    }))

    return documents
