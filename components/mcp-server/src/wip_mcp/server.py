"""WIP MCP Server — exposes World In a Pie as MCP tools and resources.

This server is designed for AI-assisted development workflows. An AI building
applications on top of WIP connects to this server to discover, create, and
query WIP entities without constructing raw HTTP calls.

Run with:  python -m wip_mcp.server          (stdio, for Claude Code / Cursor)
           python -m wip_mcp.server --http    (HTTP streamable, for K8s / remote)
           python -m wip_mcp.server --sse     (SSE, legacy — deprecated in MCP spec)
"""

import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import BulkError, WipClient

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "wip",
    instructions=(
        "World In a Pie (WIP) — a universal template-driven document storage "
        "system. Use these tools to discover WIP's data model, create "
        "terminologies and templates, store documents, and query data. "
        "WIP uses a bulk-first API: all write operations accept arrays and "
        "return per-item results. This MCP server handles the bulk envelope "
        "for you — single-item calls return the unwrapped result directly. "
        "KEY CAPABILITIES: (1) Terms support ontology relationships (is_a, "
        "part_of, etc.) for hierarchical data modeling — use create_relationships "
        "and get_term_hierarchy. (2) A PostgreSQL reporting layer enables SQL "
        "aggregations, cross-template JOINs, and analytics via run_report_query. "
        "IMPORTANT: Before creating any templates or documents, read the "
        "wip://conventions and wip://data-model resources. WIP has several "
        "non-obvious behaviours (multi-version templates, identity-based "
        "dedup, bulk-first 200 OK) that will cause subtle bugs if you "
        "rely on conventional assumptions."
    ),
)

_client: WipClient | None = None


def get_client() -> WipClient:
    global _client
    if _client is None:
        _client = WipClient()
    return _client


def _error(e: Exception) -> str:
    """Format an exception for MCP tool output."""
    if isinstance(e, BulkError):
        return f"WIP error: {e.error}"
    return f"Error: {e}"


# ===================================================================
# Resources — static context for AI consumers
# ===================================================================


@mcp.resource("wip://conventions")
def get_conventions() -> str:
    """WIP API conventions: bulk-first, versioning, namespaces, authorization."""
    return """# WIP API Conventions

## Bulk-First: 200 OK Always
WIP's write APIs accept arrays and return BulkResponse with per-item results.
This MCP server handles the bulk envelope for you — single-item tool calls
unwrap the response and return the result directly. Bulk tools (create_documents_bulk,
create_templates_bulk, create_terminologies_bulk) return the full BulkResponse.

CRITICAL: When using bulk tools, always check results[i].status — a 200 OK
response can contain per-item errors. Statuses: created, updated, unchanged,
error, skipped. Error items carry a machine-readable `error_code` in addition
to a human `error` message — branch on the code, not the message string.

## Partial Updates: update_document (PATCH)
The update_document tool applies an RFC 7396 JSON Merge Patch to an existing
document and creates a new version on success. Use this instead of
create_document when you want to change a subset of fields without rewriting
the whole document.

Semantics:
- Objects deep-merge, arrays replace wholesale, null deletes a field
- Empty patch or a no-op patch returns status "unchanged" (no new version)
- Identity fields CANNOT be changed via PATCH — you get error_code
  `identity_field_change`. To change identity, POST a new document.
- `if_match=N` is optional per-item optimistic concurrency control: if the
  current version != N, you get error_code `concurrency_conflict`.
- template_version and identity_hash are preserved — the new version validates
  against the template version recorded on the document, not the latest.

Error codes from PATCH: `not_found`, `forbidden`, `archived`,
`identity_field_change`, `concurrency_conflict`, `validation_failed`,
`reference_violation`, `internal_error`.

When to PATCH vs create_document:
- Use update_document when the identity is unchanged and you're correcting or
  enriching existing fields (e.g., adding a phone number, updating a status).
- Use create_document when you have a full document payload OR the identity
  may change (create_document is still an upsert — same identity = new version).

## Idempotent Bootstrap (apps installing themselves)
Two endpoints exist so an app can provision its namespace and templates against
a fresh WIP instance and re-run the same script repeatedly without ugly
GET → 404 → POST dances or silent schema drift.

### Namespace upsert: PUT /api/registry/namespaces/{prefix}
PUT is an upsert — creates the namespace on missing using platform defaults
(isolation_mode='open', deletion_mode='retain', description='',
allowed_external_refs=[]); updates supplied fields when existing. Always 200 OK.
Idempotent: re-running with the same body is a no-op.

### Template create with conflict validation: POST /templates?on_conflict=validate
Adds a query parameter to control collision behavior on (namespace, value):
- on_conflict='error' (default): collisions return per-item status='error',
  preserving the existing behavior.
- on_conflict='validate':
  * identical schema → status='unchanged' (returns existing template_id/version)
  * compatible schema → status='updated', is_new_version=true, version=N+1
    (compatible = added optional field only — anything else is incompatible)
  * incompatible schema → status='error', error_code='incompatible_schema',
    details={added_required, removed, changed_type, made_required,
    modified_existing, identity_changed}

The narrow compatibility rule is intentional: silent guardrails are worse than
loud ones. If the bootstrap script wants to evolve the template in a way the
platform considers incompatible, it must explicitly bump the version itself.

## Querying Documents
Two primary query tools:
- query_by_template(template_value, field_filters) — the most common way to
  query documents. Filters on field values, auto-resolves template_value to ID.
- run_report_query(sql) — raw SQL against PostgreSQL reporting tables (doc_*).
  Use for cross-template JOINs, aggregations, and complex analytics.

For a spreadsheet-like view: get_table_view(template_value).
For CSV export: export_table_csv(template_value).

## Soft Delete — Inactive Means Retired, Not Deleted
Entities are never hard-deleted, only set to status: "inactive".
(Exception: files support hard-delete to reclaim storage.)

Retired entities are invisible to new data but always resolve for existing data.
A document referencing term "ACTIVE" will always resolve, even if "ACTIVE" was
retired years later. Historical data never breaks.

WIP enforces this: inactive terms are rejected in new documents (only active
terms match during validation). You do not need to check term status yourself.

## Identity & Versioning

### Templates: Multiple Active Versions Coexist
Updating a template creates a NEW version. The previous version stays active.
This is by design — schema evolution without migration. But it means two versions
of the same template can accept documents simultaneously.

After updating, deactivate the old version if you don't need it:
deactivate_template(template_id, version=N). Otherwise, always pass
template_version when creating documents — without it, WIP resolves
"latest active", which may not be the version you expect when multiple
versions are active.

### Documents: Identity Fields Control Dedup
Templates define identity_fields. WIP hashes those fields to decide:
same hash = new version (update), different hash = new document (create).
The same create_document tool handles both — it's an upsert.

- Zero identity fields = every submission creates a new document (append-only, no update path)
- Too many identity fields = corrections create duplicates instead of versions
- Never add timestamps or per-run data to identity fields — it makes every
  hash unique, creating duplicates instead of versions
- Avoid timestamps in non-identity fields too — they trigger unnecessary
  version updates on otherwise unchanged documents

### Terms
- (namespace, terminology_id, value) — unique within terminology

## Namespaces & Authorization

All entities are scoped to a namespace.

### Permission Model
Namespaces have permission grants (read, write, admin) assigned to users or groups.
Superadmins (wip-admins group) bypass all checks. Users without a grant on a
namespace get 404 (not 403) — the namespace's existence is not leaked.

### Cross-Namespace References
Isolation mode controls what a namespace can reference:
- **open** (default): own namespace + "wip" namespace + allowed_external_refs
- **strict**: only own namespace + explicit allowed_external_refs (wip NOT automatic)

Cross-namespace term references work without grants on the referenced namespace.
Shared vocabularies (in "wip") are the common language — you need a grant to
list or modify a namespace's data, but not to reference its terms.

Reference validation runs at document creation, not template creation.

### API Key Namespace Scoping
Non-privileged API keys MUST have an explicit `namespaces` list. Keys without
namespace scoping that are not in `wip-admins` or `wip-services` get no access
(all namespaces appear as 404).

**Single-namespace keys** get automatic namespace derivation: when the caller
omits the `namespace` parameter, WIP derives it from the key's single namespace.
This means synonym resolution works without passing `namespace` on every request.

**Multi-namespace keys** must provide `namespace` explicitly on every call.
Omitting it means WIP cannot determine context for synonym resolution, and raw
values pass through unresolved.

This matters for apps: use a single-namespace key scoped to your dev namespace,
and you can skip the `namespace` parameter on all API and MCP tool calls.

## Ontology Relationships
Terms can be connected via typed relationships to model hierarchies and
associations. This is powerful for taxonomies, classification trees, org charts,
part-of-whole relationships, and any domain with inherent structure.

Available relationship types: is_a, part_of, has_part, regulates,
positively_regulates, negatively_regulates. Custom types can be added via the
_ONTOLOGY_RELATIONSHIP_TYPES terminology.

Key tools:
- create_relationships — connect terms (e.g., "Cat is_a Animal")
- list_relationships — see connections for a term
- get_term_hierarchy — traverse ancestors, descendants, parents, children
- import_terminology with OBO Graph JSON — bulk-load entire ontologies

When to use: If a terminology has natural parent-child or part-whole structure
(species taxonomy, disease classification, org hierarchy, geographic containment),
model it with ontology relationships rather than flat term lists.

## Reporting & Aggregation (PostgreSQL)
WIP syncs document data to PostgreSQL tables (one per template: doc_*).
This enables SQL queries for aggregation, cross-template JOINs, and analytics
that the document API does not support.

Key tools:
- list_report_tables — discover available tables and their columns
- run_report_query — execute SQL (SELECT only) with parameterised queries

The reporting layer requires the reporting-sync service (included in "standard"
and "full" presets, not in "core"). Data syncs within seconds of document changes.

## Template Cache
Template changes may take up to 5 seconds to propagate (cache TTL on "latest"
resolution). Lookups by explicit version are cached permanently (immutable).
If a template update seems to have no effect, wait or pass the explicit version.

## Pagination
Default page_size: 50, max: 100. List responses include a `pages` field
(computed as ceil(total / page_size)).
"""


@mcp.resource("wip://data-model")
def get_data_model() -> str:
    """WIP core data model: terminologies, terms, templates, documents, registry."""
    return """# WIP Core Data Model

## Terminologies
A terminology is a controlled vocabulary (e.g., COUNTRY, GENDER, DIAGNOSIS_CODE).
- Fields: value (unique code), label (display name), description
- Terms belong to a terminology

## Terms
A term is an entry in a terminology (e.g., "GB" in COUNTRY, "Male" in GENDER).
- Fields: value (unique within terminology), label, aliases, description
- Terms can have ontology relationships (see Ontology section below)
- Documents store both the original value AND the resolved term_id
- Inactive terms are rejected in new documents (enforced by validation)

## Templates
A template defines a document schema — like a form definition.
- Templates support inheritance (extends another template)
- Templates are versioned: same template_id, incrementing version
- Multiple versions can be active simultaneously — see conventions resource
- Templates can define identity_fields for document deduplication

### Field Types
string, number, integer, boolean, date, datetime, term, reference, file, array, object

### Semantic Types
Fields can declare a semantic_type for validation and reporting hints:
email, url, latitude, longitude, percentage, duration, geo_point

Example: a string field with semantic_type "email" is validated as an email address.

### Reference Field Types
A reference field's reference_type determines what it points to:
- **document**: references another document (e.g., invoice → customer record)
- **term**: references a term in a terminology (e.g., country field → COUNTRY terminology)
- **terminology**: references a terminology itself (rare — for meta-schemas or config)
- **template**: references a template (e.g., for typed document-to-template links)

For term references, set terminology_ref to the terminology_id it draws from.
For document references, set template_ref to constrain which template's documents are valid.

### File Field Configuration
- allowed_types: MIME type patterns (e.g., ["image/*", "application/pdf"])
- max_size_mb: up to 100
- multiple: allow multiple files; max_files sets the limit

### Array Field Configuration
- array_item_type: string, number, object, or term
- array_terminology_ref / array_template_ref for typed array items

### Other Field Properties
- Use "mandatory: true" (NOT "required") for required fields
- Validation: pattern (regex), min_length, max_length, minimum, maximum, enum

### Validation Rules (Cross-Field)
Templates can define rules across fields:
- CONDITIONAL_REQUIRED: field X required when field Y has value Z
- CONDITIONAL_VALUE: field X constrained when field Y has value Z
- MUTUAL_EXCLUSION: only one of fields X, Y can have a value
- DEPENDENCY: field X requires field Y to also be present

### Template Draft Mode
Create templates with status: "draft" to skip reference validation.
This enables circular dependencies and order-independent creation.
POST /templates/{id}/activate validates and activates cascadingly.
All-or-nothing: if any template in the chain fails validation, none activate.

### Reporting Configuration
Templates can configure PostgreSQL sync behaviour:
- sync_enabled, sync_strategy (latest_only, all_versions, disabled)
- table_name, include_metadata, flatten_arrays, max_array_elements

## Documents
A document is an instance of a template — a filled-in form.
- Validated against the template's field definitions
- Terms are resolved: you submit the value, WIP stores both value and term_id
- Versioned: same identity → same document_id, new version
- identity_fields (defined on template) control what makes a document "the same"
- Zero identity fields = append-only (every POST creates a new document)

## Files
Binary files stored in MinIO, referenced by documents.
- Upload returns a file_id (UUID7 format)
- Link the file_id to a document's file field

## Registry & Synonyms
The Registry assigns canonical IDs (UUID7) to all entities. Any entity can have
multiple identifiers (synonyms) — external IDs, vendor codes, alternate names.
All synonyms resolve to the same canonical WIP ID via O(1) lookup.

Key operations:
- Register a synonym: {"erp_id": "SAP-001"} → resolves to a WIP entity ID
- Lookup by any synonym: as fast as lookup by canonical ID
- Merge two IDs: declare one as synonym of the other (currently one-way —
  no reactivation endpoint exists yet for the deprecated entry)

This enables cross-system integration without mapping tables.

## Ontology Relationships
Terms can be connected via typed relationships:
- Types: is_a, part_of, has_part, regulates, positively_regulates, negatively_regulates
- Fields: source_term_id, target_term_id, relationship_type
- Supports traversal: ancestors, descendants, parents, children
- Supports OBO Graph JSON import for bulk relationship loading
"""


@mcp.resource("wip://development-guide")
def get_development_guide() -> str:
    """How to build applications on WIP — the 4-phase process."""
    return """# Building Applications on WIP

## The Golden Rule
Never modify WIP. Only consume its APIs.

IMPORTANT: Read wip://ponifs before Phase 3. WIP has several powerful but
non-intuitive behaviours that will cause silent failures if you rely on
conventional assumptions.

## Phase 1: Exploratory
Understand WIP's capabilities and inventory what already exists:
- get_wip_status — check all services are running
- list_namespaces — see available namespaces
- list_terminologies — see existing controlled vocabularies
- list_templates — see existing document schemas
- query_by_template — query documents with field filters
- get_template_fields — inspect a template's field definitions

Do NOT recreate terminologies or templates that already exist. Reuse them.

## Phase 2: Data Model Design
Map your domain onto WIP primitives:

1. Identify controlled vocabularies → terminologies (value + label + aliases)
2. Identify hierarchical vocabularies → terminologies WITH ontology relationships
   Ask: "Are any of these vocabularies hierarchical? Do terms have parent-child
   or part-of-whole relationships?" Examples: species taxonomy, disease
   classification, org hierarchy, geographic containment, product categories.
   If yes, plan ontology relationships (is_a, part_of, etc.) alongside terms.
3. Identify document types → templates with typed fields
4. Define relationships between templates (references, inheritance)
5. Define identity_fields for deduplication — choose carefully:
   - Too few → unrelated entities collide into one document
   - Too many → corrections create duplicates instead of versions
   - Zero → append-only, no update path (fine for event logs)
   - NEVER include timestamps or per-run data in identity fields
   - Avoid timestamps in non-identity fields too — they trigger unnecessary
     version updates on otherwise unchanged documents
6. Apply semantic_types where applicable (email, url, geo_point, etc.)
7. Field naming: use "mandatory" (NOT "required"), "terminology_ref" (NOT
   "terminology_id"). These are the WIP API field names.

### Namespace Strategy
- Shared terminologies (COUNTRY, CURRENCY) → "wip" namespace
- App-specific data (templates, documents) → app namespace (e.g., "finance")
- Domain-specific terminologies used by only one app → app namespace
- If a second app needs a terminology, promote it to "wip"

## Phase 3: Implementation
Create the data model in WIP using MCP tools:

1. Create terminologies: create_terminology(value, label, description)
   Populate with terms: create_terms(terminology_id, terms)
   Verify: list_terms(terminology_id)
   If hierarchical: create_relationships([{source_term_id, target_term_id,
   relationship_type}]) — e.g., "Cat is_a Animal". Verify: get_term_hierarchy.
2. Create templates: create_template(template) — use draft mode for
   circular dependencies, then activate_template (all-or-nothing validation)
   Verify: get_template_fields(template_value)
3. Create test documents: create_document(document)
   - Pass template_version explicitly — without it, WIP resolves "latest active",
     which may not be the version you expect if multiple versions are active
   - Updating a template does NOT deactivate the old version — both stay active.
     Deactivate the old version explicitly with deactivate_template.
4. Verify term resolution and reference resolution work
5. Register external ID synonyms if integrating with other systems:
   add_synonym(target_id, ...)
6. Configure reporting (sync_strategy, table_name) if using PostgreSQL

## Phase 4: Application Layer
Build the frontend/app using @wip/client and @wip/react.
The MCP server is mainly useful in Phases 1-3. In Phase 4,
the app uses the TypeScript client library directly.

MCP tools remain useful for debugging and data queries:
- query_by_template — query documents with field-level filters
- run_report_query — raw SQL for cross-template JOINs and aggregations
- import_documents_csv — bulk data loading from CSV/XLSX files

## Key Patterns
- Template inheritance: create a base template, extend it
- Term resolution: submit human-readable values, WIP resolves to term_ids
- Identity hashing: define identity_fields so duplicate submissions update, not duplicate
- Draft mode: create templates with status: "draft" to handle circular deps
- Registry synonyms: register external IDs for cross-system lookups
- Ontology relationships: connect terms hierarchically (is_a, part_of) for taxonomies
- SQL aggregation: use run_report_query for GROUP BY, COUNT, JOINs across templates

Detailed step-by-step procedures for each phase are in the slash commands:
/explore, /design-model, /implement, /build-app.
"""


@mcp.resource("wip://ponifs")
def get_ponifs() -> str:
    """WIP's Powerful, Non-Intuitive Features — the traps that catch every new developer."""
    return """# PoNIFs — Powerful, Non-Intuitive Features

These are WIP behaviours that violate conventional expectations. They are
by design, not bugs. Every one enables a capability that simpler designs
cannot provide. But they WILL cause silent failures if you assume
conventional patterns.

## 1. Nothing Ever Dies
Deactivation (soft-delete) makes an entity unavailable for NEW data, but it
always resolves for EXISTING references. "Inactive" means "retired", not
"deleted." Historical data never breaks.

Trap: You deactivate a term and expect documents using it to fail. They don't.
Rule: Never treat inactive as deleted. Inactive entities are invisible to new
      data but always visible to existing data.

## 2. Template Versioning — Update Does NOT Replace
Updating a template creates a new version. The OLD version stays active.
Multiple versions coexist. New documents can be created against ANY active version.

Trap: You update a template to fix a field. Both v1 and v2 are now active.
      Documents may still be created against v1 (from cache or explicit version).
Rule: After updating, deactivate the old version with deactivate_template()
      unless you specifically need multi-version operation. Always pass
      template_version when creating documents.

## 3. Document Identity — The Hash Decides
Templates define identity_fields. WIP hashes them to decide: same hash = new
version (update), different hash = new document (create). The same
create_document call handles both — it's an upsert.

Trap: Adding a timestamp to document data makes every hash unique — you get
      duplicates instead of versions. Too many identity fields means corrections
      create new documents instead of new versions. Zero identity fields means
      every submission creates a new document (no update path).
Rule: Identity fields answer "is this the same real-world thing?" — no more,
      no less. Never include timestamps, run IDs, or per-execution data.

## 4. Bulk-First — 200 OK Always
All WIP write APIs return HTTP 200 even when individual items fail. Per-item
status is in results[i].status (created, updated, error, skipped).

Trap: You check the HTTP status, see 200, and assume success. Meanwhile,
      items silently failed validation inside the response body.
Rule: The MCP server's single-item tools handle this for you — they unwrap
      and surface errors. But when using bulk tools (create_documents_bulk,
      etc.), always check per-item results.

## 5. Registry Synonyms — Multiple IDs Are Normal
Any entity can have multiple identifiers (synonyms). Two WIP IDs for the same
real-world entity is a normal state, not corruption. Merge resolves this.

Trap: You find two IDs for the same entity and think the data is corrupt.
Rule: Use merge_entries() to reconcile duplicates. Synonyms enable cross-system
      integration — your bank's ID, your ERP's code, and WIP's UUID all resolve
      to the same entity.

## 6. Template Cache — Changes Aren't Instant
"Latest active" template resolution has a 5-second cache TTL. After updating
a template, the old definition may be used for up to 5 seconds. Lookups by
explicit version are cached permanently (immutable).

Trap: You update a template and immediately create a document — it validates
      against the OLD version from cache.
Rule: Pass explicit template_version, or wait 5 seconds after template changes.

## The Compactheimer's Warning
If you are an AI assistant and your context has been compacted, you may have
lost these warnings and reverted to conventional assumptions. Signs of drift:
- Assuming template update replaces the old version
- Adding timestamps or run-specific data to documents
- Treating inactive entities as deleted
- Not checking per-item results in bulk operations

If any of these feel natural, re-read this resource.
"""


@mcp.resource("wip://query-assistant-prompt")
async def get_query_assistant_prompt() -> str:
    """Complete system prompt for a WIP query assistant.

    Dynamic resource: calls describe_data_model() at read time to embed a
    live data model catalog. Use this as the system prompt for any agent
    that answers natural-language questions against WIP data.
    """
    # Build the live data model section
    try:
        data_model = await _build_data_model_markdown(namespace=None)
    except Exception:
        data_model = (
            "**Data model unavailable.** Call the `describe_data_model` tool "
            "at the start of your session to discover available templates and fields."
        )

    return f"""You are a WIP query assistant. You help users find and explore data stored in a WIP (World In a Pie) document store through natural language.

## Your Capabilities

You have access to **read-only** MCP tools connected to a WIP instance. You can:
- Search across all documents (free text and structured queries)
- List, filter, and retrieve documents by template
- Look up terminologies and their terms
- Run SQL queries against the reporting database for aggregations and analytics
- Describe the data model to understand what's available

You **cannot** create, modify, or delete anything. All tools are read-only.

## How to Answer Questions

1. **Always use tools.** Never guess or fabricate data — query WIP for accurate answers.
2. **Call `describe_data_model` first** if you don't know what templates and fields are available.
3. **Be concise.** Give the answer, not an essay.
4. **Format nicely.** Use markdown: tables for comparisons, bold for key stats, headers for sections.
5. **Cite specifics.** Include exact values from the data.

## Query Strategy

- Use `search` for broad text searches across all entity types.
- Use `query_by_template` to list/filter documents of a specific template type.
- Term field values are UPPERCASE (e.g., "BEAST", "EVOCATION").
- Reference fields store entity IDs — use `get_document` to resolve them to full details.
- For aggregations, cross-template JOINs, or analytics, use `run_report_query` with SQL.
  - Table names: `doc_{{template_value}}` in lowercase (e.g., `doc_patient`, `doc_bank_transaction`).
  - Use `list_report_tables` to discover available tables and columns.
- Only return latest versions of documents unless the user asks about version history.

## Available Data Model

{data_model}

## Response Style

- For a single entity lookup: show its full details.
- For comparisons: use a table.
- For "what can do X" questions: list matching entities with key details.
- For counts or statistics: use `run_report_query` with SQL.
- Keep answers under 500 words unless the user asks for detailed analysis.

## What NOT to Do

- Don't guess. If the data isn't there, say so.
- Don't invent entities, fields, or values that don't exist in the data model.
- Don't attempt to modify data — you are read-only.
- Don't expose internal IDs (template_id, document_id) unless the user asks for them.
"""


# ===================================================================
# Tools — Discovery
# ===================================================================


@mcp.tool()
async def get_wip_status() -> str:
    """Check health of all WIP services. Call this first to verify connectivity."""
    try:
        results = await get_client().check_health()
        lines = ["WIP Service Status:"]
        all_healthy = True
        for name, info in results.items():
            status = "healthy" if info["healthy"] else "DOWN"
            if not info["healthy"]:
                all_healthy = False
            lines.append(f"  {name}: {status}")
            if info.get("error"):
                lines.append(f"    error: {info['error']}")
        lines.insert(1, f"  overall: {'all healthy' if all_healthy else 'DEGRADED'}")
        return "\n".join(lines)
    except Exception as e:
        return _error(e)


async def _build_data_model_markdown(namespace: str | None = None) -> str:
    """Build a markdown description of the data model. Raises on error."""
    client = get_client()

    # Fetch all active templates (fields are returned inline)
    all_templates: list[dict] = []
    page = 1
    while True:
        data = await client.list_templates(
            namespace=namespace, status="active", latest_only=True,
            page=page, page_size=100,
        )
        all_templates.extend(data.get("items", []))
        if page >= data.get("pages", 1):
            break
        page += 1

    # Fetch all active terminologies
    all_terminologies: list[dict] = []
    page = 1
    while True:
        data = await client.list_terminologies(
            namespace=namespace, page=page, page_size=100,
        )
        items = data.get("items", [])
        # Filter to active only (list_terminologies doesn't have status param)
        all_terminologies.extend(t for t in items if t.get("status") == "active")
        if page >= data.get("pages", 1):
            break
        page += 1

    # Build markdown output
    lines: list[str] = ["# WIP Data Model"]

    if namespace:
        lines.append(f"\nNamespace: **{namespace}**")

    # --- Templates overview ---
    lines.append(f"\n## Templates ({len(all_templates)})\n")
    if all_templates:
        lines.append("| Template | Label | Fields | Identity Fields | Namespace |")
        lines.append("|----------|-------|--------|-----------------|-----------|")
        for t in sorted(all_templates, key=lambda x: x.get("value", "")):
            value = t.get("value", "?")
            label = t.get("label", "")
            fields = t.get("fields", [])
            identity = ", ".join(t.get("identity_fields", []))
            ns = t.get("namespace", "")
            lines.append(f"| {value} | {label} | {len(fields)} | {identity or '—'} | {ns} |")

        # --- Fields per template ---
        lines.append("\n## Fields by Template\n")
        for t in sorted(all_templates, key=lambda x: x.get("value", "")):
            value = t.get("value", "?")
            fields = t.get("fields", [])
            if not fields:
                continue
            lines.append(f"### {value}\n")
            lines.append("| Field | Type | Mandatory | Term Terminology | Description |")
            lines.append("|-------|------|-----------|------------------|-------------|")
            for f in fields:
                name = f.get("name", "?")
                ftype = f.get("field_type", "text")
                mandatory = "yes" if f.get("mandatory") else ""
                term_ref = f.get("term_terminology_id") or f.get("term_terminology_value") or ""
                desc = (f.get("description") or "")[:60]
                lines.append(f"| {name} | {ftype} | {mandatory} | {term_ref} | {desc} |")
            lines.append("")
    else:
        lines.append("No active templates found.")

    # --- Terminologies ---
    lines.append(f"\n## Terminologies ({len(all_terminologies)})\n")
    if all_terminologies:
        lines.append("| Terminology | Label | Terms | Mutable | Namespace |")
        lines.append("|-------------|-------|-------|---------|-----------|")
        for t in sorted(all_terminologies, key=lambda x: x.get("value", "")):
            value = t.get("value", "?")
            label = t.get("label", "")
            term_count = t.get("term_count", t.get("active_term_count", "?"))
            mutable = "yes" if t.get("mutable") else ""
            ns = t.get("namespace", "")
            lines.append(f"| {value} | {label} | {term_count} | {mutable} | {ns} |")
    else:
        lines.append("No active terminologies found.")

    # --- Query conventions ---
    lines.append("\n## Query Conventions\n")
    lines.append("- Use `query_by_template` to list/filter documents of a specific template.")
    lines.append("- Use `search` for cross-template free-text search.")
    lines.append("- Term field values are UPPERCASE (e.g., creature_type: \"BEAST\").")
    lines.append("- Reference fields store entity IDs — use `get_document` to resolve.")
    lines.append("- Use `run_report_query` for SQL aggregations, JOINs, and analytics.")
    lines.append("- Table names in PostgreSQL: `doc_{template_value}` (lowercase).")

    return "\n".join(lines)


@mcp.tool()
async def describe_data_model(namespace: str | None = None) -> str:
    """Describe the full data model: all active templates with fields, and terminologies.

    Returns a markdown summary suitable for system prompt injection. Use this to
    understand what data is available before querying. Covers templates (with all
    fields, types, and constraints), terminologies, and query conventions.

    Args:
        namespace: Filter to a specific namespace. None = all namespaces.
    """
    try:
        return await _build_data_model_markdown(namespace)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def list_namespaces(include_archived: bool = False) -> str:
    """List all WIP namespaces. Namespaces scope all entities (terminologies, templates, documents)."""
    try:
        data = await get_client().list_namespaces(include_archived=include_archived)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_namespace(
    prefix: str,
    description: str = "",
    isolation_mode: str = "open",
    deletion_mode: str = "retain",
) -> str:
    """Create a new namespace. Namespaces scope all entities (terminologies, templates, documents).

    Args:
        prefix: Unique namespace prefix (e.g., 'finance', 'clintrial', 'dnd'). Lowercase, no spaces.
        description: Human-readable description of the namespace's purpose.
        isolation_mode: 'open' (default) allows cross-namespace term refs; 'strict' restricts to same namespace only.
        deletion_mode: 'retain' (default, soft-delete only) or 'full' (allows hard-delete and namespace deletion).
    """
    try:
        data = await get_client().create_namespace(
            prefix=prefix,
            description=description,
            isolation_mode=isolation_mode,
            deletion_mode=deletion_mode,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_namespace_stats(prefix: str) -> str:
    """Get statistics for a namespace — entity counts by type."""
    try:
        data = await get_client().get_namespace_stats(prefix)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_namespace(
    prefix: str, dry_run: bool = True, force: bool = False
) -> str:
    """Delete a namespace and ALL its data (terminologies, terms, templates, documents, files).

    DESTRUCTIVE — requires deletion_mode='full' on the namespace.
    Always run with dry_run=true first to see the impact report.

    Args:
        prefix: Namespace prefix to delete
        dry_run: If true (default), return impact report without making changes
        force: If true, proceed even if other namespaces reference this one
    """
    try:
        data = await get_client().delete_namespace(
            prefix, dry_run=dry_run, force=force
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — API Key Management
# ===================================================================


@mcp.tool()
async def create_api_key(
    name: str,
    owner: str = "system",
    groups: list[str] | None = None,
    namespaces: list[str] | None = None,
    description: str | None = None,
    expires_at: str | None = None,
) -> str:
    """Create a runtime API key. Returns the plaintext key (shown once, never stored).

    Args:
        name: Unique name for the key (e.g., 'my-app')
        owner: Owner identifier (default 'system')
        groups: Authorization groups (e.g., ['wip-users']). Empty = no special privileges.
        namespaces: Namespace scope (e.g., ['wip']). None = unrestricted.
        description: Human-readable description
        expires_at: ISO 8601 expiry datetime (None = never expires)
    """
    try:
        data = await get_client().create_api_key(
            name=name,
            owner=owner,
            groups=groups or [],
            namespaces=namespaces,
            description=description,
            expires_at=expires_at,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def list_api_keys() -> str:
    """List all API keys (config-file + runtime). No hashes or plaintext exposed."""
    try:
        data = await get_client().list_api_keys()
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def revoke_api_key(name: str) -> str:
    """Revoke (hard-delete) a runtime API key. Config-file keys cannot be revoked.

    Args:
        name: Name of the runtime key to revoke
    """
    try:
        data = await get_client().revoke_api_key(name)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Registry Entries & Synonyms
# ===================================================================


@mcp.tool()
async def get_entry(entry_id: str) -> str:
    """Get full details for a Registry entry — synonyms, composite keys, metadata."""
    try:
        data = await get_client().get_entry(entry_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def lookup_entry(
    entry_id: str | None = None,
    namespace: str | None = None,
    entity_type: str | None = None,
    composite_key: dict | None = None,
) -> str:
    """Look up a Registry entry by ID or by composite key.

    Provide either entry_id (lookup by ID) or namespace + entity_type + composite_key
    (lookup by key). Key lookup also searches synonyms.

    Args:
        entry_id: Look up by entry ID or value code.
        namespace: Namespace for key lookup (e.g., 'wip').
        entity_type: Entity type for key lookup (e.g., 'terms').
        composite_key: Composite key dict for key lookup.
    """
    try:
        client = get_client()
        if entry_id:
            data = await client.lookup_by_id(entry_id)
        elif namespace and entity_type and composite_key:
            data = await client.lookup_by_key(namespace, entity_type, composite_key)
        else:
            return "Error: Provide either entry_id, or namespace + entity_type + composite_key."
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def add_synonym(
    target_id: str,
    synonym_namespace: str,
    synonym_entity_type: str,
    synonym_composite_key: dict,
) -> str:
    """Add an alternative composite key (synonym) that resolves to an existing entry.

    Use this for cross-namespace linking, external/vendor ID mapping, or
    mapping multiple identifiers to the same canonical entity.

    Args:
        target_id: The canonical entry ID to add the synonym to.
        synonym_namespace: Namespace for the synonym key.
        synonym_entity_type: Entity type for the synonym key.
        synonym_composite_key: The alternative composite key dict.
    """
    try:
        data = await get_client().add_synonym(
            target_id=target_id,
            synonym_namespace=synonym_namespace,
            synonym_entity_type=synonym_entity_type,
            synonym_composite_key=synonym_composite_key,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def remove_synonym(
    target_id: str,
    synonym_namespace: str,
    synonym_entity_type: str,
    synonym_composite_key: dict,
) -> str:
    """Remove a synonym (alternative composite key) from a Registry entry.

    Args:
        target_id: The entry ID to remove the synonym from.
        synonym_namespace: Namespace of the synonym to remove.
        synonym_entity_type: Entity type of the synonym to remove.
        synonym_composite_key: The composite key dict to remove.
    """
    try:
        data = await get_client().remove_synonym(
            target_id=target_id,
            synonym_namespace=synonym_namespace,
            synonym_entity_type=synonym_entity_type,
            synonym_composite_key=synonym_composite_key,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def merge_entries(preferred_id: str, deprecated_id: str) -> str:
    """Merge two Registry entries. The deprecated entry becomes inactive and its
    entry_id is added as a synonym to the preferred entry (for backward compatibility).

    Args:
        preferred_id: The entry ID to keep as canonical.
        deprecated_id: The entry ID to deprecate and merge into the preferred one.
    """
    try:
        data = await get_client().merge_entries(
            preferred_id=preferred_id, deprecated_id=deprecated_id
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Terminologies
# ===================================================================


@mcp.tool()
async def list_terminologies(
    namespace: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List terminologies (controlled vocabularies). Examples: COUNTRY, GENDER, DIAGNOSIS_CODE."""
    try:
        data = await get_client().list_terminologies(
            namespace=namespace, page=page, page_size=page_size
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_terminology(terminology_id: str) -> str:
    """Get a terminology by ID or value (e.g., 'COUNTRY' or UUID)."""
    try:
        data = await get_client().get_terminology(terminology_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_terminology_by_value(value: str, namespace: str | None = None) -> str:
    """Get a terminology by its value code (e.g., 'COUNTRY', 'GENDER'). Case-sensitive.

    Args:
        value: The terminology value code.
        namespace: Namespace to search in. Omit to search all namespaces.
    """
    try:
        data = await get_client().get_terminology_by_value(value, namespace=namespace)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_terminology(
    value: str,
    label: str,
    namespace: str | None = None,
    description: str | None = None,
    mutable: bool = False,
) -> str:
    """Create a terminology (controlled vocabulary).

    Args:
        value: Unique code (e.g., 'COUNTRY', 'GENDER'). Convention: UPPER_SNAKE_CASE.
        label: Human-readable name (e.g., 'Country', 'Gender').
        namespace: Namespace to create in. Uses WIP_MCP_DEFAULT_NAMESPACE if omitted.
        description: Optional description of what this terminology contains.
        mutable: If true, terms can be hard-deleted (not just deprecated). Implies extensible=true.
    """
    try:
        client = get_client()
        namespace = client._ns(namespace)
        kwargs = {}
        if description:
            kwargs["description"] = description
        if mutable:
            kwargs["mutable"] = True
        data = await client.create_terminology(
            value=value, label=label, namespace=namespace, **kwargs
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_terminologies_bulk(items: list[dict], namespace: str | None = None) -> str:
    """Create multiple terminologies at once.

    Args:
        items: List of {value, label, description?} objects.
        namespace: Namespace for all items. Omit to use per-item namespace or server default.
    """
    try:
        client = get_client()
        ns = namespace or client.default_namespace
        if ns:
            for item in items:
                item.setdefault("namespace", ns)
        data = await client.create_terminologies(items)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def update_terminology(
    terminology_id: str,
    label: str | None = None,
    description: str | None = None,
    mutable: bool | None = None,
) -> str:
    """Update a terminology's label, description, or mutability.

    Args:
        terminology_id: Terminology ID, value code (e.g., 'COUNTRY'), or synonym.
        label: New label (optional).
        description: New description (optional).
        mutable: Set mutability (optional). Only allowed when term_count is 0.
    """
    try:
        updates = {}
        if label is not None:
            updates["label"] = label
        if description is not None:
            updates["description"] = description
        if mutable is not None:
            updates["mutable"] = mutable
        if not updates:
            return "Error: Provide at least one field to update (label, description, mutable)."
        data = await get_client().update_terminology(terminology_id, updates)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_terminology(
    terminology_id: str, force: bool = False, hard_delete: bool = False,
) -> str:
    """Delete a terminology. Mutable terminologies are always hard-deleted.
    Immutable ones are soft-deleted unless hard_delete=true (requires namespace deletion_mode='full').

    Blocked if terms depend on it unless force=true.

    Args:
        terminology_id: Terminology ID, value code (e.g., 'COUNTRY'), or synonym.
        force: Force deletion even if terms exist.
        hard_delete: Permanently remove (requires namespace deletion_mode='full').
    """
    try:
        data = await get_client().delete_terminology(
            terminology_id, force=force, hard_delete=hard_delete,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def restore_terminology(
    terminology_id: str, restore_terms: bool = True
) -> str:
    """Restore a previously deactivated terminology back to active status.

    Args:
        terminology_id: Terminology ID, value code (e.g., 'COUNTRY'), or synonym.
        restore_terms: Also reactivate its inactive terms (default: true).
    """
    try:
        data = await get_client().restore_terminology(
            terminology_id, restore_terms=restore_terms
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Terms
# ===================================================================


@mcp.tool()
async def list_terms(
    terminology_id: str,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List terms in a terminology. Use search to filter by value/label/alias.

    Args:
        terminology_id: Terminology ID (UUID) or value (e.g., 'COUNTRY').
        search: Optional search string to filter terms.
    """
    try:
        data = await get_client().list_terms(
            terminology_id=terminology_id,
            search=search,
            page=page,
            page_size=page_size,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_term(term_id: str) -> str:
    """Get a term by ID, value (e.g., 'STATUS:approved'), or synonym."""
    try:
        data = await get_client().get_term(term_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_terms(
    terminology_id: str,
    terms: list[dict],
) -> str:
    """Create terms in a terminology.

    Args:
        terminology_id: Terminology ID (UUID) or value (e.g., 'COUNTRY').
        terms: List of term objects. Each must have 'value' and 'label'.
            Optional: 'description', 'aliases' (list of strings).

    Example:
        create_terms("T-xxx", [
            {"value": "GB", "label": "United Kingdom", "aliases": ["UK", "Britain"]},
            {"value": "US", "label": "United States", "aliases": ["USA"]}
        ])
    """
    try:
        data = await get_client().create_terms(
            terminology_id=terminology_id, terms=terms
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def validate_term_value(terminology: str, value: str) -> str:
    """Validate whether a value exists in a terminology. Use to test term resolution.

    Args:
        terminology: Terminology ID (UUID) or value (e.g., 'COUNTRY', 'CT_AE_TERM_TEST').
        value: The term value to validate.
    """
    try:
        data = await get_client().validate_term(
            terminology=terminology, value=value
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def update_term(
    term_id: str,
    label: str | None = None,
    aliases: list[str] | None = None,
    description: str | None = None,
    sort_order: int | None = None,
) -> str:
    """Update a term's label, aliases, description, or sort order.

    Args:
        term_id: Term ID, value (e.g., 'STATUS:approved'), or synonym.
        label: New label (optional).
        aliases: New aliases list (optional). Replaces existing aliases.
        description: New description (optional).
        sort_order: New sort order (optional).
    """
    try:
        updates: dict = {}
        if label is not None:
            updates["label"] = label
        if aliases is not None:
            updates["aliases"] = aliases
        if description is not None:
            updates["description"] = description
        if sort_order is not None:
            updates["sort_order"] = sort_order
        if not updates:
            return "Error: Provide at least one field to update."
        data = await get_client().update_term(term_id, updates)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_term(term_id: str, hard_delete: bool = False) -> str:
    """Delete a term. Soft-delete (deactivate) by default.
    Terms in mutable terminologies are always hard-deleted.
    Set hard_delete=true to permanently remove from immutable terminologies
    (requires namespace deletion_mode='full').

    Args:
        term_id: Term ID, value (e.g., 'STATUS:approved'), or synonym.
        hard_delete: Permanently remove (requires namespace deletion_mode='full').
    """
    try:
        data = await get_client().delete_term(term_id, hard_delete=hard_delete)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def deprecate_term(
    term_id: str,
    reason: str,
    replaced_by_term_id: str | None = None,
) -> str:
    """Deprecate a term with a reason and optional replacement pointer.

    Deprecated terms remain queryable but are flagged as superseded.
    Use this instead of delete when the term was valid historically but
    has been replaced by a better term.

    Args:
        term_id: Term ID, value (e.g., 'STATUS:approved'), or synonym.
        reason: Reason for deprecation (e.g., 'Merged with COUNTRY').
        replaced_by_term_id: Replacement term ID, value, or synonym (optional).
    """
    try:
        data = await get_client().deprecate_term(
            term_id=term_id, reason=reason,
            replaced_by_term_id=replaced_by_term_id,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Ontology (Relationships)
# ===================================================================


@mcp.tool()
async def get_term_hierarchy(
    term_id: str,
    direction: str = "children",
    relationship_type: str | None = None,
    max_depth: int = 10,
    namespace: str | None = None,
) -> str:
    """Traverse ontology relationships for a term.

    Args:
        term_id: Term ID, value (e.g., 'STATUS:approved'), or synonym.
        direction: One of 'children', 'parents', 'ancestors', 'descendants'.
        relationship_type: Filter by type (is_a, part_of, has_part, etc.). None = all.
        max_depth: Max traversal depth for ancestors/descendants.
        namespace: Namespace to query in. Omit to use server default.
    """
    try:
        client = get_client()
        if direction == "children":
            data = await client.get_term_children(term_id, namespace=namespace)
        elif direction == "parents":
            data = await client.get_term_parents(term_id, namespace=namespace)
        elif direction == "ancestors":
            data = await client.get_term_ancestors(
                term_id,
                relationship_type=relationship_type,
                max_depth=max_depth,
                namespace=namespace,
            )
        elif direction == "descendants":
            data = await client.get_term_descendants(
                term_id,
                relationship_type=relationship_type,
                max_depth=max_depth,
                namespace=namespace,
            )
        else:
            return "Error: direction must be children, parents, ancestors, or descendants"
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_relationships(
    relationships: list[dict],
    namespace: str | None = None,
) -> str:
    """Create ontology relationships between terms.

    Args:
        relationships: List of {source_term_id, target_term_id, relationship_type}.
            source_term_id: Term ID, value (e.g., 'ALZHEIMERS_DISEASE'), or synonym.
            target_term_id: Term ID, value (e.g., 'NEUROLOGY'), or synonym.
            relationship_type: is_a, part_of, has_part, regulates, positively_regulates, negatively_regulates.
        namespace: Namespace to create in. Omit to use server default.

    Example:
        create_relationships([{
            "source_term_id": "ALZHEIMERS_DISEASE",
            "relationship_type": "is_a",
            "target_term_id": "NEUROLOGY"
        }])
    """
    try:
        data = await get_client().create_relationships(relationships, namespace=namespace)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def list_relationships(
    term_id: str,
    direction: str = "outgoing",
    relationship_type: str | None = None,
    namespace: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List ontology relationships for a specific term.

    Args:
        term_id: Term ID, value (e.g., 'STATUS:approved'), or synonym.
        direction: 'outgoing' (this term is source), 'incoming' (this term is target), or 'both'.
        relationship_type: Filter by type (is_a, part_of, etc.). None = all types.
        namespace: Namespace to query in. Omit to use server default.
        page: Page number.
        page_size: Results per page (max 100).
    """
    try:
        data = await get_client().list_relationships(
            term_id=term_id, direction=direction,
            relationship_type=relationship_type, namespace=namespace,
            page=page, page_size=page_size,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_relationships(
    relationships: list[dict],
    namespace: str | None = None,
    hard_delete: bool = False,
) -> str:
    """Delete ontology relationships between terms.

    Args:
        relationships: List of {source_term_id, target_term_id, relationship_type}.
            source_term_id: Term ID, value (e.g., 'ALZHEIMERS_DISEASE'), or synonym.
            target_term_id: Term ID, value (e.g., 'NEUROLOGY'), or synonym.
            relationship_type: is_a, part_of, has_part, etc.
        namespace: Namespace to delete from. Omit to use server default.
        hard_delete: Permanently remove (requires namespace deletion_mode='full').

    Example:
        delete_relationships([{
            "source_term_id": "ALZHEIMERS_DISEASE",
            "target_term_id": "NEUROLOGY",
            "relationship_type": "is_a"
        }])
    """
    try:
        data = await get_client().delete_relationships(
            relationships, namespace=namespace, hard_delete=hard_delete,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Templates
# ===================================================================


@mcp.tool()
async def list_templates(
    namespace: str | None = None,
    status: str | None = None,
    latest_only: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List templates (document schemas).

    Args:
        namespace: Filter by namespace.
        status: Filter by status: 'active', 'inactive', 'draft'.
        latest_only: If true, return only the latest version of each template.
    """
    try:
        data = await get_client().list_templates(
            namespace=namespace,
            status=status,
            latest_only=latest_only,
            page=page,
            page_size=page_size,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_template(
    template_id: str,
    version: int | None = None,
) -> str:
    """Get a template by ID. Returns the resolved template (with inherited fields).

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
        version: Specific version number. None = latest.
    """
    try:
        data = await get_client().get_template(
            template_id=template_id, version=version
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_template_by_value(value: str, namespace: str | None = None) -> str:
    """Get a template by its value code (e.g., 'BANK_TRANSACTION', 'PATIENT_RECORD')."""
    try:
        data = await get_client().get_template_by_value(
            value=value, namespace=namespace
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_template_raw(template_id: str) -> str:
    """Get a template WITHOUT inheritance resolution. Shows only fields defined directly on this template.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
    """
    try:
        data = await get_client().get_template_raw(template_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_template(template: dict, namespace: str | None = None) -> str:
    """Create a template (document schema).

    NOTE: Updating an existing template creates a new version — the old version
    stays active. See wip://conventions for versioning behaviour and deactivation.

    Args:
        template: Template definition. Required fields:
            - value: Unique code (e.g., 'BANK_TRANSACTION'). UPPER_SNAKE_CASE.
            - label: Display name.
            - fields: List of field definitions.
        namespace: Namespace. Omit to use template's namespace field or server default.

        Optional:
            - description: What this template represents.
            - extends: Parent template value to inherit from.
            - extends_version: Pin to specific parent version.
            - identity_fields: List of field names for deduplication.
              Choose carefully — see wip://conventions for pitfalls.
            - status: 'active' (default) or 'draft' (skip validation).

        Field definition: {
            name: str,          # field name
            label: str,         # display label
            type: str,          # string, number, integer, boolean, date, datetime, term, reference, file, array, object
            mandatory: bool,    # whether the field is required (NOTE: "mandatory", not "required")
            terminology_ref: str,  # for type=term: terminology_id of the referenced terminology
            template_ref: str,  # for type=reference: template_id of the referenced template
            semantic_type: str,  # email, url, latitude, longitude, percentage, duration, geo_point
            ...
        }

    IMPORTANT field naming:
        - Use "mandatory" (not "required") for required fields
        - Use "terminology_ref" (not "terminology_id") for term field references
        - Use "template_ref" (not "template_id") for reference field references

    Example:
        create_template({
            "value": "PATIENT",
            "label": "Patient Record",
            "namespace": "wip",
            "identity_fields": ["email"],
            "fields": [
                {"name": "name", "label": "Full Name", "type": "string", "mandatory": true},
                {"name": "email", "label": "Email", "type": "string", "mandatory": true, "semantic_type": "email"},
                {"name": "gender", "label": "Gender", "type": "term", "mandatory": true, "terminology_ref": "T-xxx"}
            ]
        })
    """
    try:
        client = get_client()
        ns = namespace or client.default_namespace
        if ns:
            template.setdefault("namespace", ns)
        data = await client.create_template(template)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        err = str(e)
        if "already exists" in err:
            return f"WIP error: {err}\n\nTo add or modify fields on an existing template, use update_template(template_id, {{\"fields\": [...]}}) instead. This creates a new version."
        return _error(e)


@mcp.tool()
async def create_templates_bulk(templates: list[dict], namespace: str | None = None) -> str:
    """Create multiple templates. Use status: 'draft' for circular dependencies, then activate.

    Updates create new versions — old versions stay active. See wip://conventions.

    Args:
        templates: List of template definitions.
        namespace: Namespace for all templates. Omit to use per-template namespace or server default.
    """
    try:
        client = get_client()
        ns = namespace or client.default_namespace
        if ns:
            for tpl in templates:
                tpl.setdefault("namespace", ns)
        data = await client.create_templates(templates)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def update_template(template_id: str, updates: dict) -> str:
    """Update a template by creating a new version. Use this to add/remove/modify fields.

    The template_id stays the same across versions — only the version number increments.
    Existing documents are unaffected (they reference the version they were created with).
    New documents will validate against the latest version by default.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
        updates: Fields to change. Only include fields you want to modify:
            - label: New display label
            - description: New description
            - fields: Complete field list (replaces all fields — include unchanged ones too)
            - identity_fields: Updated identity fields
            - extends: New parent template
            - rules: Updated validation rules
            - metadata: Updated metadata
            - reporting: Updated reporting config

    Example — add a field to an existing template:
        1. get_template(template_id) to see current fields
        2. update_template(template_id, {
               "fields": [... existing fields ..., {"name": "new_field", "label": "New", "type": "string"}]
           })

    Returns version info: template_id, value, version (new), is_new_version, previous_version.
    """
    try:
        data = await get_client().update_template(template_id, updates)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def activate_template(
    template_id: str,
    namespace: str | None = None,
    dry_run: bool = False,
) -> str:
    """Activate a draft template. Validates all references and cascades to referenced drafts.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
        namespace: Namespace scope.
        dry_run: If true, validate without activating.
    """
    try:
        data = await get_client().activate_template(
            template_id=template_id, namespace=namespace, dry_run=dry_run
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def deactivate_template(
    template_id: str,
    version: int | None = None,
    force: bool = False,
    hard_delete: bool = False,
) -> str:
    """Delete a template version. Soft-delete (deactivate) by default.
    Set hard_delete=true to permanently remove (requires namespace deletion_mode='full').

    Blocked if other templates extend it.
    If documents reference it, use force=true to delete anyway.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
        version: Specific version (default: latest for soft-delete, all for hard-delete).
        force: Force deletion even if documents exist.
        hard_delete: Permanently remove (requires namespace deletion_mode='full').
    """
    try:
        data = await get_client().deactivate_template(
            template_id=template_id, version=version, force=force,
            hard_delete=hard_delete,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_template_dependencies(template_id: str) -> str:
    """Show what depends on a template: child templates and documents.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
    """
    try:
        data = await get_client().get_template_dependencies(template_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_template_versions(
    template_value: str | None = None,
    template_id: str | None = None,
    namespace: str | None = None,
) -> str:
    """List all versions of a template.

    Provide either template_value or template_id. Returns all versions
    sorted by version number descending.

    Args:
        template_value: Template value code (e.g., 'PATIENT').
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
        namespace: Namespace to search in (only used with template_value). Omit for all namespaces.
    """
    try:
        client = get_client()
        if template_value:
            data = await client.get_template_versions_by_value(template_value, namespace=namespace)
        elif template_id:
            data = await client.get_template_versions(template_id)
        else:
            return "Error: Provide either template_value or template_id."
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def validate_template(template_id: str) -> str:
    """Validate a template's references (terminologies, parent templates).

    Checks that all terminology_ref and extends references point to
    active entities. Useful for draft templates before activation.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
    """
    try:
        data = await get_client().validate_template(template_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Documents
# ===================================================================


@mcp.tool()
async def list_documents(
    template_value: str | None = None,
    namespace: str | None = None,
    template_id: str | None = None,
    status: str | None = None,
    latest_only: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List documents, optionally filtered by template.

    Args:
        template_value: Filter by template value code (e.g., 'PATIENT'). Most convenient filter.
        template_id: Filter by template ID, value code (e.g., 'PERSON'), or synonym.
        namespace: Filter by namespace.
        status: Filter by status.
        latest_only: Only return the latest version of each document.
    """
    try:
        data = await get_client().list_documents(
            namespace=namespace,
            template_id=template_id,
            template_value=template_value,
            status=status,
            latest_only=latest_only,
            page=page,
            page_size=page_size,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_document(document_id: str, version: int | None = None) -> str:
    """Get a document by ID.

    Args:
        document_id: Document ID.
        version: Specific version. None = latest.
    """
    try:
        data = await get_client().get_document(
            document_id=document_id, version=version
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def validate_document(
    template_id: str, data: dict, namespace: str | None = None
) -> str:
    """Validate document data against a template without saving.

    Use this for pre-validation before submission, testing data against templates,
    or computing an identity hash without creating a document.

    Args:
        template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
        data: The field values to validate (a dict matching the template's fields).
        namespace: Namespace scope.

    Returns validation result with: valid (bool), errors (list), warnings (list),
    identity_hash (if valid), and template_version used.
    """
    try:
        data = await get_client().validate_document(
            template_id=template_id, data=data, namespace=namespace
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_document(document: dict, namespace: str | None = None) -> str:
    """Create a document (an instance of a template).

    Args:
        document: Document data. Required:
            - template_id: Template ID, value code (e.g., 'PERSON'), or synonym.
            - template_version: Pin to a specific template version. Recommended —
              without it, WIP resolves "latest active" which may not be what you
              expect if multiple versions are active. See wip://conventions.
            - data: The field values (a dict matching the template's fields).
        namespace: Namespace. Omit to use document's namespace field or server default.

    Term fields: Submit the human-readable value (e.g., "United Kingdom").
    WIP resolves it to the term_id automatically. If resolution fails,
    you'll get a clear error indicating which field/value failed.

    Identity: If the template defines identity_fields and this document
    matches an existing one, it creates a new version instead of a duplicate.
    Do not include timestamps or per-run data in document fields — it breaks
    dedup or causes unnecessary version churn. See wip://conventions.

    Example:
        create_document({
            "template_id": "TPL-xxx",
            "template_version": 1,
            "namespace": "wip",
            "data": {
                "name": "Jane Doe",
                "email": "jane@example.com",
                "gender": "Female"
            }
        })
    """
    try:
        client = get_client()
        ns = namespace or client.default_namespace
        if ns:
            document.setdefault("namespace", ns)
        data = await client.create_document(document)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_documents_bulk(documents: list[dict], namespace: str | None = None) -> str:
    """Create multiple documents at once. Returns per-item results.

    Args:
        documents: List of document data dicts.
        namespace: Namespace for all documents. Omit to use per-document namespace or server default.
    """
    try:
        client = get_client()
        ns = namespace or client.default_namespace
        if ns:
            for doc in documents:
                doc.setdefault("namespace", ns)
        data = await client.create_documents(documents)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def query_documents(filters: dict) -> str:
    """Query documents with complex filters (low-level). Prefer query_by_template for easier use.

    Args:
        filters: Query body with these fields:
            - template_id: Template ID, value code (e.g., 'PERSON'), or synonym (required for field filters).
            - namespace: Filter by namespace.
            - status: Filter by status ('active', 'inactive').
            - page, page_size: Pagination.
            - filters: List of {field, operator, value} objects.
              Field names must include 'data.' prefix (e.g., 'data.country').
              Operators: eq, ne, gt, gte, lt, lte, in, nin, exists, regex.
              Example: [{"field": "data.country", "operator": "eq", "value": "CH"}]
    """
    try:
        data = await get_client().query_documents(filters)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_document_versions(document_id: str) -> str:
    """List all versions of a document.

    Returns all versions with the latest version number and total count.

    Args:
        document_id: Document ID (e.g., 'DOC-xxx').
    """
    try:
        data = await get_client().get_document_versions(document_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def archive_document(document_id: str) -> str:
    """Archive a document (soft-delete, sets status to inactive).

    Args:
        document_id: Document ID to archive.
    """
    try:
        data = await get_client().archive_document(document_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def update_document(
    document_id: str,
    patch: dict,
    if_match: int | None = None,
) -> str:
    """Apply a partial update to a document via RFC 7396 JSON Merge Patch.

    Creates a new version (N+1) of the document. The previous version becomes
    inactive. NATS DOCUMENT_UPDATED is published — same event reporting-sync
    consumes for new versions, so the reporting layer refreshes automatically.

    Args:
        document_id: Document ID (e.g. 'DOC-xxx') or registered synonym.
        patch: JSON Merge Patch applied to the document's `data` field.
            - Objects are deep-merged
            - Arrays are REPLACED entirely (not merged element-wise)
            - `null` values DELETE the corresponding key
        if_match: Optional optimistic concurrency control. If supplied, the
            patch fails with `concurrency_conflict` unless the current
            version matches.

    Restrictions:
        - Cannot change identity fields (use create_document to create a new
          document instead — error code `identity_field_change`).
        - Cannot patch archived documents (unarchive first — `archived`).
        - Soft-deleted / non-existent documents return `not_found`.
        - The merged document must still validate against the template the
          document was originally created with (PATCH does NOT migrate to a
          newer template version).

    Example — update one field:
        update_document("DOC-123", {"score": 92})

    Example — delete an optional field via null:
        update_document("DOC-123", {"middle_name": None})

    Example — concurrency-safe update:
        update_document("DOC-123", {"status": "approved"}, if_match=4)
    """
    try:
        data = await get_client().update_document(
            document_id, patch, if_match=if_match,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_document(
    document_id: str,
    hard_delete: bool = False,
    version: int | None = None,
) -> str:
    """Delete a document. Soft-delete (deactivate) by default.
    Set hard_delete=true to permanently remove (requires namespace deletion_mode='full').

    Args:
        document_id: Document ID to delete.
        hard_delete: Permanently remove (requires namespace deletion_mode='full').
        version: Specific version to hard-delete (default: all versions). Ignored for soft-delete.
    """
    try:
        data = await get_client().delete_document(
            document_id, hard_delete=hard_delete, version=version,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Import/Export
# ===================================================================


@mcp.tool()
async def export_terminology(
    terminology_id: str,
    format: str = "json",
    include_relationships: bool = True,
) -> str:
    """Export a terminology with all its terms (and optionally relationships).

    Args:
        terminology_id: Terminology ID, value code (e.g., 'COUNTRY'), or synonym.
        format: 'json' or 'csv'.
        include_relationships: Include ontology relationships in export.
    """
    try:
        data = await get_client().export_terminology(
            terminology_id=terminology_id,
            format=format,
            include_relationships=include_relationships,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def import_terminology(
    data: dict | list,
    format: str = "json",
    skip_duplicates: bool = True,
    update_existing: bool = False,
    namespace: str | None = None,
) -> str:
    """Import a terminology with terms from JSON data.

    Args:
        data: The terminology data to import (JSON format).
        format: Import format ('json').
        skip_duplicates: Skip terms that already exist.
        update_existing: Update existing terms with new data.
        namespace: Target namespace. Omit to use server default.
    """
    try:
        result = await get_client().import_terminology(
            data=data,
            format=format,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
            namespace=namespace,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Table View (Denormalized Export)
# ===================================================================


@mcp.tool()
async def get_table_view(
    template_value: str,
    status: str | None = None,
    page: int = 1,
    page_size: int = 100,
    namespace: str | None = None,
) -> str:
    """Get a flattened, spreadsheet-like view of documents for a template.

    Arrays are cross-product expanded into rows (up to 1000 row limit).
    Returns columns with type info and denormalized rows — ideal for
    data analysis and review.

    Args:
        template_value: Template value code (e.g., 'PATIENT').
        status: Filter by status (default: active).
        page: Page number (default: 1).
        page_size: Rows per page (default: 100, max: 1000).
        namespace: Namespace filter.
    """
    try:
        tmpl = await get_client().get_template_by_value(
            value=template_value, namespace=namespace
        )
        template_id = tmpl.get("template_id")
        data = await get_client().get_table_view(
            template_id=template_id, status=status, page=page, page_size=page_size
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def export_table_csv(
    template_value: str,
    status: str | None = None,
    include_metadata: bool = True,
    namespace: str | None = None,
) -> str:
    """Export documents for a template as CSV.

    Returns the raw CSV content. Useful for data export, sharing, or
    importing into spreadsheets or other tools.

    Args:
        template_value: Template value code (e.g., 'PATIENT').
        status: Filter by status (default: active).
        include_metadata: Include document_id, version, timestamps (default: true).
        namespace: Namespace filter.
    """
    try:
        tmpl = await get_client().get_template_by_value(
            value=template_value, namespace=namespace
        )
        template_id = tmpl.get("template_id")
        csv_content = await get_client().export_table_csv(
            template_id=template_id, status=status, include_metadata=include_metadata
        )
        # Truncate if very large to avoid overwhelming the AI
        lines = csv_content.split("\n")
        if len(lines) > 102:
            return "\n".join(lines[:102]) + f"\n\n... truncated ({len(lines)} total lines)"
        return csv_content
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Search & Reporting
# ===================================================================


@mcp.tool()
async def search(
    query: str,
    types: list[str] | None = None,
    namespace: str | None = None,
    limit: int = 20,
) -> str:
    """Unified search across all WIP entities (via reporting-sync).

    Args:
        query: Search string.
        types: Filter by entity type: 'terminology', 'term', 'template', 'document'.
        namespace: Filter by namespace. Omit to search all namespaces.
        limit: Max results.
    """
    try:
        data = await get_client().unified_search(
            query=query, types=types, namespace=namespace, limit=limit
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def search_registry(
    query: str,
    namespace: str | None = None,
    entity_type: str | None = None,
) -> str:
    """Search the Registry for entries by ID or composite key.

    Args:
        query: Search string (matches entry IDs and composite key values).
        namespace: Filter by namespace.
        entity_type: Filter by type: 'terminologies', 'terms', 'templates', 'documents'.
    """
    try:
        data = await get_client().search_registry(
            query=query, namespace=namespace, entity_type=entity_type
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Files
# ===================================================================


@mcp.tool()
async def list_files(
    namespace: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List files stored in WIP (binary file storage via MinIO)."""
    try:
        data = await get_client().list_files(
            namespace=namespace, page=page, page_size=page_size
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_file_metadata(file_id: str) -> str:
    """Get metadata for a file (not the file content itself)."""
    try:
        data = await get_client().get_file(file_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def upload_file(
    file_path: str,
    namespace: str | None = None,
    description: str | None = None,
    tags: str | None = None,
    category: str | None = None,
) -> str:
    """Upload a file to WIP from a local path. Returns file_id and metadata.

    Args:
        file_path: Absolute path to the file on the local machine.
        namespace: Namespace to store the file in. Uses WIP_MCP_DEFAULT_NAMESPACE if omitted.
        description: Optional description of the file.
        tags: Optional comma-separated tags (e.g., 'receipt,2024,tax').
        category: Optional category (e.g., 'receipt', 'manual', 'report').
    """
    import mimetypes

    try:
        client = get_client()
        namespace = client._ns(namespace)
        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"
        if not p.is_file():
            return f"Error: Not a file: {file_path}"

        content = p.read_bytes()
        content_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        tag_list = [t.strip() for t in tags.split(",")] if tags else None

        data = await client.upload_file(
            file_content=content,
            filename=p.name,
            content_type=content_type,
            namespace=namespace,
            description=description,
            tags=tag_list,
            category=category,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_file(file_id: str, force: bool = False) -> str:
    """Soft-delete a file (sets status to inactive).

    Blocked if documents reference the file unless force=true.

    Args:
        file_id: File ID to delete.
        force: Force deletion even if referenced by documents.
    """
    try:
        data = await get_client().delete_file(file_id, force=force)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def hard_delete_file(file_id: str) -> str:
    """Permanently delete a file from MinIO storage.

    The file must already be soft-deleted (inactive). This is irreversible
    and reclaims storage. Use delete_file first, then hard_delete_file.

    Args:
        file_id: File ID to permanently remove.
    """
    try:
        data = await get_client().hard_delete_file(file_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_file_documents(file_id: str) -> str:
    """Find which documents reference a file.

    Returns document IDs, template info, and the field path where
    the file is referenced.

    Args:
        file_id: File ID to check references for.
    """
    try:
        data = await get_client().get_file_documents(file_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Template-Aware Query
# ===================================================================


@mcp.tool()
async def get_template_fields(
    template_value: str,
    namespace: str | None = None,
) -> str:
    """Get a clean summary of a template's fields — name, type, mandatory, references.

    Use this to understand what fields a template has before querying or creating
    documents. Returns the template_id so you can use it directly in queries.

    Args:
        template_value: Template value code (e.g., 'PATIENT', 'BANK_TRANSACTION').
        namespace: Optional namespace filter.
    """
    try:
        tmpl = await get_client().get_template_by_value(
            value=template_value, namespace=namespace
        )
        fields = tmpl.get("fields", [])
        summary = {
            "template_id": tmpl.get("template_id"),
            "template_value": tmpl.get("value"),
            "version": tmpl.get("version"),
            "namespace": tmpl.get("namespace"),
            "identity_fields": tmpl.get("identity_fields", []),
            "fields": [
                {
                    "name": f["name"],
                    "type": f.get("type", "string"),
                    "mandatory": f.get("mandatory", False),
                    **({"terminology_ref": f["terminology_ref"]} if f.get("terminology_ref") else {}),
                    **({"template_ref": f["template_ref"]} if f.get("template_ref") else {}),
                    **({"semantic_type": f["semantic_type"]} if f.get("semantic_type") else {}),
                }
                for f in fields
            ],
        }
        return json.dumps(summary, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def query_by_template(
    template_value: str,
    field_filters: list[dict] | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    namespace: str | None = None,
) -> str:
    """Query documents by template value with easy field filtering.

    Resolves template_value to template_id automatically. Field names are
    auto-prefixed with 'data.' so you can write 'country' instead of 'data.country'.

    Args:
        template_value: Template value code (e.g., 'PATIENT').
        field_filters: List of filters. Each: {field, operator, value}.
            Field names are auto-prefixed with 'data.' if needed.
            Operators: eq, ne, gt, gte, lt, lte, in, nin, exists, regex.
            Example: [{"field": "country", "operator": "eq", "value": "CH"}]
        status: Filter by document status (default: active).
        namespace: Namespace filter.
    """
    try:
        # Resolve template_value → template_id
        tmpl = await get_client().get_template_by_value(
            value=template_value, namespace=namespace
        )
        template_id = tmpl.get("template_id")

        # Build query body
        query: dict = {
            "template_id": template_id,
            "page": page,
            "page_size": page_size,
        }
        if status:
            query["status"] = status
        if namespace:
            query["namespace"] = namespace

        # Build filters with auto-prefix
        if field_filters:
            filters = []
            for f in field_filters:
                field = f.get("field", "")
                # Auto-prefix with data. if not already prefixed
                if not field.startswith("data.") and field not in (
                    "document_id", "template_id", "namespace", "status",
                    "version", "created_at", "updated_at",
                ):
                    field = f"data.{field}"
                filters.append({
                    "field": field,
                    "operator": f.get("operator", "eq"),
                    "value": f.get("value"),
                })
            query["filters"] = filters

        data = await get_client().query_documents(query)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Reporting & SQL Query
# ===================================================================


@mcp.tool()
async def list_report_tables(table_name: str | None = None) -> str:
    """List available reporting tables in PostgreSQL (doc_* tables + terminologies/terms).

    Use this to discover what tables exist before running SQL queries.

    Args:
        table_name: If omitted, returns a compact summary of all tables (name,
            row_count, column_count) — typically a few KB. If provided, returns
            full column detail (name, type, nullable) for that specific table.
            Call without table_name first to discover tables, then with table_name
            to inspect columns before writing SQL.
    """
    try:
        data = await get_client().list_report_tables(table_name=table_name)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def run_report_query(
    sql: str,
    params: list | None = None,
    max_rows: int = 1000,
) -> str:
    """Execute a read-only SQL query against the PostgreSQL reporting database.

    Use this for cross-template JOINs, aggregations, and analytics that
    aren't possible with the document query API.

    Args:
        sql: SQL SELECT query. Must be read-only (no INSERT/UPDATE/DELETE/DROP).
            Table names: doc_{template_value} (e.g., doc_patient, doc_bank_transaction).
            Term fields have two columns: {field} (value) and {field}_term_id.
            Use list_report_tables() first to discover available tables and columns.
        params: Optional list of parameter values for $1, $2, etc. placeholders.
        max_rows: Maximum rows to return (default 1000).

    Example:
        run_report_query("SELECT name, country FROM doc_patient WHERE country = $1", ["CH"])
    """
    try:
        data = await get_client().run_report_query(
            sql=sql, params=params, max_rows=max_rows
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def export_report_csv(
    table: str | None = None,
    sql: str | None = None,
    params: list | None = None,
) -> str:
    """Export reporting data as CSV (streaming from PostgreSQL).

    Two modes:
    - Table mode: export an entire reporting table as CSV.
    - Query mode: export a SQL query result as CSV.

    Provide either `table` or `sql`, not both.

    Args:
        table: Table name to export (e.g., 'doc_patient', 'terminologies').
            Only doc_* tables and metadata tables are allowed.
        sql: SQL SELECT query. Must be read-only. Use for filtered/joined exports.
        params: Optional parameter values for $1, $2, etc. in the SQL query.

    Example:
        export_report_csv(table="doc_patient")
        export_report_csv(sql="SELECT name FROM doc_patient WHERE country = $1", params=["CH"])
    """
    if not table and not sql:
        return "Error: provide either 'table' or 'sql'"
    if table and sql:
        return "Error: provide either 'table' or 'sql', not both"
    try:
        csv_content = await get_client().export_report_csv(
            table=table, sql=sql, params=params
        )
        lines = csv_content.split("\n")
        if len(lines) > 102:
            return "\n".join(lines[:102]) + f"\n\n... truncated ({len(lines)} total lines)"
        return csv_content
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Import (CSV/XLSX)
# ===================================================================


@mcp.tool()
async def import_documents_csv(
    file_path: str,
    template_value: str,
    column_mapping: dict | None = None,
    namespace: str | None = None,
    skip_errors: bool = True,
) -> str:
    """Import documents from a CSV or XLSX file into a template.

    Reads a local file, maps columns to template fields, and creates
    documents in bulk. If column_mapping is omitted, columns are
    auto-mapped to template fields by matching names (case-insensitive).

    Args:
        file_path: Path to a CSV or XLSX file.
        template_value: Template value code (e.g., 'PATIENT').
        column_mapping: Optional {csv_column: template_field} mapping.
            If omitted, auto-maps columns whose names match field names.
        namespace: Namespace. Uses WIP_MCP_DEFAULT_NAMESPACE if omitted.
        skip_errors: Skip rows that fail validation (default: true).
    """
    try:
        client = get_client()
        ns = client._ns(namespace)
        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"

        content = p.read_bytes()

        # If no mapping provided, auto-map by getting template fields
        if column_mapping is None:
            # Preview to get headers
            preview = await client.preview_import(content, p.name)
            if "error" in preview:
                return f"Error: {preview['error']}"

            headers = preview.get("headers", [])

            # Get template fields
            tmpl = await client.get_template_by_value(value=template_value)
            fields = tmpl.get("fields", [])
            field_names = {f["name"].lower(): f["name"] for f in fields}

            # Auto-map: match CSV headers to field names (case-insensitive)
            column_mapping = {}
            for header in headers:
                lower = header.lower().replace(" ", "_")
                if lower in field_names:
                    column_mapping[header] = field_names[lower]

            if not column_mapping:
                return (
                    f"Error: No columns could be auto-mapped.\n"
                    f"CSV columns: {headers}\n"
                    f"Template fields: {[f['name'] for f in fields]}\n"
                    f"Provide an explicit column_mapping."
                )

        # Resolve template_id
        tmpl = await client.get_template_by_value(value=template_value)
        template_id = tmpl.get("template_id")

        result = await client.import_documents(
            file_content=content,
            filename=p.name,
            template_id=template_id,
            column_mapping=column_mapping,
            namespace=ns,
            skip_errors=skip_errors,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Event Replay
# ===================================================================


@mcp.tool()
async def start_replay(
    template_value: str | None = None,
    template_id: str | None = None,
    namespace: str | None = None,
    throttle_ms: int = 10,
    batch_size: int = 100,
) -> str:
    """Start replaying stored documents as NATS events.

    Replayed events go to a dedicated NATS stream with metadata.replay=true.
    Use this to onboard new consumers or backfill data.

    Args:
        template_value: Replay documents for this template (optional, replays all if omitted).
        template_id: Alternative to template_value — template ID, value code (e.g., 'PERSON'), or synonym.
        namespace: Namespace to replay from. Uses WIP_MCP_DEFAULT_NAMESPACE if omitted.
        throttle_ms: Delay between events in ms (0-5000, default 10).
        batch_size: Documents per batch (10-1000, default 100).
    """
    try:
        client = get_client()
        namespace = client._ns(namespace)
        filter_config = {"namespace": namespace, "status": "active"}
        if template_value:
            filter_config["template_value"] = template_value
        if template_id:
            filter_config["template_id"] = template_id

        data = await client.start_replay(
            filter_config=filter_config,
            throttle_ms=throttle_ms,
            batch_size=batch_size,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_replay_status(session_id: str) -> str:
    """Get the current status of a replay session (published count, total, status)."""
    try:
        data = await get_client().get_replay_session(session_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def cancel_replay(session_id: str) -> str:
    """Cancel a replay session and delete its NATS stream."""
    try:
        data = await get_client().cancel_replay(session_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def pause_replay(session_id: str) -> str:
    """Pause a running replay session. Can be resumed later.

    Args:
        session_id: The replay session ID.
    """
    try:
        data = await get_client().pause_replay(session_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def resume_replay(session_id: str) -> str:
    """Resume a paused replay session.

    Args:
        session_id: The replay session ID.
    """
    try:
        data = await get_client().resume_replay(session_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def start_backup(
    namespace: str | None = None,
    include_files: bool = False,
    include_inactive: bool = False,
    skip_documents: bool = False,
    skip_closure: bool = False,
    skip_synonyms: bool = False,
    latest_only: bool = False,
    template_prefixes: list[str] | None = None,
    dry_run: bool = False,
) -> str:
    """Start a backup of a namespace. Returns the initial BackupJobSnapshot.

    Backups run in the background; poll get_backup_job to track progress until
    status is 'complete' or 'failed', then download_backup_archive to fetch
    the .zip.

    WARNING — v1.0 limitation: include_files=true is unsafe on namespaces with
    non-trivial file content (CASE-28: ArchiveWriter buffers all blob bytes in
    RAM and will OOM the document-store container). Leave it false until
    CASE-28 lands.

    Args:
        namespace: Source namespace (uses WIP_MCP_DEFAULT_NAMESPACE if unset).
        include_files: Include file blobs in the archive (see WARNING above).
        include_inactive: Include soft-deleted entities.
        skip_documents: Skip the documents phase entirely (definitions only).
        skip_closure: Skip the closure-table (relationships) phase.
        skip_synonyms: Skip the synonyms phase.
        latest_only: Export only the latest version of each entity.
        template_prefixes: Optional template_id prefixes to filter documents.
        dry_run: Walk the export without writing the archive.
    """
    try:
        data = await get_client().start_backup(
            namespace=namespace,
            include_files=include_files,
            include_inactive=include_inactive,
            skip_documents=skip_documents,
            skip_closure=skip_closure,
            skip_synonyms=skip_synonyms,
            latest_only=latest_only,
            template_prefixes=template_prefixes,
            dry_run=dry_run,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def start_restore(
    namespace: str,
    archive_path: str,
    mode: str = "restore",
    target_namespace: str | None = None,
    register_synonyms: bool = False,
    skip_documents: bool = False,
    skip_files: bool = False,
    batch_size: int = 50,
    continue_on_error: bool = False,
    dry_run: bool = False,
) -> str:
    """Restore a namespace from a local archive file. Returns the initial BackupJobSnapshot.

    The archive at archive_path is uploaded as multipart and a restore job is
    queued. Poll get_backup_job to track progress.

    GOTCHA — restore mode semantics:
    - mode='restore' writes back to the *source* namespace embedded in the
      archive and IGNORES target_namespace. Use this only when restoring an
      archive into the same namespace it came from.
    - mode='fresh' generates new IDs and honors target_namespace. Use this for
      round-trip into a new namespace.

    Args:
        namespace: URL-path namespace (the auth check target).
        archive_path: Local filesystem path to the .zip archive to upload.
        mode: 'restore' (preserve IDs) or 'fresh' (generate new IDs).
        target_namespace: Override target namespace. Honored only in 'fresh' mode.
        register_synonyms: Register original IDs as synonyms of new IDs (fresh mode).
        skip_documents: Skip restoring documents (definitions only).
        skip_files: Skip restoring file blobs.
        batch_size: Restore batch size (1-500).
        continue_on_error: Continue past per-item errors.
        dry_run: Walk the import without applying changes.
    """
    try:
        data = await get_client().start_restore(
            namespace=namespace,
            archive_path=archive_path,
            mode=mode,
            target_namespace=target_namespace,
            register_synonyms=register_synonyms,
            skip_documents=skip_documents,
            skip_files=skip_files,
            batch_size=batch_size,
            continue_on_error=continue_on_error,
            dry_run=dry_run,
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_backup_job(job_id: str) -> str:
    """Get the current state of a backup or restore job.

    Returns a BackupJobSnapshot with status, phase, percent, message, and
    archive_size (when complete). Poll this to track progress.
    """
    try:
        data = await get_client().get_backup_job(job_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def list_backup_jobs(
    namespace: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> str:
    """List recent backup/restore jobs, optionally filtered.

    Args:
        namespace: Filter by namespace.
        status: Filter by status ('pending', 'running', 'complete', 'failed').
        limit: Max jobs to return (1-500, default 50).
    """
    try:
        data = await get_client().list_backup_jobs(
            namespace=namespace, status=status, limit=limit
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def download_backup_archive(job_id: str, dest_path: str) -> str:
    """Download a completed backup archive to a local file.

    Streams the .zip from the document-store to dest_path. Job must be a
    backup job in 'complete' status. Parent directories of dest_path are
    created automatically.

    Args:
        job_id: The backup job ID.
        dest_path: Local filesystem path to write the .zip to.
    """
    try:
        data = await get_client().download_backup_archive(job_id, dest_path)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def delete_backup_job(job_id: str) -> str:
    """Delete a backup or restore job and its archive file.

    Cannot delete a running job. Removes the MongoDB record and the archive
    file from disk.
    """
    try:
        data = await get_client().delete_backup_job(job_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_sync_status() -> str:
    """Get the reporting-sync service status.

    Shows NATS connection, PostgreSQL connection, events processed/failed,
    and tables managed. Useful for diagnosing sync issues.
    """
    try:
        data = await get_client().get_sync_status()
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Schema patching — enrich tools with OpenAPI-derived field schemas
# ===================================================================


def _patch_tool_schemas():
    """Patch tool descriptions and parameter schemas from generated OpenAPI data.

    This function runs at import time. It reads tools.yaml for hand-written
    metadata and _generated_schemas.py for OpenAPI-derived field definitions,
    then patches the registered MCP tools so the AI sees correct field names.
    """
    try:
        from ._generated_schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS
    except ImportError:
        # Generated file doesn't exist yet — run generate_schemas.py first
        return

    config_path = Path(__file__).parent.parent.parent / "tools.yaml"
    if not config_path.exists():
        return

    try:
        import yaml
    except ImportError:
        return

    config = yaml.safe_load(config_path.read_text())
    tools_config = config.get("tools", {})

    for tool_name, tc in tools_config.items():
        # Find the registered tool
        registered_tools = mcp._tool_manager.list_tools()
        tool = None
        for t in registered_tools:
            if t.name == tool_name:
                tool = t
                break
        if not tool:
            continue

        # Patch description with composed version (hand-written + generated)
        if tool_name in TOOL_DESCRIPTIONS:
            tool.description = TOOL_DESCRIPTIONS[tool_name]

        # Patch parameter schema for dict params
        schema_ref = tc.get("openapi_schema")
        param_name = tc.get("param_name")
        if schema_ref and param_name and schema_ref in TOOL_SCHEMAS:
            openapi_schema = TOOL_SCHEMAS[schema_ref]
            props = tool.parameters.get("properties", {})
            if param_name in props:
                if tc.get("param_type") == "list":
                    props[param_name] = {
                        "type": "array",
                        "items": openapi_schema,
                        "description": props[param_name].get("description", ""),
                    }
                else:
                    # Merge OpenAPI schema into the existing property
                    merged = dict(openapi_schema)
                    if "description" in props[param_name]:
                        merged.setdefault("description", props[param_name]["description"])
                    props[param_name] = merged


_patch_tool_schemas()


# ---------------------------------------------------------------------------
# Read-only mode: WIP_MCP_MODE=readonly removes all write tools
# ---------------------------------------------------------------------------

WRITE_TOOLS = frozenset({
    # Terminologies
    "create_terminology",
    "create_terminologies_bulk",
    "update_terminology",
    "delete_terminology",
    "restore_terminology",
    # Terms
    "create_terms",
    "update_term",
    "delete_term",
    "deprecate_term",
    # Relationships
    "create_relationships",
    "delete_relationships",
    # Templates
    "create_template",
    "create_templates_bulk",
    "update_template",
    "activate_template",
    "deactivate_template",
    # Documents
    "create_document",
    "create_documents_bulk",
    "update_document",
    "archive_document",
    "delete_document",
    # Files
    "upload_file",
    "delete_file",
    "hard_delete_file",
    # Import
    "import_terminology",
    "import_documents_csv",
    # Replay
    "start_replay",
    "cancel_replay",
    "pause_replay",
    "resume_replay",
    # Backup / Restore
    "start_backup",
    "start_restore",
    "delete_backup_job",
    # Registry write ops
    "add_synonym",
    "remove_synonym",
    "merge_entries",
    # Namespace
    "create_namespace",
    "delete_namespace",
})


def _apply_read_only_mode():
    """Remove write tools when WIP_MCP_MODE=readonly."""
    mode = os.getenv("WIP_MCP_MODE", "").lower()
    if mode != "readonly":
        return

    removed = []
    for tool_name in WRITE_TOOLS:
        try:
            mcp._tool_manager.remove_tool(tool_name)
            removed.append(tool_name)
        except (KeyError, ValueError):
            pass  # Tool not registered (shouldn't happen, but safe)

    # Update server instructions to reflect read-only mode
    mcp._mcp_server.instructions = (
        "World In a Pie (WIP) — a universal template-driven document storage "
        "system. This server is running in READ-ONLY mode. You can discover "
        "WIP's data model, query data, search, and run reports, but you "
        "CANNOT create, modify, or delete any entities. "
        "KEY CAPABILITIES: (1) Terms support ontology relationships (is_a, "
        "part_of, etc.) for hierarchical data modeling — use "
        "get_term_hierarchy. (2) A PostgreSQL reporting layer enables SQL "
        "aggregations, cross-template JOINs, and analytics via run_report_query. "
        "Read the wip://conventions and wip://data-model resources to "
        "understand WIP's data model before querying."
    )

    print(
        f"MCP read-only mode: removed {len(removed)} write tools, "
        f"{len(mcp._tool_manager.list_tools())} tools available",
        file=sys.stderr,
    )


_apply_read_only_mode()


# ===================================================================
# Entry point
# ===================================================================


def main():
    if "--http" in sys.argv:
        transport = "streamable-http"
    elif "--sse" in sys.argv:
        transport = "sse"
    else:
        transport = "stdio"

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        api_key = os.getenv("API_KEY") or os.getenv("WIP_AUTH_LEGACY_API_KEY")
        if not api_key:
            print(
                f"WARNING: MCP server running in {transport} mode without API key protection.\n"
                "Anyone with network access will have full CRUD access via AI tools.\n"
                "Set API_KEY environment variable for network transports.",
                file=sys.stderr,
            )

        # Configure allowed hosts for DNS rebinding protection
        allowed_host = os.getenv("MCP_ALLOWED_HOST")
        if allowed_host and mcp.settings.transport_security:
            ts = mcp.settings.transport_security
            ts.allowed_hosts.append(allowed_host)
            ts.allowed_hosts.append(f"{allowed_host}:*")
            ts.allowed_origins.append(f"https://{allowed_host}")
            ts.allowed_origins.append(f"https://{allowed_host}:*")

        # Create the transport-appropriate Starlette app
        starlette_app = (
            mcp.streamable_http_app() if transport == "streamable-http"
            else mcp.sse_app()
        )

        # /health — unauthenticated liveness probe. Lets orchestrators
        # (compose, k8s) check the server is up without needing to know
        # the API key. Returns 200 OK as long as the uvicorn worker is
        # serving requests. Registered BEFORE the auth middleware so the
        # middleware's path-skip list can exempt it cleanly.
        from starlette.routing import Route

        async def health(_request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "service": "mcp-server"})

        starlette_app.router.routes.append(Route("/health", health, methods=["GET"]))

        # API key auth middleware (M7). Skips /health so orchestration
        # probes don't need to carry credentials.
        if api_key:
            from starlette.middleware.base import BaseHTTPMiddleware

            _UNAUTH_PATHS = {"/health"}

            class ApiKeyMiddleware(BaseHTTPMiddleware):
                async def dispatch(self, request: Request, call_next):
                    if request.url.path in _UNAUTH_PATHS:
                        return await call_next(request)
                    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
                    if not key or key != api_key:
                        return JSONResponse(
                            {"error": "Missing or invalid API key. Pass X-API-Key header."},
                            status_code=401,
                        )
                    return await call_next(request)

            starlette_app.add_middleware(ApiKeyMiddleware)

        # Allow port override via env var (default: FastMCP's 8000)
        port = int(os.getenv("MCP_PORT", mcp.settings.port))
        host = os.getenv("MCP_HOST", "0.0.0.0")

        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level=mcp.settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        import anyio
        anyio.run(server.serve)


if __name__ == "__main__":
    main()
