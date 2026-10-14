# WIP — unreleased

Changes on `develop` since v2.1.0 that the next release note must carry. Move
these sections into `vX.Y.Z.md` when the release is cut, then empty this file.

---

## Action required / behavior changes (read before upgrading)

### Upgrade by re-rendering the install, not by swapping image tags

reporting-sync now serves every request through two PostgreSQL service roles
whose passwords are deployment secrets (`postgres-sync-owner-password`,
`postgres-reporting-read-password`). The deployer generates them only when it
renders the install. An install that only gets new image tags has no such
passwords: reporting-sync then refuses to serve PostgreSQL and its `/health`
answers 503.
**Action:** upgrade with `wip-deploy redeploy` (or `install`), never with
`kubectl set image` or an edited compose file alone. On a dev install whose
source is bind-mounted, pulling the source clone is itself a partial rollout:
re-render immediately afterwards.

### reporting-sync requires authentication on every route

Every route answers 401 without an API key or session. The exceptions are the
health probes (`/health`, `/api/reporting-sync/health`) and the build-provenance
root `GET /api/reporting-sync/`. Before this release the whole service,
including raw SQL, answered anonymously.
**Action:** every caller must send `X-API-Key` (or come through the
auth-gateway). The platform's own callers — registry's namespace-deletion
cascade, document-store, the MCP server — are updated.

### Raw SQL and operator routes are admin/service-only; view writes take namespace ADMIN

These routes now require group `wip-admins` or `wip-services`; a
namespace-scoped key gets 403:
- `POST /query` and `POST /export/csv` (raw SQL);
- batch sync and its jobs (`/sync/batch…`), `/metrics…`, `/alerts…`,
  `/health/integrity`, `DELETE /namespace/{prefix}`.

Registering or deleting a view (`POST`/`DELETE /namespace/{prefix}/views…`)
takes ADMIN on the namespace **or** the privileged groups. A namespace-admin
registration is contained: the created view may only depend on relations in
the namespace's own schema (checked against the view Postgres actually
built — `pg_depend`, no SQL parsing); a cross-schema dependency rolls the
registration back with 403. Admin/service keys still register cross-schema
views deliberately. A key with only read or write on the namespace gets 403.

Namespace-scoped keys keep read access to their own namespaces through
`GET /export/csv`, `GET /namespace/{prefix}/views`, `/parity`, `/table-name`,
`/schema/{template}` — and read a registered view's rows through the new
structured endpoint `POST /namespace/{prefix}/views/{name}/rows` (namespace
read; filters as column/op/value triples validated against the view's
columns, bound parameters, sort, limit/offset; response shaped like
`POST /query`; `@wip/client` 0.55.0 ships it as `reporting.readViewRows`).
Another namespace answers 404.
**Action:** apps that ran raw SQL with a scoped key must use the install's
service key for those calls, or move view reads to the rows endpoint. An app
that registers its own views on a scoped key needs ADMIN on its namespace.

### Listings are scoped to the caller's namespaces

`POST /search`, `GET /activity/recent`, `/references/term/…/documents`,
`/entity/…/referenced-by` and `/tables` return only namespaces the caller may
read; `/entity/…/references` answers 404 for an entity in another namespace.
- an admin key sees everything, as before;
- a key with exactly one namespace uses it implicitly;
- a key with several namespaces must pass `namespace` (400 otherwise).

`/status` filters its `recent_drops` to readable namespaces.

### Raw SQL runs as a read-only role whose search path is `public`

`POST /query` runs as `wip_reporting_read` (read-only transaction, statement
timeout). Before, it ran as the bootstrap superuser `wip` with search path
`"$user", public` — so an **unqualified** table name in a query **without**
`namespace` silently resolved to the `wip` namespace's schema. That query now
fails with `relation "…" does not exist`.
**Action:** pass `namespace` (the search path then points at that namespace's
schema) or schema-qualify every table, as the API has always documented.

### Object storage: Garage module (new) and SigV4 presigned URLs

MinIO's images are no longer publicly pullable (docker.io resolves to an
HTML page, quay.io answers 401 anonymously); the platform runs a frozen,
arm64-only copy. This release adds **`garage`** (`--add garage`,
dxflrs/garage v2.1.0, arm64+amd64) as the successor object-storage
module. When garage is active it takes precedence over minio:
document-store's file storage follows it automatically (endpoint,
deployer-generated credentials, `/garage` public prefix), and the
deployer provisions the node at apply time (single-node layout, key
import, attachments bucket) — idempotently, with secrets on stdin.
Existing installs keep running MinIO unchanged; blob migration is a
separate step (CASE-864 Phase 2).

Two cross-cutting changes ship with it:

- **Presigned download URLs are now SigV4** (they were legacy V2, which
  Garage rejects as anonymous). V4 signs the Host header, so the storage
  routes (`/minio`, `/garage`) now forward the upstream Host at the edge
  (`upstream_host_header` route primitive; Caddy `header_up Host`, NGINX
  `upstream-vhost`).
  **Action:** presigned downloads require the re-rendered router config —
  one more reason this release upgrades by re-rendering, never by image
  swap alone. A dev install whose document-store hot-reloaded the new
  code serves V4 URLs that fail until the router is re-rendered.
- New route primitive `upstream_host_header` on component manifests,
  honored by both renderers.

### ingest-gateway requires authentication

Every ingest-gateway route requires an API key except
`/api/ingest-gateway/health`; `/status` and `/metrics` used to answer
anonymously. Its outbound key now also falls back to `MASTER_API_KEY`, and the
component manifest sets `API_KEY`, so it no longer writes with the dev default
key on deployed installs.

### NATS connections heal themselves; `WIP_EVENTS` has one definition

def-store, template-store, document-store, reporting-sync and ingest-gateway
share one NATS connection module (`wip_auth.events`). Before, a store whose
first connect failed published nothing until restarted, and any client gave
up after about two minutes of NATS outage — writes kept succeeding while
reporting silently diverged.
- **Startup never waits for NATS.** Services connect in the background and
  reconnect without limit; a closed client is rebuilt. ingest-gateway no
  longer crashes on start when NATS is down.
- **Events survive short outages.** While NATS is unreachable, events wait in
  a per-process retry queue (10,000 events, 1 hour) and are published in
  order once it is back. Beyond those bounds, or across a restart, events are
  dropped and counted; repair with a batch sync, as before.
- **Consumers are supervised.** The reporting-sync and ingest-gateway workers
  start whenever NATS is connected, and restart when their consumer loop
  dies (a missing stream, a deleted durable consumer).
- **`WIP_EVENTS` converges on every (re)connect** to one definition: subjects
  `wip.documents/files/templates/terminologies/terms/term_relations.>`,
  1,000,000 messages, 1 GiB, **7 days**. Missing subjects are added (file
  events had no stream on installs where reporting-sync created it first);
  subjects added by others are kept.
  **Action:** on such installs the stream had no age limit, so the first
  start purges stored events older than 7 days. reporting-sync normally
  processed them long before; if its consumer is days behind (check
  `/api/reporting-sync/metrics/consumer`), run a batch sync after upgrading.
- **Visibility.** `/health` reports `"nats"` as `connected`, `connecting`,
  `reconnecting` or `not_configured` (was `connected` / `disabled`).
  `GET /api/<service>/health/events` (wip-admins / wip-services) returns the
  connection state, published / queued / dropped-by-reason counters and the
  supervised consumers' restarts. `/health` stays HTTP 200 regardless:
  readiness and liveness probe the same endpoint.

### Terms carry typed names; aliases and translations are now projections

A term is now a code plus a set of **names**: each name carries `value`,
optional `type` (validated against the new seeded, extensible `_NAME_TYPES`
terminology — INN, TRADE_GLOBAL, TRADE_LOCAL, CODE, REGISTRY_ID, SYNONYM,
ABBREVIATION), optional `language` and `source`, and a `preferred` flag (at
most one per (type, language) group). The term API keeps accepting
plain-string `aliases` (stored as untyped names) and `translations` (stored
as language-bearing names), and responses serve derived `aliases` /
`translations` projections alongside `names` — but four behaviors change:

- **Translated labels now resolve.** Every name participates in validation
  and document term resolution; `translations` used to be display-only
  payload, invisible to validation, document-store and reporting. A value
  that collided with a translated label and was invalid before is valid now.
- **Exact-duplicate names are rejected loudly** (per-item
  `error_code='invalid_names'`), as are a name equal to the term's own
  value, an unknown `type`, and two preferred names in one group.
  Case-variant names (`'MR.'` vs `'Mr.'`) stay legal. Seeds that submitted
  literal duplicate aliases must deduplicate.
- **The `aliases` projection covers every name** — including translated
  labels — so consumers keying on it see more entries than before.
- **Term resolution gained value precedence**: every term's canonical value
  is checked before any term's names (def-store validation and
  document-store's local lookup both), so a name can no longer shadow
  another term's canonical value — duplicate-alias resolution used to
  depend on storage/pagination order. The validate response carries the
  matched name (with type and language) as `matched_name`.

Reporting gains a **`term_names` table** per namespace schema (one row per
name: `value`, `type`, `language`, `source`, `preferred`), synced by event
and batch paths, exported and listed like the other fixed tables. OBO
import keeps each synonym's predicate in the name's `source` and no longer
caps synonyms at 10 silently — `max_synonyms` defaults to unlimited and
reports `dropped_synonyms` when a cap is passed. `@wip/client` 0.56.0
ships the `TermName` types and `matched_name`.
**Action:** none for most installs — the stored data migrates itself (see
first start below) and legacy API shapes keep working. Review seeds for
literal duplicate aliases, and any consumer that assumed `aliases`
excluded translations.

### Ambiguous term names are visible, and identity fields refuse them

A name borne by several terms used to resolve silently to whichever term
storage order served first. Three changes make the ambiguity loud:

- **Validation reports every bearer.** `/validate` responses carry
  `matches` (how many distinct terms bear the submitted value) and
  `candidates` (each bearer with its matched name). A single hit keeps
  the previous shape; a value match is unambiguous by construction.
- **Document writes fail closed on ambiguous identity fields.** A term
  identity field resolving an ambiguous name is rejected (per-field code
  `ambiguous_term`) — the arbitrary pick would otherwise feed the
  identity hash. On ordinary fields the first bearer still resolves,
  now with a warning naming all candidates.
- **Authoring warns at the source.** Creating or updating a term whose
  value or names collide with another active term's value or names (per
  the terminology's case sensitivity, including collisions inside one
  bulk import) reports them in the per-item `details.name_collisions`.
  A terminology declaring `unique_names: true` rejects such writes with
  `error_code='name_collision'` instead.

**Action:** none — defaults warn, nothing previously accepted is rejected
except ambiguous names on identity fields, which were producing
storage-order-dependent identities and should be corrected, not relied on.

### Platform-maintained term closure in reporting

Each namespace schema gains a `term_closure` table: one row per
(ancestor, descendant, relation_type) with the shortest-path depth,
reflexive rows included, `parent_term_id` links counted as `is_a` (the
same union the ontology traversal uses). "Documents whose term is
anywhere in the subtree of X" becomes one indexed subquery — no app-side
recursive CTEs, and correct under multiple parents, which fixed-depth
joins silently are not (926 multi-parent terms measured in one real
vocabulary). Closures are per relation type, never blended, and are
rebuilt wholesale per namespace (debounced from term and term-relation
events; immediately after a batch sync) inside one transaction, so
readers never see a half-built closure. The table is listed by `/tables`
and exportable like the other fixed tables.
**Action:** none. Apps using the CASE-849-style registered closure view
can switch their joins to `term_closure` and drop the view at their own
pace.

### Terminology snapshot reconcile

`POST /api/def-store/terminologies/{id}/reconcile` (MCP tool
`reconcile_terminology`, `@wip/client` 0.59.0
`defStore.reconcileTerminology`) takes a terminology's complete desired
term set and a policy, and answers with the diff — created, updated with
changed fields, deprecated, errors; unchanged terms counted, not listed.
`dry_run` is the **default**: writing is the explicit choice. Built for
externally sourced vocabularies that refresh periodically, replacing each
app's hand-rolled diff script. Absent active terms are deprecated (never
deleted) and only under `on_missing='deprecate'`, optionally with
successor mappings; the snapshot is authoritative for label and names
while description/sort_order/parent apply only when set (reconcile never
clears a field, keeping re-runs idempotent); metadata is honored on
create and never rewritten; a snapshot value matching a retired term is a
per-item `exists_inactive` error — reconcile does not reactivate. Writes
route through the normal term doors, so Registry registration, names
validation, the collision guard, events and audit behave exactly like
individual writes.

### Ranked term search (typeahead); unified search actually finds terms

Def-store gains `GET /api/def-store/terms/search` (MCP tool
`search_terms`, `@wip/client` 0.57.0 `defStore.searchTerms`): literal,
case-insensitive search over a term's value, label and every name, with
match modes (`exact`/`prefix`/`word-start`/`substring`), ranked hits that
carry the exact matched string and — for name hits — the name's type and
language, and `rollup=root` to collapse hits onto their root `is_a`
ancestor (a trade name returns the molecule). Exact mode returns ALL
candidates, making it the inspection surface for ambiguous names.

The unified search (`POST /api/reporting-sync/search`) now delegates its
term type to that endpoint. Before, it fetched one page of 100 terms from
the first 20 terminologies and substring-matched in Python — any
vocabulary beyond 100 terms was effectively invisible and alias matches
were never found. Term results now cover full vocabularies and name
matches say what matched.

### Namespace deletion removes the namespace's stored files

Until now, deleting a namespace removed its file records but left every
stored file object in the bucket, and still reported `completed`. The
Registry's object-store step was only built when `MINIO_URL` was set (no
deployer ever set it), and its request used an authentication scheme MinIO
and Garage both reject. The objects of every namespace deleted so far are
still in storage, with no record pointing at them.

The step now always runs when the namespace has files. The Registry sends
the storage keys from its deletion journal to a new document-store route,
`POST /api/document-store/files/storage/delete` (wip-admins / wip-services
only), which deletes them with document-store's own storage client, so it
follows whichever object store the install runs. The route refuses a
namespace that is not being deleted (409), refuses a key whose file record
belongs to another namespace, and answers 503 if the Registry cannot
confirm the namespace's state. A failed or refused key leaves the journal
`completed_with_warnings` with the keys kept on the step.

**Action:** re-render the install (`wip-deploy redeploy --name <install>`)
so the Registry receives `DOCUMENT_STORE_URL`. Without it, deletions record
the storage step as degraded instead of deleting the objects. The Registry
no longer reads `MINIO_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` or
`MINIO_BUCKET`. The impact report and journal summary keep the key name
`minio_objects` for compatibility; it counts objects in any object store.

---

## What happens on first start after the upgrade

def-store converts stored terms to the unified names shape before serving
traffic (legacy `aliases`/`translations` fields fold into `names`,
exact duplicates deduped first-occurrence-wins, the legacy alias index is
dropped). Idempotent and fast — look for `Term-name migration:` in the log.
Restoring a pre-upgrade archive is also covered: the restore engine
converts term rows at write time.

reporting-sync does this once, idempotently, while still connected as the
bootstrap login:

1. **Bookkeeping repair.** Copies of `_wip_schema_migrations` /
   `_wip_sync_status` that the old `"$user"` search path created in the `wip`
   schema are merged into `public` (the `wip` copy wins on conflict, being the
   newer one) and dropped. A copy with a different shape is renamed aside and
   logged as CRITICAL. Look for `Bookkeeping repair:` in the log.
2. **Service roles.** `wip_sync_owner` and `wip_reporting_read` are created
   (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`), every schema and relation
   owned by the bootstrap role is re-owned to `wip_sync_owner`, and the reader
   gets read grants plus default privileges. Look for
   `PostgreSQL roles ensured … re-owned N schemas, M relations`.
3. **Self-check.** Neither pool may run as a superuser or role creator; if one
   does, or a password is missing, `/health` answers 503 and `/status` reports
   `postgres_service_roles_ok: false`.

**Rollback caution:** an older reporting-sync image connects as `wip` again and
recreates empty unqualified bookkeeping tables in the `wip` schema. Prefer
redeploying forward; if you do roll back, reconcile with `/parity` and a batch
sync afterwards.

---

## Deployment changes

- **Dev installs mount the checkout they were installed from, whatever
  the cwd.** A dev redeploy (or any other mutation verb) used to bind-mount
  the backend source of whichever git checkout the command ran in, while
  the deployer-state kept recording the original one — running wip-deploy
  from a second clone silently re-pointed a running dev install at that
  clone. The mounts now follow the same root as discovery: `--repo-root` /
  `WIP_REPO_ROOT`, else the root stamped at install, else the cwd. An
  explicit `--repo-root` that differs from the stamped root still moves the
  install, and now prints a `note:` naming both. `render` and `install`
  with `--repo-root` also mount from the flag (before, always from the cwd).
  `status --diff` discovers from the stamped root before the cwd.
- **MinIO image** is `ghcr.io/peterseb1969/minio:RELEASE.2025-09-07T16-13-09Z`.
  MinIO no longer publishes pullable images (Docker Hub answers with HTML,
  quay.io with 401); this is a copy of the last release in use. It is
  **arm64-only** — an amd64 cluster needs a multi-arch copy before a fresh
  install.
- **`wip-deploy verify --security --live`** probes a running install through
  its public URL: every protected route must refuse a request without
  credentials (JSON 401/403/404), every health probe must answer 200, and
  reporting-sync must report `postgres_service_roles_ok: true`. Certificates
  are verified only for `tls=letsencrypt`.

---

## Operational notes

- **Identity-hash migration after restoring an older archive.** Identity
  fields that are term or reference fields now hash the resolved IDs, not the
  submitted spelling. An archive written before that rule restores the old
  hashes; run `POST /api/document-store/maintenance/rehash-identities`
  (dry run first) on each restored namespace. Then rebuild the affected
  reporting tables with `POST /api/reporting-sync/sync/batch/{template}?namespace=…&force=true`
  — a batch sync without `force` keeps existing rows and does not refresh
  their `identity_hash`. On large namespaces the pass can answer 500 from a
  client timeout while the Registry still completes; check a sample of
  Registry entries against document-store before re-running.
