# Restore Modes: Merge and New-Namespace

**Status:** Draft for review — analysis + plan, no implementation yet
**Author:** BE-YAC-20260718-222350
**Context:** Extends `backup-restore-redesign.md` (v3 multi-namespace archives,
direct-Mongo engine). Picks up the remap half deferred by CASE-542 and filed as
CASE-548. Two new restore modes requested by Peter (2026-07-19):

1. **merge** — restore into an *existing, non-empty* namespace, with an
   overwrite / skip policy on identity clashes.
2. **new-namespace** — restore into a *different* namespace, minting new
   canonical IDs and auto-synonyms.

---

## Part 1 — As-is analysis

### 1.1 Two stacks, one live

Backup/restore is **not** primarily WIP-Toolkit anymore. Two stacks exist:

| Stack | Location | State |
|---|---|---|
| **Direct-Mongo engine** | `components/document-store/src/document_store/services/backup_engine.py` (`DirectBackupEngine` / `DirectRestoreEngine`), orchestrated by `backup_service.py`, exposed via `api/backup.py` + MCP tools | **Live.** v3 multi-namespace archives, restore-to-self, empty-target precondition, reporting verification phases (CASE-689). |
| **Toolkit HTTP engines** | `WIP-Toolkit/src/wip_toolkit/import_/{restore,fresh}.py`, `export/` | **Legacy.** Drive service HTTP APIs per item. The loopback REST runners were retired (CASE-544); the CLI path remains but is effectively single-namespace: `ArchiveReader.read_entities()` without an explicit namespace **raises** on a multi-namespace v3 archive, and neither engine passes one. `fresh.py` is unreachable via REST (`mode=fresh` → 400 "not yet implemented"). |

The toolkit still owns the **archive format** (`wip_toolkit/archive.py`:
`ArchiveWriter` / `ArchiveReader` / `Manifest`) — the direct engine imports it.
It also owns `import_/remap.py` (`IDRemapper`) — a complete, engine-agnostic
old→new reference rewriter (templates: extends / terminology_ref /
template_ref / target_templates / target_terminologies incl. array variants;
documents: template_id / term_references / references[].resolved /
file_references / ID-shaped data values). This is reusable as-is for the
new-namespace mode.

### 1.2 The live restore pipeline (what actually happens)

`DirectRestoreEngine.run_restore`:

1. Reject non-v3 / malformed archives (loud, pre-write).
2. **All** targets must be empty across all seven collections
   (`_check_namespace_empty`, `limit=1` count per collection — cheap).
3. Reporting precondition: namespace schema absent/empty, bookkeeping tables
   usable; `drop_stale_reporting` is the explicit opt-out (fossil-schema
   incident class, `docs/deployment/backup-restore.md`).
4. Per namespace: upsert namespace from manifest config (Registry HTTP PUT,
   with the deletion-mode guard honored), then per entity type in dependency
   order: read JSONL → strip `_id` → `insert_many` batches (`ordered=False`).
5. Structural gate after templates (reporting tables must materialize, halts
   before documents); count parity after documents (bounded 90 s wait,
   completes **with warning** on mismatch).
6. Blobs: flat, restored once for the whole archive.
7. `registry_entries` are bulk-inserted **verbatim** — no Registry API
   involvement beyond the namespace upsert; the unique index is the
   correctness check.

Identity contract: **preserve everything** (IDs, hashes, versions, synonyms
embedded in registry entries). Correct only because the target is empty and
the namespace name is unchanged.

### 1.3 Defects and dead surface found during this analysis

1. **`dry_run` is accepted and ignored — a real restore runs.**
   `api/backup.py` accepts `dry_run` (plus `register_synonyms`,
   `continue_on_error`) as form fields and stores them in `options`, but
   `make_direct_restore_runner` never passes them to the engine. A caller
   asking for a dry run gets a live restore. (Toolkit-era params that
   survived the engine swap.) Fix independently of the new modes.
2. **The engine-level `target_namespace` redirect is leaky** (CASE-548,
   confirmed): it empty-checks and upserts the *target* but inserts entities
   still carrying `namespace: <source>`. In practice unreachable via REST —
   `_authorize_archive_restore` overrides the caller's target with the
   manifest's own prefix — but the engine should reject `tgt != src` until
   the new-namespace mode exists.
3. **REST restore `batch_size` defaults to 50** (engine default is 500,
   REST cap is 500). Default-path restores do 10× more round trips than
   intended.
4. **`ArchiveReader.read_entities` / `entity_count` load the whole JSONL
   into memory** (`zf.read().decode()` then `splitlines`). For a
   clintrial-scale namespace (228k documents) that is hundreds of MB held
   as one Python string — twice, since `entity_count` re-reads it. The
   writer side is O(1) (temp files); the reader is the asymmetric hotspot.
5. **Blob handling is fully-buffered and sequential on restore**
   (`read_blob` returns `bytes`; upload one-by-one) while backup streams
   per chunk but downloads blobs sequentially too.
6. **`include_inactive` naming**: the backup query excludes only
   `status: "deleted"` by default — *inactive* entities are always included.
   The flag actually means "include hard-tombstoned rows". Cosmetic, but
   worth renaming or documenting when the surface is touched anyway.

### 1.4 The identity machinery available to the new modes

- **Registry reservation lifecycle — exists, dormant.**
  `POST /entries/provision` (Registry *generates* N IDs per the namespace's
  `id_config`, inserts `RegistryEntry` rows with `status: "reserved"`),
  `POST /entries/reserve` (client-provided IDs), `POST /entries/activate`
  (bulk flip reserved→active). Reserved entries are **invisible to
  resolution** — every lookup filters `status: "active"`. All three run the
  CASE-554 two-phase claim protocol (pending claim → insert → confirm;
  conflict releases claims and fails loudly). **No production caller uses
  these endpoints today** — they need an exercising test pass before the
  new-namespace mode leans on them, per the never-tested-default rule.
- **Two-phase composite-key claims (CASE-554, implemented).** Orphans are
  cheap to detect (indexed state+age query); a crashed restore that left
  pending claims and reserved entries is reconcilable without full scans.
- **Bulk lookup/synonym surface:** `POST /entries/lookup/by-id`,
  `POST /entries/lookup/by-key` (composite-key bulk lookup — the merge
  mode's matching primitive), bulk `add_synonyms`. The register path mints
  identity-value auto-synonyms automatically (skipped for edge types,
  CASE-430).
- **Draft status: templates only.** `DocumentStatus` is
  active/inactive/archived — **documents have no draft state**. The old
  fresh-mode design ("insert everything as draft, activate at the end")
  is unimplementable for documents through service APIs. This turns out
  not to matter: the direct-Mongo engine bypasses validation entirely, and
  the *Registry* reservation lifecycle provides the "invisible until
  complete" semantics at the identity layer — which is the layer that
  actually controls resolution. **Recommendation: do not add a document
  draft status.** Post-restore integrity verification (already designed,
  redesign doc §Post-restore) covers correctness; reserved registry
  entries cover visibility.

### 1.5 Performance profile of the current implementation

Measured shape (code-read, not benchmarked — flagged as such):

- **Backup:** one indexed cursor scan per (namespace × 7 collections) plus a
  pre-count pass (7 `count_documents`), streamed to temp files, single-pass
  zip. O(1) memory. Blob downloads sequential.
- **Restore:** per entity type: full-file JSONL string in RAM (see 1.3.4),
  then sequential `insert_many` batches of ≤500. No concurrency across
  collections (correct — dependency order matters) and none within a batch
  stream (an available win: read/insert pipelining). Reporting phases add
  two bounded polls (30 s / 90 s) and one batch sync per namespace; the 90 s
  count-parity bound will produce warning noise on large namespaces where
  sync legitimately takes longer — should scale with document count.
- **Registry `provision`:** per-ID sequential generation and per-entry
  sequential claim writes inside one HTTP call. Fine at hundreds; at 228k
  entities it wants (a) counter allocation in blocks
  (`IdCounter.next_val(count=N)`-style `$inc` by N) and (b) `insert_many`
  for claims. UUID7 namespaces don't touch the counter at all.

---

## Part 2 — The two new modes

Both modes extend the **direct-Mongo engine**. The toolkit HTTP engines are
not the substrate: they are single-namespace, per-item, and duplicate logic
the platform now owns. (Proposal: mark `import_/fresh.py` + `import_/restore.py`
as legacy pending retirement once the new modes land; `remap.py` graduates
into the engine's dependency set.)

### 2.1 Shared machinery: the RemapPlan

Both modes need the same first step — decide, per archive entity, what it
maps to in the target. Introduce one concept:

```
RemapPlan
  per entity type:
    to_insert:    archive entities with no counterpart in target
    clashes:      archive entity ↔ existing target entity (merge only)
    id_map:       old_id → new_id            (new-namespace only; identity in merge)
    hash_recompute: doc ids whose identity_hash must be recomputed
```

Built by **bulk, indexed Mongo reads** (batched `$in`, 1000 keys per query),
never per-item HTTP. `IDRemapper` consumes `id_map` unchanged.

**identity_hash caveat (from CASE-548 analysis, verified):** the hash is
namespace-independent and value-based — stable under both modes — *except*
when a template's `identity_fields` include reference fields holding
canonical IDs. Re-minting those IDs changes the identity values and
therefore the hash. The plan must detect such templates (from the archived
template definitions) and recompute `identity_hash` for their documents
after reference rewrite. Same-install merge preserves IDs, so this applies
to the new-namespace mode only.

### 2.2 Mode: `merge`

**Contract:** target namespace exists and may hold data. Same install, same
namespace name, IDs preserved. The archive is treated as a *delta source*:
entities the target lacks are inserted; entities the target already has are
resolved by a per-request clash policy.

**Scope guard (v1):** merge requires that where archive and target both hold
an entity, they agree on identity — i.e. same `entry_id` ↔ same composite
key. An `entry_id` collision with a *different* composite key (or vice
versa) is a hard per-item error, never silently resolved: that is
cross-install data wearing same-install clothes, and belongs to the
composed cross-install mode (deferred, Part 3).

**Clash detection (performance-first):**

- Registry entries: batched `$in` on
  `(namespace, entity_type, primary_composite_key_hash)` — unique-indexed.
- Documents: batched `$in` on `(namespace, identity_hash)` per template
  (`ns_identity_status_idx`), scoped to `template_id` per the
  identity-scoping rule.
- Append-only templates (empty `identity_fields`): no logical identity —
  dedup by `document_id` only (same rule reporting-sync uses). Same
  `document_id` in target → same physical row → skip; else insert.
- Terminologies / terms / templates: by composite key via the registry
  entries above.
- Files: by checksum composite key (CASE-32); existing checksum → skip
  blob + metadata, reuse the existing `file_id`.

**Clash policy — applies to documents:**

- `skip` (default): target wins; archive versions of that identity are not
  imported. Per-identity counted in the job result.
- `overwrite`: archive wins *going forward*: the archive's **latest**
  version of that identity is appended as one new version on top of the
  target's head (`version = target_head + 1`, adopting the target's
  `document_id`). Target history is preserved; archive history is **not
  spliced in** — interleaving two independent version chains has no
  defined order and would corrupt the `(document_id, version)` contract.
  For `versioned: false` templates (PoNIF #8) overwrite replaces the single
  version in place, matching the template's own lifecycle.

**Schema entities (terminologies, terms, templates) do not take the
overwrite/skip policy in v1.** They follow the idempotent-bootstrap
philosophy (`on_conflict=validate`): identical → skip (counted
`unchanged`); different → per-item error listing the diff. Merging data
into a namespace whose *schema* diverged from the archive is a schema
migration, not a restore — loud is correct. (Open question #1 for Peter:
is a `--force-schema` overwrite tier wanted later? Recommendation: no,
until a concrete case demands it.)

**Preconditions replace, not drop, the safety gates:**

- Namespace must exist (creating it would be plain restore).
- Namespace config from the manifest is **compared, not applied** —
  config drift (id_config, isolation_mode) is reported; `deletion_mode`
  untouched.
- Reporting precondition inverts: the schema is *expected* to exist;
  bookkeeping-usability is still checked; `drop_stale_reporting` is
  meaningless here and rejected.
- Structural gate and count parity run unchanged (count parity compares
  post-merge Mongo state vs postgres, so it stays valid).

**Write path:** non-clashing entities go through the existing
`insert_many` bulk path untouched. Overwrites go through a `bulk_write`
of computed inserts (new version rows) — still batched, still direct.
Post-merge, one reporting batch sync (upserts by
`(document_id, version)`) as today.

**No reservation, no draft, no synonym minting** — identities are
preserved; nothing new to reserve or alias.

**dry_run becomes real and load-bearing here:** the RemapPlan *is* the dry
run. `dry_run=true` builds and returns the plan summary (inserts / clashes
per type, schema diffs, would-overwrite list capped) without writing. This
retroactively gives the dead REST param a correct implementation — but the
merge-mode job result carries it, and plain-restore `dry_run` should be
implemented (cheap: precondition checks + manifest counts) or rejected in
the same commit.

### 2.3 Mode: `new-namespace`

**Contract:** restore into a namespace that does not exist (or is empty),
under a *different* name than the source, with **new canonical IDs minted by
the Registry** and **synonyms linking old identities**. Works same-install
(source namespace may still be live) and is the substrate for cross-install
DR later.

**Multi-namespace archives:** the request carries an explicit mapping
`{source_prefix: target_prefix}`; every archive namespace must be mapped
(or the subset to restore listed). No implicit suffix magic. (Open
question #2: single-namespace shorthand `target_namespace=<name>` stays.)

**Pipeline (per namespace, direct-Mongo throughout):**

1. **Preconditions** — as plain restore (empty target, reporting clean),
   plus: target namespace is created with the *archive's* `id_config`
   unless the caller overrides it (the "restore the algo too" ruling
   applies only to ID-preserving restore; here new IDs are minted, so the
   caller may legitimately choose the target's algorithm — default:
   carry the source config).
2. **Mint IDs (the reservation step).** Per entity type, call
   `POST /entries/provision` in batches (~1000): Registry generates IDs
   per target config, inserts **reserved** entries with the (rewritten)
   composite keys, claims two-phase. Build `id_map` old→new. Reserved
   entries do not resolve — a crashed job leaves an invisible,
   reconcilable namespace, and prefixed-ID counters are correct by
   construction (no sequence-gap collision class — the counter *is* the
   minting authority, closing the redesign doc's "prefixed sequence gap"
   edge case for this mode).
   Composite keys are rewritten before provisioning: embedded `ns` fields
   → target; embedded canonical IDs (e.g. a term's `terminology_id`) →
   `id_map` of the already-provisioned parent type. Dependency order:
   terminologies → terms → templates → files → documents.
3. **Rewrite + insert.** Stream each entity type from the archive,
   rewrite `namespace`, apply `IDRemapper` (reused verbatim), recompute
   `identity_hash` for documents of templates with reference-valued
   identity fields (§2.1), `insert_many` batches directly. No service
   validation runs — same trust model as plain restore (the archive was
   valid at export; post-restore verification is the check).
4. **Blobs.** New `file_id`s (same-install: the old IDs still exist in
   MinIO and the files collection — collision otherwise). Blob bytes
   copied under the new storage keys, streamed.
5. **Activate.** Bulk `POST /entries/activate` (batched). The namespace
   becomes resolvable essentially atomically at the identity layer.
6. **Synonyms.** Two kinds:
   - *Identity-value auto-synonyms:* minted by the Registry during
     provision/registration exactly as normal creation would
     (edge-type skip rule honored) — "auto-synonyms" for free.
   - *Old-ID lineage synonyms:* bulk `add_synonyms` mapping the source
     canonical ID to the new entity — **qualified, not bare**
     (e.g. `{"restored_from": "<old-id>"}`): on the same install with the
     source namespace still live, a bare old-ID synonym would make one
     identifier resolve to two entities across namespaces — exactly the
     ambiguity the Registry exists to prevent. Qualified synonyms keep
     the lineage queryable without poisoning resolution.
     (Open question #3: whether lineage synonyms should be opt-out —
     they add one registry write per entity, the single biggest write
     multiplier in this mode.)
7. **Reporting phases** — unchanged from plain restore.

**Why not draft/activate through service APIs (the old fresh-mode design):**
documents have no draft state to use; per-item HTTP creates are the exact
performance profile the redesign killed (validation + round-trip dominated);
and the reservation lifecycle gives the same not-visible-until-done property
at the layer that controls resolution. The three-pass HTTP engine survives
only as `remap.py`'s rewrite tables.

**Performance budget (qualitative, clintrial-scale 228k docs + ~229k
registry entries):** dominated by two Registry-mediated steps — provisioning
(~229 batched HTTP calls) and synonym registration (similar). Both are
single-round-trip-per-batch after the §1.5 provision optimizations
(block counter allocation, claims via `insert_many`), which should land
first. Everything else is the existing insert_many path plus one streamed
rewrite pass. Expected same order of magnitude as plain restore; the mode
must *never* fall back to per-entity HTTP.

---

## Part 3 — Explicitly deferred

- **Cross-install merge** (merge where IDs differ): compose RemapPlan
  matching-by-composite-key with the new-namespace minting engine. The two
  modes above are deliberately built on the shared RemapPlan so this
  becomes configuration, not a third engine.
- **Version-history splicing on overwrite** — rejected, see §2.2.
- **Schema-entity overwrite policy** — open question #1.
- **Full-install DR** (Registry global state) — unchanged from redesign doc.

---

## Part 4 — Implementation phasing

**Phase 0 — hygiene (independent, ship first):**
1. Fix the dead REST params: implement `dry_run` for plain restore or 400 it;
   drop/ignore-with-410-comment `register_synonyms` + `continue_on_error`
   until a mode consumes them. (`dry_run` silently running a real restore is
   the sharpest edge found in this analysis.)
2. Engine guard: reject `target_namespace != source` in
   `DirectRestoreEngine` (CASE-548 interim fix).
3. REST restore `batch_size` default 50 → 500.
4. Stream `ArchiveReader.read_entities` (ZipFile.open line iterator) and
   restore-side blobs; bound blob concurrency both directions.
5. Registry `provision` bulk optimizations (block counter `$inc`,
   claims `insert_many`) + an exercising test for
   provision/reserve/activate (currently zero callers).

**Phase 1 — merge mode:** RemapPlan (same-ID matching only), clash policies
skip/overwrite for documents, validate-or-error for schema entities,
precondition inversion, dry-run-as-plan, REST/MCP/`@wip/client` surface
(`mode=merge`, `on_clash=skip|overwrite`), reporting-parity intact.
Scale test against a clintrial-sized namespace.

**Phase 2 — new-namespace mode:** provision/activate integration, remapper
reuse + identity_hash recompute, qualified lineage synonyms, multi-namespace
mapping UX, blob re-keying. `mode=fresh` 400 message retires in favor of the
real mode name.

**Phase 3 — cross-install merge** (needs its own design pass on conflict
semantics; not before a concrete driving case).

---

## Open questions for Peter

1. **Schema clash tier in merge:** validate-or-error only (recommended), or
   an explicit template-overwrite (new version) escape hatch?
2. **New-namespace multi-archive UX:** explicit `{src: tgt}` map acceptable,
   or is a single-namespace-only v1 enough?
3. **Lineage synonyms** (`restored_from` old-ID → new entity): default-on,
   default-off, or omitted in v1? They are the main extra write cost and
   the main federation/audit payoff.
4. **Overwrite granularity:** the proposed doc-level overwrite imports the
   archive's *latest* version only. Confirm that matches the intended use
   ("bring this namespace up to the archive's state") vs. an audit-driven
   need for archived intermediate versions.
