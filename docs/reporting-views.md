# Registered Reporting Views

A namespace can register **named SQL views** in its PostgreSQL reporting
schema. A view encapsulates a complex query — cross-template JOINs,
recursive CTEs over `term_relations`, GROUP BY roll-ups — once, at
bootstrap, so every later `run_report_query` call reads a stable named
surface instead of repeating the SQL inline.

> **Surfaces.** The endpoints live on reporting-sync. Apps register views
> from their bootstrap over HTTP (examples below) or with `@wip/client`
> (`reporting.registerView` / `listViews` / `readViewRows` / `deleteView`).
> Agents use the MCP tools `register_reporting_view`, `list_reporting_views`,
> `read_reporting_view` and `delete_reporting_view`. Raw SQL
> (`run_report_query`) works with any key, over the namespaces that key can
> read; `read_reporting_view` (the structured rows endpoint below) reads a
> view without SQL.

## The API

All three endpoints are under the reporting-sync prefix (note the
singular `namespace`):

| Method | Path | Does |
|---|---|---|
| `POST` | `/api/reporting-sync/namespace/{ns}/views` | Register, or replace by name |
| `GET` | `/api/reporting-sync/namespace/{ns}/views` | List the namespace's registered views |
| `DELETE` | `/api/reporting-sync/namespace/{ns}/views/{name}` | Drop a view and its registration |

Register body:

```json
{
  "name": "v_study_search",
  "sql": "SELECT d.document_id, d.data_json->>'study_number' AS study_number FROM doc_ct_study d",
  "registered_by": "my-app-bootstrap"
}
```

`201` on first registration, `200` on replace (`created` flag in the
body tells you which). Replace is DROP + CREATE in one transaction, so
the **column set may change freely** between versions of your view.

### Who may call what

Every reporting-sync route requires an API key. Beyond that:

| Operation | Required |
|---|---|
| Register / replace / drop a view | ADMIN on the namespace, for a view over namespaces the key can read (see below), or `wip-admins` / `wip-services` (any view) |
| List a namespace's views | read on the namespace (an invisible namespace answers `404`) |
| Read a view's rows (`POST …/views/{name}/rows`, structured) | read on the namespace **and** on every other namespace the view draws from; a view over platform tables (`public`, `pg_catalog`) only `wip-admins` / `wip-services`. Otherwise the view answers `404`, as an unregistered one would |
| `POST /api/reporting-sync/query` (raw SQL, including reading a view) | any key. `wip-admins` / `wip-services` read every namespace; any other key reads only the namespaces it can read (below) |

A view definition is raw SQL. Schema-qualified names reach other namespaces' schemas, so namespace *write* access cannot be the boundary. The namespace-ADMIN tier exists so an app can own its views with the key it deploys with. After creating the view, registration asks Postgres (`pg_depend`) which relations it actually depends on: a dependency in another namespace the key cannot read, or in `public` bookkeeping or `pg_catalog`, rolls the registration back with `403`. No SQL parsing. A `wip-admins` / `wip-services` key may register any view.

The same dependency list gates every read: reading a view's rows takes read on **every** namespace it draws from, not only the one it is registered in. A view never hands a namespace's rows to a caller who could not read that namespace directly, whoever registered it.

The dependency list cannot see what a view's functions read (`query_to_xml` reads whatever its string argument names), so it is not the boundary; PostgreSQL is. A key that is not `wip-admins` / `wip-services` runs its SQL, and reads view rows, on a connection that logs in as a role able to read only that key's namespaces. Registered views are `security_invoker`: they read with their reader's privileges, not their owner's. Anything outside the key's namespaces fails with `permission denied`.

An app deployed by `wip-deploy` receives the install's service key, so bootstrap registration and raw view queries work unchanged. A **namespace-scoped app key** registers contained views with namespace ADMIN and reads them through the structured rows endpoint; it still cannot run raw SQL.

## Name resolution — write the SQL the way you query it

The SQL runs with `search_path` set to the namespace's schema plus
`public`, the same posture as `POST /query` with the `namespace`
parameter. So unqualified names resolve in your own schema:

- `doc_<template_value>` — the entity view per template
- `doc_<value>__v<N>` — a specific version's physical table
- `terminologies`, `terms`, `term_relations`, `templates` — the synced
  metadata tables

Another namespace's schema is reachable only when you schema-qualify
(`"otherns".doc_x`) — exactly like ad-hoc queries.

## Validation — what the platform enforces

- **Single read-only SELECT.** Write/DDL keywords are rejected, and the
  statement is validated as a prepared `EXPLAIN` subselect inside a
  read-only transaction — a second `;`-separated statement is a hard
  parse error, never something that executes. A single trailing `;` is
  tolerated and stripped.
- **The SQL must be valid NOW.** Validation EXPLAINs against the live
  schema, so a view over `doc_x` can only register after template `x`
  has synced at least once. Order your bootstrap: namespace → templates
  → (first sync) → views. Registration creates the namespace schema if
  it does not exist yet, but it cannot invent your tables.
- **Platform relation names are reserved** — `doc_*`, `*__v<N>`,
  `*__entities`, the metadata tables, `_wip_*` all answer `400`. Pick a
  distinct name; the `v_` prefix is a good convention. A name occupied
  by any relation the endpoint does not own answers `409`.

## Lifecycle — what survives what

- **Service restarts:** survived. Views persist in PostgreSQL, and a
  best-effort recreation pass runs at startup.
- **Entity-view rebuilds** (a new template version, a force rebuild):
  survived. Registered views are dropped around the rebuild and
  recreated after it commits.
- **A full reporting reset** (schema drop + re-sync): the registration
  survives in bookkeeping and recreation is attempted after rebuilds,
  but **your bootstrap's re-registration is the authoritative
  recovery** — registering is idempotent, so simply register your views
  on every bootstrap run, like templates and terminologies.
- **Views over views:** allowed (register the base first). Deleting or
  replacing a view that another registered view depends on answers
  `409` — remove or replace the dependents first.
- **Namespace deletion** removes the views and their registrations.

## Querying a registered view

Two read paths, by key tier.

**Structured rows (namespace read — the app path):**
`POST /api/reporting-sync/namespace/{ns}/views/{name}/rows` takes no SQL:

```json
{
  "filters": [{"column": "status", "op": "eq", "value": "active"},
              {"column": "study_number", "op": "in", "value": ["S-001", "S-002"]}],
  "sort": [{"column": "enrolled", "direction": "desc"}],
  "limit": 100,
  "offset": 0
}
```

Operators: `eq, ne, lt, lte, gt, gte, like, ilike, in, is_null, not_null`.
Columns are validated against the view's actual columns (`400` names them on
a miss), values are bound parameters, and the response matches `POST /query`
(`columns`, `rows` as objects keyed by column, `row_count`, `truncated`). An
empty body reads the first page unfiltered. Only *registered* views answer
here — a plain table is `404`; export tables via `GET /export/csv`. Runs on
the read-only reporting role.

**Raw SQL (admin/service keys):** exactly like any reporting relation — MCP:

```
run_report_query(sql="SELECT * FROM v_study_search WHERE study_number = $1",
                 params=["S-001"], namespace="clintrial")
```

or `POST /api/reporting-sync/query` with `{"sql": "...", "namespace": "clintrial"}`.
With a namespace-scoped key, `POST /query` reads only the namespaces that key
can read (see *Who may call what* above).

## A complete example — hierarchy-aware search

The motivating case: find all studies classified under a parent
indication term *including every descendant* (ontology traversal via
`term_relations`), encapsulated once instead of shipped inline on every
search call.

```python
import httpx

VIEW_SQL = """
WITH RECURSIVE subtree AS (
  SELECT source_term_id AS term_id
  FROM term_relations
  WHERE target_term_id = d_root.term_id AND relation_type = 'is_a'
  UNION ALL
  SELECT tr.source_term_id
  FROM term_relations tr JOIN subtree s ON tr.target_term_id = s.term_id
  WHERE tr.relation_type = 'is_a'
)
SELECT ...
"""  # your JOINs over doc_* tables here

def register_views(base_url: str, api_key: str, namespace: str) -> None:
    """Part of the idempotent bootstrap — safe to run on every start."""
    resp = httpx.post(
        f"{base_url}/api/reporting-sync/namespace/{namespace}/views",
        headers={"X-API-Key": api_key},
        json={"name": "v_study_search", "sql": VIEW_SQL,
              "registered_by": "clintrial-bootstrap"},
        timeout=30,
    )
    resp.raise_for_status()  # 201 created / 200 replaced
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `400 View SQL is invalid: relation "doc_x" does not exist` | Template `x` has not synced to reporting yet — register views after first sync, or schema-qualify a foreign table |
| `400 View SQL is invalid: syntax error at or near ";"` | Multi-statement SQL — one SELECT only |
| `400 ... read-only SELECT` | A write/DDL keyword in the SQL |
| `400 View name collides with the platform's reporting relations` | Reserved name — use `v_<something>` |
| `409 Relation ... is not a registered view` | The name is taken by a relation the endpoint does not own |
| `409 ... dependent objects` on replace/delete | Another registered view depends on this one — handle dependents first |
| View briefly missing right after a template version landed | The rebuild window — it is recreated when the rebuild commits; retry |
