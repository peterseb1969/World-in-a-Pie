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

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_REGISTRY_URL` | `http://localhost:8001` | Registry service URL |
| `WIP_DEF_STORE_URL` | `http://localhost:8002` | Def-Store service URL |
| `WIP_TEMPLATE_STORE_URL` | `http://localhost:8003` | Template-Store service URL |
| `WIP_DOCUMENT_STORE_URL` | `http://localhost:8004` | Document-Store service URL |
| `WIP_REPORTING_SYNC_URL` | `http://localhost:8005` | Reporting-Sync service URL |
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

Three resources provide baseline context to the AI without tool calls:

| Resource URI | Description |
|---|---|
| `wip://conventions` | Bulk-first API patterns, identity hashing, versioning, pagination |
| `wip://data-model` | Core entity types: terminologies, terms, templates, documents, files, relationships |
| `wip://development-guide` | The 4-phase development process with guidance per phase |

---

## Tools

### Discovery (3 tools)

| Tool | Description |
|------|-------------|
| `get_wip_status()` | Health check for all WIP services. Call first to verify connectivity. |
| `list_namespaces()` | List all namespaces. Namespaces scope all entities. |
| `get_namespace_stats(prefix)` | Entity counts by type for a namespace. |

### Terminologies (5 tools)

| Tool | Description |
|------|-------------|
| `list_terminologies()` | List controlled vocabularies. Supports pagination and namespace filter. |
| `get_terminology(id)` | Get by ID (e.g., `T-xxxxxxxx`). |
| `get_terminology_by_value(value)` | Get by value code (e.g., `COUNTRY`). Case-sensitive. |
| `create_terminology(value, label, ...)` | Create a single terminology. Returns unwrapped result. |
| `create_terminologies_bulk(items)` | Create multiple. Returns full `BulkResponse`. |

### Terms (4 tools)

| Tool | Description |
|------|-------------|
| `list_terms(terminology_id)` | List terms in a terminology. Supports search filter. |
| `get_term(id)` | Get a term by ID. |
| `create_terms(terminology_id, terms)` | Create terms in bulk. Each needs `value` and `label`. |
| `validate_term_value(terminology_id, value)` | Test whether a value exists in a terminology. |

### Ontology / Relationships (2 tools)

| Tool | Description |
|------|-------------|
| `get_term_hierarchy(term_id, direction)` | Traverse relationships: `children`, `parents`, `ancestors`, `descendants`. Optional `relationship_type` filter. |
| `create_relationships(relationships)` | Create typed relationships (`is_a`, `part_of`, `has_part`, `regulates`, etc.). |

### Templates (10 tools)

| Tool | Description |
|------|-------------|
| `list_templates()` | List templates. Supports `status`, `latest_only`, namespace, pagination. |
| `get_template(id, version?)` | Get resolved template (with inherited fields). |
| `get_template_by_value(value)` | Get by value code (e.g., `BANK_TRANSACTION`). |
| `get_template_raw(id)` | Get without inheritance resolution. |
| `get_template_fields(template_value)` | Clean summary of a template's fields — name, type, mandatory, references. Returns `template_id` for use in queries. |
| `create_template(template)` | Create a single template. Supports draft mode. |
| `create_templates_bulk(templates)` | Create multiple templates. |
| `activate_template(id)` | Activate a draft template with cascading validation. |
| `deactivate_template(id, version?, force?)` | Soft-delete a template version. Blocked if child templates exist. Use `force=true` to bypass document dependency check. |
| `get_template_dependencies(id)` | Show child templates and documents that depend on this template. |
| `query_by_template(template_value, field_filters?, ...)` | Query documents by template value code. Auto-resolves `template_value` to `template_id`. Field names auto-prefixed with `data.` — write `country` not `data.country`. |

### Documents (5 tools)

| Tool | Description |
|------|-------------|
| `list_documents()` | List documents. Filter by `template_value`, `template_id`, status, namespace. |
| `get_document(id, version?)` | Get a document by ID. |
| `create_document(document)` | Create a single document. Term values are auto-resolved. |
| `create_documents_bulk(documents)` | Create multiple documents. Returns per-item results. |
| `query_documents(filters)` | Query with complex field-level filters. |

### Import / Export (2 tools)

| Tool | Description |
|------|-------------|
| `export_terminology(id)` | Export terminology with terms and optional relationships. JSON or CSV. |
| `import_terminology(data)` | Import terminology from JSON. Supports `skip_duplicates` and `update_existing`. |

### Search & Reporting (2 tools)

| Tool | Description |
|------|-------------|
| `search(query, types?)` | Unified full-text search across all entity types (via reporting-sync / PostgreSQL). |
| `search_registry(query)` | Search Registry entries by ID or composite key. |

### Files (3 tools)

| Tool | Description |
|------|-------------|
| `list_files()` | List files stored in MinIO. |
| `get_file_metadata(id)` | Get file metadata (not content). |
| `upload_file(file_path, namespace?, description?, tags?, category?)` | Upload a file from a local path. Auto-detects content type. Tags are comma-separated. |

### Reporting & SQL (2 tools)

| Tool | Description |
|------|-------------|
| `list_report_tables()` | List available PostgreSQL reporting tables (doc_* tables + terminologies/terms) with columns, types, and row counts. |
| `run_report_query(sql, params?, max_rows?)` | Execute a read-only SQL SELECT against the reporting database. Use for cross-template JOINs and aggregations. Table names: `doc_{template_value}`. Use `$1, $2` for parameters. |

### CSV Import (1 tool)

| Tool | Description |
|------|-------------|
| `import_documents_csv(file_path, template_value, column_mapping?, namespace?, skip_errors?)` | Import documents from a CSV/XLSX file. Auto-maps columns to template fields by name if no explicit mapping given. |

### Event Replay (3 tools)

| Tool | Description |
|------|-------------|
| `start_replay(template_value?, template_id?, namespace?, throttle_ms?, batch_size?)` | Start replaying stored documents as NATS events. Use to onboard new consumers or backfill data. |
| `get_replay_status(session_id)` | Get current replay session status (published count, total, status). |
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
- **No term/terminology update or delete tools** — use the Console UI or direct API
- **No file download** — files can be uploaded, listed, and inspected, but content download requires direct HTTP
- **No Registry entry management** — register, lookup, synonym, and merge operations require direct API calls
