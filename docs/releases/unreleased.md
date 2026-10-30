# WIP — unreleased

Changes on `develop` since v2.1.0 that the next release note must carry. Move
these sections into `vX.Y.Z.md` when the release is cut, then empty this file.

---

## Action required / behavior changes (read before upgrading)

### A `prefixed` ID prefix must be unique on the install

The Registry minted `prefixed` IDs from a counter per (namespace, entity type,
prefix), while every `entry_id` must be unique install-wide. Two namespaces
using one prefix minted the same IDs, and the second one's writes failed
intermittently with a raw MongoDB duplicate-key error.

Every namespace write (`POST /api/registry/namespaces`, `PUT …/{prefix}`,
`POST …/import`) now refuses, before storing anything, an `id_config` whose
`prefixed` prefix:
- another namespace or another entity type of the same namespace already
  uses → `409`, naming the holder;
- is empty, or ends in a digit (`DOC-00` and `DOC-` would both mint
  `DOC-000010`) → `422`.

Re-sending a namespace's stored `id_config` unchanged is never refused, so a
bootstrap resume or a same-name restore keeps working. At startup the
Registry records each namespace's prefixes and logs a warning for any two
namespaces that already share one.
**Action:** look for `id_config prefix conflict` in the Registry log after
the upgrade; give one side of each reported pair another prefix. An import
under a new name onto the install that still holds the source now needs its
own prefix (it would have minted the source's IDs). Apps whose seed
`namespaces.json` carries a prefixed `id_config` and runs on a dev and a
deployed namespace of one install get a 409 at the second namespace — drop
the literal prefix there.

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

### Backup archives reach the object store on Garage installs

Document-store keeps backup and restore archives in their own bucket,
`wip-backups`. Provisioning created only `wip-attachments`, and the files key
may not create buckets, so on every Garage install each archive stayed in
document-store's scratch storage: cleared after `WIP_BACKUP_RETENTION_HOURS`
(default 24) and lost with the container, while the job reported `complete`.

- Provisioning now creates `wip-backups` and grants the files key on it,
  idempotently, next to `wip-attachments`; document-store's manifest names the
  bucket explicitly (`WIP_BACKUP_BUCKET`).
- A backup or restore whose archive could not be stored in the object store —
  missing bucket, refused write, or a store that is down — still completes, but
  the job now carries a warning saying the archive is in scratch only, until
  when, and (for a backup) where to download it.

**Action:** run `wip-deploy redeploy` on each Garage install to create the
bucket. Until then, download any backup you need to keep
(`GET /api/document-store/backup/jobs/{id}/download`) before the scratch sweep
or a container restart removes it.

### Reporting catch-up after a restore no longer slows with the square of a template's size

A batch sync paged each template's documents by offset, and every page made
document-store sort all of the template's documents (the list's default order
cannot use an index): measured on 43,856 documents, the first and the fortieth
page each examined all 43,856. A 115k-document template took about four minutes.

- The batch sync now pages by cursor (`_id` order). A new index on the
  documents collection, `ns_template_status_id_idx`
  (`namespace, template_id, status, _id`), lets each page read one page of
  keys — a test checks the query plan against MongoDB. The template's total,
  for progress, comes from one single-document request.
- A restore's reporting count check waits while reporting rows keep landing,
  instead of a fixed 90 seconds: it gives up after 60 seconds without
  progress (or 30 minutes overall), and its warning names the batch-jobs and
  parity endpoints to watch. Meanwhile the job's progress message shows
  "reporting catching up: X of Y rows".

**Note:** document-store builds the new index at its first start after the
upgrade; on a large collection that start takes correspondingly longer.

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

### Template writes that change nothing report `unchanged`

- **`PUT /templates`** reports `status: "unchanged"` (with the current
  `version` and `is_new_version: false`) when the update mints no version. It
  used to report `"updated"` for every successful item, so a caller acting on
  `updated`, for example by deactivating the previous version, acted on a
  no-op.
- **An identical edge-type re-post** (create-as-upsert) returns `unchanged`.
  An edge type's `source_ref` / `target_ref` constraint is derived from its
  endpoint declaration when the template is served, so the copy a caller
  sends on those fields is neither compared nor stored. A bootstrap that
  re-posts the served shape used to mint a new version on every run.

**Action:** none. Installs where a bootstrap already minted duplicate
edge-type versions keep them; deactivate the extras by hand if wanted.

### Conditional rules and nested fields are validated on write

Two declarative checks that passed silently now apply to every document
write and to `/validate`:

- **`conditional_required` rules fire.** Template-store serves every
  optional rule key, an unset `required` as `null`, and document-store read
  that null as "not required", so the rule never applied. Unset now means
  required; only an explicit `required: false` turns a rule off. Rules with
  a null `allowed_values` (`conditional_value`) or `target_fields`
  (`mutual_exclusion`) no longer raise.
- **Fields inside nested templates are resolved like top-level ones.** Term,
  reference and file fields of an object field's `template_ref` or an object
  array's `array_template_ref` used to be type-checked only: an unknown term
  was accepted, a reference was never existence-checked, and nothing was
  recorded. They now go through the same checks as top-level fields, and
  their resolutions are stored under the nested path
  (`ingredients[0].ingredient`, `address.country`) in `term_references`,
  `references` and `file_references`. Reporting keeps them in
  `term_references_json`; nested fields get no per-field `_term_id` column.
  Identity hashing is unchanged: a dotted identity path into an object field
  still hashes its submitted value.

**Action:** documents already stored with a value these checks reject (an
unknown nested term, a dangling nested reference, an imported document
missing a conditionally required field) stay as they are, but their next
update is rejected until the value is corrected. To find them first, run
`POST /api/document-store/validation/validate` over the existing documents
of each namespace that uses nested templates or conditional rules.

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

### Merging a newer backup into an older namespace works; merges carry term deprecations

A merge (`mode: merge`) of a newer backup of a namespace into an older state
of it was refused whenever a term had been added since. The terminology's
`term_count`, a count def-store derives from its active terms, was compared
as content. `extend_terminologies`, the flag the refusal suggested, could
not get past that comparison. A term deprecated in the newer backup was
refused too, because its lifecycle fields differed.

Now:
- `term_count` is not compared, and the merge recounts it with def-store's
  rule (active terms) after it adds or retires terms. A merged terminology
  ends with the right count.
- **A term the archive retired** (deprecated or inactive) that the target
  still has active is retired on the target, with the archive's reason and
  replacement (mapped to the target's term ids).
- **Retirement travels one way:** a term the target retired stays retired,
  and the difference is reported like other target-wins fields.

A new template version in the newer backup was not affected; it merges with
`add_missing`.

### A merge asks what happens to the target's older versions; `@wip/client` 0.65.0, WIP-Toolkit 0.7.1

A merge with `on_clash: overwrite` or `newer` appends the archive's latest
version on top of the target's. It used to leave the target's older version
`active` beside it, unlike a normal write, which retires the version it
supersedes. The document then had two active versions, and the post-merge
parity check waited its full 90 seconds and warned.

The new merge option `older_versions` makes it a choice:
- `retire` (default): the target's older versions are soft-deleted, as a
  normal write does.
- `keep`: they stay active beside the new head.

It is accepted by the upload restore and restore-from-job endpoints, the MCP
`start_restore` tool, and `@wip/client` 0.65.0 (`RestoreOptions.older_versions`,
type `OlderVersionsPolicy`). It is recorded in the restore provenance record's
`merge` block. Outside `mode: merge` a non-default value is rejected, like the
other merge options.

WIP-Toolkit 0.7.1 adds `wip-toolkit restore`, a thin client of the restore
endpoint: it uploads the archive, starts the job and follows it, with every
mode and merge option as a flag (`--older-versions retire|keep` among them).
It also fixes `wip-toolkit update-document`, which sent its PATCH to
`/api/document-store/api/document-store/documents` (the service prefix
twice), a path the document-store does not serve.

**Action:** callers that relied on the old behaviour (older versions staying
active after an overwrite merge) pass `older_versions: keep`. Apps re-vendor
`wip-client-0.65.0.tgz` to get the typed option.

### A restore keeps document-store responsive while it plans; identity synonyms re-qualify in batches

- **Planning off the event loop.** A merge restore, dry run included, read and planned the whole archive synchronously on document-store's event loop, in two full passes. The single worker then answered nothing, `/livez` included, for as long as planning took: about 45 minutes on a 463,529-version archive before CASE-959. A fresh restore's planner did the same per entity type. These passes now run on a worker thread. The engine's progress events, whose sink is an `asyncio.Queue` and not thread-safe, are handed back to the loop from there.
- **Batched re-qualification.** The identity-synonym re-qualification every restore runs (and `/maintenance/requalify-identity-synonyms`) made two single-item Registry requests per document: about 90 documents a second, roughly 40 minutes for 222k documents. It now sends one add request and one remove request per batch of 500. A bare synonym is still removed only after its qualified form was added. A failed remove is now reported as an error: before, it counted as re-qualified and left the bare synonym in place. A restore reports the step as `phase_requalify` with per-batch progress, instead of sitting silently in `phase_synonyms`.

### `wip-deploy install`: a wrong repo root, a registry URL and a missing `--hostname` stop with the cause

One k8s install hit three silent defaults at once:

- **Wrong repository root.** Run from another git repository, `install` (and every verb that builds a fresh spec, such as `validate` and `render`) took that repository as the WIP checkout. Validation then reported every preset module as `unknown component (known: [])`. These verbs now stop with the same message the additive verbs already gave: `discovery found no components/apps under <root> — this is not a World-in-a-Pie checkout`, followed by the ways to point at the right one. The check is one helper shared by every path.
- **Registry with a scheme.** `--registry https://ghcr.io/x` was taken verbatim, and every image reference (`<registry>/<name>:<tag>`) would have failed to pull. `images.registry` now refuses a URL scheme, a trailing slash or surrounding spaces, naming the value to use (`use 'ghcr.io/x'`). A port is fine: `localhost:5000/x`. Saved specs are checked when they load.
- **No `--hostname`.** `install --target compose|k8s` silently used `wip.local` as the Ingress host, the OIDC issuer and the TLS certificate's name. The result was an install that looked healthy and could not be logged into. It is now refused. `--target dev` keeps `localhost`, and the preview verbs keep `wip.local`. A re-run over an existing install still shows the existing-install guard first; `--reconcile` without `--hostname` is refused too, so a reconcile cannot move a live install to the placeholder.

**Action:** scripts that run `wip-deploy install` on compose or k8s without `--hostname` now pass it explicitly. Fix any `--registry` value with `https://` or a trailing `/`.

### Dex users keep their existing passwords: `user add --password-hash-file`, `user import`

Every redeploy-family verb renders Dex's users from the spec, so users
created by hand in the rendered `config.yaml` were lost on the next render.
The only supported way to declare them was `user add`, which generates a new
password, so every existing user had to be given one.

A user can now be declared with an existing bcrypt hash. In the spec this is
`auth.users[].password: supplied` (default `generated`; saved specs load
unchanged).
- Only the hash secret `dex-password-<username>.bcrypt` exists for such a
  user. It is operator-supplied: the deployer never generates it, and an
  apply that finds it missing stops and names the user.
- `wip-deploy user add … --password-hash-file <file>` declares one user.
- `wip-deploy user import <file>` declares many in one apply, from a Dex
  config (its `staticPasswords`) or a CSV file (`username,email,hash` and an
  optional `groups` column). Groups come from the CSV column, then
  `--groups-file` (YAML username → groups), then `--group`.
  - Users already declared are left as they are; any other problem refuses
    the whole import.
  - `--dry-run` previews it.
  - If the apply fails, the stored hashes are removed again.
- `user list` shows such users as `supplied hash`.

**Action:** an install with hand-made Dex users runs `wip-deploy user import
<saved dex config> --group … --groups-file …` once; after that, re-renders
keep them.

### The app scaffold no longer demands a version bump for a rebuilt, unchanged wheel

The scaffold refuses to ship a vendored wheel (`wip-toolkit`, `wip-archive`)
whose content changed under an unchanged version, because pip never reinstalls
a version it already has. It compared raw bytes. A newer build backend stamps
its own version into the wheel (`Generator:` in `*.dist-info/WHEEL`), so an
unchanged `wip-archive` 0.3.0 rebuilt with hatchling 1.33.0 instead of 1.32.4
aborted an app refresh and asked for a version bump nobody owed.

- The check compares what a wheel ships: every member byte for byte, except the
  `Generator:` line and `RECORD` (which only hashes the members). A real code or
  `METADATA` change under an unchanged version is still fatal. When only the
  stamp differs, the app keeps the wheel it has.
- Both wheel projects pin `hatchling==1.33.0` as their build backend, so an
  unchanged tree rebuilds to identical bytes.

**Action:** an app whose refresh stopped on `wip_archive-0.3.0 … different
content` re-runs `create-app-project.sh .`; no version bump is needed.

### Reporting parity counts live documents on both sides

The parity check (`check_reporting_parity`, and the count phase at the end of
every restore) compared the number of active document VERSION rows in MongoDB
with every row in the reporting tables. Two correct states failed it:
- **A document soft-deleted after reporting had synced it:** its row stays,
  with status `deleted`, and was counted. Such a namespace reported a
  mismatch, and a merge into it waited the full 90 seconds and warned.
- **A document with several active versions** (left by a merge with
  `older_versions: keep`): counted once per version on the MongoDB side.

Both sides now count live documents: documents whose latest version is active
(the `latest_only=true` count), against reporting rows that are not `deleted`.
`all_versions` templates are still not compared.

### Reporting parity covers term tables and registered views; batch sync restores term relations

The parity check only looked at document tables. A namespace whose
`term_relations` and `term_closure` were empty after a restore, while its
terms were back, reported `ok: true`. Two changes:
- **Parity checks everything reporting derives.** Every registered view must
  exist (`views`). With counts, `derived_tables` compares active `terms` and
  `term_relations` rows with def-store's active totals, checks that every
  named term has `term_names` rows, and checks `term_closure`'s reflexive rows
  against what its rebuild writes. Failures are counted in `derived_issues`
  and named by an issue code. `ok` is true only when all of it passes, so the
  count phase at the end of a restore now waits for, and warns about, these
  tables too.
- **The namespace batch sync (`POST /sync/batch`) syncs term relations.** Its
  definitions companion synced terminologies and terms but not relations,
  which is how a restore left `term_relations` and `term_closure` empty. It
  now syncs relations after the terms, ending with a closure rebuild.

**Action:** a script that treats parity `ok` as its pass condition may now
see `false` on a namespace that passed before. Read `derived_tables` and
`views`; a re-run of the namespace batch sync repairs the term tables, and
re-registering repairs a missing view.

### `latest_only` + `status` filters on each document's latest version

With `GET /api/document-store/documents?latest_only=true`, a `status` filter
ran before the latest version of each document was picked. So
`status=inactive` returned each document's highest *inactive* version: the
superseded versions of live documents came back next to the soft-deleted
ones, and `total` and `pages` counted them. The status filter now applies to
each document's latest version, so `latest_only=true&status=inactive` lists
exactly the soft-deleted documents. `status=active` and calls without
`latest_only` are unchanged. This also affects the MCP `list_documents`
tool, which defaults to `latest_only=true`.

`latest_only=true` with a `cursor` used to ignore the cursor silently and
page by `page`. It is now refused with 422.

### Document archive is removed; `@wip/client` 0.63.0, `@wip/react` 0.23.0

Document "archive" is gone. It was designed as policy-driven tiering of
superseded versions, which was never built; what shipped archived the latest
version on demand and behaved as a second soft delete, with rules that
contradicted delete's. Soft delete replaces it.

**Removed, in one step (no deprecation window):**
- `POST /api/document-store/documents/archive` and the `document.archived`
  event;
- the `archived` document status and the PATCH `archived` error code;
- `archived_relationship_endpoint`;
- the MCP `archive_document` tool;
- `@wip/client` `documents.archiveDocument` (0.63.0) and `@wip/react`
  `useArchiveDocument` (0.23.0).

Namespace archive (`POST /namespaces/{prefix}/archive`) is a different
feature and is unchanged.

**Existing data:**
- document-store rewrites every `archived` document row to `inactive` at
  startup;
- reporting-sync rewrites `status = 'archived'` reporting rows to `deleted`
  at startup;
- a restore writes an `archived` row from an older backup as `inactive`.

File reference counts need no fix-up: archive released them exactly as soft
delete does.

**Relationships:** an edge whose endpoint is soft-deleted is now accepted
with a warning, the same rule a plain reference to an inactive document
follows. Before, an archived endpoint was refused while a deleted one was
accepted.

**Action:**
- **Callers of the removed endpoint, method, hook or tool break on upgrade.**
  Use soft delete instead: `DELETE /api/document-store/documents`,
  `documents.deleteDocument`, `useDeleteDocument`, `delete_document`.
- **Apps re-vendor** `wip-client-0.63.0.tgz` and `wip-react-0.23.0.tgz`.
  WIP-RC must remove its Archive action and Active/All archive toggle.
- **Replayed `document.archived` events.** If an install's NATS stream still
  holds one, reporting-sync now handles it like any document write and stores
  the payload's `archived` status. Startup rewrites existing rows only.

### Re-posting a deleted document brings it back as a proper successor

Re-posting the identity of a soft-deleted document always brought it back,
but by accident. The create path looked only for an active version, took
the write for a brand-new document, collided with version 1, and the
duplicate-key retry pushed it to the next version. The revived document
reported `updated`, but its `created_at` was reset to the re-post time and
its `metadata.custom` was dropped.

A write now follows the identity's latest version, whatever its status. A
soft-deleted document comes back as its successor:
- it keeps the entity's `created_at`;
- it carries `metadata.custom` forward unless the caller supplies it;
- it reports `updated`;
- it does not release the deleted version's file references a second time.

The revival happens even when the data is identical. On a `versioned: false`
template the document comes back in place, at the same version. PATCH on a
deleted document still answers `not_found`: bringing a document back takes
a full re-post.

### A retired entity releases its synonyms; a soft-deleted document no longer blocks a new identity

A soft-deleted document kept its Registry synonyms claimed. A new document
whose identity spellings matched it, but whose identity resolved differently,
was refused with `Composite key already registered under different entry`.
The typical case is a term identity field whose term was hard-deleted and
re-created. Deleting the documents too worked only in a `deletion_mode: full`
namespace.

Retiring an entity now releases its synonyms. Retiring means:
- soft-deleting a document or a term;
- deprecating a term;
- soft-deleting a terminology, which retires its terms with it;
- deactivating the last active version of a template.

A released synonym stays recorded on the entity's Registry entry with
`status: "inactive"`. It no longer resolves, and it no longer blocks another
entity from registering the same key.

What is not released:
- the entity's canonical ID;
- its primary key;
- its own `{ns, type, …}` value synonym.

So a retired entity stays addressable by ID and by value, and re-posting a
deleted document's identity still revives it.

Reviving an entity claims its synonyms again. Reviving means:
- re-posting a document's identity;
- reactivating, re-posting or activating a template version;
- restoring a terminology.

A synonym that another entity took in the meantime stays with that entity.
The revived document's write result carries a warning naming the holder.

**Registry:**
- `POST /api/registry/entries/deactivate` (with `dry_run`) and
  `/entries/reactivate` are new. They are bulk and privileged, and the stores
  call them.
- Lookups, `/resolve`, search and registration dedup ignore inactive
  synonyms.
- A merge moves an inactive synonym as a record only. It never takes over
  the claim, which may belong to another entry by then.
- A term merge does not release the absorbed term's synonyms: they move to
  the survivor and stay live.

**Restores:**
- Every restore mode ends by releasing the synonyms of the entities it
  restored as retired. This also covers archives taken before this change.
- An id-preserving restore claims no inactive synonym.
- A fresh restore does not re-add a synonym the archive holds inactive. On
  that path the record of an already-released synonym is not carried over.
- `/maintenance/requalify-identity-synonyms` keeps a retired document's
  synonym released in its new qualified form.

**Action:** documents, templates and vocabularies retired before this release
still hold their synonyms. Run the new pass per namespace, dry run first:
`POST /maintenance/reconcile-synonym-lifecycle` with
`{"namespace": …, "dry_run": true}`. It exists on document-store,
template-store and def-store, and requires namespace admin. `revive: true`
also reclaims the synonyms of active entities; it is applied only, never on a
dry run.

A failed Registry call during a delete or revival does not fail the write:
- document-store reports it as a warning on the item;
- template-store and def-store log it.

The pass repairs both.

### A value that names several entities is refused, never resolved to one of them

A bare value used to resolve through the Registry's `search_values` fallback
with a single `find_one`, which returned whichever matching entry came first.
`search_values` holds every value of an entry's active keys. So a value was
ambiguous whenever several entries carried it:
- the same identity spelling under two templates;
- a template's value, which every one of its documents carries in its
  auto-synonym.

In those cases a reference resolved to an arbitrary entity.

**Registry:** `POST /api/registry/entries/lookup/candidates` is new, bulk, and
gated by read access. For each value it returns every entry the value
resolves to, with `matched_via` and `matched_composite_key` (the qualifier),
plus `total` and `truncated`. An exact entry ID is the only candidate.
`/lookup/by-id` is unchanged.

**Document reference fields:** a bare string reference resolves through the
candidates. The field's `target_templates` narrow them. One survivor resolves.
Several fail the write with the per-field code `ambiguous_reference`, which
lists `{document_id, template_value, matched_composite_key}` per candidate.

**Other callers:** `resolve_entity_id` (`wip_auth.resolve`) raises
`AmbiguousEntityError`, a subclass of `EntityNotFoundError`, so callers that
answer 404 still do, with a message naming the candidates. The non-strict
batch path leaves an ambiguous value unresolved.
`RegistryClientBase.resolve_identifier` raises `AmbiguousIdentifierError`, a
`RegistryError`.

**Action:** a write or lookup that relied on the arbitrary pick now fails. Use
the canonical ID or a qualified value, or give the reference field
`target_templates` that single out the intended document.

### Merge and fresh restores stream the documents

Only a plain `restore` streamed the archive. A `merge` and a `fresh` restore
read every document version row into memory before writing, so their peak
memory grew with the archive's document payload and large restores crashed
memory-limited pods. Both now plan from a compact row per document and read
each document's version chain again, one at a time, where it is checked and
written.

Measured on synthetic archives (Python heap): a merge's planning input for
40 000 documents x 2 versions went from 201.9 to 43.1 MiB; a fresh restore's
planning phase from 106 to 20.4 MiB, and with ten times larger documents
from 449 to 20.5 MiB. What remains grows with the number of documents and
registry entries, not with their size. Definitions (terminologies, terms,
templates) are still held whole.

**Action:** none. An archive whose documents are not sorted by
`document_id` (exported before the backup sorted them, July 2026) cannot be
streamed this way; it restores as before, holding the documents in memory,
with a warning naming the archive. A new backup of the source streams.

### Operator-supplied secrets reach their apps (`ANTHROPIC_API_KEY`)

An optional env var with `from_secret` was filled only when the deployer
collected that secret, and it collects for required vars only, so
`ANTHROPIC_API_KEY` never reached react-console, wip-kb, wip-aa, wip-cooking,
wip-val, dnd, song or dev-kb, even with `secrets/anthropic-api-key` in the
install's store, and nothing said so. A manifest now marks such a var
`supplied_by: operator`:

- the deployer never generates the secret; it fills the var whenever the
  secret is in the store, and every apply and `render` prints a `note:`
  naming the app, the var and the expected file when it is absent;
- deployer-generated optional secrets (the Garage keys on document-store)
  keep the old rule, so a key file left behind by `remove-module garage` is
  not handed to document-store;
- operator-supplied secrets are exempt from the secret-store drift check:
  the operator adds, rotates and removes them on purpose.

**Action:** to give an app its Anthropic key, put it in
`~/.wip-deploy/<install>/secrets/anthropic-api-key` and re-apply
(`wip-deploy redeploy`). App manifests outside
this repo that read an operator-placed secret need `supplied_by: operator`.

### Group mappings and connectors no longer restart the backend

The federated group policy reached the six token-validating services and
the auth-gateway as `WIP_AUTH_FEDERATED_GROUPS`, read at start, so every
`wip-deploy group-map` and `connector` change applied the whole install.
Now:

- the policy is a file, `federated-groups.json`, next to `api-keys.json` in
  the mounted `/etc/wip` directory, rendered on every install (empty
  without connectors). The services take it from
  `WIP_AUTH_FEDERATED_GROUPS_FILE` and re-read it every 10 s
  (`WIP_AUTH_FEDERATED_GROUPS_FILE_RELOAD_SECONDS`); the gateway re-reads
  it when it changes. A file that does not parse keeps the previous policy;
- `group-map add|remove` restarts nothing; `connector add|remove` restarts
  only Dex. auth-gateway mounts `/etc/wip` again, for this file.

**Action required:** as for the declared keys, the re-read is in the
service images. Re-render with this release's image set; an install that
keeps older images loses its federated group policy, because those services
still look for `WIP_AUTH_FEDERATED_GROUPS`, which the deployer no longer sets.

### Declared API keys apply without restarting the backend

Adding, removing or rotating a declared key (`auth.api_keys`) used to
restart every backend service, because each one's config hash covered the
rendered `api-keys.json`. Now:

- every service that checks keys (registry, def-store, template-store,
  document-store, reporting-sync, ingest-gateway) re-reads the file while
  running, every 10 s (`WIP_AUTH_API_KEYS_FILE_RELOAD_SECONDS`, `0` turns it
  off), and swaps the config keys in one step. A file that does not parse
  keeps the previous keys and logs CRITICAL;
- the file is mounted through its directory (`/etc/wip`), not as a single
  file or k8s `subPath`, so a running container sees it change. On k8s the
  kubelet refreshes the mount first: about 80 s measured on the platform
  cluster;
- `api-keys.json` is no longer part of the config hash, so nothing restarts
  for a declared key. auth-gateway and mcp-server no longer mount the file;
  they never read it;
- `rotate-key` names the secret file instead of printing the new key;
  `--show-key` prints it. For a declared key it says how the key arrives,
  and its post-rotation check waits up to 150 s;
- an MCP server configured with `WIP_API_KEY_FILE` follows a rotated key
  file: outbound calls use the new key and the network transports accept it,
  without a reconnect.

**Action required:** the reload is in the service images. An install whose
images predate this release gets the new mounts and hashes on its next
re-render, but its services read the key file only at start, so a declared
key change would reach them only on their next restart. Re-render with the
image set of this release (`wip-deploy redeploy --tag <this release>`), the
same rule as every rendering change.

### Every restore leaves a provenance record; `wip-archive` 0.3.0

Restored data was only partly identifiable: a fresh (remap) restore
appended a sentence to the namespace description, and an id-preserving
restore or a merge left nothing in the namespace. Every restore mode now
writes one structured record per namespace it wrote. The record holds the
restore job and mode; when and by whom; the archive's SHA-256 and versions;
the source namespace, host and export time; the archive's `derived_from`
transform chain; and, for a merge, the clash policy and planned counts.
Records are only added, never replaced. A backup carries the namespace's
records in its manifest (`NamespaceEntry.provenance`, a new optional field
in `wip-archive` 0.3.0; archive format stays 3.1). A restore of that backup
copies them in, marked `inherited`, before adding its own. Each record
links the validation job its restore started.

Read it with `GET /api/document-store/backup/provenance?namespace=<prefix>`
(oldest first, paginated, namespace read permission). An empty list means
no restore has written into the namespace. The id-preserving restore
still leaves every restored entity an exact copy; the record is a separate
row.

**Action:**
- **Re-render the install** (`wip-deploy redeploy`). The registry's
  MongoDB user gains find and remove on the new
  `wip_document_store.restore_provenance` collection, which namespace
  deletion now clears. Registry code from this release on an install
  rendered before it fails every namespace deletion, dry runs included,
  with "not authorized": deletion counts each collection it will clear
  before it starts.
- **Apps that vendor `wip-archive`** pick up 0.3.0 on their next
  scaffold refresh.

### Scaffolds keep local config out of git; `--kb` needs a key file

App repos had committed per-machine files, and before key files existed
those included literal WIP API keys. Two scaffold changes stop this:

- **App scaffold, every run:** `.mcp.json` and `.claude/kb.json` join `.env`
  and the session files in `.gitignore`. Before this, the entries were added
  only when a project was created, and the two config files were not among
  them. A tier-3 app gets a tracked `.claude/kb.json.example` with
  placeholder values. A new `.githooks/pre-commit` gate refuses staged local
  config, the value of any secret file on the machine, and known key
  shapes. The scaffold enables it unless the clone already sets
  `core.hooksPath`, and leaves an existing `.githooks/pre-commit` alone. A
  refresh prints the `git rm --cached` command for any local-config file
  the repo still tracks; it does not commit.
- **Both scaffolds:** `--kb` no longer falls back to
  `~/.wip-deploy/kb/secrets/api-key`. The KB key is issued per installation,
  so `--kb-key <path>` is required unless `.claude/kb.json` already records a
  key file. A missing or unreadable key file is an error, raised before
  `kb.json` is written.

**Action:** pass `--kb-key` when enabling tier 3. In an existing app that
still tracks `.mcp.json` or `.claude/kb.json`, run the scaffold refresh,
then the `git rm --cached` command it prints, and commit. Repos whose
history holds a literal key should retire that key.

### App phase commands read rule cards instead of the depth documents

Four short rule cards now live in `docs/cards/` and are bundled into every
app's `docs/cards/` by the scaffold: `core.md` (what an app is, the model
rule, the approval gate, the eight PoNIFs in one line each), `model.md`
(Phase 2–3), `build.md` (Phase 4) and `ship.md` (the deployable-app
contract as a checklist). The phase commands' mandated reading points at
them: `/wip-explore` reads `core.md` and `model.md` instead of the four MCP
resources, `/wip-design-model` and `/wip-implement` read `model.md` instead
of `WIP_PoNIFs.md`, the build playbook reads `build.md` instead of
`WIP_DevGuardrails.md`, both READMEs and the PoNIFs, and `/wip-deploy` and
the deploy playbook point at `ship.md`. The depth documents stay bundled
and are opened when a card does not answer a question. `/wip-setup` and
`/wip-wake` keep their full baseline reload.

Each card records a hash of every source it condenses (the served
`wip://` resource text for the MCP resources). A test in the mcp-server
suite fails when a source changes after the card's last review, and names
the `git diff` to read before the hash is updated.

**Action:** none for the platform. An app picks the cards up on its next
scaffold refresh. When editing Vision, the PoNIFs, the served resources,
the guardrails, the technology stack, the UI guidance, the deployable-app
contract or the app CLAUDE.md template, expect the card drift test to name
the card to re-review.

### A scaffold refresh keeps MCP servers the project added to `.mcp.json`

The scaffolds rewrote `.mcp.json` whole, so a refresh silently dropped any
MCP server a project had added itself (WIP-Cooking lost its Playwright
server for browser QA). Only the `wip` server is the scaffold's now: it is
regenerated, and every other server and top-level key is kept. A file with
nothing else in it is written exactly as before.

**Action:** a project that already lost a server on an earlier refresh
re-adds it once, e.g. from its git history.

### A slim app profile for agents with a small working set

`scripts/create-app-project.sh --profile slim` scaffolds an app for an agent
that must stay well inside its context (a local model): it reads short rule
cards instead of the depth documents, and scripts do its bookkeeping.

- **CLAUDE.md** is `scaffold/templates/claude-md/app-slim.md` (~1k tokens,
  against ~8k for the full one): the app line, the dev namespace, one line
  per phase command, the session protocol, the 300k-token stop rule, an
  empty app-gotchas section, and where the cards and depth documents are.
- **Commands:** the shared phase commands, without `/wip-analyst`,
  `/wip-add-app`, `/wip-bootstrap`, `/wip-fireside` and `/wip-status`, and
  with short `/wip-wake`, `/wip-setup` and `/wip-report` from
  `docs/slash-commands/app-slim/`. The slim wake is one script call
  (`wake-rollover.py`, then the new `wake-brief.py`), the core card, the
  phase card, and a WIP check only when the next task touches WIP data.
- **`wake-brief.py`** prints the recorded phase and its age, the previous
  session's summary, recent commits, uncommitted files, the brief's open
  cases and decisions, and a warning when the commit hook is not active.
- **`wake-rollover.py --close FILE`** closes the current session: the agent
  writes only the summary text; the script sets `status: closed` and
  `ended_at`, keeps an existing summary, replaces a stub, and mirrors to kb.
  Both profiles can use it; the slim `/wip-report session-end` does.
- **Compaction** keeps the session: the slim re-anchor hook prints the core
  card and the recorded phase instead of sending the agent to `/wip-wake`.
- **Status line:** the recorded profile is shown next to the context figure;
  `slim` turns red at 300k tokens used, `full` at 80%. The tokens are what
  the context holds now: `current_usage` when the harness sends it (Qwen
  Code), else the fill percentage times the window (Claude Code). Qwen
  Code's `total_input_tokens` is the sum over every request of the session
  and would trip the 300k rule within a few dozen requests. The activity
  part reads Qwen Code's prompt hook too: Qwen sends a slash command
  expanded into the command's text with the typed command as the last line,
  and fires the hook with an empty prompt on its own turns, which no longer
  clears the activity. Qwen Code's tool hooks are recognised as well
  (`skill`, `run_shell_command` next to Claude Code's `Skill`, `Bash`); a
  skill Qwen's agent calls itself shows by name only, since Qwen sends no
  arguments with it.

The profile is recorded as `PROFILE` in `.claude/.app-meta` (every app now
records it; existing apps get `PROFILE="full"`). A refresh keeps the recorded
profile unless `--profile` is given again; an unknown value is refused.

**Action:** none for existing apps. To move an app to the slim profile, run
the scaffold with `--profile slim`. Its existing CLAUDE.md is kept and the
slim one is written as `CLAUDE.md.refresh`: move the app's own sections into
the slim file's "App-specific gotchas", then replace CLAUDE.md with it (or
pass `--force-claude-md`).

### Apps record their phase and log their commits from git

Two pieces of app-agent bookkeeping no longer depend on the model:

- **Phase:** each phase command (`/wip-explore`, `/wip-design-model`,
  `/wip-implement`, `/wip-build-app`, `/wip-improve`) first runs
  `.claude/scripts/set-phase.py <phase>`, which writes a tracked
  `.claude/.app-phase` ("3 implement"). Committed with the phase's work, its
  `git log` is the phase history.
- **Commit log:** a new `.githooks/post-commit` runs
  `.claude/scripts/log-commit.py`, which appends each commit's entry to
  `reports/<session-id>/commits.md` from git: hash, time, author, files and
  lines, the subject as heading, the body as the Why, and the `Tests:`,
  `PoNIF:` and `Discovered:` commit-message trailers. An already-listed hash
  is not logged twice, and a commit that changes only files under `reports/`
  (a session close, the commit log itself) is not logged at all — so the
  commit that sweeps up the last entry does not leave a new one behind. The app CLAUDE.md's "After Every Commit" now asks only
  for those trailers.

**Action:** an app picks both up on its next scaffold refresh. The hook is
written only when `.githooks/post-commit` is absent, and runs only when
`core.hooksPath` is `.githooks` — a clone with its own hooks path gets no
entries until it calls `.claude/scripts/log-commit.py` from its own hook.

### Query-preset production images build for their base path and pass the healthcheck

The `--preset query` production `Dockerfile` declared no `VITE_BASE_PATH`
ARG, so the `--build-arg VITE_BASE_PATH=/apps/<slug>/` its CI workflow
passes never reached `vite build`: the SPA built with base `/` and a
deployed app's assets 404. Its `node:22-slim` runtime also had neither
`curl` nor `wget`, so the deployer's HTTP healthcheck probe exited 127 and
the app reported permanently unhealthy. The scaffold's `Dockerfile` now
declares the ARG and installs `wget` in the runtime stage.

**Action:** the scaffold creates this file only for a new app, and a
refresh does not rewrite it. In an existing query-preset app, add
`ARG VITE_BASE_PATH=/` plus `ENV VITE_BASE_PATH=${VITE_BASE_PATH}` before
`npm run build`, and install `wget` (or `curl`) in the runtime stage — or
copy both from `scripts/scaffold-query/Dockerfile`.

### App guidance matches the platform again

Writing the rule cards turned up app-facing documents that taught code the
platform rejects. Corrected:

- **Bootstrap** (`/wip-bootstrap`, the bootstrap playbook): relations are
  created with `create_term_relations`, not `create_relations`, and the
  seed files' 2-part `TERMINOLOGY:TERM_VALUE` must be prefixed with the
  namespace (`ns:TERMINOLOGY:TERM_VALUE`) — the 2-part form is rejected with
  422. `create_terms` takes a terminology value as well as a UUID, and a
  cross-namespace terminology reference is `wip:COUNTRY`, not a UUID lookup.
- **`baseUrl`**: `technology-stack.md` and the app CLAUDE.md template use
  `` `${import.meta.env.BASE_URL}wip` ``; a bare `'/wip'` works only for an
  app served at `/`.
- **`WIP_DevGuardrails.md`**: Guide 4's client example uses the real
  options, hooks and error classes; Guide 6 states the client's actual retry
  policy (GET only, 2 retries from 500 ms); Guide 3's container skeleton
  points at the deployable-app contract instead of a superseded Caddy
  image and `VITE_WIP_*` variables; Guide 2's React and Router versions
  match `technology-stack.md`.
- **The build playbook**: term dropdowns use `useTerms`, references
  `resolveReference(client, …)`, document lists `useDocuments({ … })`.
- **The deployable-app contract**: `wip-deploy check-app-deployability` is
  a shipped CLI verb, and the scaffold-gaps table says what the query
  scaffold does today.
- **PoNIFs #3 and #8** (served `wip://ponifs` and `WIP_PoNIFs.md`):
  changing `identity_fields` is refused (`identity_fields_immutable`), and
  `versioned: false` is available on any template with identity fields,
  not only edge types.

**Action:** none. Apps pick up the corrected documents on their next
scaffold refresh.

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

### `@wip/react` 0.22.0: `useReportQuery` owns its query key

`useReportQuery(sql, params, options)` typed its options as
`Omit<UseQueryOptions, 'queryFn'>`, which left `queryKey` required: the hook's
own documented call, `useReportQuery(sql, undefined, { namespace })`, failed
`tsc` with "Property 'queryKey' is missing". The hook builds its key itself
(`wipKeys.reporting.query(sql, params, namespace)`), and the options were spread
after it, so a caller who passed a `queryKey` to satisfy the type silently
replaced the hook's namespace-scoped key. The options type now omits
`queryKey` as well.
**Action:** drop any explicit `queryKey` from `useReportQuery` options (it no
longer compiles; the hook's key is the same one `wipKeys` builds). Re-vendor
`wip-react-0.22.0.tgz`.

### `@wip/client` 0.65.0 / `@wip/react` 0.24.0: the maintenance passes have typed clients and hooks

`POST /api/document-store/maintenance/rehash-identities` and
`POST /api/document-store/maintenance/requalify-identity-synonyms` answered a
bare JSON object, so the OpenAPI snapshot had no response type and `@wip/client`
had no method for either (an app had to use a raw `fetch`). Both endpoints now
declare typed responses (`RehashIdentitiesResponse`,
`RequalifyIdentitySynonymsResponse`); the response bodies themselves are
unchanged.

- `@wip/client` (added in 0.64.0, which 0.65.0 replaced):
  `documents.rehashIdentities(request)` and
  `documents.requalifyIdentitySynonyms(request)`, with typed requests and
  responses. The namespace goes in the request body. Both passes default to a
  dry run on the server, need admin permission on the namespace, and are
  synchronous (no job to poll).
- `@wip/react` 0.24.0: `useRehashIdentities()` and
  `useRequalifyIdentitySynonyms()`. They invalidate caches only after an
  applied pass (`dry_run: false`).

**Action:** none for existing callers; this is additive. Apps that want the
methods or hooks re-vendor `wip-client-0.65.0.tgz` and `wip-react-0.24.0.tgz`.

### The identity-hash migration converges after a failed Registry leg; `applied` means fully migrated; `@wip/client` 0.66.0

`POST /api/document-store/maintenance/rehash-identities` writes MongoDB first,
then re-keys the Registry's claims. When the Registry leg failed, the
documents' stored hash was already the new one, so every later run counted
them unchanged and never asked the Registry again: their claims stayed on the
old hash for good. `applied` had counted them as migrated anyway.

- Every apply now re-sends the Registry re-key for documents whose stored hash
  is already right. The Registry answers `unchanged` for a claim that is
  current, and re-keys one an earlier run left behind. Re-running the apply
  after a Registry outage now completes it.
- `applied` counts only documents whose rows were rewritten **and** whose
  Registry re-key succeeded. New counts: `registry_failed` (re-key did not
  succeed, from this run or an earlier one) and `registry_repaired` (stored
  hash already right, claim re-keyed by this run). Such documents are listed
  in `items` with `status: "unchanged"` and their `registry_status`; the item
  status enum gains `unchanged`.
- The re-key is sent in batches of 500; a failed batch marks only its own
  documents.
- The collision `detail` no longer tells the caller to archive a document
  (archive was removed).
- `@wip/client` 0.66.0 types the new fields and status, and its README's
  browser `baseUrl` example is `` `${import.meta.env.BASE_URL}wip` `` (a bare
  `'/wip'` works only for an app served at `/`).

**Action:** a caller that read `applied` as "rows rewritten" now gets "fully
migrated"; read `registry_failed` too. If an earlier apply reported any
`registry_status` starting with `error`, run the apply again. Apps using the
typed client re-vendor `wip-client-0.66.0.tgz`.

### Namespace validation checks a reference's namespace and names what its lookup resolves to; `@wip/client` 0.67.0

The namespace validation job (`POST /api/document-store/backup/namespaces/{namespace}/validate`,
and the job every fresh or merge restore starts) checked only that a
document reference's held id exists. A fresh restore maps a reference's
recorded namespace to the new one even when its target was not in the
archive and so kept its old id. The result names the new namespace and an
old id, and on the source install that id still exists, so the check passed
it.

- A held id that lives in a different namespace than the reference records
  is a new error, `misplaced_document_ref`. The summary gains
  `misplaced_document_refs`.
- An orphaned or misplaced reference whose `lookup_value` differs from the
  held id now says what that lookup resolves to on this install, or that it
  could not be resolved there. That is the identifier the reference was
  written with, and the one a repair would use.
- `@wip/client` 0.67.0 types `misplaced_document_refs` on
  `NamespaceIntegritySummary`.

**Action:** none; a validation result may now report errors it missed
before. Apps using the typed client re-vendor `wip-client-0.67.0.tgz`.

### Backups record what they reference but do not carry; `wip-archive` 0.4.0

An archived reference to an entity outside the archive stored only that
entity's id: a template field drawing from a `wip` vocabulary, a document
pointing into a sibling namespace, a term relation into another ontology.
On another install that id names nothing, or the wrong thing, and nothing in
the archive said which entity was meant.

- A backup now writes `referents.jsonl` at the archive root: the Registry
  entry of every entity the archived rows reference but the archive does not
  carry, with its primary key and synonyms. Those are the entity's identity
  in a form another install can resolve, for example `{ns: wip, type: term,
  terminology: _TIME_UNITS, value: seconds}`, or a document's
  `{@template: …, <identity field>: …}` synonym.
- `Manifest.referents` counts the rows. `0` means the archive references
  nothing outside itself. An archive from before this change has no key and
  reads as `None`, so a restore can tell "nothing external" from "not
  recorded".
- Additive: older readers ignore the file and the key, and the archive
  format stays 3.1.
- Restores do not use the table yet. Repairing out-of-archive references on
  a fresh or merge restore from it is the next step of the same work.

**Action:** none. Backups taken from now on carry the table. Apps that
vendor `wip-archive` pick up 0.4.0 on their next scaffold refresh.

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

### reporting-sync's startup backfill retries until it is complete

At startup reporting-sync backfills terminologies, terms, term relations and
template metadata into PostgreSQL. It waited for def-store only and ran each
step once: template-store was never waited for, and a listing that failed (a
service still starting, or answering 500 while its database restarted) was
logged as `synced: 0` and never retried. The tables then stayed empty until
each entity's next write. This was seen after redeploys, Podman VM restarts
and image rollouts.

Each step now waits for its own source and retries, every 5 s doubling to
every 60 s with no deadline, until its listing completes. It logs WARNING
while incomplete and INFO once complete. The def-store steps and the
template-store step run independently, so one source being down does not hold
back the other.

The batch-sync endpoints (`POST /api/reporting-sync/sync/batch/terminologies`,
`…/terms`, `…/term_relations`, `…/templates`) now also return
`"complete": true|false`: `false` means a listing failed, so a `synced: 0`
can be told apart from an empty namespace.

**No action required.**

---

### Writes resolve names against the Registry, never the cache

Services cache name-to-id resolutions for 5 minutes. A namespace deleted and
re-created under the same name kept resolving `ns:VALUE` to the deleted
entities for those minutes, so writes failed with "<deleted id> not found"
(for example creating a term in a re-created terminology). The rule that
writes bypass the cache was opt-in per route, and about a dozen write routes
had not opted in.

wip-auth now marks every request other than GET, HEAD and OPTIONS as a write,
and resolution in a write always asks the Registry (the fresh answer also
replaces the stale cache entry, so reads heal with it). Reads still use the
cache and can lag a re-created namespace by up to 5 minutes until the next
write refreshes the entry.

**No action required** beyond running images with this wip-auth.

---

### App routes can name the groups allowed through the gateway

Behind the auth-gateway, any logged-in user could open any app. The in-app
`ALLOWED_GROUPS` check runs only in apps that have their own OIDC client, and
gateway-mode apps have none, so nothing enforced it.

A manifest route can now declare `allowed_groups`:

```yaml
routes:
  - {path: /apps/rc, auth_required: true, allowed_groups: [wip-admins]}
```

The deployer renders the list into that route's verify URL (Caddy
`forward_auth` on compose and dev, NGINX `auth-url` on k8s). The gateway
answers 403 to a logged-in user in none of the groups: a short "no access"
page for browsers, JSON for API callers. Behind NGINX only the status reaches
the browser. No session is still the login redirect, and an empty or absent
list keeps today's behaviour. React Console's route now allows `wip-admins`
only.

**Action:** re-render the install (`wip-deploy redeploy --name <install>`) so
the proxy configuration carries the groups, and run an auth-gateway image with
this change. Apps that relied on `ALLOWED_GROUPS` while running behind the
gateway can move the list to their route.

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

### A Registry outage answers 503, not "Namespace not found"

Namespace permission checks go to the Registry. When the Registry could not be
reached, or answered 5xx, the check used to report "no access":
- a namespace-scoped request answered **404 "Namespace not found"**;
- a listing across namespaces answered **403 "No accessible namespaces"**;
- that wrong answer was cached for 30 s, so it kept coming after the Registry
  was back.

Now the check answers **503 "Authorization service unavailable"**, caches
nothing, and access returns on the next request once the Registry is up.
Access is still refused during the outage. A `PATCH /documents` item reports
`error_code: "registry_error"` instead of `forbidden`. A Registry answer of "no
access" is unchanged: still 404 or 403, still cached.

**Action:** callers that treat 404 as "this namespace does not exist" should
treat 503 as "try again", not as a missing namespace.

---

### File routes: an unreachable object store answers 503; refusals get their own status

With the object store down, a file upload used to escape the connection error
to the server and answer a bare **500 "Internal Server Error"**, with no detail.
An unreachable Registry did the same during an upload. Now:

- an object store that cannot be reached (connection refused, DNS, timeout)
  answers **503** with `detail` "Object store unreachable at … — retry
  shortly", on every document-store route that touches storage;
- an upload whose Registry cannot be reached answers **503**; a Registry that
  answered but refused the file ID answers **502**;
- `POST /files`, `GET /files/{id}/download` and `DELETE /files/{id}/hard`
  map the refusal to a status instead of one status for all:
  a file in the wrong state (`invalid_state` — deleted, not yet inactive)
  is **409**, a fault on the server is **500**.

**Behavior changes:** a hard-delete of a file that is still active, a
download URL for a deleted file, and `GET /files/{id}/content` for a deleted
file answered **400** and now answer **409**. A hard-delete while the store is
down answers 503 and keeps the file record, as it always kept it — retry once
the store is back.

**Action:** callers that branch on 400 for these cases should branch on 409;
treat 503 as "try again".

---

### Registry: a synonym takes its entry's namespace and type; an unknown entity type is an error

`/synonyms/add` used to store whatever `synonym_namespace` and
`synonym_entity_type` the caller sent, and report `added`:
- an unknown type (the singular `document`) made a synonym no lookup could
  ever match;
- a valid but foreign one made a synonym that resolved to the wrong thing —
  another namespace's key answering with this entity, or a type's lookup
  returning an entity of another type.

Now:
- **`/synonyms/add`** stores the synonym in its entry's namespace with its
  entry's entity type. The two fields are optional — omit them. Sent values
  that differ are refused per item: `error_code: "invalid_request"`,
  `details.rule: "synonym_scope_mismatch"` (or `invalid_entity_type` for a
  type that does not exist). A synonym is not a cross-namespace link; to
  reference another namespace's entity, address it as `NS:VALUE`.
- **`/synonyms/remove`** defaults both fields to the entry's, and still
  honours explicit ones as sent, so a synonym stored under another scope
  before this release can be removed.
- **`/synonyms/merge`** refuses two entries of different namespace or entity
  type (`merge_scope_mismatch`).
- **Lookups** (`/entries/lookup/by-key`, `/lookup/by-id`, `/entries/resolve`,
  `/search/by-fields`, `/search/by-term`) report an unknown entity type as a
  per-item `invalid_entity_type` error instead of `not_found`; the
  `GET /entries` and `GET /entries/search` filters answer it with **400**.
  A key lookup never returns an entry whose own namespace or type differs
  from the one asked for: such a synonym can only predate this release, and
  it is logged and reported `not_found`.

No install checked had a synonym scoped outside its entry (wip-local,
dev-test, prod-test, kb.internal), and every platform service already sends
the entry's own scope. The MCP `add_synonym` / `remove_synonym` tools now
take the two fields as optional.

**Action:** callers that send `synonym_namespace` / `synonym_entity_type`
should send their entry's own values or drop them. A restore of an archive
holding a synonym scoped outside its entry now fails loudly instead of
re-creating it.

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

- **External identity providers (Dex connectors).** `wip-deploy connector
  add|set-secret|remove|list` declares Google, GitHub, a corporate OIDC or
  LDAP provider in the spec (`auth.connectors`), rendered into Dex on every
  target. The redirect URI is derived (`<issuer>/callback`); the client
  secret is operator-supplied into the secret backend (`dex-connector-<id>`)
  and never generated, so an apply with a missing one stops with
  instructions. `wip-deploy group-map add|remove|list` grants WIP groups to
  federated users by verified email or domain (`auth.group_mappings`).
  `wip-admins` and `wip-services` are never granted to a federated login,
  neither by mapping nor by the provider's own groups: every token-validating
  service and the auth-gateway drop them (`WIP_AUTH_FEDERATED_GROUPS`, set
  only when a connector is declared). See wip-guide §4.6.
- **Dex static users hold several groups.** A spec user carries `groups`
  (a list) instead of `group`; `wip-deploy user add --group` takes a
  comma-separated list, and the new `wip-deploy user set-groups <user>
  --group a,b` replaces a user's groups in place, keeping the password.
  Saved installs whose users still say `group` load unchanged. The first
  user verb or re-render writes `groups` into `deployment.deployer-state`,
  which an older `wip-deploy` then refuses to load: upgrade the deployer on
  every machine that drives the install.
- **A stale or incomplete secret store stops the apply.** Every apply now
  records a fingerprint of each secret it ran with (`secret_digests` in
  `deployment.deployer-state`, never the values) and checks the store
  against it, and on k8s against the live `wip-secrets`, before generating
  anything. A known secret that is missing or different stops the apply
  with the names. Previously the deployer generated fresh datastore
  passwords from an old copy of the install folder and wrote them into the
  cluster while the pods kept the old ones. New verbs: `wip-deploy secrets
  pull` (k8s: adopt the running values) and `wip-deploy secrets accept`
  (record the folder as right). Every apply names the secrets it generated.
  See wip-guide §7.2.
- **Scoped verbs also restart what they change elsewhere.** `add-app`,
  `app-deploy`, `remove-app` and a scoped `redeploy` compare every
  workload's config hash with the previous render and add the out-of-scope
  services whose configuration changed — a secret they read, the router's
  routes, MongoDB's user set — printing their names. Before, those changes
  went live only at the services' next unrelated restart.
- **A failed dev-target build no longer takes the stack down.** A full dev
  apply removed every `wip-*` container and then built the images inside
  `compose up --build`, so a failed build left nothing running. The apply
  now builds first and touches containers only after the build succeeded.
  podman-compose also runs with `--parallel 4`: unbounded, a dozen
  simultaneous builds each opened an SSH session into the macOS podman
  machine, and sshd's MaxStartups limit (10) dropped the surplus builds
  with `ssh: handshake failed: EOF`.
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
