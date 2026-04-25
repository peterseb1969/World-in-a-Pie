# WIP Test Data Generator

Comprehensive test data generation module for World In a Pie (WIP) services.

## Overview

This module provides realistic test data for:
- **Def-Store**: 15 terminologies with 210+ terms
- **Template Store**: 24 templates with inheritance and validation rules
- **Document Store**: Configurable document volumes for testing

## Installation

Install both deps into the project venv:

```bash
.venv/bin/pip install -r components/seed_data/requirements.txt
```

## Quick Start

The script (v1.3+) always routes through the Caddy proxy at
`https://<host>:8443` — wip-deploy v2 no longer publishes service
ports to the host. The API key is auto-discovered from
`~/.wip-deploy/<deployment>/secrets/api-key` when running against
localhost; for remote hosts, pass `--api-key` or `--api-key-file`.

```bash
# From project root — uses the local wip-dev-local deployment's api-key
python scripts/seed_comprehensive.py

# Dry run (preview what will be created)
python scripts/seed_comprehensive.py --dry-run

# Minimal profile for quick testing
python scripts/seed_comprehensive.py --profile minimal

# Remote host (must supply key explicitly)
python scripts/seed_comprehensive.py --host wip-pi.local \
    --api-key-file /secure/wip-pi.api-key

# Pick a specific local deployment when several exist
python scripts/seed_comprehensive.py --deployment wip-staging-local
```

## Data Profiles

| Profile | Terminologies | Terms | Templates | Documents | Use Case |
|---------|---------------|-------|-----------|-----------|----------|
| minimal | 15 | ~210 | 24 | 50 | Quick dev testing |
| standard | 15 | ~210 | 24 | 500 | Functional testing |
| full | 15 | ~210 | 24 | 2,000 | Comprehensive coverage |
| performance | 15 | ~210 | 24 | 100,000 | Benchmarking |

## CLI Options

```bash
python scripts/seed_comprehensive.py [options]

Options:
  --host HOSTNAME       WIP host (default: localhost or WIP_HOST env)
  --port PORT           Caddy proxy port (default: 8443; use 443 for K8s Ingress)
  --api-key KEY         API key (overrides --api-key-file, WIP_API_KEY, auto-discovery)
  --api-key-file PATH   Read the key from this file (single line)
  --deployment NAME     Pick a wip-deploy deployment under ~/.wip-deploy/ when several exist
  --profile PROFILE     Data profile: minimal, standard, full, performance
  --services SERVICES   Comma-separated: all, def-store, template-store, document-store
  --skip-terminologies  Skip terminology seeding (use existing)
  --skip-templates      Skip template seeding (use existing)
  --benchmark           Run performance benchmarks after seeding
  --output FILE         Write benchmark results to JSON file
  --dry-run             Preview without making changes
```

API key resolution (first hit wins):
1. `--api-key`
2. `--api-key-file`
3. `WIP_API_KEY` env var
4. `~/.wip-deploy/<deployment>/secrets/api-key` — auto-pick the
   deployment if exactly one exists; require `--deployment` if more
   than one. Skipped when `--host` is non-localhost.

## Examples

```bash
# Seed only terminologies
python scripts/seed_comprehensive.py --services def-store

# Seed documents using existing terminologies/templates
python scripts/seed_comprehensive.py --skip-terminologies --skip-templates

# Run performance benchmarks
python scripts/seed_comprehensive.py --profile performance --benchmark --output results.json

# Target specific services
python scripts/seed_comprehensive.py --services def-store,template-store
```

## Module Structure

```
seed_data/
├── __init__.py          # Module exports
├── terminologies.py     # 15 terminology definitions
├── templates.py         # 24 template definitions
├── generators.py        # Faker-based data generators
├── documents.py         # Document generation configs
├── performance.py       # Benchmarking utilities
└── requirements.txt     # Dependencies
```

## Terminologies

| Code | Name | Terms | Special Features |
|------|------|-------|------------------|
| SALUTATION | Salutations | 5 | Aliases (Mr, MR, Mr., MR. -> same term) |
| GENDER | Gender | 4 | Multi-language translations |
| COUNTRY | Countries | 54 | ISO 3166 codes with aliases |
| CURRENCY | Currencies | 30 | ISO 4217 with symbols |
| LANGUAGE | Languages | 20 | ISO 639-1 codes |
| DOC_STATUS | Document Status | 5 | Workflow states with colors |
| PRIORITY | Priority Levels | 5 | With sort_order and weights |
| DEPARTMENT | Departments | 15 | Hierarchical (parent_term_id) |
| PRODUCT_CATEGORY | Product Categories | 20 | E-commerce taxonomy |
| PAYMENT_METHOD | Payment Methods | 8 | Transaction types |
| EMPLOYMENT_TYPE | Employment Types | 6 | HR classifications |
| MARITAL_STATUS | Marital Status | 5 | Personal data |
| BLOOD_TYPE | Blood Types | 8 | Healthcare with compatibility metadata |
| UNIT_OF_MEASURE | Units of Measure | 25 | SI and Imperial with conversions |
| SEVERITY | Severity Levels | 5 | Issue tracking |

## Templates

### Base Templates
- **ADDRESS** - Reusable address structure
- **CONTACT_INFO** - Email/phone with validation
- **MONEY** - Currency and amount pair

### Domain Templates
- **PERSON** - Base person with all field types
- **PRODUCT** - E-commerce product
- **ORDER** - Order with line items array
- **CUSTOMER** - CRM customer record
- **INVOICE** - Financial invoice
- **MEDICAL_RECORD** - Healthcare record
- **ISSUE_TICKET** - Support ticket

### Inheritance Templates
```
PERSON (base)
├── EMPLOYEE (extends PERSON)
│   ├── MANAGER (extends EMPLOYEE) → 3-level inheritance
│   └── INTERN (extends EMPLOYEE)
└── CONTRACTOR (extends PERSON)

PRODUCT (base)
├── PHYSICAL_PRODUCT (extends PRODUCT)
└── DIGITAL_PRODUCT (extends PRODUCT)

ADDRESS (base)
└── BILLING_ADDRESS (extends ADDRESS)
```

### Edge Case Templates
- **MINIMAL** - Single field (testing simplicity)
- **ALL_TYPES** - One of each field type
- **DEEP_NEST** - 4 levels of nesting
- **LARGE_FIELDS** - 50+ fields (performance)
- **COMPLEX_RULES** - All 6 validation rule types
- **ARRAY_HEAVY** - Multiple array fields

## Field Types Covered

| Type | Example Templates | Validation |
|------|-------------------|------------|
| string | PERSON.name | pattern, min/max length |
| number | MONEY.amount | min/max value |
| integer | PERSON.age | min/max value |
| boolean | PERSON.active | type check |
| date | PERSON.birth_date | YYYY-MM-DD format |
| datetime | ORDER.created_at | ISO 8601 |
| term | PERSON.gender | Terminology validation |
| object | PERSON.address | Nested template |
| array | ORDER.lines | Array of terms/objects |

## Validation Rules Covered

| Rule Type | Example |
|-----------|---------|
| conditional_required | EMPLOYEE: department set -> manager_id required |
| conditional_value | INVOICE: status=paid -> payment_method in [CARD, BANK] |
| mutual_exclusion | CONTACT_INFO: phone XOR mobile |
| dependency | EMPLOYEE: end_date requires start_date |
| pattern | PERSON: email format validation |
| range | PRODUCT: price between 0.01 and 999999.99 |

## Benchmarking

The benchmark mode measures:

| Operation | Target | Description |
|-----------|--------|-------------|
| create_document | <100ms | Single document creation |
| get_document | <50ms | Document retrieval |
| list_documents | <200ms | Paginated listing |
| validate_document | <150ms | Validation without save |
| query_documents | <500ms | Complex queries |
| bulk_create_100 | <2s | Batch creation |
| term_validation | <50ms | Term lookup |
| template_resolution | <100ms | Inheritance resolution |

### Benchmark Output

```
WIP Performance Benchmark Results
==================================
Date: 2024-01-30
Profile: standard

Operation               p50     p95     p99     ops/sec   Status
--------------------------------------------------------------
create_document        45ms    89ms   120ms      22.2     PASS
get_document           12ms    25ms    35ms      83.3     PASS
list_documents         85ms   150ms   200ms      11.8     PASS
term_validation         8ms    15ms    22ms     125.0     PASS
template_resolution    25ms    45ms    65ms      40.0     PASS
```

## Programmatic Usage

```python
from seed_data import terminologies, templates, generators, documents

# Get all terminology definitions
all_terms = terminologies.get_terminology_definitions()

# Get a specific terminology
salutation = terminologies.get_terminology_by_code("SALUTATION")

# Get all template definitions
all_templates = templates.get_template_definitions()

# Generate a person document
person = generators.generate_person(index=0)

# Generate documents for a template
docs = documents.generate_documents_for_template("PERSON", count=100)

# Get document counts for a profile
counts = documents.get_document_counts("standard")
```

## Extending

### Adding a New Terminology

Edit `terminologies.py`:

```python
MY_TERMINOLOGY = {
    "code": "MY_TERMINOLOGY",
    "name": "My Terminology",
    "description": "Description here",
    "case_sensitive": False,
    "terms": [
        {"code": "T1", "value": "Term One", "label": "Term One", "aliases": ["t1"]},
        {"code": "T2", "value": "Term Two", "label": "Term Two"},
    ]
}

# Add to get_terminology_definitions() return list
```

### Adding a New Template

Edit `templates.py`:

```python
MY_TEMPLATE = {
    "code": "MY_TEMPLATE",
    "name": "My Template",
    "identity_fields": ["id"],
    "fields": [
        {"name": "id", "type": "string", "mandatory": True},
        {"name": "name", "type": "string", "mandatory": True},
    ],
    "rules": []
}

# Add to get_template_definitions() return list
```

### Adding a Document Generator

Edit `generators.py`:

```python
def generate_my_template(index: int = 0) -> dict[str, Any]:
    return {
        "id": f"MY-{index:06d}",
        "name": fake.name(),
    }

# Add to GENERATORS dict
GENERATORS["MY_TEMPLATE"] = generate_my_template
```

## Requirements

- Python 3.11+
- faker >= 22.0.0
- requests >= 2.31.0

## Services Required

Before running, ensure the wip-deploy stack is up. Caddy must be
reachable on `https://<host>:8443` (or `:443` for K8s Ingress) and
the `/api/<service>/health` endpoints for registry, def-store,
template-store, document-store, and reporting-sync must return 200.

Quick check:
```bash
podman ps | grep wip-
curl -ksw "%{http_code}\n" https://localhost:8443/api/def-store/health -o /dev/null
```

The script checks each service's health before seeding and aborts
with a clear message if anything is unreachable.
