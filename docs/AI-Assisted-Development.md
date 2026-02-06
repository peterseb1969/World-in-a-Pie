# AI-Assisted Development on WIP

## Purpose

This document defines a **strict, phased process** for an AI assistant (such as Claude) to build applications on top of World In a Pie (WIP). The AI acts as a developer; WIP acts as the backend. The AI must never modify WIP itself—only consume its APIs.

### The Golden Rule

> **Never, ever change WIP. The mission is to leverage it.**

WIP is a generic, domain-agnostic storage and reporting engine. It provides primitives (terminologies, templates, documents, reporting). The AI's job is to map a user's domain onto those primitives and build an application layer on top.

---

## Prerequisites

Before starting, the AI needs:

1. **Access to this file** and `Vision.md` in the same directory
2. **Network access** to a running WIP instance (hostname + port)
3. **An API key** or OIDC credentials for authentication
4. **A user** who knows what they want to store and why

---

## Phase 1: Exploratory

**Goal:** Understand WIP's capabilities by reading documentation and probing live APIs.

**Gate:** Do not proceed to Phase 2 until the AI can explain WIP's core concepts and has cataloged all available API endpoints.

### Step 1.1: Read All Local Documentation

Read every `.md` file in the project directory. Pay special attention to:

- **Vision.md** — Philosophy, primitives, Registry, synonym management, federation potential
- **This file** — The development process you must follow
- Any additional docs the user provides (architecture, data models, authentication)

Key concepts the AI **must** internalize:

| Concept | What It Means | Why It Matters |
|---------|---------------|----------------|
| **Registry** | Central ID generator for all entities | Every entity gets its ID from the Registry. IDs are namespaced, prefixed, and guaranteed unique |
| **Synonyms** | Multiple identifiers resolving to one entity | Legacy IDs, aliases, and external references all map to a canonical WIP ID. This is how WIP handles real-world messiness |
| **Terminologies** | Controlled vocabularies (code + value + aliases) | The building blocks of validated data. Terms are resolved by code, value, or alias |
| **Templates** | Document schemas with typed fields and validation | Define what data looks like. Fields can reference terminologies for controlled values |
| **Documents** | Validated, versioned data conforming to a template | The actual data. Identity fields determine uniqueness; same identity = new version |
| **Identity Hash** | SHA-256 of identity fields | How WIP decides if a document is new or an update to an existing one |
| **Reporting Sync** | Real-time MongoDB to PostgreSQL sync via NATS | Enables SQL queries over document data. Critical for reporting use cases |
| **Soft Delete** | Nothing is ever physically deleted | Entities are set to `status: inactive`. Historical references always resolve |

### Step 1.2: Ask the User for Connection Details

Ask the user:

1. **What is the WIP hostname?** (e.g., `localhost`, `wip-pi.local`, `wip.example.com`)
2. **What ports are the services running on?** (defaults: Registry 8001, Def-Store 8002, Template-Store 8003, Document-Store 8004, Reporting-Sync 8005)
3. **What authentication method is configured?** (API key, OIDC/JWT, or dual mode)
4. **What is the API key?** (if applicable)
5. **Is the WIP Console available?** (typically port 3000 or 8443)
6. **Is Mongo Express available?** (typically port 8081 — useful for inspecting raw data)

### Step 1.3: Catalog Available APIs

Fetch the OpenAPI/Swagger documentation from each running service:

```bash
# Fetch Swagger docs from each service
curl -s http://<hostname>:8001/docs       # Registry
curl -s http://<hostname>:8002/docs       # Def-Store
curl -s http://<hostname>:8003/docs       # Template-Store
curl -s http://<hostname>:8004/docs       # Document-Store
curl -s http://<hostname>:8005/docs       # Reporting-Sync (if available)
```

For each service, record:

- All available endpoints (method + path)
- Request/response schemas
- Required vs optional parameters
- Authentication requirements
- Bulk operation endpoints (these are important for efficiency)

**Do not rely solely on documentation.** The Swagger docs are the source of truth for what the API actually accepts.

### Step 1.4: Verify Connectivity

Test authentication and basic connectivity:

```bash
# Test API key authentication
curl -s -H "X-API-Key: <key>" http://<hostname>:8002/api/def-store/terminologies | head -20

# Check what already exists in WIP
curl -s -H "X-API-Key: <key>" http://<hostname>:8002/api/def-store/terminologies | jq '.items | length'
curl -s -H "X-API-Key: <key>" http://<hostname>:8003/api/template-store/templates | jq '.items | length'
curl -s -H "X-API-Key: <key>" http://<hostname>:8004/api/document-store/documents | jq '.total'
```

### Step 1.5: Inventory Existing Data

**This step is critical.** Before designing anything, check what already exists:

- List all terminologies and their codes
- List all templates and their codes
- Note active vs inactive entities

Do not recreate terminologies or templates that already exist in WIP. Reuse what is there. A WIP instance may already have `COUNTRY`, `GENDER`, `CURRENCY`, or other common vocabularies loaded.

---

## Phase 2: Data Model Design

**Goal:** Translate the user's domain into WIP primitives (terminologies, templates, documents).

**Gate:** Do not proceed to Phase 3 until the user has explicitly approved the proposed data model.

### Step 2.1: Gather Domain Knowledge

Ask the user:

1. **What data do you need to store?** Ask for examples, spreadsheets, JSON files, CSVs, or plain descriptions.
2. **What are the entities?** (e.g., patients, invoices, sensor readings, recipes)
3. **What fields does each entity have?** Ask for specifics: names, types, which fields have controlled values.
4. **What makes each entity unique?** These become identity fields in the template.
5. **Are there relationships between entities?** (e.g., an order has line items, a patient has visits)

If the user provides files (CSV, XLSX, JSON), analyze them to:
- Identify columns/fields and their data types
- Detect columns with repeating values (candidates for terminologies)
- Identify natural keys (candidates for identity fields)
- Note data quality issues (nulls, inconsistencies, varying formats)

### Step 2.2: Identify Terminologies

For every field with controlled/repeating values, propose a terminology:

| Field in User's Data | Proposed Terminology | Rationale |
|----------------------|---------------------|-----------|
| Status column with "Active", "Inactive", "Pending" | `ORDER_STATUS` | Controlled vocabulary, limited set |
| Country names in various formats | `COUNTRY` (may already exist) | Needs aliases for "UK", "United Kingdom", "GB" |
| Category codes | `PRODUCT_CATEGORY` | Hierarchical controlled values |

**Synonym awareness:** For each terminology, think about aliases. Real-world data is messy:
- "United Kingdom", "UK", "GB", "Great Britain" should all resolve to the same term
- "M", "Male", "Mr", "Mr." should all resolve to the same term
- Old codes from legacy systems should be registered as synonyms

### Step 2.3: Design Templates

For each entity, design a WIP template:

```
Template: <CODE>
  Name: <Human-readable name>
  Identity Fields: [<fields that determine uniqueness>]
  Fields:
    - name: <field_name>
      label: <Human-readable label>
      type: string | number | integer | date | boolean | term | array | object
      required: true | false
      terminology_ref: <TERMINOLOGY_CODE>  (for term-type fields only)
```

Key rules:
- **Every field needs a `label`** — this is required by the Template-Store
- **`type: term`** fields must reference a terminology via `terminology_ref`
- **Identity fields** determine what makes a document unique. Same identity = new version, not new document
- **Nested objects** use `type: object` with `properties`
- **Arrays** use `type: array` with `items`

### Step 2.4: Present the Data Model to the User

Present the complete proposed model:

1. List of terminologies with their terms (codes, values, aliases)
2. List of templates with all fields, types, and validation rules
3. How identity fields determine document uniqueness
4. Which existing WIP terminologies/templates will be reused
5. A diagram or table showing entity relationships

**Wait for explicit user approval before proceeding.** The data model is the foundation — getting it wrong means rework.

### Step 2.5: Document the Mapping

Create a clear mapping from the user's original data to WIP primitives:

```
User's "Customer" spreadsheet
  → Template: CUSTOMER (identity: [customer_id])
  → Fields:
      customer_id  → string, required (identity field)
      name         → string, required
      country      → term (COUNTRY terminology)
      status       → term (CUSTOMER_STATUS terminology)
      email        → string
      created_date → date
```

---

## Phase 3: Understand User Interaction Needs

**Goal:** Determine how the user wants to interact with their data and why.

**Gate:** Do not proceed to Phase 4 until interaction requirements are clear.

### Step 3.1: Ask "Why"

Ask the user:

1. **Why are you storing this data?** (compliance, analysis, operational, archival)
2. **Who will use the system?** (just you, a team, external users)
3. **How often does data change?** (real-time, daily batches, rarely)
4. **What questions do you want to answer?** (these drive reporting requirements)

### Step 3.2: Ask "How"

Ask the user about preferred interaction methods:

| Method | Best For | Complexity |
|--------|----------|------------|
| **curl / CLI scripts** | Automation, batch loading, quick testing | Low |
| **Python/Node scripts** | Programmatic access, ETL pipelines | Low-Medium |
| **Single-page web app** | Interactive data entry and browsing | Medium-High |
| **Multi-page web app** | Full application with navigation, dashboards | High |
| **WIP Console only** | Admin use, no custom app needed | Zero (already exists) |

### Step 3.3: Assess Reporting Needs

If the user needs reporting or analytics:

- **Simple queries** → Use the Document-Store table view API (`GET /api/document-store/table/<template_id>`)
- **Complex queries / joins** → Use the PostgreSQL reporting layer directly
- **Dashboards** → Consider Metabase or similar BI tools connected to PostgreSQL
- **Exports** → Generate CSV/JSON from API responses or SQL queries

### Step 3.4: Assess Authentication Needs

If the application has multiple users:

- **API key only** → Suitable for scripts, automation, single-user
- **OIDC (Dex/Authentik)** → Required for multi-user web apps with login
- **Dual mode** → API key for service calls, OIDC for user-facing access

If the user wants a web app with login, the app must integrate with the same OIDC provider that WIP uses. This is a significant architectural decision.

---

## Phase 4: Technical Design

**Goal:** Design the technical solution and get user sign-off on the implementation plan.

**Gate:** Do not proceed to Phase 5 until the user has approved the implementation plan.

### Step 4.1: Explore Technical Possibilities

Based on Phases 2 and 3, the AI has full creative freedom to propose solutions. Everything is on the table:

- Simple bash scripts with curl commands
- Python CLI tool using `httpx` or `requests`
- Node.js Express server with server-side rendering
- Vue/React SPA calling WIP APIs directly
- Multi-page application with routing, auth, dashboards
- Jupyter notebooks for data analysis
- Combined approaches (script for data loading + web app for querying)

### Step 4.2: Discuss Trade-Offs with the User

Present options with honest trade-offs:

- **Simplicity vs features** — A curl script loads data in 10 minutes; a web app takes hours
- **Maintenance burden** — More code means more to maintain
- **User skill level** — Match the solution to the user's technical comfort
- **Iteration speed** — Start simple, add complexity as needed

### Step 4.3: Create an Implementation Plan

Once the user agrees on the approach, create a detailed plan:

1. **What will be built** — Clear deliverables
2. **Technology choices** — Languages, frameworks, libraries
3. **File structure** — Where code will live
4. **Implementation phases** — Ordered steps (see Phase 5)
5. **What the user will be able to do** after each phase

### Step 4.4: Define Data Ingestion Strategy

Based on data volume:

| Volume | Strategy | Notes |
|--------|----------|-------|
| < 100 records | Direct REST API calls | Simple, synchronous |
| 100 - 10,000 | Bulk API endpoints | Use batch endpoints with appropriate sizes |
| 10,000 - 100,000 | Bulk with throttling | Batch size ~1000, registry batch ~50 |
| 100,000+ | Ingest Gateway (NATS JetStream) | Async, persistent, highest throughput |

**Important:** For large imports, tune batch sizes to avoid timeouts. WIP's bulk endpoints accept `batch_size` and `registry_batch_size` parameters.

---

## Phase 5: Test-Driven Implementation

**Goal:** Build the solution incrementally, testing every API interaction before relying on it.

**Gate:** Each sub-phase must be verified before moving to the next.

### The Cardinal Rule of Phase 5

> **Test every API call with curl before writing code that depends on it.**

Do not assume behavior from Swagger documentation. APIs may have undocumented requirements (like the `label` field on template fields), validation rules, or error responses that only surface when tested.

### Sub-Phase 5.1: Create Terminologies

**Order matters.** Terminologies must exist before templates can reference them.

For each terminology in the data model:

```bash
# 1. Check if it already exists
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8002/api/def-store/terminologies?code=<CODE>" | jq .

# 2. Create if it doesn't exist
curl -s -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  "http://<hostname>:8002/api/def-store/terminologies" \
  -d '{"code": "<CODE>", "name": "<Name>", "description": "<Description>"}' | jq .

# 3. Add terms (bulk)
curl -s -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  "http://<hostname>:8002/api/def-store/terminologies/<ID>/terms/bulk" \
  -d '{"terms": [{"code": "...", "value": "...", "aliases": ["..."]}]}' | jq .

# 4. Verify terms were created
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8002/api/def-store/terminologies/<ID>/terms" | jq '.items | length'
```

**Tell the user:** "You can verify these in WIP Console at `https://<hostname>:8443` under Terminologies."

### Sub-Phase 5.2: Create Templates

Templates must exist before documents can be created.

```bash
# 1. Create the template
curl -s -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  "http://<hostname>:8003/api/template-store/templates" \
  -d '{
    "code": "<CODE>",
    "name": "<Name>",
    "identity_fields": ["<field1>", "<field2>"],
    "fields": [
      {"name": "<field>", "label": "<Label>", "type": "<type>", "required": true}
    ]
  }' | jq .

# 2. Verify the template
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8003/api/template-store/templates/<ID>" | jq .
```

**Tell the user:** "You can see the template structure in WIP Console under Templates."

### Sub-Phase 5.3: Test Document Creation

Before loading bulk data, test with a single document:

```bash
# 1. Create one test document
curl -s -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  "http://<hostname>:8004/api/document-store/documents" \
  -d '{
    "template_id": "<TPL-ID>",
    "data": { ... }
  }' | jq .

# 2. Verify it was created
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8004/api/document-store/documents/<DOC-ID>" | jq .

# 3. Check term resolution worked
# Look for "term_references" in the response — this confirms terms resolved correctly
```

If the test document fails, fix the issue before proceeding to bulk loading.

### Sub-Phase 5.4: Load Data

Once single-document creation is verified, load the actual data:

- Use bulk endpoints for efficiency
- Implement progress reporting (log every N records)
- Handle errors gracefully — log failures, continue with valid records
- For large datasets, respect batch size limits

**Tell the user:** "You can monitor document creation in real-time via WIP Console under Documents, or check MongoDB Express at `http://<hostname>:8081` if available."

### Sub-Phase 5.5: Verify Data Integrity

After loading:

```bash
# Count documents by template
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8004/api/document-store/documents?template_id=<TPL-ID>" | jq '.total'

# Check table view works
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8004/api/document-store/table/<TPL-ID>?limit=5" | jq .

# If reporting sync is active, check PostgreSQL
# (Data should appear in PostgreSQL within seconds of creation)
```

### Sub-Phase 5.6: Build the Application Layer

Now build whatever was agreed in Phase 4:

- **Start with read operations** — query and display data before building write operations
- **Test each feature** against the live WIP instance as you build it
- **Use WIP's validation** — let the API reject bad data rather than duplicating validation logic
- **Handle WIP error responses** — parse error details and present them to the user

### Sub-Phase 5.7: Verify Reporting (If Applicable)

If the user needs SQL access:

```bash
# Check if reporting sync is active
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8005/api/reporting-sync/health" | jq .

# Query PostgreSQL directly
psql -h <hostname> -U wip -d wip_reporting -c "SELECT count(*) FROM <table>;"
```

---

## Common Pitfalls

| Pitfall | Prevention |
|---------|------------|
| Creating terminologies that already exist | Always check existing data first (Phase 1.5) |
| Missing `label` field on template fields | Every field requires both `name` and `label` |
| Wrong creation order | Terminologies → Terms → Templates → Documents. Always. |
| Assuming API behavior from docs | Test with curl first. Always. |
| Huge batch without tuning | Use `batch_size=1000` and `registry_batch_size=50` for large imports |
| Trying to delete data | WIP uses soft-delete only. Deactivate, don't delete. |
| Ignoring term aliases | Real data is messy. Plan aliases when creating terminologies. |
| Forgetting identity fields | Without proper identity fields, every submission creates a new document instead of a new version |
| Modifying WIP code | Never. Build on top of it, not inside it. |
| Skipping user approval | Always wait for explicit sign-off at Phase 2 and Phase 4 gates. |

---

## Template Versioning Awareness

When a template is updated, WIP creates a **new template_id** with an incremented version. The original template remains active. This means:

- Existing documents keep their original template reference
- New documents can use either version
- The AI should always use the latest template version for new documents
- Migration between template versions is the application's responsibility, not WIP's

---

## Quick Reference: API Authentication

All API calls require authentication via one of:

```
# API Key (header)
-H "X-API-Key: <your-api-key>"

# JWT Bearer Token (header)
-H "Authorization: Bearer <jwt-token>"
```

The API key is simpler for development and scripts. OIDC/JWT is required for user-facing applications with login.

---

## Quick Reference: Creation Order

```
1. Check existing terminologies and templates (reuse what exists)
          ↓
2. Create terminologies (controlled vocabularies)
          ↓
3. Create terms within each terminology (codes, values, aliases)
          ↓
4. Create templates (document schemas referencing terminologies)
          ↓
5. Test with a single document (verify everything works)
          ↓
6. Bulk load documents
          ↓
7. Verify data integrity (counts, table view, reporting)
          ↓
8. Build application layer
```

---

## Summary

This process exists because:

1. **WIP is powerful but generic** — the AI must understand its primitives before building on them
2. **Data models are hard to change** — get user approval before implementing
3. **APIs have undocumented behaviors** — test before you code
4. **Order matters** — terminologies before templates before documents
5. **The user is the domain expert** — the AI is the technical implementer

Follow the phases. Respect the gates. Test everything. Never change WIP.
