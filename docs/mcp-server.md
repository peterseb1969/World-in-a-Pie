# MCP Server

The WIP MCP server exposes World In a Pie as an AI-native interface. An AI assistant connects via MCP (Model Context Protocol) and gets structured tools to discover, create, and query all WIP entities — without constructing raw HTTP calls.

**Location:** `components/mcp-server/`

---

## Purpose

The MCP server serves two roles:

1. **AI-assisted development (Phases 1-3):** An AI building applications on WIP uses MCP tools to explore existing data, design data models, and create terminologies/templates/documents. This is the primary use case during the [4-phase development process](#development-workflow).

2. **Conversational data access:** Once apps have populated WIP with structured data, any AI assistant with MCP access can query across all datasets in natural language. This turns WIP into a personal data assistant — the AI queries validated, cross-referenced data instead of guessing from unstructured files.

---

## Running

```bash
# stdio transport (for Claude Code, Cursor, etc.)
python -m wip_mcp.server

# SSE transport (for remote/web clients)
python -m wip_mcp.server --sse
```

### Environment Variables

The MCP server connects **directly** to each service (not via Caddy), because it runs on the same host as the WIP services. These defaults are correct for local development. Application code should use `@wip/client` through the Caddy proxy instead — see `libs/wip-client/README.md`.

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_REGISTRY_URL` | `http://localhost:8001` | Registry service URL (direct) |
| `WIP_DEF_STORE_URL` | `http://localhost:8002` | Def-Store service URL (direct) |
| `WIP_TEMPLATE_STORE_URL` | `http://localhost:8003` | Template-Store service URL (direct) |
| `WIP_DOCUMENT_STORE_URL` | `http://localhost:8004` | Document-Store service URL (direct) |
| `WIP_REPORTING_SYNC_URL` | `http://localhost:8005` | Reporting-Sync service URL (direct) |
| `WIP_API_KEY` | `dev_master_key_for_testing` | API key for authentication |

### Claude Code Configuration

```bash
claude mcp add wip-server -- python -m wip_mcp.server
```

Or in `.claude/mcp.json`:
```json
{
  "mcpServers": {
    "wip-server": {
      "command": "python",
      "args": ["-m", "wip_mcp.server"],
      "cwd": "/path/to/WorldInPie/components/mcp-server"
    }
  }
}
```

---

## Resources (Static Context)

Four resources provide baseline context to the AI without tool calls:

| Resource URI | Description |
|---|---|
| `wip://conventions` | Bulk-first API patterns, identity hashing, versioning, pagination, querying |
| `wip://data-model` | Core entity types: terminologies, terms, templates, documents, files, relationships |
| `wip://development-guide` | The 4-phase development process with guidance per phase |
| `wip://ponifs` | Powerful, Non-Intuitive Features — 6 WIP behaviours that violate conventional expectations, plus the Compactheimer's Warning for AI assistants |

---

## Tools

### Discovery (3 tools)

| Tool | Description |
|------|-------------|
| `get_wip_status()` | Health check for all WIP services. Call first to verify connectivity. |
| `list_namespaces()` | List all namespaces. Namespaces scope all entities. |
| `get_namespace_stats(prefix)` | Entity counts by type for a namespace. |

### Registry Entries & Synonyms (6 tools)

| Tool | Description |
|------|-------------|
| `get_entry(entry_id)` | Get full details for a Registry entry — synonyms, composite keys, metadata. |
| `lookup_entry(entry_id?, namespace?, entity_type?, composite_key?)` | Look up by ID or by composite key. Key lookup also searches synonyms. |
| `add_synonym(target_id, synonym_namespace, synonym_entity_type, synonym_composite_key)` | Add an alternative composite key that resolves to an existing entry. For cross-namespace linking and external ID mapping. |
| `remove_synonym(target_id, synonym_namespace, synonym_entity_type, synonym_composite_key)` | Remove a synonym from a Registry entry. |
| `merge_entries(preferred_id, deprecated_id)` | Merge two entries. Deprecated entry becomes inactive; its ID is added as synonym to the preferred entry. |
| `search_registry(query)` | Search Registry entries by ID or composite key. |

### Terminologies (8 tools)

| Tool | Description |
|------|-------------|
| `list_terminologies()` | List controlled vocabularies. Supports pagination and namespace filter. |
| `get_terminology(id)` | Get by ID (e.g., `T-xxxxxxxx`). |
| `get_terminology_by_value(value)` | Get by value code (e.g., `COUNTRY`). Case-sensitive. |
| `create_terminology(value, label, ...)` | Create a single terminology. Returns unwrapped result. |
| `create_terminologies_bulk(items)` | Create multiple. Returns full `BulkResponse`. |
| `update_terminology(id, label?, description?)` | Update a terminology's label or description. |
| `delete_terminology(id, force?)` | Deactivate (soft-delete). Blocked if terms depend on it unless `force=true`. |
| `restore_terminology(id, restore_terms?)` | Restore a previously deactivated terminology. Optionally reactivates its terms. |

### Terms (7 tools)

| Tool | Description |
|------|-------------|
| `list_terms(terminology_id)` | List terms in a terminology. Supports search filter. |
| `get_term(id)` | Get a term by ID. |
| `create_terms(terminology_id, terms)` | Create terms in bulk. Each needs `value` and `label`. |
| `validate_term_value(terminology_id, value)` | Test whether a value exists in a terminology. |
| `update_term(id, label?, aliases?, description?, sort_order?)` | Update a term's properties. |
| `delete_term(id)` | Deactivate (soft-delete) a term. |
| `deprecate_term(id, reason, replaced_by_term_id?)` | Deprecate with a reason and optional replacement pointer. Term remains queryable but flagged as superseded. |

### Ontology / Relationships (4 tools)

| Tool | Description |
|------|-------------|
| `get_term_hierarchy(term_id, direction)` | Traverse relationships: `children`, `parents`, `ancestors`, `descendants`. Optional `relationship_type` filter. |
| `list_relationships(term_id, direction?, relationship_type?)` | List relationships for a term. Direction: `outgoing`, `incoming`, or `both`. |
| `create_relationships(relationships)` | Create typed relationships (`is_a`, `part_of`, `has_part`, `regulates`, etc.). |
| `delete_relationships(relationships)` | Delete relationships. Each item: `{source_term_id, target_term_id, relationship_type}`. |

### Templates (12 tools)

| Tool | Description |
|------|-------------|
| `list_templates()` | List templates. Supports `status`, `latest_only`, namespace, pagination. |
| `get_template(id, version?)` | Get resolved template (with inherited fields). |
| `get_template_by_value(value)` | Get by value code (e.g., `BANK_TRANSACTION`). |
| `get_template_raw(id)` | Get without inheritance resolution. |
| `get_template_fields(template_value)` | Clean summary of a template's fields — name, type, mandatory, references. Returns `template_id` for use in queries. |
| `get_template_versions(template_value?, template_id?)` | List all versions of a template. Provide either value or ID. |
| `validate_template(template_id)` | Validate a template's references (terminologies, parent templates). Useful before activation. |
| `create_template(template)` | Create a single template. Supports draft mode. |
| `create_templates_bulk(templates)` | Create multiple templates. |
| `activate_template(id)` | Activate a draft template with cascading validation. |
| `deactivate_template(id, version?, force?)` | Soft-delete a template version. Blocked if child templates exist. Use `force=true` to bypass document dependency check. |
| `get_template_dependencies(id)` | Show child templates and documents that depend on this template. |

### Documents (8 tools)

| Tool | Description |
|------|-------------|
| `list_documents()` | List documents. Filter by `template_value`, `template_id`, status, namespace. |
| `get_document(id, version?)` | Get a document by ID. |
| `get_document_versions(document_id)` | List all versions of a document with latest version and total count. |
| `create_document(document)` | Create a single document. Term values are auto-resolved. |
| `create_documents_bulk(documents)` | Create multiple documents. Returns per-item results. |
| `archive_document(document_id)` | Archive (soft-delete) a document. |
| `query_documents(filters)` | Query with complex field-level filters. |
| `query_by_template(template_value, field_filters?, ...)` | Query by template value code. Auto-resolves template_value to template_id. Field names auto-prefixed with `data.`. |

### Table View & Export (2 tools)

| Tool | Description |
|------|-------------|
| `get_table_view(template_value, ...)` | Denormalized view with columns and rows — ideal for data analysis. |
| `export_table_csv(template_value, status?, include_metadata?)` | Export documents as CSV. Returns raw CSV content (truncated at 100 rows for AI context). |

### Import / Export (2 tools)

| Tool | Description |
|------|-------------|
| `export_terminology(id)` | Export terminology with terms and optional relationships. JSON or CSV. |
| `import_terminology(data)` | Import terminology from JSON. Supports `skip_duplicates` and `update_existing`. |

### Search (2 tools)

| Tool | Description |
|------|-------------|
| `search(query, types?)` | Unified full-text search across all entity types (via reporting-sync / PostgreSQL). |
| `search_registry(query)` | Search Registry entries by ID or composite key values. |

### Files (6 tools)

| Tool | Description |
|------|-------------|
| `list_files()` | List files stored in MinIO. |
| `get_file_metadata(id)` | Get file metadata (not content). |
| `upload_file(file_path, namespace?, description?, tags?, category?)` | Upload a file from a local path. Auto-detects content type. Tags are comma-separated. |
| `delete_file(file_id, force?)` | Soft-delete a file. Blocked if referenced by documents unless `force=true`. |
| `hard_delete_file(file_id)` | Permanently remove from MinIO. File must be soft-deleted first. Irreversible. |
| `get_file_documents(file_id)` | Find which documents reference a file — document IDs, templates, and field paths. |

### Reporting & SQL (3 tools)

| Tool | Description |
|------|-------------|
| `list_report_tables()` | List available PostgreSQL reporting tables (doc_* tables + terminologies/terms) with columns, types, and row counts. |
| `run_report_query(sql, params?, max_rows?)` | Execute a read-only SQL SELECT against the reporting database. Use for cross-template JOINs and aggregations. Table names: `doc_{template_value}`. Use `$1, $2` for parameters. |
| `get_sync_status()` | Get reporting-sync status: NATS/PostgreSQL connections, events processed/failed, tables managed. |

### CSV Import (1 tool)

| Tool | Description |
|------|-------------|
| `import_documents_csv(file_path, template_value, column_mapping?, namespace?, skip_errors?)` | Import documents from a CSV/XLSX file. Auto-maps columns to template fields by name if no explicit mapping given. |

### Event Replay (5 tools)

| Tool | Description |
|------|-------------|
| `start_replay(template_value?, template_id?, namespace?, throttle_ms?, batch_size?)` | Start replaying stored documents as NATS events. Use to onboard new consumers or backfill data. |
| `get_replay_status(session_id)` | Get current replay session status (published count, total, status). |
| `pause_replay(session_id)` | Pause a running replay session. Can be resumed later. |
| `resume_replay(session_id)` | Resume a paused replay session. |
| `cancel_replay(session_id)` | Cancel a replay session and delete its NATS stream. |

---

## Design Decisions

### Bulk Envelope Abstraction

WIP uses a bulk-first API — all write endpoints accept arrays and return `BulkResponse`. The MCP server handles this transparently:

- **Single-item tools** (`create_terminology`, `create_template`, `create_document`) wrap the input in `[item]`, call the bulk API, and unwrap `results[0]`. The AI sees a clean single result.
- **Bulk tools** (`create_terminologies_bulk`, `create_templates_bulk`, `create_documents_bulk`) pass through the full `BulkResponse` with per-item status.

This means the AI never needs to construct or parse bulk envelopes.

### OpenAPI Schema Patching

Tool parameter schemas are derived from the actual OpenAPI specs of WIP services, not hand-written. The flow:

1. `scripts/generate_schemas.py` fetches `/openapi.json` from each running service
2. Extracts request model schemas (e.g., `CreateTerminologyRequest`, `FieldDefinition`)
3. Writes `_generated_schemas.py` with `TOOL_SCHEMAS` and `TOOL_DESCRIPTIONS`
4. At startup, `server.py` patches registered tool metadata with these schemas

This ensures the AI sees correct field names (`mandatory` not `required`, `terminology_ref` not `terminology_id`) — the same names the API validates against.

Configuration for which schemas map to which tools lives in `tools.yaml`.

### Error Handling

All errors are caught and returned as formatted strings (not exceptions). The AI receives:
- `WIP error: <message>` for bulk item failures (`BulkError`)
- `Error: <message>` for transport/connectivity errors

---

## Development Workflow

The MCP server is designed around a 4-phase process for building applications on WIP:

| Phase | Purpose | Key MCP Tools |
|-------|---------|---------------|
| **1. Explore** | Discover existing data model | `get_wip_status`, `list_namespaces`, `list_terminologies`, `list_templates` |
| **2. Design** | Plan terminologies, templates, relationships | `get_terminology_by_value`, `get_template_by_value`, `get_template_dependencies` |
| **3. Implement** | Create data model in WIP | `create_terminology`, `create_terms`, `create_template`, `activate_template`, `create_document`, `import_documents_csv` |
| **4. Build App** | Build frontend (uses @wip/client, not MCP) | MCP used for debugging and `query_by_template`, `run_report_query` |

---

## Known Limitations

- **No template update tools** — create a new version instead (WIP's versioning model)
- **No file download** — files can be uploaded, listed, and inspected, but content download requires direct HTTP
- **CSV export truncated** — `export_table_csv` truncates at 100 rows to avoid overwhelming AI context
