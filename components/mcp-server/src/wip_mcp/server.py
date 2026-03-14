"""WIP MCP Server — exposes World In a Pie as MCP tools and resources.

This server is designed for AI-assisted development workflows. An AI building
applications on top of WIP connects to this server to discover, create, and
query WIP entities without constructing raw HTTP calls.

Run with:  python -m wip_mcp.server          (stdio, for Claude Code / Cursor)
           python -m wip_mcp.server --sse     (SSE, for remote clients)
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
        "for you — single-item calls return the unwrapped result directly."
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
    """WIP API conventions: bulk-first pattern, identity hashing, versioning."""
    return """# WIP API Conventions

## Bulk-First: Every Write Endpoint is Bulk
All write endpoints (POST/PUT/DELETE) accept a JSON array and return BulkResponse.
Single operations are just [item]. There are no single-entity write endpoints.

Response: { results: [...], total: N, succeeded: N, failed: N }
- Always HTTP 200 — errors are per-item in results[i].status == "error"
- Never check HTTP status codes for duplicates/conflicts; check result.status
- Updates use PUT with entity ID in the body (not URL)
- Deletes use DELETE with body: [{"id": "..."}]

NOTE: This MCP server handles the bulk envelope for you. Single-item tool calls
(create_terminology, create_template, create_document) unwrap the response and
return the result directly. Bulk tool calls return the full envelope.

## Identity & Versioning
- Templates: (namespace, template_id, version) — same template_id across versions
- Documents: (namespace, document_id, version) — identity_fields control dedup
- Terms: (namespace, terminology_id, value) — unique within terminology

## Namespaces
All entities are scoped to a namespace. Default: "wip". Namespaces provide
multi-tenant isolation. Same entity_id can exist in different namespaces.

## Soft Delete
Data is never hard-deleted, only set to status: "inactive".
Exception: files support hard-delete to reclaim storage.

## Pagination
Default page_size: 50, max: 100. All list responses include a `pages` field.
"""


@mcp.resource("wip://data-model")
def get_data_model() -> str:
    """WIP core data model: terminologies, terms, templates, documents."""
    return """# WIP Core Data Model

## Terminologies
A terminology is a controlled vocabulary (e.g., COUNTRY, GENDER, DIAGNOSIS_CODE).
- Fields: value (unique code), label (display name), description
- Terms belong to a terminology

## Terms
A term is an entry in a terminology (e.g., "GB" in COUNTRY, "Male" in GENDER).
- Fields: value (unique within terminology), label, aliases, description
- Terms can have ontology relationships (is_a, part_of, etc.)
- Documents store both the original value AND the resolved term_id

## Templates
A template defines a document schema — like a form definition.
- Fields define type, mandatory/optional, terminology references, validation rules
- Templates support inheritance (extends another template)
- Templates are versioned: same template_id, incrementing version
- Field types: string, number, integer, boolean, date, term, reference, file, array, object
- Use "mandatory: true" (NOT "required") for required fields
- A term field uses "terminology_ref" to specify which terminology_id it draws from
- A reference field uses "template_ref" to specify which template_id it references
- Templates can define identity_fields for document deduplication

## Documents
A document is an instance of a template — a filled-in form.
- Validated against the template's field definitions
- Terms are resolved: you submit the value, WIP stores both value and term_id
- Versioned: same identity → same document_id, new version
- identity_fields (defined on template) control what makes a document "the same"

## Files
Binary files stored in MinIO, referenced by documents.
- Upload returns a file_id (FILE-XXXXXX)
- Link the file_id to a document's file field

## Relationships (Ontology)
Terms can be connected via typed relationships:
- is_a, part_of, has_part, regulates, positively_regulates, negatively_regulates
- Fields: source_term_id, target_term_id, relationship_type
- Supports traversal: ancestors, descendants, parents, children
"""


@mcp.resource("wip://development-guide")
def get_development_guide() -> str:
    """How to build applications on WIP — the 4-phase process."""
    return """# Building Applications on WIP

## The Golden Rule
Never modify WIP. Only consume its APIs.

## Phase 1: Exploratory
Understand WIP's capabilities. Use these tools:
- get_wip_status — check all services are running
- list_namespaces — see available namespaces
- list_terminologies — see existing controlled vocabularies
- list_templates — see existing document schemas

## Phase 2: Data Model Design
Map your domain onto WIP primitives:
1. Identify controlled vocabularies → create terminologies
2. Identify document types → design templates with fields
3. Define relationships between templates (references, inheritance)
4. Define identity_fields for deduplication

Use create_terminology, create_terms, create_template (with status: "draft"
for circular dependencies, then activate_template).

## Phase 3: Implementation
Create the data model in WIP:
1. Create terminologies and populate with terms
2. Create templates (use draft mode if needed)
3. Create test documents to verify validation
4. Verify term resolution and reference resolution work

## Phase 4: Application Layer
Build the frontend app using @wip/client and @wip/react.
The MCP server is mainly useful in Phases 1-3. In Phase 4,
the app itself uses the TypeScript client library.

## Key Patterns
- Template inheritance: create a base template, extend it
- Term resolution: submit human-readable values, WIP resolves to term_ids
- Identity hashing: define identity_fields so duplicate submissions update, not duplicate
- Draft mode: create templates with status: "draft" to handle circular deps
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


@mcp.tool()
async def list_namespaces(include_archived: bool = False) -> str:
    """List all WIP namespaces. Namespaces scope all entities (terminologies, templates, documents)."""
    try:
        data = await get_client().list_namespaces(include_archived=include_archived)
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
    """Get a terminology by its ID (e.g., 'T-xxxxxxxx')."""
    try:
        data = await get_client().get_terminology(terminology_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_terminology_by_value(value: str) -> str:
    """Get a terminology by its value code (e.g., 'COUNTRY', 'GENDER'). Case-sensitive."""
    try:
        data = await get_client().get_terminology_by_value(value)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_terminology(
    value: str,
    label: str,
    namespace: str = "wip",
    description: str | None = None,
) -> str:
    """Create a terminology (controlled vocabulary).

    Args:
        value: Unique code (e.g., 'COUNTRY', 'GENDER'). Convention: UPPER_SNAKE_CASE.
        label: Human-readable name (e.g., 'Country', 'Gender').
        namespace: Namespace to create in. Default: 'wip'.
        description: Optional description of what this terminology contains.
    """
    try:
        kwargs = {}
        if description:
            kwargs["description"] = description
        data = await get_client().create_terminology(
            value=value, label=label, namespace=namespace, **kwargs
        )
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_terminologies_bulk(items: list[dict]) -> str:
    """Create multiple terminologies at once.

    Args:
        items: List of {value, label, namespace?, description?} objects.
    """
    try:
        data = await get_client().create_terminologies(items)
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
        terminology_id: The terminology ID (e.g., 'T-xxxxxxxx') or use
            get_terminology_by_value first to find the ID.
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
    """Get a term by its ID."""
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
        terminology_id: The terminology to add terms to.
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
async def validate_term_value(terminology_id: str, value: str) -> str:
    """Validate whether a value exists in a terminology. Use to test term resolution."""
    try:
        data = await get_client().validate_term(
            terminology_id=terminology_id, value=value
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
) -> str:
    """Traverse ontology relationships for a term.

    Args:
        term_id: The term to start from.
        direction: One of 'children', 'parents', 'ancestors', 'descendants'.
        relationship_type: Filter by type (is_a, part_of, has_part, etc.). None = all.
        max_depth: Max traversal depth for ancestors/descendants.
    """
    try:
        client = get_client()
        if direction == "children":
            data = await client.get_term_children(term_id)
        elif direction == "parents":
            data = await client.get_term_parents(term_id)
        elif direction == "ancestors":
            data = await client.get_term_ancestors(
                term_id,
                relationship_type=relationship_type,
                max_depth=max_depth,
            )
        elif direction == "descendants":
            data = await client.get_term_descendants(
                term_id,
                relationship_type=relationship_type,
                max_depth=max_depth,
            )
        else:
            return f"Error: direction must be children, parents, ancestors, or descendants"
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_relationships(relationships: list[dict]) -> str:
    """Create ontology relationships between terms.

    Args:
        relationships: List of {source_term_id, target_term_id, relationship_type, namespace?}.
            relationship_type: is_a, part_of, has_part, regulates, positively_regulates, negatively_regulates.

    Example:
        create_relationships([{
            "source_term_id": "T-001",
            "relationship_type": "is_a",
            "target_term_id": "T-002"
        }])
    """
    try:
        data = await get_client().create_relationships(relationships)
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
        template_id: Template ID (e.g., 'TPL-xxxxxxxx').
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
    """Get a template WITHOUT inheritance resolution. Shows only fields defined directly on this template."""
    try:
        data = await get_client().get_template_raw(template_id)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_template(template: dict) -> str:
    """Create a template (document schema).

    Args:
        template: Template definition. Required fields:
            - value: Unique code (e.g., 'BANK_TRANSACTION'). UPPER_SNAKE_CASE.
            - label: Display name.
            - fields: List of field definitions.
            - namespace: Namespace (default: 'wip').

        Optional:
            - description: What this template represents.
            - extends: Parent template value to inherit from.
            - extends_version: Pin to specific parent version.
            - identity_fields: List of field names for deduplication.
            - status: 'active' (default) or 'draft' (skip validation).

        Field definition: {
            name: str,          # field name
            label: str,         # display label
            type: str,          # string, number, integer, boolean, date, term, reference, file, array, object
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
        data = await get_client().create_template(template)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_templates_bulk(templates: list[dict]) -> str:
    """Create multiple templates. Use status: 'draft' for circular dependencies, then activate."""
    try:
        data = await get_client().create_templates(templates)
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
        template_id: Template to activate.
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
async def get_template_dependencies(template_id: str) -> str:
    """Show what depends on a template: child templates and documents."""
    try:
        data = await get_client().get_template_dependencies(template_id)
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
        template_id: Filter by template ID (alternative to template_value).
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
async def create_document(document: dict) -> str:
    """Create a document (an instance of a template).

    Args:
        document: Document data. Required:
            - template_id: Which template this document uses.
            - data: The field values (a dict matching the template's fields).
            - namespace: Namespace (default: 'wip').

    Term fields: Submit the human-readable value (e.g., "United Kingdom").
    WIP resolves it to the term_id automatically. If resolution fails,
    you'll get a clear error indicating which field/value failed.

    Identity: If the template defines identity_fields and this document
    matches an existing one, it creates a new version instead of a duplicate.

    Example:
        create_document({
            "template_id": "TPL-xxx",
            "namespace": "wip",
            "data": {
                "name": "Jane Doe",
                "email": "jane@example.com",
                "gender": "Female"
            }
        })
    """
    try:
        data = await get_client().create_document(document)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def create_documents_bulk(documents: list[dict]) -> str:
    """Create multiple documents at once. Returns per-item results."""
    try:
        data = await get_client().create_documents(documents)
        return json.dumps(data, indent=2, default=str)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def query_documents(filters: dict) -> str:
    """Query documents with complex filters (low-level). Prefer query_by_template for easier use.

    Args:
        filters: Query body with these fields:
            - template_id: Filter by template ID (required for field filters).
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
        terminology_id: Terminology ID to export.
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
) -> str:
    """Import a terminology with terms from JSON data.

    Args:
        data: The terminology data to import (JSON format).
        format: Import format ('json').
        skip_duplicates: Skip terms that already exist.
        update_existing: Update existing terms with new data.
    """
    try:
        result = await get_client().import_terminology(
            data=data,
            format=format,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _error(e)


# ===================================================================
# Tools — Search & Reporting
# ===================================================================


@mcp.tool()
async def search(
    query: str,
    types: list[str] | None = None,
    limit: int = 20,
) -> str:
    """Unified search across all WIP entities (via reporting-sync).

    Args:
        query: Search string.
        types: Filter by entity type: 'terminology', 'term', 'template', 'document'.
        limit: Max results.
    """
    try:
        data = await get_client().unified_search(
            query=query, types=types, limit=limit
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
    namespace: str = "wip",
    description: str | None = None,
    tags: str | None = None,
    category: str | None = None,
) -> str:
    """Upload a file to WIP from a local path. Returns file_id and metadata.

    Args:
        file_path: Absolute path to the file on the local machine.
        namespace: Namespace to store the file in.
        description: Optional description of the file.
        tags: Optional comma-separated tags (e.g., 'receipt,2024,tax').
        category: Optional category (e.g., 'receipt', 'manual', 'report').
    """
    import mimetypes

    try:
        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"
        if not p.is_file():
            return f"Error: Not a file: {file_path}"

        content = p.read_bytes()
        content_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        tag_list = [t.strip() for t in tags.split(",")] if tags else None

        data = await get_client().upload_file(
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
async def list_report_tables() -> str:
    """List available reporting tables in PostgreSQL (doc_* tables + terminologies/terms).

    Use this to discover what tables exist before running SQL queries.
    Each table includes column names, types, and row counts.
    """
    try:
        data = await get_client().list_report_tables()
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
        namespace: Namespace (default: 'wip').
        skip_errors: Skip rows that fail validation (default: true).
    """
    try:
        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"

        content = p.read_bytes()
        ns = namespace or "wip"

        # If no mapping provided, auto-map by getting template fields
        if column_mapping is None:
            # Preview to get headers
            preview = await get_client().preview_import(content, p.name)
            if "error" in preview:
                return f"Error: {preview['error']}"

            headers = preview.get("headers", [])

            # Get template fields
            tmpl = await get_client().get_template_by_value(value=template_value)
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
        tmpl = await get_client().get_template_by_value(value=template_value)
        template_id = tmpl.get("template_id")

        result = await get_client().import_documents(
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
    namespace: str = "wip",
    throttle_ms: int = 10,
    batch_size: int = 100,
) -> str:
    """Start replaying stored documents as NATS events.

    Replayed events go to a dedicated NATS stream with metadata.replay=true.
    Use this to onboard new consumers or backfill data.

    Args:
        template_value: Replay documents for this template (optional, replays all if omitted).
        template_id: Alternative to template_value — use the template ID directly.
        namespace: Namespace to replay from.
        throttle_ms: Delay between events in ms (0-5000, default 10).
        batch_size: Documents per batch (10-1000, default 100).
    """
    try:
        filter_config = {"namespace": namespace, "status": "active"}
        if template_value:
            filter_config["template_value"] = template_value
        if template_id:
            filter_config["template_id"] = template_id

        data = await get_client().start_replay(
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


# ===================================================================
# Entry point
# ===================================================================


def main():
    transport = "sse" if "--sse" in sys.argv else "stdio"
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
