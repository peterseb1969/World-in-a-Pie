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

### Object storage: Garage replaces MinIO (now the default; MinIO retired)

MinIO's images are no longer publicly pullable (docker.io resolves to an
HTML page, quay.io answers 401 anonymously); the platform ran a frozen,
arm64-only copy. **Garage** (dxflrs/garage v2.1.0, arm64+amd64) is now
the platform's object store: the `standard` and `full` presets include
the `garage` module, document-store's manifest reads the Garage endpoint
and deployer-generated credentials directly, and the deployer provisions
the node at apply time (single-node layout, key import, attachments
bucket) — idempotently, with secrets on stdin.

**The `minio` module no longer exists.** An install whose saved spec
still lists it renders without an object store (file storage disabled)
until migrated. The proven migration recipe: full backup → delete the
file-bearing namespace(s) → `add-module garage` → restore → verify
checksums → `remove-module minio` (run against a repo state that still
had the module, or edit the saved spec). All platform installs were
migrated this way or as zero-blob flips (CASE-864 Phase 2: dev-test, kb,
prod-test with 207/207 blobs checksum-verified plus an in-app download,
wip-local). Service images need no change — the storage client is
S3-generic; images ≥ 20261017a carry the required SigV4 presigning.

Two cross-cutting changes ship with it:

- **Presigned download URLs are now SigV4** (they were legacy V2, which
  Garage rejects as anonymous). V4 signs the Host header, so the storage
  route (`/garage`) now forwards the upstream Host at the edge
  (`upstream_host_header` route primitive; Caddy `header_up Host`, NGINX
  `upstream-vhost` — restoring the full in-cluster FQDN on k8s, since
  that is the host services sign against).
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

### MongoDB requires authentication; each client is its own MongoDB user

MongoDB accepted anonymous clients on every target. Any workload that could
reach it could read and write every service's database, bypassing every
guardrail WIP enforces.
- **Per-client users.** MongoDB now serves with `--auth`. Each client
  connects as its own user:

  | Client | Access |
  |---|---|
  | def-store, template-store | their own database |
  | registry | its own database, plus only `find` / `remove` on the seven collections namespace deletion clears in the other databases |
  | document-store | all four (backup reads them; restore writes raw rows) |
  | react-console | `read` on all four (admin scope, read-only) |

  Apps can never write to MongoDB: WIP data changes only through WIP's API.
- **Where it is declared.** Components declare a `mongo_client` block
  (`databases`, `collection_privileges`); apps declare `roles: [read-all]`.
  The deployer generates `mongo-password-<client>` and `mongo-root-password`,
  injects `MONGO_USER` / `MONGO_PASSWORD` into each client and the passwords
  into MongoDB, and renders MongoDB's start script
  (`config/mongodb/entrypoint.sh`; k8s ConfigMap `wip-mongodb-entrypoint`),
  which holds no secrets.
- **How users get there.** On every start, the script creates or updates
  root, the users and the custom role through a temporary `mongod` without
  `--auth` on 127.0.0.1:27018 (unreachable from other containers). It then
  restarts MongoDB with authentication. Users the step created that are no
  longer declared are dropped (an app removed).

  A fresh data directory, an existing one and a changed user set take the
  same path, so **an existing install comes up authenticated on its next
  start**. No migration step, and no backup → fresh → restore needed.
- **Credentials stay out of the logs.** The services print `MONGO_URI` at
  startup. Credentials therefore travel as client arguments (`wip_auth.mongo`),
  never inside the URI.

**Action:** re-render with `wip-deploy redeploy`.
- **Images first:** the credentials come from `wip_auth.mongo` and the
  services' own code, both baked into images. On k8s and compose, re-render
  together with images built from this release. With older images, MongoDB
  refuses every service, which leaves the platform down. Dev installs
  bind-mount the source and are not affected.
- **react-console** needs its own `MONGO_USER` / `MONGO_PASSWORD` support
  (WIP-ReactConsole). Until then its MongoDB page connects anonymously and is
  refused.
- **Verification:** `wip-deploy verify --security` now fails an install
  whose MongoDB runs without authentication.

### NATS requires authentication; each service is its own NATS user

NATS accepted anonymous clients on every target. Any workload that could reach
it could read every change event, publish forged events that reporting-sync
applied, delete the streams, and publish ingest work items that ingest-gateway
forwarded with the platform's admin key.
- **Per-service users.** NATS now refuses anonymous connections. Each NATS
  client (def-store, template-store, document-store, reporting-sync,
  ingest-gateway) connects as its own user and may publish only its own event
  subjects and use only the JetStream API it needs. No service can delete or
  purge the platform streams (`WIP_EVENTS`, `WIP_INGEST`,
  `WIP_INGEST_RESULTS`). document-store may create and delete streams in
  general, which replay needs (`WIP_REPLAY_<session>`); the platform streams
  are denied to it explicitly. Each user reads replies only on its own inbox
  prefix (`_INBOX_<user>`).
- **Where it is declared.** Components declare a `nats_client` block (their
  permitted subjects). The deployer generates a `nats-password-<component>`
  secret per client, injects `NATS_USER` / `NATS_PASSWORD` into the client
  and the passwords into NATS, and renders `config/nats/nats.conf` (k8s:
  ConfigMap `wip-nats-config`). The config holds no secrets: it references
  the passwords as environment variables.
- **JetStream store on its volume.** NATS kept its JetStream store in
  `/tmp/nats/jetstream` inside the container, and the 5 GiB volume mounted at
  `/data` stayed empty. Every NATS restart therefore lost all streams:
  unconsumed events, the ingest backlog, replay streams. The store is now
  `/data/jetstream` (CASE-891).
- **Monitoring on localhost.** The monitoring endpoint (port 8222, no
  authentication, `/connz` lists every client) listens on 127.0.0.1 only.
  It is no longer a Service port on k8s, and the healthcheck runs inside
  the container.
- **Ingest is written with the producer's key.** ingest-gateway forwarded
  every work item with the install's master key, and it had no outbound key
  of its own beyond that. Now a producer puts its WIP API key in the
  message's `X-API-Key` NATS header and the gateway writes with exactly that
  key, so the producer's namespace grants apply. **A message without the
  header is refused** (published as a failed result with
  `http_status_code: 401`) and nothing is written. The gateway no longer
  receives `API_KEY` / `DOCUMENT_STORE_API_KEY`.
- **Apps get NATS through roles.** An app manifest declares
  `nats_client: {roles: [...]}` with any of `ingest-producer`,
  `event-subscriber` (one durable on `WIP_EVENTS` named after the app, which
  sees every namespace), `replay-subscriber` (a plain subscription to
  `wip.replay.>`) and `nats-monitor` (read-only stream and consumer
  listings, no message contents). The deployer expands each role to fixed grants and injects
  `NATS_URL` / `NATS_USER` / `NATS_PASSWORD`. Raw subject lists are rejected
  in app manifests. react-console holds `nats-monitor` for its NATS page,
  which needs RC's credential support (WIP-ReactConsole `3c989bd`). An RC
  image without it connects anonymously and NATS refuses it. See
  `docs/wip-deployable-app-contract.md` ("NATS access is a role").
- **Clients outside an app manifest are refused.** Scripts and external
  services have no NATS user.
- **Diagnosing a refusal.** A publish the server refuses shows up in the
  client as a timeout. NATS logs `Publish Violation - Subject "<subject>"`
  with the user name.

**Action:** re-render with `wip-deploy redeploy`.
- **Images first:** the services send credentials from `wip_auth.events`,
  which is baked into their images. On k8s and compose, re-render together
  with images built from this release. Re-rendering while services still run
  older images makes NATS refuse all of them: no events, no reporting sync,
  no ingest. Dev installs bind-mount the wip-auth source and are not
  affected.
- **Rollout order:** a client with credentials can still connect to a NATS
  server that doesn't enforce them yet. So on k8s the order the pods roll in
  doesn't matter, apart from a brief window in which old, credential-less
  pods are refused. They queue their events (see below) until replaced.
- **Lost streams:** the first start with the store on the volume begins with
  empty streams, as every NATS restart did before. Run a reporting batch sync
  afterwards if reporting-sync had not consumed everything. Unprocessed
  ingest items are gone.
- **Verification:** `wip-deploy verify --security` now fails an install whose
  NATS accepts anonymous clients.

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
  supervised consumers' restarts. `/health` stays HTTP 200 regardless: the
  event pipeline's state is for observability, not routing (a store with
  NATS down still accepts writes and queues their events).

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

### Explicit null clears optional term and terminology fields

Term updates align with the platform's RFC 7396 vocabulary: an EXPLICITLY
submitted `null` clears `description`, `label` (display falls back to the
value) and `parent_term_id`, and resets `sort_order` to 0 — while an
OMITTED field keeps meaning "unchanged", so nothing existing breaks.
`value` and `names` reject explicit null loudly (`names` clears with
`[]`). Terminology `description` clears the same way. Reconcile snapshots
may carry explicit nulls with the same meaning, idempotently — and an
explicit null also normalizes the historical `""` workaround to a truly
cleared field. The MCP `update_term` tool exposes this as a `clear`
parameter. Until now there was NO way to remove a term's description;
consumers treating `""` as absent can drop that shim once their data is
normalized.

### `@wip/client` browser writes over 64 KB work (0.60.1)

Up to 0.60.0 the client sent every request with Fetch's `keepalive` flag,
mistaking it for HTTP connection reuse. The flag actually lets a request
outlive the page, and browsers cap the combined body size of in-flight
keepalive requests at 64 KiB. In a browser, a write above that (a file
upload, a long text field, or several concurrent writes or `bulkImport`
batches that together pass it) failed with `TypeError: Failed to fetch`
before leaving the page, so no server log ever showed it. Node callers were
unaffected, and connection reuse needs no flag in either runtime. 0.60.1
drops the flag.
**Action:** apps that use `@wip/client` in the browser re-vendor
`wip-client-0.60.1.tgz`.

### CSV export: query export takes `namespace`; SQL errors are HTTP errors

- `POST /api/reporting-sync/export/csv` accepts `namespace`, with the same
  meaning as on `POST /query`: unqualified table names resolve in that
  namespace's schema. Before, the request model rejected the field with 422,
  so "export what I just queried" worked only for schema-qualified SQL.
- Both export routes prepare the SQL before the response starts. An invalid
  statement or an unknown relation or column is now a **400**, and a missing
  table on `GET /export/csv` is a **404**. Before, these came back as **200
  with an empty body**, because the error was raised after streaming had
  begun. A failure that only appears mid-stream (e.g. a statement timeout on
  a large export) can still truncate the body.
- MCP `export_report_csv` gains `namespace`. Table mode requires it; before,
  table mode always failed with 422 because the route requires a namespace.

**Action:** callers that relied on an empty CSV for a missing table must
handle the 404.

### `@wip/client` 0.61.0: parity and CSV export on `client.reporting`

`reporting.getParity(namespace, { includeCounts? })` returns the typed
`NamespaceParityResult`. `reporting.exportTableCsv(table, namespace, …)` and
`reporting.exportQueryCsv(sql, { params?, namespace?, … })` return a `Blob`;
query export requires an admin or service key. Apps that call these
endpoints with plain `fetch` can switch to the typed methods after
re-vendoring `wip-client-0.61.0.tgz`.

### `@wip/react` 0.21.0: template hooks take a namespace

A template value is unique only within a namespace, but the hooks looked
templates up by value alone. With a key that can see more than one namespace
(an admin or multi-namespace key), `useTemplateByValue` and `useFormSchema`
returned whichever namespace's template came first. 0.21.0 adds the option:

- `useTemplateByValue(value, { namespace? })` and
  `useFormSchema(value, { namespace? })`. Pass `namespace` whenever the key
  can see more than one namespace. A scoped lookup does not fall back to the
  `wip` namespace. Omitting `namespace` stays right for a single-namespace key,
  whose namespace the server derives.
- `useTriggerBatchSync` variables gain `namespace?`. It picks the template when
  its value exists in several namespaces and scopes the sync to that
  namespace's documents. The hook documents it as required together with
  `force`.
- **Query-key shape.** `wipKeys.templates.byValue(value, namespace?)` now
  returns `['wip', 'templates', 'by-value', value, namespace ?? null]`; the
  unscoped lookup carries `null`. Code that builds this key by hand with the
  old four-element form must append the namespace (or `null`); use the
  `wipKeys` helper instead of a hand-built array.

No newer `@wip/client` is needed: the client methods behind these hooks
(`templates.getTemplateByValue`, `reporting.triggerBatchSync`) already accepted
`namespace` in 0.60.0.
**Action:** apps that read templates by value through these hooks, or force a
batch sync, with a key that can see more than one namespace pass `namespace`.
Re-vendor `wip-react-0.21.0.tgz`.

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

### Document-store write doors resolve against the Registry, not the cache

After an entity was hard-deleted and re-created under the same value,
document-store kept resolving the value to the dead canonical id from its
process-local cache (up to 5 minutes), so writes failed with
"Template '<old-uuid>' not found" until the process was restarted —
while the Registry answered correctly the whole time. Every document-store
write door (document create, PATCH, migrate, delete, archive, file
metadata/delete, CSV import) now resolves identifiers with the cache
bypassed, matching what def-store and template-store already did. Reads
keep the cache; its staleness stays bounded by the 5-minute TTL. The
"restart document-store after recycling same-valued entities" workaround
is obsolete. (`resolve_or_404` in wip-auth gained the `bypass_cache`
parameter the single-id doors needed.)

### Bearer tokens from Dex authenticate against the backend services

A Dex token sent as `Authorization: Bearer` to any backend service failed
on every `wip-deploy` install. The JWKS fetch went to a path Dex does not
serve (`/dex/.well-known/jwks.json`) and surfaced as HTTP 500. Past that,
the issuer was compared against Dex's internal URL while tokens carry the
public one, and the audience `wip` matched no Dex client. The deployer now
renders, from the same sources as Dex's own config:
- `WIP_AUTH_JWT_ISSUER_URL`: the public issuer (`https://<hostname>/dex`);
- `WIP_AUTH_JWT_JWKS_URI`: `http://wip-dex:5556/dex/keys`;
- `WIP_AUTH_JWT_AUDIENCE`: every Dex client the deployment registers
  (`wip-gateway` plus each app's own client).

wip-auth accepts a comma-separated `WIP_AUTH_JWT_AUDIENCE` (any listed client
matches) and refuses an empty one. An unreachable key endpoint now answers
503 instead of 500. The gateway and API-key paths are unchanged.
**Action:** re-render (`wip-deploy redeploy`). The six backend services'
environment changes, and image-based installs need images carrying the new
wip-auth.

### Install directories are private; mounted secret files stay container-readable

The spec-declared API-keys file (`config/auth/api-keys.json`) was rendered
0600 and bind-mounted into the backend services. On Linux compose hosts,
registry and mcp-server run as a non-root user and read it through the
"other" permission bits. Under rootless podman, and under a root daemon
whenever the operator's uid differs from the container's, wip-auth failed
with `PermissionError` at startup. Meanwhile the Dex config, which carries
the gateway's client secret, was 0644 inside a 0755 install directory,
readable by every local user. Every apply and render now makes the directory
it writes 0700, and container-mounted files are 0644 inside it. The
directory, not the file mode, keeps other host users out, so non-root
containers can still read their mounts. `.env` and Garage's config stay
0600: no non-root container reads them.
**Action:** none. Existing installs become 0700 on their next apply.

### `wip-deploy render` is a true dry run

`render` shared install's secret handling, so a preview persisted every
secret it resolved into the install's store (`~/.wip-deploy/<name>/secrets`)
whatever `--output-dir` said. It created credential directories for names
that were never installed, and it added missing secrets to a live install's
store. Without `--output-dir` it also wrote a tree built from command-line
flags over the install's own rendered files, which `rebuild`, `restart` and
`start` act on. Now `render` reads an existing store without writing to it
(missing secrets are generated in memory only), refuses any target directory
that holds a `deployment.deployer-state`, and looks for an imported CA in the
secret store rather than the output directory.
**Action:** to refresh a live install, use `wip-deploy redeploy`; to preview
one, pass `--output-dir <scratch dir>`. Earlier previews may have left
secret-only directories under `~/.wip-deploy/` (no `deployment.deployer-state`
inside). Nothing reads them, and they can be deleted.

### Terms: one resolver for document writes, a real merge, hard deletes that stick

**Document writes resolve term values through def-store.** document-store used
to match term fields against its own cached copy of each terminology. That copy
included deprecated and inactive terms permanently, and was up to 5 minutes
stale for deleted terms and edited names. So documents could store a deprecated
or deleted term's id with no warning, while def-store's own `validate` gave a
different answer. Document writes now ask def-store, by the same rule as
`POST /api/def-store/validate`:
- only active terms match;
- a term's value beats any term's name;
- the terminology's case rule applies.

A vocabulary change applies to the very next write. Within one bulk write or CSV
import, each distinct value is resolved once.

The way to ask is the shared `wip_auth.term_resolution` module
(`resolve_term_values`, `term_resolution_batch`), the term-value counterpart of
`wip_auth.resolve`. Any service resolving term values uses it rather than
matching against a vocabulary of its own. def-store's bulk resolution is
now one indexed query per terminology per request: two case-insensitive
indexes on `terms` are built on first start.
**Action:** a write submitting a deprecated or inactive term's value is now
rejected (`invalid_term`), unless an active term carries that value as a name.

**Merging terms: `POST /api/def-store/terms/merge`** (MCP `merge_terms`,
`@wip/client` 0.62.0 `defStore.mergeTerms`). It absorbs one term into another of the
same terminology:
1. the absorbed term's value and names become names of the survivor;
2. it is deprecated with `replaced_by_term_id` = survivor;
3. its Registry entry is merged, so its id and `ns:terminology:value` key
   resolve to the survivor.

Existing documents keep the absorbed term's id. A merge that cannot complete
changes nothing.
**Action:** the Registry's `merge_entries` (and the MCP tool) now **refuses term
entries**, because a Registry-only merge left the absorbed term's value still
resolving to it. Use the term merge.

**Hard-deleting from a mutable terminology removes the Registry entry.** Before,
the Registry refused the deletion in `retain` namespaces, as a per-item error
nobody read. The deleted value kept resolving to the dead id, and re-creating it
reused that id. Now the entry goes too, and re-creating the value mints a new
id. If the Registry still refuses, the delete reports
`error_code: registry_entry_not_removed`.
- Entries left behind by earlier deletes: list them with
  `POST /api/def-store/maintenance/registry-ghosts?namespace=<ns>` (namespace
  admin; a dry run by default), and remove them with `dry_run=false`.
  Entries from mutable or deleted terminologies are removed; a term missing from
  an immutable terminology is only reported.
- The shared Registry client's `hard_delete_entry` now raises when the Registry
  refuses an item, instead of returning quietly.
- Rollout order: update the Registry before def-store. A def-store that already
  sends the exemption to an older Registry gets the deletion refused and
  reports `registry_entry_not_removed`; nothing breaks silently, and the
  ghost-cleanup endpoint removes those entries afterwards.

### Edge-type filters resolve in the request's namespace; an unknown value is 404

`GET /documents/{id}/relationships?template=` and `GET /documents/{id}/traverse?types=`
resolved a bare edge-type value through template-store without a namespace.
When the same value existed in more than one namespace, the lookup could
select another namespace's edge type, and the call returned nothing for
edges that exist. Both filters now resolve through the Registry, like every
other identifier on these routes:
- a bare value names the edge type in the request's namespace (the
  `namespace` parameter, else the seed document's);
- `NS:VALUE` names another namespace;
- a template ID is accepted.

**Behavior change:** a filter value that resolves to nothing used to return
an empty page (or zero traversal nodes). It now returns **404** naming the
value, so a misspelled or wrong-namespace edge type can no longer pass for
"no edges".
**Action:** callers that filter on an edge type that may not exist yet must
handle the 404.

---

### Ingest producers read their own outcomes over HTTP

A producer holding the `ingest-producer` NATS role could submit work items
but had no supported way to learn whether they were created, updated or
rejected. The results stream carries every producer's outcomes, so the role
cannot read it. ingest-gateway now serves each producer its own outcomes:
- `GET /api/ingest-gateway/results/{correlation_id}` returns an item's
  outcome, or 404;
- `POST /api/ingest-gateway/results/lookup` with `{"correlation_ids": [...]}`
  returns one `found` / `not_found` entry per ID, in request order.

The caller sees only the outcomes of items submitted with its own API key:
the gateway resolves the key in each message's `X-API-Key` header to its
principal and records it as `submitted_by` on the outcome. Admins may pass
`?submitted_by=apikey:<name>` to read another producer's outcomes, and
`?submitted_by=` (empty) for items that carried no usable key. Outcomes
last only as long as the results stream keeps them, so poll soon after
submitting.

**Behavior change:** outcomes are published on
`wip.ingest.results.<owner>.<correlation>`, where both tokens are hashes,
instead of `wip.ingest.results.<correlation_id>`. A subscriber to
`wip.ingest.results.>` still receives every outcome, and the payload still
carries `correlation_id` verbatim, now with `submitted_by` alongside.
A correlation ID containing whitespace used to make its outcome
unpublishable: NATS refused the subject and dropped the gateway's
connection, and the outcome was lost. It now publishes normally.
**Action:** none for producers. A subscriber that filtered on a single
`wip.ingest.results.<correlation_id>` subject must switch to the HTTP lookup.
ingest-gateway's NATS user gains read access to its own results stream
(`$JS.API.STREAM.MSG.GET.WIP_INGEST_RESULTS`), so the install must be
re-rendered (`wip-deploy redeploy`) for the lookup to work.

---

### Readiness can report a down dependency; liveness and startup never kill a waiting pod

On k8s, readiness and liveness probed the same endpoint, and there was no
startup probe. Two consequences:
- A store still retrying MongoDB at startup (up to 120 s, during which
  uvicorn serves nothing) was killed by liveness after about 80 s
  (registry) or 95 s (the other stores): three failed checks 30 s apart,
  starting at `start_period_seconds + 5`. The restart reset its retry, so a
  MongoDB that stayed down that long kept the stores in a restart loop.
- No endpoint could report a down dependency without turning it into a
  restart, so a store whose MongoDB was unreachable stayed `Ready`.
  reporting-sync's `/health`, which already answered 503 on unusable
  PostgreSQL service roles, crash-looped its pod instead.

Now:
- `healthcheck.liveness_endpoint` (new, optional) is what the k8s
  livenessProbe and a new startupProbe check; `endpoint` stays the
  readiness check and compose's healthcheck. Without it, every probe uses
  `endpoint`, as before.
- The startupProbe checks every 5 s for `start_period_seconds + 150` s.
  k8s runs no other probe until it first succeeds, so a pod waiting for a
  dependency at startup is no longer killed.
- `healthcheck.retries` becomes every probe's `failureThreshold` (the
  default, 3, is the k8s default).
- Services serve `GET /livez`: 200, no I/O, no credentials (wip-auth
  `install_liveness`).
- registry, def-store, template-store and document-store use `/ready`
  (pings MongoDB, 503 while it is unreachable) for readiness and `/livez`
  for liveness. reporting-sync keeps `/health` for readiness and uses
  `/livez` for liveness.

**Behavior change:** on k8s, a store whose MongoDB is unreachable is taken
out of its Service's endpoints until MongoDB answers, instead of staying
`Ready` and failing requests. Every Deployment has one replica, so for that
time callers reach no pod at all rather than the store's own errors. On compose, the
stores' container health now reflects MongoDB, so `wip-deploy`'s wait for
"all healthy" also waits for MongoDB. `/health` keeps its always-200
contract, and `wip-deploy verify --security --live` is unaffected.
**Action:** re-render the install (`wip-deploy redeploy`); the services
must run this release's images, since the manifests now probe `/livez`.

---

### MCP: registered reporting views (108 tools)

Registered reporting views could be managed only over HTTP, and an agent
with a namespace-scoped key could not read one through MCP at all (raw SQL
via `run_report_query` needs an admin or service key). Four new tools wrap
reporting-sync's view endpoints:
- `register_reporting_view(name, sql, namespace?, registered_by?)` —
  register or replace; returns `created` and `schema_qualified`;
- `list_reporting_views(namespace?)`;
- `read_reporting_view(name, namespace?, filters?, sort?, limit?, offset?)`
  — rows through structured filters, READ on the namespace;
- `delete_reporting_view(name, namespace?)`.

`namespace` is a URL path segment on these endpoints, so it is not derived
from the key: pass it, or set `WIP_MCP_DEFAULT_NAMESPACE`. Register and
delete are write tools, removed in read-only mode (now 51 write, 57
read-only tools). `@wip/client` already had the same four methods.

---

### Terminology deletion checks its template dependencies for real

The guard "a terminology referenced by templates cannot be deleted without
`force`" was silently off on every install: def-store's manifest never
handed the service `TEMPLATE_STORE_URL` or a key, the check's call went to
`localhost:8003` inside def-store's own container, and any failure — an
unreachable Template Store and a non-200 answer alike — collapsed to a
count the guard read as "no dependencies". A successful delete therefore
never meant "nothing depended on this".

The manifest now wires both variables, and the guard fails closed:

- A non-forced **soft delete** is refused while active templates reference
  the terminology, **and** while that cannot be verified ("Could not verify
  template dependencies … retry, or use force=true"). `force=true` keeps
  its meaning — the deactivate stays reversible via restore.
- A **hard-delete** (`hard_delete=true`, namespace `deletion_mode='full'`)
  is refused while templates of **any** status reference the terminology —
  an inactive template still validates the documents pinned to it — or when
  that cannot be verified. `force` does not apply to hard-delete.
- `GET /terminologies/{id}/dependencies` reports an incomplete check as
  `template_count: -1` with `has_dependencies: null` (unknown), never
  `false`.

**Action:** re-render the install (`wip-deploy redeploy --name <install>`)
so def-store receives `TEMPLATE_STORE_URL` and `TEMPLATE_STORE_API_KEY`.
Without them every non-forced terminology delete is now refused instead of
silently succeeding.

---

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

### A deleted namespace's reporting schema stays deleted

Deleting a namespace dropped its PostgreSQL reporting schema, but an event
still in flight could create it again: a document or term event processed
after the drop, a retry, or the term-closure rebuild that runs a few seconds
after term changes. The result was an orphan schema holding only the late
rows, left behind for good (dozens on long-lived dev installs). A namespace
later re-created under the same prefix would start next to that stale data.

reporting-sync now records every namespace it drops in
`public._wip_deleted_namespaces`, in the same transaction as the drop. A
missing schema is created only when no such record exists, or when the
Registry says the namespace is `active` again (re-created under the same
prefix), in which case the record is cleared. Events for a deleted namespace
are acknowledged and counted in the sync status as
`events_skipped_deleted_namespace`, not retried and not reported as drops.
Registering a view in a deleted namespace answers 404. If the Registry
cannot be reached when a deleted namespace's schema would be created, the
event is retried rather than the schema created.

**Action:** remove the orphans earlier versions left, once per install.
`POST /api/reporting-sync/maintenance/orphan-schemas` (wip-admins /
wip-services) lists schemas whose namespace the Registry does not know
(`dry_run=true`, the default) and drops them with `dry_run=false`. Only
schemas holding nothing but tables reporting-sync creates are dropped; any
other schema is listed under `skipped`. If the Registry cannot answer for a
schema, nothing is dropped.

---

### The Registry enforces namespace authorization on entries, synonyms and search

Until now the Registry's entry, synonym and search routes accepted any valid
API key and never checked the caller's namespaces. A key scoped to one
namespace could read another namespace's entries, attach synonyms to them,
and claim a composite key inside it. That namespace's own later
registration of the same value then silently adopted the foreign entry.

Now:
- **Entry allocation is privileged-only.** `POST /entries/register`,
  `/provision`, `/reserve`, `/activate`, `PUT /entries` and `DELETE /entries`
  answer 403 to any key outside `wip-admins` / `wip-services`, including in
  the key's own namespace. These are the stores' internal protocol; app keys
  create terms, templates, documents and files through their store.
- **Synonym writes need write on every namespace they touch.**
  `/synonyms/add` and `/synonyms/remove` need write on the entry's namespace
  and on the synonym's; `/synonyms/merge` needs write on both entries'
  namespaces. A key with only read gets a per-item `forbidden`.
- **Reads see only readable namespaces.** `GET /entries/{id}`,
  `/entries/lookup/by-id`, `/lookup/by-key`, `/resolve` and the three
  `/search/*` routes report an entry in a namespace the caller cannot read as
  not found (404 for the single get), so its existence is not leaked.
  Isolation rules (`open`, `allowed_external_refs`) govern references, not
  reads, as in the stores: a cross-namespace reference keeps working,
  because the service validating it resolves it with its own key.
- **Spec-declared keys keep their write grants at the Registry.** The
  Registry now honours a config-file key's declared grants, as every other
  service already did; before, it answered only read for them.

The master key, every store and every deployer-managed app call the Registry
as privileged callers and are unaffected.
**Action:** a tool that registered or deleted Registry entries with a
namespace-scoped key must use the install's service key, or create the
entity through its store.

---

### Every install has a canary key; `verify --live` checks authorization, not only authentication

`wip-deploy verify --security --live` checked that a request **without**
credentials is refused. Nothing checked that a running install refuses a
caller who is authenticated but has no permission. Three things that unit
tests cannot see could open that gap: a misconfigured admin group in an
install's env, a route made public at the edge, or a roll whose images do
not carry the fix.

Now:
- **Every install carries a canary key.** The deployer adds `wip-canary` to
  every install's config-file keys. It has no groups and no grants, and it
  is scoped to a namespace that is never created. Its secret
  `wip-canary-api-key` is generated on first apply, beside the master key.
  The name is reserved, so a spec may not declare a key called
  `wip-canary`.
- **`verify --live` adds a check, "authenticated callers without permission
  refused".**
  - The canary must get a JSON 403 or 404 from one privileged and one
    namespace-scoped route per active service; a 401 fails, because it
    means a service does not know the canary.
  - The admin key must pass the same requests.
  - Write routes are probed with an invalid body, so nothing is written.
  - The canary must see no namespace and no Registry entry in `wip`.
- **The release checklist runs `verify --security --live`** on the
  release-candidate install after the roll.

**Behavior change:** every install now renders `api-keys.json` and mounts
it into the services that declare `WIP_AUTH_API_KEYS_FILE`, including
installs whose spec declares no keys. The next redeploy restarts those
services once.
**Expect a failure on older images:** before this release, the Registry
let any valid key call the allocation routes. Against those images the
Registry probes fail until the install runs this release's images.

---

### A term looked up by terminology + value accepts the terminology's id and `ns:VALUE`

Every def-store endpoint that takes a term as `?terminology=` plus a raw
value worked only when the terminology was given as its bare value:
- `GET /terms/{term}`
- bulk term updates
- the ontology endpoints
- term audit

The terminology's canonical id, the form templates store, answered 404 for
a term that exists, and so did `ns:VALUE`. The id also demanded a
`?namespace=` it does not need.

Now:
- **Any terminology form works.** The terminology may be its id,
  `ns:VALUE`, or its bare value.
- **Only the bare value needs a namespace.** It is still read in the
  request's namespace, and without one the answer is still 422.
- **The term value stays opaque** and is never colon-parsed.
- **Search follows the same rule.** `GET /terms/search` takes its namespace
  from a terminology given as id or `ns:VALUE`, so it needs no
  `?namespace=` either.

Read access is still checked on the term's own namespace.
**No action required.** `@wip/client` `getTerm` and the MCP term tools pass
the parameter through and pick this up with the new images.

### `wip-toolkit` 0.6.2 and `wip-archive` 0.2.0: the wheels were behind their sources

Both packages changed after 0.5.1 and 0.1.1 were set (2026-07-23) without a
version bump, so one version name covered two different sets of bytes. An app
that scaffolded in that window holds the old build; `pip` treats the old
version as satisfied. The scaffold refresh guard noticed and aborted: a
refresh built the 0.5.1 wheel from this repo and found it differed from the
copy the app vendors. Both are minor bumps because both gained features:
- **Archives declare format 3.1**, and restore remaps resolved edge-endpoint
  halves and qualified cross-namespace references through a fresh restore.
- **`inspect` runs on a shared archive analysis core.**
- **A default export says what it drops.** Its manifest is honest about the
  omission and the toolkit warns loudly.
- **The toolkit health check probes the prefixed route.** In proxy mode it
  answered 404 for every service behind an ingress.

- **The toolkit requires `wip-archive>=0.2.0`.** It imports archive modules
  that 0.1.1 does not have. 0.6.0, briefly on `develop`, declared the
  dependency without a floor: installed over an existing 0.1.1 it was accepted
  and then failed at startup with `No module named 'wip_archive.model'`.
  0.6.2 differs from 0.6.1 only by a lint cleanup (unused imports); its
  version moved because its bytes did.

**Action:** apps vendoring the wheels re-run the scaffold refresh to pick up
`wip_toolkit-0.6.2` and `wip_archive-0.2.0`. A refresh that aborted on the
wheel mismatch no longer trips that guard. Install both wheels in one command,
`pip install libs/wip_archive-*.whl libs/wip_toolkit-*.whl`: `wip-archive` is
not on PyPI, so the toolkit wheel alone fails to resolve its dependency. The
regenerated app CLAUDE.md says the same.

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

- **The MCP server's network transports authenticate on every install.**
  They enabled their key check only when `API_KEY` or
  `WIP_AUTH_LEGACY_API_KEY` was set; the deployer provides the key as
  `MASTER_API_KEY`, so on k8s installs any workload in the namespace got an
  MCP session — acting with the server's admin key — without credentials
  (compose/dev were covered only because their `.env` happens to carry
  `API_KEY`). The transports now require `X-API-Key` equal to the key the
  server itself uses (`WIP_API_KEY` > `MASTER_API_KEY` > `API_KEY` >
  `WIP_API_KEY_FILE`), compare it in constant time, no longer accept it as a
  `?api_key=` query parameter, and **refuse to start without a key** unless
  `MCP_ALLOW_UNAUTHENTICATED=1` is set for local development. In-cluster
  clients (WIP-KB's query agent) already send `X-API-Key`. Takes effect with
  the next mcp-server image.
- **A rotated key now reaches the services that read it, and the master
  key can be rotated.** Services read their secrets and `api-keys.json`
  only at start, and a change to that content alone left every rendered
  workload identical — k8s rolled nothing (and `subPath` mounts never
  refresh in a running pod), compose recreated nothing for a changed key
  file. Each non-storage workload now carries a hash of those inputs (k8s
  pod-template annotation, compose label `wip.dev/config-hash`), so an
  apply restarts exactly the services whose key changed. `wip-deploy
  rotate-key --secret api-key` rotates the install's master key;
  `rotate-key` names the services it restarted and then probes the install
  (new key accepted, old refused; `--no-check` skips it). Database
  passwords and other generated secrets are refused — they need a
  store-side change. **First apply after upgrading the deployer:** the
  hash is new on every non-storage workload, so that one apply rolls all
  Deployments (k8s) or recreates those containers (compose) once.
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
- **Compose and dev containers receive only the secrets they declare.**
  Every service or app that referenced any secret also got the install's
  whole `.env` as `env_file`, so each such container — apps included —
  held the PostgreSQL superuser and MinIO root passwords, the Dex user
  passwords and the master key, whatever its manifest declared. `.env` is
  now only the `${VAR}` interpolation source (as it already was for the
  values themselves), so a container sees exactly its manifest's
  `from_secret` variables — the same set k8s gives it. Code that read an
  undeclared variable worked only through the leak; none was found in the
  platform services or the local apps. The next apply recreates every
  container that referenced a secret.
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
