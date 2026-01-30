"""
Faker-based data generators for realistic test data.

Provides generators for each template type that produce valid,
realistic data for testing.

VALIDATION-AWARE: All generators satisfy their template's validation rules:
- conditional_required: Fields are set when conditions are met
- conditional_value: Values are constrained to allowed lists when conditions apply
- mutual_exclusion: Exactly one of mutually exclusive fields is set
- dependency: Dependent fields only set when required fields exist
- pattern: Generated values match required patterns
- range: Values stay within specified min/max bounds
"""

import random
import string
from datetime import datetime, timedelta, date
from typing import Any

try:
    from faker import Faker
except ImportError:
    raise ImportError("Faker is required. Install with: pip install faker")

from . import terminologies

# Initialize Faker with multiple locales for variety
fake = Faker(['en_US', 'en_GB', 'de_DE', 'fr_FR'])
Faker.seed(42)  # Reproducible data

# Department values that don't require manager_id (per EMPLOYEE template rule)
HR_LEGAL_DEPARTMENTS = ["Human Resources", "Legal"]

# Allowed payment methods for paid invoices (per INVOICE template rule)
INVOICE_PAYMENT_METHODS = ["Credit Card", "Bank Transfer", "PayPal"]

# Priority values that require follow-up (per MEDICAL_RECORD template rule)
HIGH_PRIORITY_VALUES = ["Critical", "High"]

# Statuses that indicate resolved/completed (per various template rules)
RESOLVED_STATUSES = ["Approved"]
SHIPPED_STATUSES = ["Approved", "Archived"]

# Countries that require state field (per ADDRESS template rule)
COUNTRIES_REQUIRING_STATE = ["United States", "Canada", "Australia"]


def get_random_term_value(terminology_code: str, use_alias: bool = False) -> str:
    """Get a random term value from a terminology, optionally using an alias."""
    terminology = terminologies.get_terminology_by_code(terminology_code)
    if not terminology:
        return ""

    term = random.choice(terminology["terms"])

    if use_alias and term.get("aliases"):
        # 30% chance to use an alias instead of primary value
        if random.random() < 0.3:
            return random.choice(term["aliases"])

    return term["value"]


def get_specific_term_value(terminology_code: str, term_code: str) -> str:
    """Get a specific term's value by its code."""
    terminology = terminologies.get_terminology_by_code(terminology_code)
    if not terminology:
        return ""

    for term in terminology["terms"]:
        if term["code"] == term_code:
            return term["value"]
    return ""


def get_term_code(terminology_code: str) -> str:
    """Get a random term code from a terminology."""
    terminology = terminologies.get_terminology_by_code(terminology_code)
    if not terminology:
        return ""

    term = random.choice(terminology["terms"])
    return term["code"]


def get_random_term_value_excluding(terminology_code: str, exclude_values: list[str]) -> str:
    """Get a random term value excluding specific values."""
    terminology = terminologies.get_terminology_by_code(terminology_code)
    if not terminology:
        return ""

    valid_terms = [t for t in terminology["terms"] if t["value"] not in exclude_values]
    if not valid_terms:
        return terminology["terms"][0]["value"]

    return random.choice(valid_terms)["value"]


def get_random_term_value_from_list(terminology_code: str, allowed_values: list[str]) -> str:
    """Get a random term value from a specific list of allowed values."""
    terminology = terminologies.get_terminology_by_code(terminology_code)
    if not terminology:
        return allowed_values[0] if allowed_values else ""

    valid_terms = [t for t in terminology["terms"] if t["value"] in allowed_values]
    if not valid_terms:
        return allowed_values[0] if allowed_values else ""

    return random.choice(valid_terms)["value"]


def generate_address(country: str | None = None) -> dict[str, Any]:
    """Generate a realistic address.

    VALIDATION RULES SATISFIED:
    - ADDRESS.state: Required for USA, Canada, Australia (conditional_required)
    """
    # Map of supported countries and their codes
    country_options = {
        "USA": "United States",
        "GBR": "United Kingdom",
        "DEU": "Germany",
        "FRA": "France",
        "CAN": "Canada",
        "AUS": "Australia"
    }

    # If country specified, use it; otherwise pick randomly
    if country and country in country_options.values():
        country_name = country
        country_code = [k for k, v in country_options.items() if v == country][0]
    else:
        country_code = random.choice(list(country_options.keys()))
        country_name = country_options[country_code]

    address = {
        "street": fake.street_address(),
        "city": fake.city(),
        "postal_code": fake.postcode(),
        "country": country_name
    }

    # RULE: state is required for USA, Canada, Australia (conditional_required)
    # Always add state for these countries to satisfy the validation rule
    if country_name in COUNTRIES_REQUIRING_STATE:
        address["state"] = fake.state()

    # Sometimes add street2 (optional field)
    if random.random() < 0.2:
        address["street2"] = f"Apt {random.randint(1, 999)}"

    return address


def generate_contact_info() -> dict[str, Any]:
    """Generate contact information.

    VALIDATION RULES SATISFIED:
    - CONTACT_INFO: mutual_exclusion requires exactly one of phone/mobile (require_one=True)
    - CONTACT_INFO: If preferred_contact="mobile", mobile must be set (conditional_value)
    """
    contact = {
        "email": fake.email(),
    }

    # RULE: mutual_exclusion with require_one=True means exactly one of phone/mobile must be set
    # Pick one randomly to satisfy the rule
    use_mobile = random.random() < 0.5
    if use_mobile:
        contact["mobile"] = fake.phone_number()
    else:
        contact["phone"] = fake.phone_number()

    # Sometimes add preferred contact
    # RULE: If preferred_contact="mobile", mobile must be set (conditional_value)
    if random.random() < 0.3:
        if "mobile" in contact:
            # Can set preferred_contact to mobile since mobile is set
            contact["preferred_contact"] = random.choice(["email", "mobile"])
        else:
            # Cannot set preferred_contact to mobile since mobile is not set
            contact["preferred_contact"] = random.choice(["email", "phone"])

    return contact


def generate_money(min_amount: float = 10, max_amount: float = 10000) -> dict[str, Any]:
    """Generate a money amount."""
    return {
        "currency": get_random_term_value("CURRENCY"),
        "amount": round(random.uniform(min_amount, max_amount), 2)
    }


def generate_person(index: int = 0) -> dict[str, Any]:
    """Generate a person document.

    VALIDATION RULES SATISFIED:
    - PERSON: birth_date required if age not provided (conditional_required)
    - PERSON: email pattern validation (pattern - faker generates valid emails)
    - PERSON: age between 0-150 (range)
    """
    birth_date = fake.date_of_birth(minimum_age=18, maximum_age=80)

    person = {
        "salutation": get_random_term_value("SALUTATION", use_alias=True),
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "email": fake.email() if index == 0 else f"person{index}@{fake.domain_name()}",
        "birth_date": birth_date.isoformat(),
        "gender": get_random_term_value("GENDER", use_alias=True),
        "nationality": get_random_term_value("COUNTRY"),
        "active": random.random() < 0.9,
    }

    # RULE: Either birth_date OR age must be provided (conditional_required)
    # Optionally use age instead of birth_date to test this rule
    if random.random() < 0.2:
        person["age"] = (datetime.now().date() - birth_date).days // 365
        del person["birth_date"]

    # Add languages (array of terms)
    if random.random() < 0.7:
        num_languages = random.randint(1, 4)
        person["languages"] = [get_random_term_value("LANGUAGE") for _ in range(num_languages)]
        # Remove duplicates
        person["languages"] = list(set(person["languages"]))

    # Add address (nested object)
    if random.random() < 0.8:
        person["address"] = generate_address()

    # Add middle name sometimes
    if random.random() < 0.3:
        person["middle_name"] = fake.first_name()

    # Add notes sometimes
    if random.random() < 0.2:
        person["notes"] = fake.paragraph(nb_sentences=2)

    return person


def generate_employee(index: int = 0) -> dict[str, Any]:
    """Generate an employee document (extends PERSON).

    VALIDATION RULES SATISFIED:
    - EMPLOYEE: manager_id required if department NOT in [Human Resources, Legal] (conditional_required)
    - EMPLOYEE: termination_date requires hire_date (dependency - always satisfied)
    - EMPLOYEE: salary required for employment_type="Full-time" (conditional_value)
    - EMPLOYEE: employee_id pattern EMP-NNNNNN (pattern)
    - Inherits PERSON rules (birth_date/age, email pattern)
    """
    # Start with person data
    person = generate_person(index)

    # Add employee-specific fields
    person["employee_id"] = f"EMP-{100000 + index:06d}"
    person["hire_date"] = fake.date_between(start_date="-10y", end_date="today").isoformat()
    person["department"] = get_random_term_value("DEPARTMENT")
    person["job_title"] = fake.job()
    person["employment_type"] = get_random_term_value("EMPLOYMENT_TYPE")

    # RULE: manager_id REQUIRED for non-HR/Legal departments (conditional_required)
    if person["department"] not in HR_LEGAL_DEPARTMENTS:
        person["manager_id"] = f"EMP-{random.randint(100000, 100050):06d}"

    # RULE: salary REQUIRED for Full-time employment (conditional_value with not_empty)
    if person["employment_type"] == "Full-time":
        person["salary"] = generate_money(min_amount=40000, max_amount=200000)

    # Sometimes add termination date (dependency on hire_date is always satisfied)
    if random.random() < 0.1:
        hire_date = date.fromisoformat(person["hire_date"])
        term_date = fake.date_between(start_date=hire_date, end_date="today")
        person["termination_date"] = term_date.isoformat()
        person["active"] = False

    return person


def generate_contractor(index: int = 0) -> dict[str, Any]:
    """Generate a contractor document (extends PERSON).

    VALIDATION RULES SATISFIED:
    - CONTRACTOR: contract_end requires contract_start (dependency - always satisfied)
    - CONTRACTOR: contractor_id pattern CON-NNNNNN (pattern)
    - Inherits PERSON rules (birth_date/age, email pattern)
    """
    person = generate_person(index)

    person["contractor_id"] = f"CON-{100000 + index:06d}"
    person["contract_start"] = fake.date_between(start_date="-2y", end_date="today").isoformat()
    person["department"] = get_random_term_value("DEPARTMENT")
    person["hourly_rate"] = generate_money(min_amount=50, max_amount=500)

    # Sometimes add company name
    if random.random() < 0.6:
        person["company_name"] = fake.company()

    # Sometimes add contract end (dependency on contract_start is always satisfied)
    if random.random() < 0.5:
        start = date.fromisoformat(person["contract_start"])
        end = start + timedelta(days=random.randint(90, 365))
        person["contract_end"] = end.isoformat()

    return person


def generate_manager(index: int = 0) -> dict[str, Any]:
    """Generate a manager document (extends EMPLOYEE -> PERSON).

    VALIDATION RULES SATISFIED:
    - MANAGER: budget_authority required for management_level in [director, vp, c_level] (conditional_required)
    - Inherits EMPLOYEE rules (manager_id for non-HR/Legal, salary for Full-time)
    - Inherits PERSON rules (birth_date/age, email pattern)
    """
    employee = generate_employee(index)

    # Add manager-specific fields
    employee["management_level"] = random.choice(["team_lead", "manager", "director", "vp", "c_level"])

    # Add direct reports
    if random.random() < 0.8:
        num_reports = random.randint(1, 10)
        employee["direct_reports"] = [f"EMP-{random.randint(100000, 100500):06d}" for _ in range(num_reports)]

    # RULE: budget_authority REQUIRED for director, vp, c_level (conditional_required)
    if employee["management_level"] in ["director", "vp", "c_level"]:
        employee["budget_authority"] = generate_money(min_amount=100000, max_amount=10000000)

    return employee


def generate_intern(index: int = 0) -> dict[str, Any]:
    """Generate an intern document (extends EMPLOYEE -> PERSON).

    VALIDATION RULES SATISFIED:
    - INTERN: mentor_id pattern EMP-NNNNNN (pattern)
    - Inherits EMPLOYEE rules (manager_id for non-HR/Legal, salary for Full-time)
    - Inherits PERSON rules (birth_date/age, email pattern)

    Note: Since we override employment_type to "Intern", the salary conditional_value
    rule (salary required for Full-time) won't apply.
    """
    employee = generate_employee(index)

    # Override employment type - this means salary rule won't require salary
    employee["employment_type"] = "Intern"
    # Remove salary if it was added (not required for Interns)
    employee.pop("salary", None)

    # Add intern-specific fields
    employee["school"] = f"{fake.city()} University"
    employee["mentor_id"] = f"EMP-{random.randint(100000, 100050):06d}"

    # Sometimes add graduation date
    if random.random() < 0.7:
        grad_date = fake.date_between(start_date="today", end_date="+2y")
        employee["graduation_date"] = grad_date.isoformat()

    # Sometimes add program name
    if random.random() < 0.5:
        employee["program_name"] = random.choice([
            "Summer Internship Program",
            "Graduate Rotational Program",
            "Engineering Fellowship",
            "Business Development Internship"
        ])

    return employee


def generate_product(index: int = 0) -> dict[str, Any]:
    """Generate a product document.

    VALIDATION RULES SATISFIED:
    - PRODUCT: stock_quantity required when in_stock=True (conditional_required)
    - PRODUCT: price between 0.01 and 999999.99 (range)
    - PRODUCT: sku pattern [A-Z0-9-]{3,20} (pattern)
    """
    categories = ["ELEC", "CLOTH", "HOME", "SPORT", "BOOK", "BEAUTY", "FOOD", "TOYS"]
    category = random.choice(categories)

    in_stock = random.random() < 0.85

    product = {
        "sku": f"{category[:2]}-{random.randint(10000, 99999)}",
        "name": fake.catch_phrase(),
        "description": fake.paragraph(nb_sentences=3),
        "category": terminologies.PRODUCT_CATEGORY["terms"][categories.index(category)]["value"],
        "price": round(random.uniform(9.99, 999.99), 2),  # Within range 0.01-999999.99
        "currency": get_random_term_value("CURRENCY"),
        "in_stock": in_stock,
    }

    # RULE: stock_quantity REQUIRED when in_stock=True (conditional_required)
    if in_stock:
        product["stock_quantity"] = random.randint(1, 1000)

    # Sometimes add unit
    if random.random() < 0.3:
        product["unit"] = get_random_term_value("UNIT_OF_MEASURE")

    # Sometimes add tags
    if random.random() < 0.5:
        product["tags"] = [fake.word() for _ in range(random.randint(1, 5))]

    return product


def generate_order_line() -> dict[str, Any]:
    """Generate an order line item."""
    quantity = random.randint(1, 10)
    unit_price = round(random.uniform(9.99, 299.99), 2)

    line = {
        "product_sku": f"SKU-{random.randint(10000, 99999)}",
        "product_name": fake.catch_phrase(),
        "quantity": quantity,
        "unit_price": unit_price,
    }

    # Sometimes add discount
    if random.random() < 0.3:
        line["discount_percent"] = random.choice([5, 10, 15, 20, 25])

    # Calculate line total
    discount = line.get("discount_percent", 0) / 100
    line["line_total"] = round(quantity * unit_price * (1 - discount), 2)

    return line


def generate_order(index: int = 0) -> dict[str, Any]:
    """Generate an order document.

    VALIDATION RULES SATISFIED:
    - ORDER: payment_method required for status="Approved" (conditional_required)
    - ORDER: tracking_number required for status in [Approved, Archived] (conditional_required)
    - ORDER: billing_address requires shipping_address (dependency - always satisfied)
    """
    # Generate order lines first
    num_lines = random.randint(1, 5)
    lines = [generate_order_line() for _ in range(num_lines)]

    subtotal = sum(line["line_total"] for line in lines)
    tax_amount = round(subtotal * 0.08, 2)  # 8% tax
    shipping = round(random.uniform(0, 25), 2)
    total = round(subtotal + tax_amount + shipping, 2)

    # Determine status
    statuses = ["Draft", "Pending Review", "Approved", "Archived"]
    status = random.choice(statuses)

    order = {
        "order_number": f"ORD-{datetime.now().strftime('%Y%m%d')}{index:04d}",
        "customer_email": fake.email(),
        "status": status,
        "order_date": datetime.now().isoformat(),
        "shipping_address": generate_address(),
        "lines": lines,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "shipping_cost": shipping,
        "total": total,
        "currency": get_random_term_value("CURRENCY"),
    }

    # Add billing address sometimes (dependency on shipping_address is always satisfied)
    if random.random() < 0.3:
        order["billing_address"] = generate_address()

    # RULE: payment_method REQUIRED for status="Approved" (conditional_required)
    if status == "Approved":
        order["payment_method"] = get_random_term_value("PAYMENT_METHOD")
    elif status == "Archived" and random.random() < 0.7:
        # Optional for Archived
        order["payment_method"] = get_random_term_value("PAYMENT_METHOD")

    # RULE: tracking_number REQUIRED for status in [Approved, Archived] (conditional_required)
    if status in SHIPPED_STATUSES:
        order["tracking_number"] = f"TRK{''.join(random.choices(string.ascii_uppercase + string.digits, k=12))}"

    # Sometimes add notes
    if random.random() < 0.2:
        order["notes"] = fake.sentence()

    return order


def generate_customer(index: int = 0) -> dict[str, Any]:
    """Generate a customer document.

    VALIDATION RULES SATISFIED:
    - CUSTOMER: tax_id required if company_name is set (conditional_required)
    - CUSTOMER: email required for active customers (conditional_value - always set email)
    - CUSTOMER: email pattern validation (pattern - faker generates valid emails)
    """
    is_business = random.random() < 0.3

    customer = {
        "customer_number": f"CUST-{100000 + index:06d}",
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "email": fake.company_email() if is_business else fake.email(),
        "billing_address": generate_address(),
        "active": random.random() < 0.9,
        "created_date": fake.date_time_between(start_date="-3y", end_date="now").isoformat(),
    }

    # RULE: If company_name is set, tax_id is REQUIRED (conditional_required)
    # So we always add tax_id when adding company_name
    if is_business:
        customer["company_name"] = fake.company()
        customer["tax_id"] = f"TAX{random.randint(100000000, 999999999)}"

    # Sometimes add phone
    if random.random() < 0.7:
        customer["phone"] = fake.phone_number()

    # Sometimes add shipping address
    if random.random() < 0.5:
        customer["shipping_address"] = generate_address()

    # Sometimes add preferences
    if random.random() < 0.4:
        customer["preferred_currency"] = get_random_term_value("CURRENCY")
    if random.random() < 0.3:
        customer["preferred_language"] = get_random_term_value("LANGUAGE")

    # Sometimes add notes
    if random.random() < 0.1:
        customer["notes"] = fake.paragraph()

    return customer


def generate_invoice(index: int = 0) -> dict[str, Any]:
    """Generate an invoice document.

    VALIDATION RULES SATISFIED:
    - INVOICE: paid_date requires payment_method (dependency)
    - INVOICE: paid_date required for status="Approved" (conditional_required)
    - INVOICE: payment_method must be in [Credit Card, Bank Transfer, PayPal] for status="Approved" (conditional_value)
    - INVOICE: tax_rate between 0-100 (range)
    """
    # Generate line items
    num_lines = random.randint(1, 5)
    lines = [generate_order_line() for _ in range(num_lines)]

    subtotal = sum(line["line_total"] for line in lines)
    tax_rate = random.choice([0, 5, 8, 10, 15, 20])  # Always valid range 0-100
    tax_amount = round(subtotal * tax_rate / 100, 2)
    total = round(subtotal + tax_amount, 2)

    # Determine status
    statuses = ["Draft", "Pending Review", "Approved", "Archived"]
    status = random.choice(statuses)

    issue_date = fake.date_between(start_date="-60d", end_date="today")
    due_date = issue_date + timedelta(days=30)

    invoice = {
        "invoice_number": f"INV-{issue_date.strftime('%Y')}-{index:06d}",
        "customer_number": f"CUST-{random.randint(100000, 100500):06d}",
        "status": status,
        "issue_date": issue_date.isoformat(),
        "due_date": due_date.isoformat(),
        "lines": lines,
        "subtotal": subtotal,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total": total,
        "currency": get_random_term_value("CURRENCY"),
    }

    # Add order reference sometimes
    if random.random() < 0.6:
        invoice["order_number"] = f"ORD-{fake.date_this_year().strftime('%Y%m%d')}{random.randint(1, 9999):04d}"

    # RULES for status="Approved":
    # 1. paid_date REQUIRED (conditional_required)
    # 2. payment_method MUST be in allowed list (conditional_value)
    # 3. paid_date requires payment_method (dependency) - so set payment_method first
    if status == "Approved":
        # MUST use only allowed payment methods for Approved status
        invoice["payment_method"] = random.choice(INVOICE_PAYMENT_METHODS)
        invoice["paid_date"] = fake.date_between(start_date=issue_date, end_date="today").isoformat()
        invoice["payment_reference"] = f"PAY{''.join(random.choices(string.ascii_uppercase + string.digits, k=10))}"

    # Sometimes add notes
    if random.random() < 0.15:
        invoice["notes"] = fake.sentence()

    return invoice


def generate_medical_record(index: int = 0) -> dict[str, Any]:
    """Generate a medical record document.

    VALIDATION RULES SATISFIED:
    - MEDICAL_RECORD: chief_complaint required for record_type="visit" (conditional_required)
    - MEDICAL_RECORD: lab_results required for record_type="lab" (conditional_required)
    - MEDICAL_RECORD: medications required for record_type="prescription" (conditional_required)
    - MEDICAL_RECORD: follow_up_date requires diagnosis (dependency)
    - MEDICAL_RECORD: follow_up_date required for priority in [Critical, High] (conditional_value)
    """
    record_types = ["visit", "lab", "imaging", "procedure", "prescription"]
    record_type = random.choice(record_types)

    record_date = fake.date_time_between(start_date="-1y", end_date="now")

    record = {
        "patient_id": f"PAT-{10000000 + index:08d}",
        "record_date": record_date.isoformat(),
        "record_type": record_type,
        "patient_name": f"{fake.first_name()} {fake.last_name()}",
        "date_of_birth": fake.date_of_birth(minimum_age=0, maximum_age=100).isoformat(),
        "gender": get_random_term_value("GENDER"),
        "provider_name": f"Dr. {fake.last_name()}",
    }

    # Add blood type sometimes
    if random.random() < 0.6:
        record["blood_type"] = get_random_term_value("BLOOD_TYPE", use_alias=True)

    # Add allergies sometimes
    if random.random() < 0.4:
        allergies = ["Penicillin", "Peanuts", "Shellfish", "Latex", "Aspirin", "Sulfa", "Pollen"]
        record["allergies"] = random.sample(allergies, k=random.randint(1, 3))

    # RULE: Type-specific required fields (conditional_required)
    if record_type == "visit":
        # REQUIRED: chief_complaint for visit records
        record["chief_complaint"] = fake.sentence()
        record["diagnosis"] = fake.sentence()
        record["treatment_plan"] = fake.paragraph()

    elif record_type == "lab":
        # REQUIRED: lab_results for lab records
        record["lab_results"] = [
            {"test": "Blood Glucose", "value": random.randint(70, 140), "unit": "mg/dL"},
            {"test": "Hemoglobin", "value": round(random.uniform(12, 17), 1), "unit": "g/dL"},
        ]

    elif record_type == "prescription":
        # REQUIRED: medications for prescription records
        medications = ["Lisinopril 10mg", "Metformin 500mg", "Atorvastatin 20mg", "Omeprazole 20mg"]
        record["medications"] = random.sample(medications, k=random.randint(1, 3))

    # Add facility sometimes
    if random.random() < 0.7:
        record["facility"] = f"{fake.city()} Medical Center"

    # Decide on priority - but be careful with Critical/High as they require follow_up_date
    add_priority = random.random() < 0.3
    if add_priority:
        # RULE: If priority is Critical/High, follow_up_date is REQUIRED (conditional_value)
        # And follow_up_date requires diagnosis (dependency)
        priority = get_random_term_value("PRIORITY")
        record["priority"] = priority

        if priority in HIGH_PRIORITY_VALUES:
            # MUST add diagnosis first (dependency rule)
            if "diagnosis" not in record:
                record["diagnosis"] = fake.sentence()
            # MUST add follow_up_date (conditional_value with not_empty constraint)
            record["follow_up_date"] = fake.date_between(start_date="today", end_date="+30d").isoformat()
    else:
        # No priority set, but might still add follow_up_date if we have diagnosis
        if record.get("diagnosis") and random.random() < 0.5:
            record["follow_up_date"] = fake.date_between(start_date="today", end_date="+30d").isoformat()

    # Add vital signs sometimes
    if random.random() < 0.5:
        record["vital_signs"] = {
            "blood_pressure": f"{random.randint(100, 140)}/{random.randint(60, 90)}",
            "heart_rate": random.randint(60, 100),
            "temperature": round(random.uniform(97.0, 99.5), 1),
            "weight_kg": round(random.uniform(50, 120), 1)
        }

    return record


def generate_issue_ticket(index: int = 0) -> dict[str, Any]:
    """Generate an issue ticket document.

    VALIDATION RULES SATISFIED:
    - ISSUE_TICKET: resolved_at required for status="Approved" (conditional_required)
    - ISSUE_TICKET: resolution_notes required for status="Approved" (conditional_required)
    - ISSUE_TICKET: resolved_at requires assignee_email (dependency)
    """
    statuses = ["Draft", "Pending Review", "Approved", "Archived"]
    status = random.choice(statuses)

    created_at = fake.date_time_between(start_date="-90d", end_date="now")

    ticket = {
        "ticket_number": f"TKT-{100000 + index:06d}",
        "title": fake.sentence(nb_words=6)[:200],
        "description": fake.paragraph(nb_sentences=5),
        "status": status,
        "priority": get_random_term_value("PRIORITY"),
        "reporter_email": fake.email(),
        "created_at": created_at.isoformat(),
    }

    # Add severity sometimes
    if random.random() < 0.6:
        ticket["severity"] = get_random_term_value("SEVERITY")

    # RULE: resolved_at requires assignee_email (dependency)
    # So we need to add assignee_email BEFORE resolved_at
    # Add assignee for non-draft (and always for Approved since resolved_at requires it)
    if status != "Draft":
        ticket["assignee_email"] = fake.company_email()

    # Add department sometimes
    if random.random() < 0.5:
        ticket["department"] = get_random_term_value("DEPARTMENT")

    # Add tags sometimes
    if random.random() < 0.4:
        tags = ["bug", "feature", "enhancement", "documentation", "urgent", "customer", "backend", "frontend"]
        ticket["tags"] = random.sample(tags, k=random.randint(1, 3))

    # Add updated_at
    ticket["updated_at"] = fake.date_time_between(start_date=created_at, end_date="now").isoformat()

    # RULE: For Approved status, MUST have resolved_at and resolution_notes (conditional_required)
    # Note: assignee_email is already set above for non-Draft status, satisfying the dependency
    if status == "Approved":
        ticket["resolved_at"] = fake.date_time_between(start_date=created_at, end_date="now").isoformat()
        ticket["resolution_notes"] = fake.paragraph()

    return ticket


def generate_minimal(index: int = 0) -> dict[str, Any]:
    """Generate a minimal document."""
    return {"id": f"MIN-{index:06d}"}


def generate_all_types(index: int = 0) -> dict[str, Any]:
    """Generate a document with all field types."""
    return {
        "string_field": f"test-{index}",
        "number_field": round(random.uniform(-1000, 1000), 2),
        "integer_field": random.randint(-1000, 1000),
        "boolean_field": random.choice([True, False]),
        "date_field": fake.date(),  # Returns ISO string directly
        "datetime_field": fake.iso8601(),  # Returns ISO 8601 datetime string
        "term_field": get_random_term_value("GENDER"),
        "object_field": generate_address(),
        "string_array": [fake.word() for _ in range(random.randint(1, 5))],
        "number_array": [round(random.uniform(0, 100), 2) for _ in range(random.randint(1, 5))],
        "term_array": [get_random_term_value("LANGUAGE") for _ in range(random.randint(1, 3))],
        "object_array": [generate_money() for _ in range(random.randint(1, 3))],
    }


def generate_array_heavy(index: int = 0) -> dict[str, Any]:
    """Generate a document with multiple arrays."""
    return {
        "id": f"ARR-{index:06d}",
        "tags": [fake.word() for _ in range(random.randint(1, 10))],
        "scores": [round(random.uniform(0, 100), 2) for _ in range(random.randint(1, 10))],
        "counts": [random.randint(0, 1000) for _ in range(random.randint(1, 10))],
        "flags": [random.choice([True, False]) for _ in range(random.randint(1, 5))],
        "languages": [get_random_term_value("LANGUAGE") for _ in range(random.randint(1, 5))],
        "countries": [get_random_term_value("COUNTRY") for _ in range(random.randint(1, 3))],
        "addresses": [generate_address() for _ in range(random.randint(1, 3))],
        "money_amounts": [generate_money() for _ in range(random.randint(1, 3))],
    }


def generate_deep_nest(index: int = 0) -> dict[str, Any]:
    """Generate a document with deep nesting (4 levels)."""
    return {
        "root_id": f"NEST-{index:06d}",
        "level1": {
            "name": fake.word(),
            "level2": {
                "name": fake.word(),
                "level3": {
                    "name": fake.word(),
                    "level4": {
                        "name": fake.word(),
                        "value": random.randint(1, 100)
                    }
                }
            }
        }
    }


def generate_large_fields(index: int = 0) -> dict[str, Any]:
    """Generate a document with 50+ fields for performance testing."""
    doc = {"id": f"LRG-{index:06d}"}

    # Add 10 string fields
    for i in range(1, 11):
        doc[f"string_field_{i}"] = fake.sentence(nb_words=5)[:200]

    # Add 10 number fields
    for i in range(1, 11):
        doc[f"number_field_{i}"] = round(random.uniform(-1000, 1000), 2)

    # Add 10 integer fields
    for i in range(1, 11):
        doc[f"integer_field_{i}"] = random.randint(-1000, 1000)

    # Add 5 boolean fields
    for i in range(1, 6):
        doc[f"boolean_field_{i}"] = random.choice([True, False])

    # Add 5 date fields
    for i in range(1, 6):
        doc[f"date_field_{i}"] = fake.date()

    # Add 5 datetime fields
    for i in range(1, 6):
        doc[f"datetime_field_{i}"] = fake.iso8601()

    # Add 5 term fields
    term_refs = ["GENDER", "COUNTRY", "CURRENCY", "LANGUAGE", "PRIORITY"]
    for i, ref in enumerate(term_refs, 1):
        doc[f"term_field_{i}"] = get_random_term_value(ref)

    return doc


def generate_complex_rules(index: int = 0) -> dict[str, Any]:
    """Generate a document for COMPLEX_RULES template.

    VALIDATION RULES SATISFIED:
    - COMPLEX_RULES: end_date required for status="Approved" (conditional_required)
    - COMPLEX_RULES: status must be Draft/Pending Review for priority=Critical/High (conditional_value)
    - COMPLEX_RULES: exactly one of phone/mobile required (mutual_exclusion with require_one)
    - COMPLEX_RULES: end_date requires start_date (dependency)
    - COMPLEX_RULES: email pattern (pattern)
    - COMPLEX_RULES: amount between 0-1000000 (range)
    """
    # RULE: For Critical/High priority, status MUST be Draft or Pending Review (conditional_value)
    # So we need to coordinate priority and status selection
    priority = get_random_term_value("PRIORITY")
    if priority in HIGH_PRIORITY_VALUES:
        # MUST use only allowed statuses for high priority
        status = random.choice(["Draft", "Pending Review"])
    else:
        # Can use any status
        status = random.choice(["Draft", "Pending Review", "Approved", "Archived"])

    doc = {
        "id": f"CMPLX-{index:06d}",
        "status": status,
        "priority": priority,
        "email": fake.email(),  # Pattern rule - faker generates valid emails
        "amount": round(random.uniform(0, 1000000), 2),  # Range rule: 0 to 1000000
        "quantity": random.randint(1, 100),
    }

    # RULE: mutual_exclusion with require_one=True - exactly one of phone/mobile
    if random.random() < 0.5:
        doc["phone"] = fake.phone_number()
    else:
        doc["mobile"] = fake.phone_number()

    # RULE: For status="Approved", end_date is REQUIRED (conditional_required)
    # RULE: end_date requires start_date (dependency)
    if status == "Approved":
        # MUST set start_date first (dependency), then end_date (conditional_required)
        start = fake.date_between(start_date="-30d", end_date="today")
        doc["start_date"] = start.isoformat()
        doc["end_date"] = fake.date_between(start_date=start, end_date="+30d").isoformat()
    elif random.random() < 0.5:
        # Optionally add dates for other statuses
        start = fake.date_between(start_date="-30d", end_date="today")
        doc["start_date"] = start.isoformat()
        if random.random() < 0.5:
            doc["end_date"] = fake.date_between(start_date=start, end_date="+30d").isoformat()

    return doc


def generate_physical_product(index: int = 0) -> dict[str, Any]:
    """Generate a physical product document (extends PRODUCT).

    VALIDATION RULES SATISFIED:
    - PHYSICAL_PRODUCT: dimension_unit requires dimensions (dependency)
    - PHYSICAL_PRODUCT: weight required for shipping_class in [oversized, hazardous] (conditional_required - always satisfied since weight is mandatory)
    - Inherits PRODUCT rules (stock_quantity when in_stock, price range)
    """
    # Start with base product
    product = generate_product(index)

    # Physical product specific fields
    product["weight"] = round(random.uniform(0.1, 100), 3)
    product["weight_unit"] = get_random_term_value("UNIT_OF_MEASURE")
    product["requires_shipping"] = True

    # Sometimes add dimensions
    if random.random() < 0.6:
        product["dimensions"] = {
            "length": round(random.uniform(1, 100), 1),
            "width": round(random.uniform(1, 100), 1),
            "height": round(random.uniform(1, 50), 1)
        }
        # RULE: dimension_unit requires dimensions (dependency) - only add if dimensions exist
        product["dimension_unit"] = get_random_term_value("UNIT_OF_MEASURE")

    # Sometimes add shipping class
    if random.random() < 0.4:
        product["shipping_class"] = random.choice(["standard", "oversized", "fragile", "hazardous"])

    return product


def generate_digital_product(index: int = 0) -> dict[str, Any]:
    """Generate a digital product document (extends PRODUCT).

    VALIDATION RULES SATISFIED:
    - DIGITAL_PRODUCT: max_downloads required for license_type="single" (conditional_required)
    - Inherits PRODUCT rules (stock_quantity when in_stock, price range)
    """
    # Start with base product
    product = generate_product(index)

    # Digital product specific fields
    product["file_size_mb"] = round(random.uniform(0.1, 5000), 3)
    product["file_format"] = random.choice(["pdf", "epub", "mp3", "mp4", "zip", "exe", "dmg"])

    license_type = random.choice(["single", "multi", "enterprise", "subscription"])
    product["license_type"] = license_type

    # RULE: max_downloads REQUIRED for license_type="single" (conditional_required)
    if license_type == "single":
        product["max_downloads"] = random.randint(1, 10)
    elif random.random() < 0.3:
        # Optional for other license types
        product["max_downloads"] = random.randint(1, 100)

    # Sometimes add download URL
    if random.random() < 0.5:
        product["download_url"] = f"https://downloads.example.com/{product['sku']}"

    # Digital products are always in stock
    product["in_stock"] = True
    product["stock_quantity"] = 999999  # Unlimited digital inventory

    return product


def generate_billing_address(index: int = 0) -> dict[str, Any]:
    """Generate a billing address document (extends ADDRESS).

    VALIDATION RULES SATISFIED:
    - BILLING_ADDRESS: tax_id required for country in [DEU, FRA] (conditional_required)
    - BILLING_ADDRESS: country must be one of [USA, GBR, DEU, FRA, CAN, AUS, JPN] (enum)
    - Inherits ADDRESS rules (state required for USA, CAN, AUS)
    """
    # Limited countries allowed for billing (per template enum)
    allowed_countries = ["United States", "United Kingdom", "Germany", "France", "Canada", "Australia", "Japan"]
    country = random.choice(allowed_countries)

    # Get base address with the specific country
    address = generate_address(country)

    # Add billing-specific fields
    address["billing_name"] = fake.company() if random.random() < 0.5 else fake.name()

    # Sometimes add attention_to
    if random.random() < 0.3:
        address["attention_to"] = fake.name()

    # RULE: tax_id REQUIRED for EU countries (Germany, France) (conditional_required)
    if country in ["Germany", "France"]:
        address["tax_id"] = f"EU{random.randint(100000000, 999999999)}"
    elif random.random() < 0.3:
        # Optional for other countries
        address["tax_id"] = f"TAX{random.randint(100000000, 999999999)}"

    return address


# Mapping of template codes to generators
GENERATORS = {
    # Domain templates
    "PERSON": generate_person,
    "EMPLOYEE": generate_employee,
    "CONTRACTOR": generate_contractor,
    "MANAGER": generate_manager,
    "INTERN": generate_intern,
    "PRODUCT": generate_product,
    "ORDER": generate_order,
    "CUSTOMER": generate_customer,
    "INVOICE": generate_invoice,
    "MEDICAL_RECORD": generate_medical_record,
    "ISSUE_TICKET": generate_issue_ticket,
    # Inheritance templates
    "PHYSICAL_PRODUCT": generate_physical_product,
    "DIGITAL_PRODUCT": generate_digital_product,
    "BILLING_ADDRESS": generate_billing_address,
    # Edge case templates
    "MINIMAL": generate_minimal,
    "ALL_TYPES": generate_all_types,
    "ARRAY_HEAVY": generate_array_heavy,
    "DEEP_NEST": generate_deep_nest,
    "LARGE_FIELDS": generate_large_fields,
    "COMPLEX_RULES": generate_complex_rules,
}


def get_generator(template_code: str):
    """Get the generator function for a template code."""
    return GENERATORS.get(template_code)


def generate_document(template_code: str, index: int = 0) -> dict[str, Any] | None:
    """Generate a document for a given template code."""
    generator = get_generator(template_code)
    if generator:
        return generator(index)
    return None
