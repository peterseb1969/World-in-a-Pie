# Backup / Restore Redesign

**Status:** Design — not yet implemented
**Author:** BE-YAC-20260408-2138
**Context:** Followup to `backup-restore-approach.md` (the reuse-vs-rewrite
decision for CASE-23 Phase 3). That doc established the toolkit-wrapping
approach; this doc replaces the *data model* of backup and restore after
CASE-23 Phase 3 failed on the clintrial smoke test for reasons rooted in
the approach itself.

**Prerequisites:**
- **CASE-31** (Registry edge index) — not a blocker for this design, but
  enables a much cleaner closure phase. This doc specifies a workaround
  that uses the existing Registry browse endpoint so the redesign can
  land before CASE-31 does.
- **CASE-32** (File composite key = checksum) — **blocker for fresh mode
  and cross-install restore**. ID-preserving "restore mode" can land
  without it.

---

## Problem statement

The current backup implementation (via `wip-toolkit`'s `run_export`) walks
every namespace's data by calling back into the owning service's HTTP
filter APIs (`GET /documents?namespace=X&...&page=...`). On large
namespaces this path is:

1. **Fragile.** Filter queries on 228k-document namespaces triggered an
   HTTP 500 in the closure phase during the clintrial smoke test
   (2026-04-08), which in turn destabilized MongoDB.
2. **Indirect.** The exporter re-derives the namespace's entity graph at
   export time by paginating service APIs, even though every entity
   carries its `namespace` field first-class and every Registry entry
   is indexed on `(namespace, entity_type, status)`.
3. **Unnecessarily coupled to service runtime health.** A backup cannot
   run if the service it is backing up is degraded — but that is
   precisely when you most want a backup.

The restore implementation inherits the same coupling: it drives restore
through the service create-APIs, which means restore time is dominated by
validation and ID-generation round-trips rather than by raw data movement.

## Goals

1. **Backup is a direct read of per-namespace collections.** No paginated
   filter queries against service APIs. Mongo `find({namespace: X})` per
   collection, streamed.
2. **Restore has a matrix of modes, each with a clear identity contract.**
   The four modes (restore, target_namespace, fresh, cross-install DR)
   are distinguished by how they handle entity IDs and references, not by
   where the data comes from.
3. **References resolve via the platform's own synonym/composite-key
   machinery.** Restore's reference-rewriting pass uses Registry lookups,
   not service-specific heuristics. Files included (depends on CASE-32).
4. **The draft-then-activate state machine is retired for the common case**
   and reserved only for the modes that genuinely need it (fresh mode).
5. **Cross-namespace references are a first-class, explicit policy choice**
   per restore invocation, not a silent default.
6. **The dump is a reconstitutable substrate.** Given a dump plus an empty
   target namespace, restore produces a semantically equivalent namespace.
   "Semantically equivalent" is defined below.

## Non-goals

- Changing the ID-generation algorithms or the meaning of entity IDs.
- Introducing a new serialization format (the current JSONL + manifest
  archive layout is fine).
- Changing how the toolkit talks to services for `wip-toolkit import/export`
  CLI invocations (those stay; they are a client of the same dump
  format).
- Solving the general edge-index problem — that is CASE-31 and requires
  a platform-wide refactor. This design works around it explicitly.

---

## Identity: what changes across namespaces

Before the modes, the identity model needs to be stated plainly because it
drives every decision.

**Entity IDs are not globally unique.** Per `id_algorithm.py`, the default
is UUID7, but a namespace can be configured with `prefixed` (e.g.
`TERM-000042`), `pattern`, `nanoid`, or custom algorithms. The platform
only guarantees that `(namespace, entity_type, entity_id)` is unique, and
even that only inside one install. Across installs nothing is guaranteed.

**Composite keys are the only identity that survives.** Every entity
carries a Registry composite key (post-CASE-32 including files) that is
derived deterministically from the entity's own fields. Two installs
holding the same logical entity will agree on its composite key, even if
their entity_ids differ.

**This is the axis restore modes split on.** A mode that preserves IDs is
fast but requires an empty target namespace on the same install (or at
most a same-algorithm sister install). A mode that rewrites IDs has to go
through composite-key lookup and is therefore much more work but is the
only option for fresh copies and cross-install migration.

---

## Dump format

Directory/ZIP layout (unchanged from the current archive except for the
additions called out):

```
<archive>.zip
├── manifest.json             # namespace, counts, source install metadata
├── terminologies.jsonl       # Terminology documents (Beanie shape)
├── terms.jsonl               # Term documents
├── templates.jsonl           # Template documents
├── documents.jsonl           # Document documents
├── files.jsonl               # File metadata (not blob bytes)
├── term_relationships.jsonl  # def-store term_relationships collection
├── registry_entries.jsonl    # Registry entries for this namespace
├── registry_externals.jsonl  # NEW: one-hop external refs (see below)
└── blobs/
    └── <file_id>             # Raw MinIO blob, named by file_id
```

### Manifest additions

The manifest grows metadata about the source install:

```json
{
  "namespace": "clintrial",
  "exported_at": "2026-04-08T22:30:00Z",
  "source_install": {
    "schema_version": "1.4",
    "id_algorithms": {
      "terminologies": "uuid7",
      "terms":         "uuid7",
      "templates":     "uuid7",
      "documents":     "uuid7",
      "files":         "uuid7"
    },
    "hash_version": 1
  },
  "counts": {
    "terminologies": 12,
    "terms":        171,
    "templates":      8,
    "documents": 228286,
    "files":       2189,
    "term_relationships": 0,
    "registry_entries":  228666
  }
}
```

`schema_version` and `hash_version` let restore detect incompatible source
installs. `id_algorithms` lets restore warn on mode choice (e.g. fresh mode
from a `prefixed` source into a UUID7 target is fine; `restore` mode into
a target with different algorithms is not).

### registry_externals.jsonl (new)

When dumping `clintrial`, some documents may reference terms in the `wip`
namespace (open isolation mode with cross-namespace term references).
Those external term_ids are not in `clintrial`'s registry_entries.jsonl —
but the composite keys for those referenced entries *are* needed for
cross-install restore to re-resolve them.

The backup pipeline does a **one-hop walk**: for every reference in the
namespace's documents that points outside the namespace, it adds the
referenced Registry entry to `registry_externals.jsonl`. This is a small,
bounded set — clintrial's externals are dozens of entries, not millions.

Restore uses this file to re-resolve external references against the
target install's Registry by composite key.

### What is NOT in the dump

- Reporting-sync's PostgreSQL tables. Derivable from documents via NATS
  replay after restore.
- NATS message history. Ephemeral by design.
- Registry synonyms for entries outside the dumped namespace, beyond the
  one-hop walk above.

---

## Backup pipeline

Replaces the current closure-walk-via-service-APIs with direct reads.

### Phase 1 — Collection dumps

For each target collection (`terminologies`, `terms`, `templates`,
`documents`, `files`, `term_relationships`):

```python
cursor = collection.find({"namespace": ns, "status": {"$ne": "deleted"}})
async for doc in cursor:
    writer.write_entity(collection_name, doc)
```

Index-backed, streams through a cursor, constant memory. Runs in the
document-store container (or wherever the dump job lives), talks directly
to MongoDB. No HTTP calls to service APIs at all.

### Phase 2 — Registry entries

```python
cursor = registry_entries.find({"namespace": ns})
async for entry in cursor:
    writer.write_entity("registry_entries", entry)
```

Indexed on `(namespace, entity_type, status)`. Fast.

### Phase 3 — External reference walk

(Workaround for the missing edge index. This is the piece CASE-31 would
simplify.)

For each dumped document, parse its `data` field against the already-dumped
template definitions to find reference fields (`term_ref`, `reference`
with `reference_type: term`, etc.). For each reference target, check if
its entry_id is in the already-dumped registry_entries. If not, it is an
external. Collect external entry_ids into a set.

Then one more Registry query: fetch those specific entries and write them
to `registry_externals.jsonl`.

On a namespace with no cross-namespace references, this file is empty.
On clintrial with shared terminologies, it holds the handful of external
term entries.

### Phase 4 — Blob dump

For each file entry in `files.jsonl`, read the blob from MinIO and write
it to `blobs/<file_id>`. This uses the CASE-28 streaming path that just
landed. Constant memory.

### Phase 5 — Manifest finalization and zip

Write manifest.json, flush the zip.

### Key properties

- No service filter-API calls. No closure-phase fan-out. No fragility on
  large namespaces.
- Runs against a live system with minimal contention (cursor reads are
  cheap).
- Resumable in principle (the cursor position can be checkpointed if a
  phase fails mid-way).
- Constant memory per phase. Scales to arbitrary namespace sizes.

---

## Restore modes

### Mode 1: `restore` — preserve IDs, empty target namespace, same install

The simplest and fastest mode. Target namespace must not exist or must be
empty. Restore bulk-inserts the dumped Beanie documents directly into their
respective collections with zero modification.

**Procedure:**
1. Validate target namespace is empty (or create it from manifest metadata
   if missing). Refuse otherwise.
2. Compare `id_algorithms` in manifest with target install's expectations
   for that namespace. Refuse on mismatch.
3. If `schema_version` or `hash_version` differ between source and target,
   refuse (or offer an explicit rehash pass — separate feature).
4. For each collection: bulk insert from the corresponding JSONL.
5. Bulk insert registry_entries. The Registry's unique index on
   `(namespace, entity_type, primary_composite_key_hash)` acts as a
   correctness check: if anything is duplicated (e.g. from a partial prior
   restore attempt) the insert fails loudly.
6. Extract blobs from the archive into MinIO keyed by `file_id`.
7. Post-restore integrity verification pass: walk every reference field,
   confirm the target entry exists in the registry_entries collection.
   Fail fast with a precise "document X field Y references missing Z"
   error if not.
8. Trigger reporting-sync catch-up (below).

**No draft/activate.** References are valid from the moment of insert
because every target entity is also in the dump.

**Uses:** same-install rollback, exact clone of a namespace to a new name
(with `target_namespace`, see below).

### Mode 2: `target_namespace` — preserve IDs, different namespace name, same install

*Previously I thought this could bulk-insert with a namespace field rewrite.
That is incorrect because entity_ids are not globally unique, so the
rewritten collection could collide with identically-IDed entries in
another namespace, and because composite_key_hash embeds the namespace.
This mode is therefore a variant of fresh mode, not of restore mode.*

See "fresh mode" below. `target_namespace` is fresh mode with an explicit
destination namespace name.

### Mode 3: `fresh` — new IDs, may share an install with the source namespace

Fresh mode assigns new IDs from the target Registry. References are rewritten
via composite-key lookup. This is the mode that CASE-32 (file composite
key) is a prerequisite for.

**Procedure:**

1. **Pass 1 — insert as draft.** Walk the dump in dependency order
   (terminologies → terms → templates → documents → files → relationships).
   For each entity, call the owning service's create-API with:
   - `status: "draft"` (skips reference validation)
   - The entity's stored fields, minus any reference fields — or with
     reference fields left as their **source composite key** rather than
     their source ID
   - A new ID will be assigned by the target Registry

   Each created entity gets a new ID. Build a mapping:
   `{(entity_type, source_id) → target_id}`.

2. **Pass 2 — rewrite references.** For every entity created in pass 1,
   look at its reference fields. Each reference in the dump can be
   expressed as a composite key by looking up the source Registry entry.
   Query the target Registry by composite key (via the normal synonym
   resolution endpoint) to get the target ID. Update the entity with the
   new reference value.

   For file references: the source composite key is `{"checksum": sha}`
   (post-CASE-32). Target Registry lookup finds the (possibly new) file_id
   in the target namespace. If the file doesn't exist yet in the target
   namespace, the file was created in pass 1 and already has a target ID
   in the mapping.

   For cross-namespace external references: look up the composite key in
   `registry_externals.jsonl`, then query the target Registry against the
   external namespace by that composite key. If not found, fail per the
   cross-namespace policy (see below).

3. **Pass 3 — activate.** Flip every entity from `draft` to `active`.
   The activate endpoint revalidates references. If pass 2 missed anything
   the activation fails with a precise error. In practice, if passes 1
   and 2 succeeded, activation is a no-op from a validation standpoint.

4. **Blob upload.** For each file, upload its blob from the archive.
   With CASE-32's checksum-as-composite-key, uploading the same bytes twice
   returns the existing file_id — which is correct idempotent behavior.

5. **Reporting-sync catch-up** (below).

**Used for:**
- `target_namespace` — clone a namespace to a new name (most common
  fresh-mode case; likely the only common case)
- Sandbox/staging clone of production data
- Bootstrap: install a distributable app template into a clean namespace
- Potentially: merging data from multiple sources into one namespace
  (needs more thought — this is where fresh mode stops being edge-case)

**Trade-offs:**
- Slow: every entity goes through a create + update + activate round trip.
- Dependent on CASE-32 for file references.
- The draft/activate dance is necessary here — references span entities
  that don't exist yet during pass 1.

### Mode 4: `cross-install DR` — new IDs, different install

Same engine as fresh mode, with one addition: external references via
`registry_externals.jsonl` (see dump format) use composite keys to re-resolve
against the target install's Registry. If the target install does not
have the referenced external entry, restore either fails loudly or
(optionally) creates a placeholder entity — but the default is to fail,
because silent creation is exactly the "silent data corruption" that
CLAUDE.md warns against.

**Used for:** DR, migration to a new box, upgrading between installs.

### Mode matrix (revised)

| Mode | Target | IDs | Refs | Draft/activate | Blocked by CASE-32 |
|---|---|---|---|---|---|
| **restore** | Empty ns, same install, same algos | Preserved verbatim | Preserved verbatim | No | No |
| **target_namespace** | Empty ns, new name, same install | New IDs | Composite-key lookup | Yes | Yes |
| **fresh** | Any empty ns | New IDs | Composite-key lookup | Yes | Yes |
| **cross-install DR** | Empty ns on new install | New IDs | Composite-key lookup + externals | Yes | Yes |

`target_namespace`, `fresh`, and `cross-install DR` are variants of one
engine distinguished by configuration (target name, external re-resolution
on/off, integrity check strictness). Only `restore` has a separate fast path.

**Peter's observation during design:** `restore` mode is sufficient for
the vast majority of real-world cases. `fresh` mode is genuinely
edge-case — possibly limited to merging data — and should be evaluated
on its own merits rather than being the default. The design accommodates
it but does not privilege it.

---

## Cross-namespace reference policy

When a namespace uses cross-namespace references (e.g., `clintrial`
documents reference shared terms in `wip`), restore needs an explicit
policy per invocation:

- **strict** (default): any dangling external reference after pass 2 fails
  the restore with a full list of unresolved references. Operator must
  restore the dependency namespace first or correct the source.
- **best-effort**: attempt re-resolution via composite key against the
  target install; if found, use it; if not, fall back to strict.
- **lenient** (not recommended, flagged with a prominent warning): insert
  the entity with a dangling reference and log it. Violates "references
  must resolve" but is offered for explicit operator override.

Restore defaults to **best-effort** for cross-install DR (the common case
where target has the dependency namespaces but with different IDs) and
**strict** for same-install modes (where dependencies should already
resolve).

---

## Post-restore steps

Independent of mode:

### 1. Reporting-sync catch-up

Direct Mongo inserts bypass the NATS events that populate PostgreSQL.
After restore, reporting-sync needs to re-sync the restored namespace.

Two options:
- **Event replay**: synthesize NATS events for every restored entity and
  publish them. Reporting-sync handles them normally.
- **Full re-sync**: use the existing reporting-sync `/replay?namespace=X`
  endpoint, which reads Mongo and rebuilds PostgreSQL tables from scratch.

Full re-sync is simpler and already exists; use it by default.

### 2. Integrity verification

A mandatory post-restore pass walks every reference in every restored
entity and confirms it resolves. This catches:
- Dump corruption
- Incomplete archives
- Logic errors in the restore engine itself

On failure, the restore job enters a `needs_attention` state with the
unresolved references listed. The data is not rolled back by default
(the operator decides).

### 3. Status transitions

Restore job phases: `queued → dumping_precheck → inserting →
rewriting_refs (fresh only) → activating (fresh only) → blobs →
reporting_sync_catchup → verifying → complete`.

---

## Edge cases and open questions

### Namespace rename is not supported at the platform level

The Registry has no `rename` operation (verified in
`registry/api/namespaces.py`). The only way to "rename" a namespace is
to `target_namespace`-restore a dump into the new name and then delete
the old namespace. Restore must not attempt to rename in place.

### Mutable terminologies interact with fresh mode

If a namespace uses mutable terminologies, deleted terms are hard-deleted
(not soft-deleted). Backup has to decide whether to include the term-delete
history (it does not today; it dumps the current active state). Restore in
fresh mode re-creates the current state, losing any tombstones. This is
fine for DR but not fine for audit replay — call it out in the dump
format's `schema_version` if audit semantics ever become a goal.

### ID-algorithm sequence gaps in `prefixed` mode

If the source namespace used `prefixed` and was at sequence 1000 at
dump time, restoring into a fresh namespace with `prefixed` at sequence 0
creates a gap: the restored entities have IDs TERM-000001..TERM-001000,
and the next newly-created entity will be TERM-000001 again — collision.
Restore into `prefixed` targets must bump the counter to `max(dumped_seq)+1`
before accepting new writes. Worth a dedicated test.

### Registry composite-key collisions

In `restore` mode with a non-empty target namespace (a partial restore
resumption), composite-key uniqueness forces failure on duplicates. Good
— it means the resume logic needs to be explicit: fetch Registry entries
for the namespace, diff against the dump, insert only new ones. This is
a separate feature if needed.

### Backup of the Registry itself

Registry's own state (id_counters, schema config) is not namespace-scoped
and is not in any of the dump files above. This is fine for namespace
restore but is a gap for full-install DR. Out of scope for this design;
full-install DR needs a separate track that dumps all namespaces plus
Registry global state.

---

## Relationship to CASE-23 Phase 3

CASE-23 Phase 3 established the REST backup/restore endpoints wrapping
the toolkit. That work:

- Produced a working backup/restore pipeline for small namespaces (aa,
  seed both smoke-test clean as of 2026-04-08).
- Shipped progress reporting, job lifecycle, and authentication.
- Failed on clintrial closure walk — the failure that motivated this
  redesign.

This redesign keeps the REST endpoint surface and the `BackupJob` model
from CASE-23 Phase 3. It replaces the *implementation* inside
`backup_service.py` and `wip-toolkit`'s `run_export` / `run_import`:

- `run_export` is rewritten to do direct Mongo reads instead of service
  HTTP calls. The `wip-toolkit` CLI becomes a client of an exposed
  "dump endpoint" rather than a self-contained fan-out runner.
- `run_import` gains a mode flag and a three-pass implementation for
  fresh mode. `restore` mode becomes a fast bulk path.
- `backup_service.py` loses most of its async/sync bridging complexity
  because the new toolkit calls are async-native.

---

## Implementation phases

1. **CASE-32** — File checksum composite key. Small, standalone, unblocks
   fresh-mode file references. Land first.
2. **Dump format spec + manifest changes** — Lock the new archive layout,
   including `registry_externals.jsonl`. Update `ArchiveWriter` /
   `ArchiveReader` in the toolkit. Should be incremental from today's
   format (just additions).
3. **Direct-read backup path** — Replace `run_export`'s service-walking
   with Mongo cursor reads. Keep the existing archive output. Validate
   on aa + seed, then clintrial (the failing case).
4. **Restore mode: `restore`** — Implement the fast bulk path. Validate
   with rollback scenarios on aa + seed.
5. **Restore mode: `fresh`** — Implement the three-pass path. Validate
   with `target_namespace` (the most likely real use) on aa + seed.
6. **Cross-install DR** — Add `registry_externals.jsonl` handling and
   the cross-namespace policy knob. Test on a two-namespace scenario.
7. **Reporting-sync integration** — Wire up the catch-up step. Validate
   that restored namespaces show up correctly in reporting queries.
8. **Post-restore integrity verification** — Implement and make mandatory.
9. **CASE-23 Phase 3 STEP 9** — Deployment docs including the
   `WIP_BACKUP_DIR` sizing rule and the new restore-mode matrix.

Phases 1–3 close the clintrial backup failure. Phases 4–9 deliver the
full redesign.

---

## Open items for Peter

1. **Fresh mode as edge case?** This design treats `restore` as the
   common case and `fresh` as the edge case. Confirm. If true, phase 5
   may be deprioritized relative to 1–4.
2. **Cross-namespace policy default.** `strict` for same-install,
   `best-effort` for cross-install. Acceptable?
3. **Schema/hash version compatibility.** Refuse on mismatch, or offer
   a rehash pass? Rehash is more work but avoids operator pain during
   upgrades.
4. **`registry_externals.jsonl` scope.** One-hop walk — good enough, or
   should it be a full transitive closure?
5. **Full-install DR** (Registry global state, id_counters, all
   namespaces in one archive). In scope here or a separate track?
