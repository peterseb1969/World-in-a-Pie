# WIP Tools CLI Design

A command-line toolkit for bulk data operations, migrations, and maintenance tasks on WIP instances.

## Status: Partially Implemented

**Note:** The actual implementation lives at `WIP-Toolkit/src/wip_toolkit/`.

---

## Overview

`wip-tools` is a standalone Python CLI that connects to any WIP instance via API. It provides commands for:

- Template migrations (move documents between template versions)
- Bulk data export/import
- Data validation and repair
- Term management operations
- Ad-hoc queries and transformations

## Design Principles

1. **Standalone** - No dependencies on WIP internals, uses only public APIs
2. **Safe by default** - Dry-run mode, confirmations for destructive operations
3. **Resumable** - Long operations save progress, can resume after interruption
4. **Scriptable** - JSON output mode, exit codes, works in pipelines
5. **Idempotent** - Re-running the same operation is safe

---

## Installation

```bash
# From PyPI (future)
pip install wip-tools

# From source
cd tools/wip-tools
pip install -e .

# Or run via container
podman run --rm -it wip-tools migrate ...
```

---

## Configuration

```bash
# Environment variables
export WIP_URL=https://wip-pi.local:8443
export WIP_API_KEY=your_api_key_here

# Or config file (~/.wip-tools.yaml)
default:
  url: https://localhost:8443
  api_key: dev_master_key_for_testing
  verify_ssl: true

production:
  url: https://wip.company.com
  api_key: ${WIP_PROD_API_KEY}  # Environment variable reference

# Use a profile
wip-tools --profile production migrate ...
```

---

## Command Reference

### Global Options

```bash
wip-tools [global-options] <command> [command-options]

Global Options:
  --url URL           WIP base URL (or WIP_URL env var)
  --api-key KEY       API key (or WIP_API_KEY env var)
  --profile NAME      Use named profile from config file
  --no-verify-ssl     Skip SSL certificate verification
  --dry-run           Show what would be done without making changes
  --json              Output results as JSON (for scripting)
  --verbose, -v       Increase verbosity (can repeat: -vv, -vvv)
  --quiet, -q         Suppress non-error output
  --yes, -y           Skip confirmation prompts
```

### Connection & Health

```bash
# Test connection
wip-tools ping
# Output: Connected to WIP at https://localhost:8443 (5 services healthy)

# Show service versions and status
wip-tools status
# Output:
# Service          Version    Status    Documents
# registry         1.0.0      healthy   -
# def-store        1.0.0      healthy   15 terminologies, 1,234 terms
# template-store   1.0.0      healthy   24 templates
# document-store   1.0.0      healthy   52,000 documents
# reporting-sync   1.0.0      healthy   synced (lag: 0)
```

### Template Migration

The primary use case - moving documents from one template version to another.

```bash
# Migrate all documents from PERSON v1 to PERSON v2
wip-tools migrate PERSON --from-version 1 --to-version 2

# With transformation script
wip-tools migrate PERSON --from-version 1 --to-version 2 \
  --transform ./transforms/person_v1_to_v2.py

# Dry run first
wip-tools migrate PERSON --from-version 1 --to-version 2 --dry-run

# Migrate specific documents by filter
wip-tools migrate PERSON --from-version 1 --to-version 2 \
  --filter '{"data.country": "Germany"}'

# Resume interrupted migration
wip-tools migrate --resume migration-job-abc123
```

**Transform Script Example:**

```python
# transforms/person_v1_to_v2.py

def transform(doc: dict) -> dict:
    """Transform document from PERSON v1 to v2 schema."""
    data = doc["data"]

    # v2 splits 'name' into 'first_name' and 'last_name'
    if "name" in data and "first_name" not in data:
        parts = data["name"].split(" ", 1)
        data["first_name"] = parts[0]
        data["last_name"] = parts[1] if len(parts) > 1 else ""
        del data["name"]

    # v2 renames 'gender' to 'sex'
    if "gender" in data:
        data["sex"] = data.pop("gender")

    return doc
```

**Migration Process:**

```
1. Fetch source template (validate exists)
2. Fetch target template (validate exists)
3. Query documents matching source template
4. For each document (in batches):
   a. Apply transform function (if provided)
   b. Validate against target template (dry-run: report only)
   c. Create new document with target template
   d. Mark old document as inactive (or delete if --delete-old)
   e. Record progress to state file
5. Report summary (migrated, failed, skipped)
```

### Export & Import

```bash
# Export all documents for a template
wip-tools export documents --template CUSTOMER -o customers.json

# Export with filter
wip-tools export documents --template CUSTOMER \
  --filter '{"data.status": "active"}' \
  -o active_customers.json

# Export as CSV (flattened)
wip-tools export documents --template CUSTOMER --format csv -o customers.csv

# Export terminologies
wip-tools export terminology COUNTRY -o country.json
wip-tools export terminology --all -o terminologies/

# Export templates
wip-tools export template CUSTOMER -o customer_template.json
wip-tools export template --all -o templates/

# Full instance backup
wip-tools export --all -o backup/

# Import documents
wip-tools import documents customers.json

# Import with template override (for migration)
wip-tools import documents customers.json --template CUSTOMER_V2

# Import terminology
wip-tools import terminology country.json

# Import all from backup
wip-tools import --all backup/
```

**Export Format (JSON):**

```json
{
  "export_version": "1.0",
  "exported_at": "2024-01-30T10:00:00Z",
  "source": "https://wip-pi.local:8443",
  "type": "documents",
  "template": {
    "template_id": "TPL-000001",
    "value": "CUSTOMER",
    "version": 2
  },
  "count": 1500,
  "documents": [
    {
      "document_id": "0192abc...",
      "template_id": "TPL-000001",
      "version": 1,
      "data": { ... },
      "term_references": { ... }
    }
  ]
}
```

### Validation & Repair

```bash
# Validate all documents against current templates
wip-tools validate --all
# Output:
# Validating 52,000 documents...
# CUSTOMER: 15,000 valid, 3 invalid
# PRODUCT: 8,000 valid, 0 invalid
# ...
# Total: 51,997 valid, 3 invalid

# Validate specific template
wip-tools validate --template CUSTOMER

# Show validation errors in detail
wip-tools validate --template CUSTOMER --show-errors

# Re-validate and fix (re-submit to trigger validation)
wip-tools validate --template CUSTOMER --repair

# Check referential integrity
wip-tools check-integrity
# Output:
# Checking term references...
#   3 documents reference non-existent terms
# Checking template references...
#   0 documents reference non-existent templates
# Checking identity hash consistency...
#   All hashes valid

# Repair integrity issues
wip-tools check-integrity --repair
```

### Term Operations

```bash
# List terminologies
wip-tools term list
# Output:
# COUNTRY          195 terms   active
# GENDER             3 terms   active
# DEPARTMENT        12 terms   active

# Search terms
wip-tools term search "United" --terminology COUNTRY
# Output:
# T-000042  US   United States   COUNTRY
# T-000043  GB   United Kingdom  COUNTRY
# T-000044  AE   United Arab Emirates  COUNTRY

# Rename a term value (updates term, not documents)
wip-tools term rename COUNTRY --code US --new-value "United States of America"

# Merge terms (reassign all document references)
wip-tools term merge COUNTRY --from DE --into DEU
# This will:
# 1. Find all documents referencing term DE
# 2. Update term_references to point to DEU
# 3. Optionally delete/deprecate the old term

# Add alias to term
wip-tools term add-alias COUNTRY --code US --alias "USA" --alias "U.S.A."

# Bulk add terms from CSV
wip-tools term import COUNTRY new_countries.csv
```

### Query & Analysis

```bash
# Count documents by template
wip-tools query count --by-template
# Output:
# CUSTOMER     15,000
# PRODUCT       8,000
# ORDER        29,000

# Count by field value
wip-tools query count --template CUSTOMER --by-field country
# Output:
# United States    5,000
# Germany          3,000
# United Kingdom   2,500
# ...

# Find duplicates (same identity hash, multiple active versions)
wip-tools query duplicates --template CUSTOMER

# List documents matching filter
wip-tools query list --template CUSTOMER \
  --filter '{"data.created_at": {"$gt": "2024-01-01"}}' \
  --limit 100

# Get specific document
wip-tools query get 0192abc1-def2-7abc-8def-123456789abc
```

### Bulk Updates

```bash
# Update field value in all matching documents
wip-tools bulk-update --template CUSTOMER \
  --filter '{"data.status": "pending"}' \
  --set '{"data.status": "active"}'

# Apply transformation script to matching documents
wip-tools bulk-update --template CUSTOMER \
  --filter '{"data.country": "UK"}' \
  --transform ./transforms/fix_uk_addresses.py

# Delete (soft) all matching documents
wip-tools bulk-delete --template CUSTOMER \
  --filter '{"data.status": "cancelled", "data.created_at": {"$lt": "2023-01-01"}}'
```

---

## Safety Features

### Dry Run Mode

All destructive operations support `--dry-run`:

```bash
wip-tools migrate PERSON --from-version 1 --to-version 2 --dry-run
# Output:
# DRY RUN - No changes will be made
# Would migrate 5,000 documents from PERSON v1 to PERSON v2
# Transform: ./transforms/person_v1_to_v2.py
#
# Sample transformation (first 3 documents):
# - 0192abc1... ✓ Valid
# - 0192abc2... ✓ Valid
# - 0192abc3... ✗ Invalid: missing required field 'email'
#
# Estimated: 4,998 would succeed, 2 would fail
```

### Confirmation Prompts

```bash
wip-tools bulk-delete --template CUSTOMER --filter '{"data.status": "cancelled"}'
# Output:
# This will DELETE 1,234 documents matching:
#   Template: CUSTOMER
#   Filter: {"data.status": "cancelled"}
#
# Are you sure? [y/N]:
```

Skip with `--yes` or `-y` for scripting.

### Progress & Resume

Long operations save state to allow resumption:

```bash
wip-tools migrate PERSON --from-version 1 --to-version 2
# Output:
# Migration started: migration-job-abc123
# Progress: 2,500 / 5,000 (50%)
# ^C (interrupted)

# Later:
wip-tools migrate --resume migration-job-abc123
# Output:
# Resuming migration-job-abc123 from document 2,500
# Progress: 5,000 / 5,000 (100%)
# Migration complete: 5,000 migrated, 0 failed
```

State files stored in `~/.wip-tools/jobs/`.

### Audit Trail

All operations are logged:

```bash
wip-tools history
# Output:
# 2024-01-30 10:00  migrate    PERSON v1→v2       5,000 docs  success
# 2024-01-29 15:30  export     CUSTOMER           15,000 docs success
# 2024-01-29 14:00  bulk-update PRODUCT           200 docs    success

wip-tools history --job migration-job-abc123 --detail
# Shows full details of specific job
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Connection error |
| 3 | Authentication error |
| 4 | Validation error (some documents failed) |
| 5 | User cancelled |
| 130 | Interrupted (Ctrl+C) |

---

## JSON Output Mode

For scripting and automation:

```bash
wip-tools --json query count --by-template
# Output:
{
  "success": true,
  "data": {
    "CUSTOMER": 15000,
    "PRODUCT": 8000,
    "ORDER": 29000
  }
}

wip-tools --json migrate PERSON --from-version 1 --to-version 2
# Output:
{
  "success": true,
  "job_id": "migration-job-abc123",
  "summary": {
    "total": 5000,
    "migrated": 4998,
    "failed": 2,
    "skipped": 0
  },
  "errors": [
    {"document_id": "0192abc3...", "error": "missing required field 'email'"}
  ]
}
```

---

## Implementation Structure

```
WIP-Toolkit/
├── pyproject.toml
├── README.md
├── src/wip_toolkit/
│   ├── __init__.py
│   ├── __main__.py          # Entry point
│   ├── cli.py               # Click command definitions
│   ├── client.py            # WIP API client
│   ├── config.py            # Configuration management
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── migrate.py
│   │   ├── export_import.py
│   │   ├── validate.py
│   │   ├── term.py
│   │   ├── query.py
│   │   └── bulk.py
│   ├── transforms/
│   │   ├── __init__.py
│   │   └── base.py          # Transform protocol
│   └── utils/
│       ├── __init__.py
│       ├── progress.py      # Progress bars
│       ├── jobs.py          # Job state management
│       └── output.py        # JSON/table formatting
├── tests/
│   └── ...
└── examples/
    └── transforms/
        ├── person_v1_to_v2.py
        └── fix_addresses.py
```

---

## Dependencies

```toml
[project]
dependencies = [
    "click>=8.0",           # CLI framework
    "httpx>=0.25",          # HTTP client (async support)
    "rich>=13.0",           # Pretty output, progress bars
    "pyyaml>=6.0",          # Config files
    "pydantic>=2.0",        # Data validation
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
]
```

---

## Future Enhancements

1. **Interactive mode** - REPL for exploratory operations
2. **Plugins** - Custom commands via entry points
3. **Scheduling** - Built-in cron-like scheduling for recurring tasks
4. **Diff tool** - Compare documents/templates between instances
5. **Sync tool** - Bidirectional sync between WIP instances

---

## Implementation Priority

| Command | Priority | Effort | Notes |
|---------|----------|--------|-------|
| `ping`, `status` | High | Low | Basic connectivity |
| `export` | High | Medium | Foundation for backup/migration |
| `import` | High | Medium | Paired with export |
| `migrate` | High | High | Primary use case |
| `validate` | Medium | Medium | Data quality |
| `query` | Medium | Low | Useful for exploration |
| `term` | Medium | Medium | Term management |
| `bulk-update` | Low | Medium | Power user feature |
| `check-integrity` | Low | Medium | Maintenance |
