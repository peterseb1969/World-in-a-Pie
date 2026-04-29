# MCP Server

The WIP MCP server exposes World In a Pie as an AI-native interface. An AI assistant connects via MCP (Model Context Protocol) and gets structured tools to discover, create, and query all WIP entities — without constructing raw HTTP calls.

**Location:** `components/mcp-server/`

---

## Purpose

The MCP server serves two roles:

1. **AI-assisted development (Phases 1-3):** An AI building applications on WIP uses MCP tools to explore existing data, design data models, and create terminologies/templates/documents. This is the primary use case during the [4-phase development process](#development-workflow).

2. **Conversational data access:** Once apps have populated WIP with structured data, any AI assistant with MCP access can query across all datasets in natural language. This turns WIP into a personal data assistant — the AI queries validated, cross-referenced data instead of guessing from unstructured files.

---

## Modes

The server runs in one of two modes, controlled by the `WIP_MCP_MODE` environment variable:

### Normal Mode (default)

All 70+ tools are available — full read/write access to the WIP data model. This is the mode used during application development (Phases 1–4 below).

```bash
python -m wip_mcp                  # stdio
python -m wip_mcp --http           # HTTP streamable
```

### Read-Only Mode

Set `WIP_MCP_MODE=readonly` to remove all 32 write tools. The server exposes only 40 read-only tools: queries, searches, exports, and reports. The AI physically cannot create, modify, or delete any entities.

```bash
WIP_MCP_MODE=readonly python -m wip_mcp
```

This is a structural safety mechanism — write tools are removed from the MCP tool registry before the server starts. There is no code path to invoke them. The AI cannot even see the tools exist.

**Use cases:**
- **Query Claude / Analyst agent** — pairs with the `/analyst` slash command to create an AI that can explore and report on data but cannot modify it
- **Shared/multi-tenant deployments** — expose WIP data to agents you don't fully trust
- **Demo environments** — let users explore without risk of data modification

**Write tools removed (32):**

| Category | Tools removed |
|----------|--------------|
| Terminologies | `create_terminology`, `create_terminologies_bulk`, `update_terminology`, `delete_terminology`, `restore_terminology` |
| Terms | `create_terms`, `update_term`, `delete_term`, `deprecate_term` |
| Term Relations | `create_term_relations`, `delete_term_relations` |
| Templates | `create_template`, `create_templates_bulk`, `create_edge_type`, `activate_template`, `deactivate_template` |
| Documents | `create_document`, `create_documents_bulk`, `archive_document` |
| Files | `upload_file`, `delete_file`, `hard_delete_file` |
| Import | `import_terminology`, `import_documents_csv` |
| Replay | `start_replay`, `cancel_replay`, `pause_replay`, `resume_replay` |
| Registry | `add_synonym`, `remove_synonym`, `merge_entries` |
| Namespace | `delete_namespace` |

**Read-only tools available (38):**

Discovery, listing, get-by-ID, search, query, export, validation, hierarchy, report tables, SQL queries (`run_report_query` — enforces read-only SQL), sync status, file metadata, template fields, and document versions.

---

## Transports

| Flag | Transport | Use case |
|------|-----------|----------|
| *(none)* | stdio | Local development — Claude Code, Cursor, any MCP-capable IDE |
| `--http` | Streamable HTTP | K8s deployment, remote access |
| `--sse` | SSE (deprecated) | Legacy clients that don't support streamable HTTP |

**stdio** requires no network configuration — the IDE launches the server as a subprocess.

**HTTP/SSE** transports expose a network endpoint and should always be configured with an API key.

---

## Environment Variables

### Server Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_MCP_MODE` | *(empty)* | Set to `readonly` to disable all write tools |
| `MCP_PORT` | `8000` | Port for HTTP/SSE transports |
| `MCP_HOST` | `0.0.0.0` | Bind address for HTTP/SSE transports |
| `MCP_ALLOWED_HOST` | *(none)* | DNS rebinding protection — set to the hostname clients use (e.g. `wip-kubi.local`) |
| `API_KEY` | *(none)* | API key required for HTTP/SSE clients (also accepts `WIP_AUTH_LEGACY_API_KEY`) |

### WIP Service URLs

The MCP server can route traffic through a unified proxy (like Caddy) or connect directly to individual services. The unified proxy is the standard approach for both development and production.

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_API_URL` | *(none)* | Base URL for all API requests (e.g., `https://localhost:8443`). If set, overrides the individual service URLs below. |
| `WIP_VERIFY_TLS` | `true` | Set to `false` when using a self-signed cert for local dev (`https://localhost:8443`). |
| `REGISTRY_URL` | `http://localhost:8001` | Registry service (used if `WIP_API_URL` is unset) |
| `DEF_STORE_URL` | `http://localhost:8002` | Def-Store service (used if `WIP_API_URL` is unset) |
| `TEMPLATE_STORE_URL` | `http://localhost:8003` | Template-Store service (used if `WIP_API_URL` is unset) |
| `DOCUMENT_STORE_URL` | `http://localhost:8004` | Document-Store service (used if `WIP_API_URL` is unset) |
| `REPORTING_SYNC_URL` | `http://localhost:8005` | Reporting-Sync service (used if `WIP_API_URL` is unset) |

### API Key Resolution

The server authenticates to WIP services using an API key, resolved in priority order:

1. `WIP_API_KEY` — direct env var
2. `WIP_API_KEY_FILE` — path to a file containing the key (supports key rotation without restarting)
3. Fallback: `dev_master_key_for_testing` (local development only)

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_MCP_DEFAULT_NAMESPACE` | *(none)* | Default namespace for tools that accept a namespace parameter |

---

## Configuration Examples

### Claude Code — Normal Mode (stdio, local dev)

```bash
claude mcp add wip -- /path/to/World-in-a-Pie/.venv/bin/python -m wip_mcp
```

Or in your project's `.mcp.json`:
```json
{
  "mcpServers": {
    "wip": {
      "command": "/path/to/World-in-a-Pie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "env": {
        "WIP_API_KEY": "your_api_key"
      }
    }
  }
}
```

### Claude Code — Read-Only Mode (analyst agent)

```json
{
  "mcpServers": {
    "wip": {
      "command": "/path/to/World-in-a-Pie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "env": {
        "WIP_API_KEY": "your_api_key",
        "WIP_MCP_MODE": "readonly"
      }
    }
  }
}
```

### Running Both Modes Simultaneously

You can configure two MCP servers in the same `.mcp.json` — one for building, one for querying:

```json
{
  "mcpServers": {
    "wip": {
      "command": "/path/to/World-in-a-Pie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "env": { "WIP_API_KEY": "your_api_key" }
    },
    "wip-reader": {
      "command": "/path/to/World-in-a-Pie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "env": {
        "WIP_API_KEY": "your_api_key",
        "WIP_MCP_MODE": "readonly"
      }
    }
  }
}
```

### Kubernetes (HTTP)

The K8s deployment in `k8s/services/mcp-server.yaml` runs the server in HTTP mode on port 8007 with API key auth and DNS rebinding protection.

To deploy a read-only instance alongside the normal one:

```yaml
env:
  - name: WIP_MCP_MODE
    value: "readonly"
  - name: MCP_PORT
    value: "8008"
  - name: MCP_ALLOWED_HOST
    value: "wip-kubi.local"
  - name: API_KEY
    valueFrom:
      secretKeyRef:
        name: wip-secrets
        key: api-key
```

### Docker Compose

```yaml
wip-mcp-server:
  build: components/mcp-server
  environment:
    - WIP_API_KEY_FILE=/run/secrets/wip_api_key
    - MCP_PORT=8007
  command: ["python", "-m", "wip_mcp", "--http"]

wip-mcp-reader:
  build: components/mcp-server
  environment:
    - WIP_MCP_MODE=readonly
    - WIP_API_KEY_FILE=/run/secrets/wip_api_key
    - MCP_PORT=8008
  command: ["python", "-m", "wip_mcp", "--http"]
```

---

## Security

- **stdio transport** inherits the permissions of the parent process (the IDE). No additional auth needed.
- **HTTP/SSE transports** should always set `API_KEY`. Without it, anyone with network access has full tool access. The server prints a warning to stderr if no API key is configured.
- **Read-only mode** is structural, not a permission check — write tools are removed from the registry at startup. The AI cannot discover or invoke them.
- **DNS rebinding protection** — set `MCP_ALLOWED_HOST` for HTTP/SSE transports.

---

## Resources (Static Context)

Five resources provide baseline context to the AI without tool calls:

| Resource URI | Description |
|---|---|
| `wip://conventions` | Bulk-first API patterns, identity hashing, versioning, pagination, querying |
| `wip://data-model` | Core entity types: terminologies, terms, templates, documents, files, relations |
| `wip://development-guide` | The 4-phase development process with guidance per phase |
| `wip://ponifs` | Powerful, Non-Intuitive Features — 6 WIP behaviours that violate conventional expectations, plus the Compactheimer's Warning for AI assistants |
| `wip://query-assistant-prompt` | Query assistant prompt for SQL reporting |

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

### Ontology / Term Relations (4 tools)

These connect *terms* (taxonomy edges like `is_a`, `part_of`). Distinct from document-level *relationships* — see the Documents section for `get_document_relationships` / `traverse_documents`.

| Tool | Description |
|------|-------------|
| `get_term_hierarchy(term_id, direction)` | Traverse term relations: `children`, `parents`, `ancestors`, `descendants`. Optional `relation_type` filter. |
| `list_term_relations(term_id, direction?, relation_type?)` | List term relations. Direction: `outgoing`, `incoming`, or `both`. |
| `create_term_relations(relations)` | Create typed term relations (`is_a`, `part_of`, `has_part`, `regulates`, etc.). |
| `delete_term_relations(relations)` | Delete term relations. Each item: `{source_term_id, target_term_id, relation_type}`. |

### Templates (13 tools)

Templates are the schema for entity documents. **Edge types** — schemas for relationships between documents — are also implemented as templates (with `usage: "relationship"`) but get a dedicated creation tool below to surface the conceptual distinction. The other tools work uniformly on both.

| Tool | Description |
|------|-------------|
| `list_templates()` | List templates. Supports `status`, `latest_only`, namespace, pagination. |
| `get_template(id, version?)` | Get resolved template (with inherited fields). |
| `get_template_by_value(value)` | Get by value code (e.g., `BANK_TRANSACTION`). |
| `get_template_raw(id)` | Get without inheritance resolution. |
| `get_template_fields(template_value)` | Clean summary of a template's fields — name, type, mandatory, references. Returns `template_id` for use in queries. |
| `get_template_versions(template_value?, template_id?)` | List all versions of a template. Provide either value or ID. |
| `validate_template(template_id)` | Validate a template's references (terminologies, parent templates). Useful before activation. |
| `create_template(template)` | Create a single entity template. Supports draft mode. For edge types, use `create_edge_type` instead — it validates the edge contract before delegating. |
| `create_templates_bulk(templates)` | Create multiple templates. |
| `create_edge_type(value, source_templates, target_templates, fields, ...)` | Create an edge type (a template with `usage: "relationship"`) with the contract validated up front: `source_templates` and `target_templates` non-empty, `source_ref` and `target_ref` reference fields present, `versioned` set explicitly. Thin wrapper over `create_template` — same storage, clearer ingress. |
| `activate_template(id)` | Activate a draft template with cascading validation. |
| `deactivate_template(id, version?, force?)` | Soft-delete a template version. Blocked if child templates exist. Use `force=true` to bypass document dependency check. |
| `get_template_dependencies(id)` | Show child templates and documents that depend on this template. |

### Documents (10 tools)

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
| `get_document_relationships(document_id, direction?, template?, namespace?, active_only?, page?, page_size?)` | List relationship documents (instances of edge types — templates with `usage='relationship'`) pointing at or from a document. Indexed on `data.source_ref` / `data.target_ref`. Direction: `incoming`, `outgoing`, or `both` (default `both`). |
| `traverse_documents(document_id, depth?, types?, direction?, namespace?)` | BFS graph traversal via relationship documents. Capped at `depth=10` and 1000 nodes; sets `truncated=true` when a cap fires. Returns flat node list with `depth`, `path`, and `via_relationship`. |

### Table View & Export (2 tools)

| Tool | Description |
|------|-------------|
| `get_table_view(template_value, ...)` | Denormalized view with columns and rows — ideal for data analysis. |
| `export_table_csv(template_value, status?, include_metadata?)` | Export documents as CSV. Returns raw CSV content (truncated at 100 rows for AI context). |

### Import / Export (2 tools)

| Tool | Description |
|------|-------------|
| `export_terminology(id)` | Export terminology with terms and optional relations. JSON or CSV. |
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
| **2. Design** | Plan terminologies, templates, relations | `get_terminology_by_value`, `get_template_by_value`, `get_template_dependencies` |
| **3. Implement** | Create data model in WIP | `create_terminology`, `create_terms`, `create_template`, `activate_template`, `create_document`, `import_documents_csv` |
| **4. Build App** | Build frontend (uses @wip/client, not MCP) | MCP used for debugging and `query_by_template`, `run_report_query` |

---

### Synonym Resolution Transparency

All MCP tools that accept entity IDs (e.g., `terminology_id`, `template_id`, `template_value`) benefit from universal synonym resolution in the underlying services. You can pass human-readable values like `"PATIENT"` or `"STATUS"` wherever a canonical UUID is expected — the service resolves it transparently. This means:

- `create_document({"template_id": "PATIENT", ...})` works — `PATIENT` resolves to the canonical template UUID
- `list_terms(terminology_id="STATUS")` works — `STATUS` resolves to the canonical terminology UUID
- `query_by_template(template_value="PERSON")` already worked by value lookup, but synonym resolution adds an additional fallback path

Tools that already accept `value` parameters (like `get_template_by_value`) do direct value lookup, not synonym resolution. Both approaches reach the same entity.

---

## Known Limitations

- **No template update tools** — create a new version instead (WIP's versioning model)
- **No file download** — files can be uploaded, listed, and inspected, but content download requires direct HTTP
- **CSV export truncated** — `export_table_csv` truncates at 100 rows to avoid overwhelming AI context
