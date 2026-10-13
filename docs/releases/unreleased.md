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

---

## What happens on first start after the upgrade

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
