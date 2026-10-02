# Registered Reporting Views

A namespace can register **named SQL views** in its PostgreSQL reporting
schema. A view encapsulates a complex query — cross-template JOINs,
recursive CTEs over `term_relations`, GROUP BY roll-ups — once, at
bootstrap, so every later `run_report_query` call reads a stable named
surface instead of repeating the SQL inline.

> **Status.** The endpoints live on reporting-sync and are stable. There
> are **no MCP tools for registration yet** — register from your app's
> bootstrap over HTTP (examples below). *Reading* a registered view
> needs no new tooling: the MCP `run_report_query` tool queries it like
> any table.

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

Exactly like any reporting relation — MCP:

```
run_report_query(sql="SELECT * FROM v_study_search WHERE study_number = $1",
                 params=["S-001"], namespace="clintrial")
```

or `POST /api/reporting-sync/query` with `{"sql": "...", "namespace": "clintrial"}`.

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
