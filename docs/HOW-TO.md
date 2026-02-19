# WIP HOW-TO: Complete API Reference with Examples

Hands-on guide with curl examples for every WIP operation. All examples build on each other — start at the top and work down.

**Prerequisites:** A running WIP instance and an API key.

```bash
# Set these once for all examples
export API_KEY="dev_master_key_for_testing"
export HOST="http://localhost"
```

---

## Table of Contents

1. [Bulk-First Convention](#1-bulk-first-convention)
2. [Namespaces](#2-namespaces)
3. [Terminologies](#3-terminologies)
4. [Terms](#4-terms)
5. [Templates](#5-templates)
6. [Documents](#6-documents)
7. [Files](#7-files)
8. [Registry Operations](#8-registry-operations)
9. [Search & Reporting](#9-search--reporting)
10. [Validation](#10-validation)

---

## 1. Bulk-First Convention

**Every write endpoint accepts a JSON array. Single operations are `[item]`. Responses are always HTTP 200 with per-item results.**

```json
// Request body — always a JSON array
[{"value": "GENDER", "label": "Gender"}]

// Response — always BulkResponse
{
  "results": [
    {"index": 0, "status": "created", "id": "019abc12-..."}
  ],
  "total": 1,
  "succeeded": 1,
  "failed": 0
}
```

**Rules:**
- Never check HTTP status for business errors — check `results[i].status == "error"` instead
- Updates use PUT with the entity ID **in the body**, not the URL
- Deletes use DELETE with a body containing `[{"id": "..."}]`
- GET endpoints are NOT bulk — single-entity GET and paginated list GET work normally

---

## 2. Namespaces

Every entity lives in a namespace. The default `wip` namespace is created by `initialize-wip`.

### Initialize default namespaces

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8001/api/registry/namespaces/initialize-wip" | jq .
```

### Create a custom namespace

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/namespaces" \
  -d '{
    "prefix": "clinic",
    "description": "Clinical data namespace",
    "created_by": "admin"
  }' | jq .
```

### List namespaces

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8001/api/registry/namespaces" | jq '.[].prefix'
```

### Get namespace stats

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8001/api/registry/namespaces/wip/stats" | jq .
# Returns: entity_counts by type (terminologies, terms, templates, documents, files)
```

---

## 3. Terminologies

Terminologies are controlled vocabularies. They must exist before templates can reference them.

### Create a terminology

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies" \
  -d '[{
    "value": "COUNTRY",
    "label": "Country",
    "description": "ISO countries",
    "namespace": "wip"
  }]' | jq .

# Response:
# {
#   "results": [{"index": 0, "status": "created", "id": "019..."}],
#   "total": 1, "succeeded": 1, "failed": 0
# }
```

Save the terminology ID:
```bash
COUNTRY_TERM_ID=$(curl -s -X POST -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies" \
  -d '[{"value": "CURRENCY", "label": "Currency", "namespace": "wip"}]' \
  | jq -r '.results[0].id')

echo "Currency terminology ID: $COUNTRY_TERM_ID"
```

### Create multiple terminologies at once

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies" \
  -d '[
    {"value": "GENDER", "label": "Gender", "namespace": "wip"},
    {"value": "DOC_STATUS", "label": "Document Status", "namespace": "wip"}
  ]' | jq .
```

### List terminologies

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies?namespace=wip" | jq '.items[] | {terminology_id, value, term_count}'
```

### Get a terminology by value

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies/by-value/COUNTRY" | jq .
```

### Get a terminology by ID

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies/$TERMINOLOGY_ID" | jq .
```

### Update a terminology

```bash
curl -s -X PUT -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies" \
  -d '[{
    "terminology_id": "'"$TERMINOLOGY_ID"'",
    "label": "Country (ISO 3166)",
    "description": "ISO 3166-1 country codes"
  }]' | jq .
```

### Delete a terminology (soft)

```bash
curl -s -X DELETE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies" \
  -d '[{"id": "'"$TERMINOLOGY_ID"'"}]' | jq .

# Force delete (even if templates reference it):
# -d '[{"id": "...", "force": true}]'
```

### Restore a deleted terminology

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies/$TERMINOLOGY_ID/restore?restore_terms=true" | jq .
```

---

## 4. Terms

Terms are values within a terminology. They support aliases for fuzzy matching.

### Create terms in a terminology

First, get the terminology ID:
```bash
COUNTRY_ID=$(curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies/by-value/COUNTRY" | jq -r '.terminology_id')
```

Create terms (bulk):
```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies/$COUNTRY_ID/terms" \
  -d '[
    {"value": "Germany", "aliases": ["DE", "DEU", "deutschland"], "label": "Germany"},
    {"value": "United Kingdom", "aliases": ["UK", "GB", "GBR", "Great Britain"], "label": "UK"},
    {"value": "United States", "aliases": ["US", "USA", "America"], "label": "USA"}
  ]' | jq .
```

### Create terms with batch tuning (large imports)

For 10k+ terms, use batch parameters:
```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terminologies/$COUNTRY_ID/terms?batch_size=1000&registry_batch_size=50" \
  -d '[
    {"value": "France", "aliases": ["FR", "FRA"]},
    {"value": "Italy", "aliases": ["IT", "ITA"]}
  ]' | jq .
```

### List terms in a terminology

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies/$COUNTRY_ID/terms?page=1&page_size=50" \
  | jq '.items[] | {term_id, value, aliases}'
```

### Search terms

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terminologies/$COUNTRY_ID/terms?search=united" \
  | jq '.items[] | {term_id, value}'
```

### Get a term by ID

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8002/api/def-store/terms/$TERM_ID" | jq .
```

### Update terms

```bash
curl -s -X PUT -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terms" \
  -d '[{
    "term_id": "'"$TERM_ID"'",
    "aliases": ["DE", "DEU", "deutschland", "BRD"],
    "label": "Federal Republic of Germany"
  }]' | jq .
```

### Deprecate a term

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terms/deprecate" \
  -d '[{
    "term_id": "'"$OLD_TERM_ID"'",
    "reason": "Replaced by more specific term",
    "replaced_by_term_id": "'"$NEW_TERM_ID"'"
  }]' | jq .
```

### Delete terms (soft)

```bash
curl -s -X DELETE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/terms" \
  -d '[{"id": "'"$TERM_ID"'"}]' | jq .
```

---

## 5. Templates

Templates define document schemas. Create templates for referenced entities before templates that reference them.

### Create a simple template

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "value": "CUSTOMER",
    "label": "Customer",
    "description": "Customer records",
    "namespace": "wip",
    "identity_fields": ["customer_id"],
    "fields": [
      {"name": "customer_id", "label": "Customer ID", "type": "string", "mandatory": true},
      {"name": "name", "label": "Company Name", "type": "string", "mandatory": true},
      {"name": "email", "label": "Email", "type": "string", "semantic_type": "email"},
      {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"}
    ],
    "reporting": {"sync_enabled": true, "sync_strategy": "latest_only"}
  }]' | jq .

# Response includes: "id" (template_id), "version": 1, "status": "created"
```

Save the template ID:
```bash
CUSTOMER_TPL=$(curl -s -X POST -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "value": "CUSTOMER",
    "label": "Customer",
    "namespace": "wip",
    "identity_fields": ["customer_id"],
    "fields": [
      {"name": "customer_id", "label": "Customer ID", "type": "string", "mandatory": true},
      {"name": "name", "label": "Company Name", "type": "string", "mandatory": true},
      {"name": "email", "label": "Email", "type": "string", "semantic_type": "email"},
      {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"}
    ],
    "reporting": {"sync_enabled": true, "sync_strategy": "latest_only"}
  }]' | jq -r '.results[0].id')

echo "Customer template ID: $CUSTOMER_TPL"
```

### Create a template with references

The INVOICE template references CUSTOMER — so CUSTOMER must exist first:

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "value": "INVOICE",
    "label": "Invoice",
    "namespace": "wip",
    "identity_fields": ["invoice_number"],
    "fields": [
      {"name": "invoice_number", "label": "Invoice Number", "type": "string", "mandatory": true},
      {
        "name": "customer",
        "label": "Customer",
        "type": "reference",
        "reference_type": "document",
        "target_templates": ["CUSTOMER"],
        "mandatory": true
      },
      {"name": "amount", "label": "Amount", "type": "number", "mandatory": true},
      {"name": "currency", "label": "Currency", "type": "term", "terminology_ref": "CURRENCY"},
      {"name": "issue_date", "label": "Issue Date", "type": "date", "mandatory": true},
      {"name": "notes", "label": "Notes", "type": "string"}
    ],
    "reporting": {"sync_enabled": true, "sync_strategy": "latest_only"}
  }]' | jq .
```

### Create a template with inheritance

Child templates inherit parent fields:

```bash
# Parent template
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "value": "BASE_ENTITY",
    "label": "Base Entity",
    "namespace": "wip",
    "identity_fields": ["entity_code"],
    "fields": [
      {"name": "entity_code", "label": "Entity Code", "type": "string", "mandatory": true},
      {"name": "description", "label": "Description", "type": "string"},
      {"name": "status", "label": "Status", "type": "term", "terminology_ref": "DOC_STATUS"}
    ]
  }]' | jq .

# Child template — extends BASE_ENTITY, adds its own fields
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "value": "FACILITY",
    "label": "Facility",
    "namespace": "wip",
    "extends": "'"$BASE_ENTITY_TPL"'",
    "fields": [
      {"name": "address", "label": "Address", "type": "string"},
      {"name": "capacity", "label": "Capacity", "type": "integer"}
    ]
  }]' | jq .

# Pin to a specific parent version (optional):
# "extends_version": 1
```

### Create a draft template (skip reference validation)

Drafts allow circular dependencies and order-independent creation:

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "value": "DRAFT_EXAMPLE",
    "label": "Draft Example",
    "namespace": "wip",
    "status": "draft",
    "fields": [
      {"name": "ref_field", "label": "Ref", "type": "reference", "reference_type": "document", "target_templates": ["NOT_YET_CREATED"]}
    ]
  }]' | jq .

# Activate when ready (validates all references):
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/$DRAFT_TPL_ID/activate" | jq .
```

### List templates

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates?namespace=wip&latest_only=true" \
  | jq '.items[] | {template_id, value, version, status}'
```

### Get a template by value

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/by-value/CUSTOMER" | jq .
```

### Get a template by ID (resolved — includes inherited fields)

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/$TEMPLATE_ID" | jq .
```

### Get a template raw (own fields only, no inheritance resolution)

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/$TEMPLATE_ID/raw" | jq .
```

### Get all versions of a template

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/by-value/CUSTOMER/versions" \
  | jq '.items[] | {version, status, fields: (.fields | length)}'
```

### Update a template (creates new version)

```bash
curl -s -X PUT -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{
    "template_id": "'"$CUSTOMER_TPL"'",
    "fields": [
      {"name": "customer_id", "label": "Customer ID", "type": "string", "mandatory": true},
      {"name": "name", "label": "Company Name", "type": "string", "mandatory": true},
      {"name": "email", "label": "Email", "type": "string", "semantic_type": "email"},
      {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"},
      {"name": "phone", "label": "Phone", "type": "string"}
    ]
  }]' | jq .

# Response includes: "version": 2, "is_new_version": true
```

**Important:** When updating a child template that uses inheritance, send ONLY the child's own fields. Including inherited fields turns them into overrides, breaking inheritance.

### Check template dependencies

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/$TEMPLATE_ID/dependencies" | jq .
# Returns: child_template_count, document_count
```

### Cascade parent update to children

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8003/api/template-store/templates/$PARENT_TPL_ID/cascade" | jq .
```

### Delete a template (soft)

```bash
curl -s -X DELETE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates" \
  -d '[{"id": "'"$TEMPLATE_ID"'"}]' | jq .

# Force delete (even if documents exist):
# -d '[{"id": "...", "force": true}]'
```

---

## 6. Documents

Documents store validated, versioned data conforming to a template.

### Create a document

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$CUSTOMER_TPL"'",
    "namespace": "wip",
    "data": {
      "customer_id": "CUS-001",
      "name": "Acme Corp",
      "email": "billing@acme.com",
      "country": "Germany"
    },
    "created_by": "admin"
  }]' | jq .

# Response:
# {
#   "results": [{
#     "index": 0,
#     "status": "created",
#     "id": "019...",
#     "document_id": "019...",
#     "identity_hash": "a1b2c3...",
#     "version": 1,
#     "is_new": true,
#     "warnings": []
#   }],
#   "total": 1, "succeeded": 1, "failed": 0
# }
```

### Create a document with synonyms (external IDs)

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$CUSTOMER_TPL"'",
    "namespace": "wip",
    "data": {
      "customer_id": "CUS-002",
      "name": "Widget Inc",
      "email": "hello@widget.com",
      "country": "UK"
    },
    "synonyms": [
      {"erp_id": "SAP-WIDGET-001"},
      {"salesforce_id": "SF-00099"}
    ]
  }]' | jq .
```

### Create a document with a reference

The INVOICE references a CUSTOMER. You can reference by:
- **Document ID** (UUID7) — direct lookup
- **Business key** (identity field value) — e.g., `"CUS-001"`
- **Synonym** (external ID) — e.g., `"SAP-WIDGET-001"`

```bash
# Reference by business key (customer_id value)
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$INVOICE_TPL"'",
    "namespace": "wip",
    "data": {
      "invoice_number": "INV-2024-001",
      "customer": "CUS-001",
      "amount": 1500.00,
      "currency": "Euro",
      "issue_date": "2024-06-15"
    }
  }]' | jq .
```

```bash
# Reference by synonym (external ID)
# Note: synonym resolution uses Registry lookup. The synonym must be registered
# for the same entity (see "Create a document with synonyms" above).
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$INVOICE_TPL"'",
    "namespace": "wip",
    "data": {
      "invoice_number": "INV-2024-002",
      "customer": "SAP-WIDGET-001",
      "amount": 2300.00,
      "currency": "USD",
      "issue_date": "2024-07-01"
    }
  }]' | jq .
```

### Create multiple documents (bulk)

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents?continue_on_error=true" \
  -d '[
    {
      "template_id": "'"$CUSTOMER_TPL"'",
      "data": {"customer_id": "CUS-003", "name": "Delta Ltd", "country": "France"}
    },
    {
      "template_id": "'"$CUSTOMER_TPL"'",
      "data": {"customer_id": "CUS-004", "name": "Epsilon GmbH", "country": "DE"}
    }
  ]' | jq .
```

### Versioning — update by resubmitting same identity

If the template has `identity_fields: ["customer_id"]`, submitting a document with the same `customer_id` creates a **new version** rather than a new document:

```bash
# Version 1: original
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$CUSTOMER_TPL"'",
    "data": {"customer_id": "CUS-001", "name": "Acme Corp", "email": "old@acme.com", "country": "Germany"}
  }]' | jq '{status: .results[0].status, version: .results[0].version, is_new: .results[0].is_new}'
# → {"status": "created", "version": 1, "is_new": true}

# Version 2: same customer_id, different data
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$CUSTOMER_TPL"'",
    "data": {"customer_id": "CUS-001", "name": "Acme Corp", "email": "new@acme.com", "country": "Germany"}
  }]' | jq '{status: .results[0].status, version: .results[0].version, is_new: .results[0].is_new}'
# → {"status": "updated", "version": 2, "is_new": false}
# Same document_id, same identity_hash — version incremented
```

### Get a document by ID

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents/$DOCUMENT_ID" | jq .
```

### Get a specific version

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents/$DOCUMENT_ID/versions/1" | jq .
```

### Get version history

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents/$DOCUMENT_ID/versions" | jq .
# Returns: identity_hash, current_version, versions[]
```

### Get document by identity hash

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents/by-identity/$IDENTITY_HASH" | jq .
```

### List documents

```bash
# By namespace
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents?namespace=wip&page=1&page_size=50" \
  | jq '{total, pages, items: [.items[] | {document_id, template_value, version}]}'

# By template (ID or value)
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents?template_id=$CUSTOMER_TPL" | jq .

curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/documents?template_value=CUSTOMER" | jq .
```

### Query documents (complex filters)

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents/query" \
  -d '{
    "template_id": "'"$INVOICE_TPL"'",
    "filters": [
      {"field": "data.amount", "operator": "gte", "value": 1000},
      {"field": "data.currency", "operator": "eq", "value": "Euro"}
    ],
    "sort_by": "created_at",
    "sort_order": "desc",
    "page": 1,
    "page_size": 20
  }' | jq .
```

Available operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`, `exists`, `regex`

### Delete documents (soft)

```bash
curl -s -X DELETE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{"id": "'"$DOCUMENT_ID"'"}]' | jq .
```

### Archive documents

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents/archive" \
  -d '[{"id": "'"$DOCUMENT_ID"'", "archived_by": "admin"}]' | jq .
```

---

## 7. Files

Files are binary objects stored in MinIO. Upload first, then reference from documents.

### Upload a file

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files" \
  -F "file=@/path/to/contract.pdf" \
  -F "namespace=wip" \
  -F "description=Signed contract" \
  -F "tags=legal,contracts" \
  -F "category=contracts" | jq '{file_id, filename, content_type, size_bytes, status}'

# status will be "orphan" until linked to a document
```

### Link a file to a document

Use the file ID in a document's `type: "file"` field:

```bash
# Assuming the template has a field: {"name": "contract", "type": "file", ...}
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/documents" \
  -d '[{
    "template_id": "'"$CONTRACT_TPL"'",
    "data": {
      "contract_number": "CON-001",
      "contract": "'"$FILE_ID"'"
    }
  }]' | jq .

# The file status changes from "orphan" to "active"
```

### List files

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files?namespace=wip" \
  | jq '.items[] | {file_id, filename, status, reference_count}'
```

### Get file metadata

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files/$FILE_ID" | jq .
```

### Download a file

```bash
# Get a pre-signed URL (default: 1 hour expiry)
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files/$FILE_ID/download" | jq .

# Or stream directly
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files/$FILE_ID/content" -o downloaded_file.pdf
```

### Update file metadata

```bash
curl -s -X PATCH -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/files" \
  -d '[{
    "file_id": "'"$FILE_ID"'",
    "tags": ["legal", "contracts", "signed"],
    "category": "signed-contracts"
  }]' | jq .
```

### List orphan files

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files/orphans/list" | jq '.[].file_id'
```

### Delete files (soft, then hard)

```bash
# Soft-delete
curl -s -X DELETE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/files" \
  -d '[{"id": "'"$FILE_ID"'"}]' | jq .

# Hard-delete (permanent — only works on inactive files)
curl -s -X DELETE -H "X-API-Key: $API_KEY" \
  "$HOST:8004/api/document-store/files/$FILE_ID/hard" | jq .
```

---

## 8. Registry Operations

The Registry is the central ID generator and identity resolver. Every entity gets its ID from the Registry.

### Browse registry entries

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8001/api/registry/entries?namespace=wip&entity_type=documents&page=1&page_size=10" \
  | jq '.items[] | {entry_id, entity_type, primary_composite_key}'
```

### Search the registry

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8001/api/registry/entries/search?q=CUS-001&namespace=wip" | jq .
```

### Get entry detail

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8001/api/registry/entries/$ENTRY_ID" | jq .

# Returns: entry_id, primary_composite_key, synonyms[], search_values[], ...
```

### Register a composite key

This is the low-level call that services use internally. You can also use it directly:

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/register" \
  -d '[{
    "namespace": "wip",
    "entity_type": "documents",
    "composite_key": {"external_system": "legacy", "legacy_id": "OLD-42"},
    "created_by": "migration-script"
  }]' | jq .

# Response:
# {
#   "results": [{
#     "input_index": 0,
#     "status": "created",
#     "registry_id": "019abc...",
#     "namespace": "wip",
#     "entity_type": "documents"
#   }],
#   "total": 1, "created": 1, "already_exists": 0, "errors": 0
# }
```

**Empty composite key = always generates a new ID** (no dedup):
```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/register" \
  -d '[{
    "namespace": "wip",
    "entity_type": "documents",
    "composite_key": {}
  }]' | jq .
```

**Same composite key = returns existing ID** (upsert):
```bash
# First call: creates new entry
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/register" \
  -d '[{"namespace": "wip", "entity_type": "terms", "composite_key": {"terminology": "COUNTRY", "value": "Germany"}}]' \
  | jq '.results[0] | {status, registry_id}'
# → {"status": "created", "registry_id": "019abc..."}

# Second call: returns same ID
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/register" \
  -d '[{"namespace": "wip", "entity_type": "terms", "composite_key": {"terminology": "COUNTRY", "value": "Germany"}}]' \
  | jq '.results[0] | {status, registry_id}'
# → {"status": "already_exists", "registry_id": "019abc..."} — same ID
```

### Lookup by ID

Resolves any identifier — canonical ID, synonym value, or merged ID:

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/lookup/by-id" \
  -d '[
    {"entry_id": "019abc12-def3-7abc-..."},
    {"entry_id": "SAP-WIDGET-001", "namespace": "wip", "entity_type": "documents"}
  ]' | jq '.results[] | {status, entry_id, matched_via}'

# Optional filters: "namespace" and "entity_type" constrain the search
# matched_via: "entry_id" (direct) or "composite_key_value" (synonym/search_values)
```

### Lookup by composite key

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/lookup/by-key" \
  -d '[{
    "namespace": "wip",
    "entity_type": "documents",
    "composite_key": {"identity_hash": "a1b2c3...", "template_id": "019..."},
    "search_synonyms": true
  }]' | jq .
```

### Add a synonym

Register an additional identifier for an existing entry:

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/synonyms/add" \
  -d '[{
    "target_id": "'"$DOCUMENT_ID"'",
    "synonym_namespace": "wip",
    "synonym_entity_type": "documents",
    "synonym_composite_key": {"vendor_id": "VENDOR-X-42"},
    "created_by": "integration-script"
  }]' | jq .

# Now "VENDOR-X-42" resolves to the same document via lookup/by-id
```

### Remove a synonym

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/synonyms/remove" \
  -d '[{
    "target_id": "'"$DOCUMENT_ID"'",
    "synonym_namespace": "wip",
    "synonym_entity_type": "documents",
    "synonym_composite_key": {"vendor_id": "VENDOR-X-42"}
  }]' | jq .
```

### Merge two entries

Merge moves all synonyms from the deprecated entry to the preferred entry:

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/synonyms/merge" \
  -d '[{
    "preferred_id": "'"$PREFERRED_DOC_ID"'",
    "deprecated_id": "'"$DEPRECATED_DOC_ID"'"
  }]' | jq .

# After merge: looking up the deprecated_id resolves to preferred_id
```

### Provision IDs (registry generates)

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/provision" \
  -d '{
    "namespace": "wip",
    "entity_type": "documents",
    "count": 5
  }' | jq '.ids[] | {entry_id, status}'

# Returns 5 reserved IDs. Activate them after entity creation.
```

### Reserve client-provided IDs

The ID must match the configured format for the entity type (e.g., UUID7 for documents):

```bash
# Use one of the provisioned IDs, or generate a UUID7
RESERVE_ID="019abc12-def3-7abc-8000-000000000001"

curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/reserve" \
  -d '[{
    "entry_id": "'"$RESERVE_ID"'",
    "namespace": "wip",
    "entity_type": "documents"
  }]' | jq .
```

### Activate reserved entries

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries/activate" \
  -d '[{"entry_id": "'"$RESERVE_ID"'"}]' | jq .
```

### Update registry entries

```bash
curl -s -X PUT -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries" \
  -d '[{
    "entry_id": "'"$ENTRY_ID"'",
    "metadata": {"imported_from": "legacy-system"},
    "updated_by": "migration"
  }]' | jq .
```

### Delete registry entries (soft)

```bash
curl -s -X DELETE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8001/api/registry/entries" \
  -d '[{"entry_id": "'"$ENTRY_ID"'"}]' | jq .
```

---

## 9. Search & Reporting

The Reporting-Sync service provides unified search, activity tracking, and integrity checks.

### Unified search (across all entity types)

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8005/api/reporting-sync/search" \
  -d '{
    "query": "Acme",
    "types": ["terminology", "term", "template", "document", "file"],
    "limit": 20
  }' | jq '.results[] | {type, id, value}'
```

### Recent activity

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/activity/recent?limit=20" | jq .
```

### Find documents referencing a term

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/references/term/$TERM_ID/documents?limit=100" | jq .
```

### Find entities referencing a target entity

```bash
# What references this template?
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/entity/template/$TEMPLATE_ID/referenced-by?limit=100" | jq .

# What references this terminology?
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/entity/terminology/$TERMINOLOGY_ID/referenced-by" | jq .
```

### Integrity check

```bash
# Quick check (last 5000 documents)
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/health/integrity?document_limit=5000&recent_first=true" | jq .

# Full check (all documents — may take minutes for 500k+ docs)
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/health/integrity?document_limit=0" | jq .
```

### Reporting sync status

```bash
# Health check (root-level, not under /api prefix)
curl -s "$HOST:8005/health" | jq .

# Sync worker status
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/status" | jq .

# Metrics (events/sec, consumer lag, latency)
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/metrics" | jq .

# Consumer metrics (NATS stream details)
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/metrics/consumer" | jq .
```

### Trigger batch sync

```bash
# Sync a specific template
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/sync/batch/CUSTOMER?force=true" | jq .

# Sync all templates
curl -s -X POST -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/sync/batch?force=true" | jq .

# Check sync job status
curl -s -H "X-API-Key: $API_KEY" \
  "$HOST:8005/api/reporting-sync/sync/batch/jobs" | jq .
```

---

## 10. Validation

### Validate a term value

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/validate" \
  -d '{
    "terminology_value": "COUNTRY",
    "value": "UK"
  }' | jq '{valid, matched_term: .matched_term.value, matched_via}'

# → {"valid": true, "matched_term": "United Kingdom", "matched_via": "alias"}
```

### Validate multiple term values

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8002/api/def-store/validate/bulk" \
  -d '{
    "items": [
      {"terminology_value": "COUNTRY", "value": "Germany"},
      {"terminology_value": "COUNTRY", "value": "Narnia"},
      {"terminology_value": "CURRENCY", "value": "EUR"}
    ]
  }' | jq '{total, valid_count, invalid_count, results: [.results[] | {value, valid}]}'
```

### Validate a document (dry run — no save)

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8004/api/document-store/validation/validate" \
  -d '{
    "template_id": "'"$CUSTOMER_TPL"'",
    "data": {
      "customer_id": "TEST-001",
      "name": "Test Corp",
      "country": "InvalidCountry"
    }
  }' | jq '{valid, errors, warnings}'
```

### Validate a template's references

```bash
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  "$HOST:8003/api/template-store/templates/$TEMPLATE_ID/validate" \
  -d '{"check_terminologies": true, "check_templates": true}' | jq .
```

---

## Pagination

All list endpoints use the same pattern:

| Parameter | Default | Maximum |
|-----------|---------|---------|
| `page` | 1 | — |
| `page_size` | 50 | 100 |

Response always includes:
```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "page_size": 50,
  "pages": 3
}
```

To iterate all pages:
```bash
PAGE=1
while true; do
  RESPONSE=$(curl -s -H "X-API-Key: $API_KEY" \
    "$HOST:8002/api/def-store/terminologies?page=$PAGE&page_size=100")
  ITEMS=$(echo "$RESPONSE" | jq '.items | length')
  echo "Page $PAGE: $ITEMS items"
  [ "$ITEMS" -eq 0 ] && break
  PAGE=$((PAGE + 1))
done
```

---

## Authentication

All endpoints (except `/health`) require one of:

```bash
# API Key
-H "X-API-Key: dev_master_key_for_testing"

# JWT Bearer Token (from OIDC/Dex)
-H "Authorization: Bearer eyJ..."
```

---

## Quick Reference: Creation Order

```
1. Initialize namespaces
2. Create terminologies
3. Create terms within terminologies
4. Create templates for referenced entities (e.g., CUSTOMER)
5. Create templates for referencing entities (e.g., INVOICE → CUSTOMER)
6. Upload files (if templates have file fields)
7. Create referenced documents (e.g., customers)
8. Create referencing documents (e.g., invoices)
9. Verify integrity
```

## Quick Reference: Entity Endpoints

| Entity | Create | List | Get | Update | Delete |
|--------|--------|------|-----|--------|--------|
| **Namespace** | `POST /api/registry/namespaces` | `GET /api/registry/namespaces` | `GET /api/registry/namespaces/{prefix}` | `PUT /api/registry/namespaces/{prefix}` | `DELETE /api/registry/namespaces/{prefix}` |
| **Terminology** | `POST /api/def-store/terminologies` | `GET /api/def-store/terminologies` | `GET /api/def-store/terminologies/{id}` | `PUT /api/def-store/terminologies` | `DELETE /api/def-store/terminologies` |
| **Term** | `POST /api/def-store/terminologies/{tid}/terms` | `GET /api/def-store/terminologies/{tid}/terms` | `GET /api/def-store/terms/{id}` | `PUT /api/def-store/terms` | `DELETE /api/def-store/terms` |
| **Template** | `POST /api/template-store/templates` | `GET /api/template-store/templates` | `GET /api/template-store/templates/{id}` | `PUT /api/template-store/templates` | `DELETE /api/template-store/templates` |
| **Document** | `POST /api/document-store/documents` | `GET /api/document-store/documents` | `GET /api/document-store/documents/{id}` | — (resubmit) | `DELETE /api/document-store/documents` |
| **File** | `POST /api/document-store/files` | `GET /api/document-store/files` | `GET /api/document-store/files/{id}` | `PATCH /api/document-store/files` | `DELETE /api/document-store/files` |
| **Registry** | `POST /api/registry/entries/register` | `GET /api/registry/entries` | `GET /api/registry/entries/{id}` | `PUT /api/registry/entries` | `DELETE /api/registry/entries` |
