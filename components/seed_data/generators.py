"""
Faker-based data generators for realistic test data.

Provides generators for each template type that produce valid,
realistic data for testing.
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


def get_term_code(terminology_code: str) -> str:
    """Get a random term code from a terminology."""
    terminology = terminologies.get_terminology_by_code(terminology_code)
    if not terminology:
        return ""

    term = random.choice(terminology["terms"])
    return term["code"]


def generate_address() -> dict[str, Any]:
    """Generate a realistic address."""
    country_code = random.choice(["USA", "GBR", "DEU", "FRA", "CAN", "AUS"])
    country_names = {
        "USA": "United States",
        "GBR": "United Kingdom",
        "DEU": "Germany",
        "FRA": "France",
        "CAN": "Canada",
        "AUS": "Australia"
    }

    address = {
        "street": fake.street_address(),
        "city": fake.city(),
        "postal_code": fake.postcode(),
        "country": country_names[country_code]
    }

    # Add state for countries that require it
    if country_code in ["USA", "CAN", "AUS"]:
        address["state"] = fake.state()  # Faker's state() works for all locales

    # Sometimes add street2
    if random.random() < 0.2:
        address["street2"] = f"Apt {random.randint(1, 999)}"

    return address


def generate_contact_info() -> dict[str, Any]:
    """Generate contact information."""
    contact = {
        "email": fake.email(),
    }

    # Add phone or mobile (at least one required by rules)
    if random.random() < 0.5:
        contact["phone"] = fake.phone_number()
    else:
        contact["mobile"] = fake.phone_number()

    # Sometimes add preferred contact
    if random.random() < 0.3:
        if "mobile" in contact:
            contact["preferred_contact"] = "mobile"
        elif "phone" in contact:
            contact["preferred_contact"] = "phone"
        else:
            contact["preferred_contact"] = "email"

    return contact


def generate_money(min_amount: float = 10, max_amount: float = 10000) -> dict[str, Any]:
    """Generate a money amount."""
    return {
        "currency": get_random_term_value("CURRENCY"),
        "amount": round(random.uniform(min_amount, max_amount), 2)
    }


def generate_person(index: int = 0) -> dict[str, Any]:
    """Generate a person document."""
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

    # Optionally add age instead of birth_date (test conditional_required)
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
    """Generate an employee document (extends PERSON)."""
    # Start with person data
    person = generate_person(index)

    # Add employee-specific fields
    person["employee_id"] = f"EMP-{100000 + index:06d}"
    person["hire_date"] = fake.date_between(start_date="-10y", end_date="today").isoformat()
    person["department"] = get_random_term_value("DEPARTMENT")
    person["job_title"] = fake.job()
    person["employment_type"] = get_random_term_value("EMPLOYMENT_TYPE")

    # Add manager_id for non-HR/Legal departments
    if person["department"] not in ["Human Resources", "Legal"]:
        person["manager_id"] = f"EMP-{random.randint(100000, 100050):06d}"

    # Add salary for full-time employees
    if person["employment_type"] == "Full-time":
        person["salary"] = generate_money(min_amount=40000, max_amount=200000)

    # Sometimes add termination date
    if random.random() < 0.1:
        hire_date = date.fromisoformat(person["hire_date"])
        term_date = fake.date_between(start_date=hire_date, end_date="today")
        person["termination_date"] = term_date.isoformat()
        person["active"] = False

    return person


def generate_contractor(index: int = 0) -> dict[str, Any]:
    """Generate a contractor document (extends PERSON)."""
    person = generate_person(index)

    person["contractor_id"] = f"CON-{100000 + index:06d}"
    person["contract_start"] = fake.date_between(start_date="-2y", end_date="today").isoformat()
    person["department"] = get_random_term_value("DEPARTMENT")
    person["hourly_rate"] = generate_money(min_amount=50, max_amount=500)

    # Sometimes add company name
    if random.random() < 0.6:
        person["company_name"] = fake.company()

    # Sometimes add contract end
    if random.random() < 0.5:
        start = date.fromisoformat(person["contract_start"])
        end = start + timedelta(days=random.randint(90, 365))
        person["contract_end"] = end.isoformat()

    return person


def generate_manager(index: int = 0) -> dict[str, Any]:
    """Generate a manager document (extends EMPLOYEE -> PERSON)."""
    employee = generate_employee(index)

    # Add manager-specific fields
    employee["management_level"] = random.choice(["team_lead", "manager", "director", "vp", "c_level"])

    # Add direct reports
    if random.random() < 0.8:
        num_reports = random.randint(1, 10)
        employee["direct_reports"] = [f"EMP-{random.randint(100000, 100500):06d}" for _ in range(num_reports)]

    # Add budget authority for director+
    if employee["management_level"] in ["director", "vp", "c_level"]:
        employee["budget_authority"] = generate_money(min_amount=100000, max_amount=10000000)

    return employee


def generate_intern(index: int = 0) -> dict[str, Any]:
    """Generate an intern document (extends EMPLOYEE -> PERSON)."""
    employee = generate_employee(index)

    # Override employment type
    employee["employment_type"] = "Intern"

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
    """Generate a product document."""
    categories = ["ELEC", "CLOTH", "HOME", "SPORT", "BOOK", "BEAUTY", "FOOD", "TOYS"]
    category = random.choice(categories)

    product = {
        "sku": f"{category[:2]}-{random.randint(10000, 99999)}",
        "name": fake.catch_phrase(),
        "description": fake.paragraph(nb_sentences=3),
        "category": terminologies.PRODUCT_CATEGORY["terms"][categories.index(category)]["value"],
        "price": round(random.uniform(9.99, 999.99), 2),
        "currency": get_random_term_value("CURRENCY"),
        "in_stock": random.random() < 0.85,
    }

    # Add stock quantity if in stock
    if product["in_stock"]:
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
    """Generate an order document."""
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

    # Add billing address sometimes
    if random.random() < 0.3:
        order["billing_address"] = generate_address()

    # Add payment method for approved orders
    if status in ["Approved", "Archived"]:
        order["payment_method"] = get_random_term_value("PAYMENT_METHOD")

    # Add tracking for shipped orders
    if status in ["Approved", "Archived"]:
        order["tracking_number"] = f"TRK{''.join(random.choices(string.ascii_uppercase + string.digits, k=12))}"

    # Sometimes add notes
    if random.random() < 0.2:
        order["notes"] = fake.sentence()

    return order


def generate_customer(index: int = 0) -> dict[str, Any]:
    """Generate a customer document."""
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

    # Add company info for business customers
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
    """Generate an invoice document."""
    # Generate line items
    num_lines = random.randint(1, 5)
    lines = [generate_order_line() for _ in range(num_lines)]

    subtotal = sum(line["line_total"] for line in lines)
    tax_rate = random.choice([0, 5, 8, 10, 15, 20])
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

    # Add payment info for paid invoices
    if status == "Approved":
        invoice["paid_date"] = fake.date_between(start_date=issue_date, end_date="today").isoformat()
        invoice["payment_method"] = random.choice(["Credit Card", "Bank Transfer", "PayPal"])
        invoice["payment_reference"] = f"PAY{''.join(random.choices(string.ascii_uppercase + string.digits, k=10))}"

    # Sometimes add notes
    if random.random() < 0.15:
        invoice["notes"] = fake.sentence()

    return invoice


def generate_medical_record(index: int = 0) -> dict[str, Any]:
    """Generate a medical record document."""
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

    # Add type-specific fields
    if record_type == "visit":
        record["chief_complaint"] = fake.sentence()
        record["diagnosis"] = fake.sentence()
        record["treatment_plan"] = fake.paragraph()

    elif record_type == "lab":
        record["lab_results"] = [
            {"test": "Blood Glucose", "value": random.randint(70, 140), "unit": "mg/dL"},
            {"test": "Hemoglobin", "value": round(random.uniform(12, 17), 1), "unit": "g/dL"},
        ]

    elif record_type == "prescription":
        medications = ["Lisinopril 10mg", "Metformin 500mg", "Atorvastatin 20mg", "Omeprazole 20mg"]
        record["medications"] = random.sample(medications, k=random.randint(1, 3))

    # Add facility sometimes
    if random.random() < 0.7:
        record["facility"] = f"{fake.city()} Medical Center"

    # Add priority sometimes
    if random.random() < 0.3:
        record["priority"] = get_random_term_value("PRIORITY")

    # Add follow-up for high priority or diagnosis
    if record.get("priority") in ["Critical", "High"] or record.get("diagnosis"):
        if random.random() < 0.7:
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
    """Generate an issue ticket document."""
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

    # Add assignee for non-draft
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

    # Add resolution for resolved tickets
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


# Mapping of template codes to generators
GENERATORS = {
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
    "MINIMAL": generate_minimal,
    "ALL_TYPES": generate_all_types,
    "ARRAY_HEAVY": generate_array_heavy,
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
