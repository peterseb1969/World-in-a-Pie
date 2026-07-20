# Restore Modes: Merge and New-Namespace

**Status:** Merge shipped in a first implementation (Phases 1-2) and then
**superseded** by Peter's 2026-07-20 review, which restructured it into two
passes — a definitions-compatibility precondition, then documents (Part 2).
The shipped code diverges from this document in three named ways; Part 4
lists them and the restructure that closes them. The new-namespace (ID
re-minting) mode is deferred to Phase 3.

**Author:** BE-YAC-20260718-222350 (Phase 1-2 + 2026-07-20 revisions:
BE-YAC-20260720-010210)
**Context:** Extends `backup-restore-redesign.md` (v3 multi-namespace archives,
direct-Mongo engine). Picks up the remap half deferred by CASE-542 and filed as
CASE-548. Two new restore modes were requested by Peter (2026-07-19); the
2026-07-20 review reshaped the first and deferred the second:

1. **merge** (§2.2-2.3) — restore into an *existing, non-empty* namespace.
   Two passes: definitions compatibility (verify by default, opt-in
   extension), then documents under a `skip` / `overwrite` policy. Matching is
   by content, so the same mechanics cover an archive from this install and
   one from another — there is no separate cross-install mode.
2. **new-namespace** (§2.4) — restore under a *different* namespace name with
   **new canonical IDs minted by the Registry**. Re-minting is what the mode
   IS. **Deferred** — its non-development justifications turned out to belong
   elsewhere.

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

### 1.6 Coordination: template identity unification (CASE-709…CASE-712)

The template-identity workstreams (`template-identity-unification.md`,
distilled from FIRESIDE-23) intersect this plan in four places:

1. **Restore must recreate composite-key claims — a verified live gap.**
   `composite_key_claims` (the CASE-554 uniqueness gate, its own Registry
   collection) is not in the restore engine's `COLLECTION_MAP`: restored
   registry entries come back claim-less, and reconcile only *deletes*
   dangling claims, never rebuilds missing ones (only the one-shot
   `backfill_claims` does). Latent today; load-bearing once CASE-709 makes
   template identity depend on the claim gate. Claim recreation at restore
   applies to **all** entity types and belongs in CASE-709's restore
   scope; both new modes inherit it (merge clash detection assumes
   entries and claims are coherent; new-namespace provisioning creates
   claims via the Registry, which handles it by construction).
2. **Template composite keys are constructed, not read** (§2.1) — pre-709
   archives carry empty template composite keys.
3. **Merge schema-clash policy is the operator's, not the platform's**
   (§2.2): CASE-709's create-as-upsert adds `upsert` as a *choice*;
   the merge default stays `fail` per Peter's ruling.
4. **CASE-710 per-version reporting tables change restore verification.**
   The structural gate and count parity are per-template today; per-version
   tables make them per-`(template, version)`, and merge-overwrite plus
   `latest_only` sync means rows *move* between version tables on
   version-crossing updates. Restore verification (this engine) must be a
   named consumer in CASE-710's design pass before that workstream lands.

---

## Part 2 — Merge, in two passes

**Revised 2026-07-20 (Peter).** The first implementation (Phases 1-2, shipped
and then superseded — see Part 4) treated schema entities and documents the
same way: one pass, one per-item "clash policy" for everything. That was
wrong, and produced a demonstrable hole (§2.5). Terminologies and templates
are not things you resolve item-by-item while writing documents; they are the
**precondition** for writing documents at all. So merge is two passes:

1. **Definitions (§2.2)** — establish that the two sides' terminologies,
   terms and templates are compatible, and emit the ID mapping table as a
   by-product. Verify-only by default; refuses on incompatibility.
2. **Documents (§2.3)** — merge documents against the now-guaranteed shared
   schema, using the mapping, with a per-request resolution policy.

Everything runs on the **direct-Mongo engine**. The toolkit HTTP engines are
not the substrate: they are single-namespace, per-item, and duplicate logic
the platform now owns. (Proposal: mark `import_/fresh.py` +
`import_/restore.py` as legacy pending retirement; `remap.py` graduates into
the engine's dependency set.)

### 2.1 Shared machinery: matching, and the mapping table

**Matching and resolution are different concerns and must stay separate**
(Peter, 2026-07-20). Matching answers *"is this the same thing?"*; resolution
answers *"what do I do about it?"*. The first implementation collapsed them —
it made "the archive is from another install" a **mode** with a built-in
resolution (always skip) rather than a **matching strategy** — which is how
the schema-divergence hole in §2.5 got in.

**Matching is by content, never by UUID.** Two installs that independently
created `GENDER` hold it under two different canonical IDs; the same install
restoring its own archive holds it under one. Content matching covers both
without a mode flag: identity mapping falls out in the same-install case,
a non-trivial mapping in the other. There is therefore **no `cross_install`
parameter** — the shipped one is removed. The mechanics are generic; merging
two entirely non-overlapping namespaces is the degenerate case where the
mapping is empty and everything inserts.

**The mapping table is pass 1's output**, not a separate step: establishing
that the target's `GENDER` is the same terminology as the archive's IS the
act of learning `A-uuid → B-uuid`. It covers terminologies, terms and
templates, and pass 2 consumes it via `IDRemapper`
(`WIP-Toolkit/src/wip_toolkit/import_/remap.py`, engine-agnostic).

**What must be rewritten when an ID maps.** Skipping the archive's copy is
only half the job — the rows that referenced it still carry the source's ID,
and inserting those unrewritten imports dangling references, which
Vision.md's "References Must Resolve" forbids outright. Three distinct
identifiers behave differently and must not be conflated:

| Identifier | Behaviour |
|---|---|
| Canonical entity ID (`document_id`, `template_id`, `entry_id`, …) | Opaque and globally unique under UUID7; rewritten only where the mapping says so |
| Registry composite key **and its hash** | Embeds `ns` and parent IDs — rewritten and **rehashed** whenever either changes |
| `identity_hash` | Value-based and namespace-free — unaffected by either |

Composite-key hashing therefore has to be available off the Registry's write
path; it lives in `wip_auth.composite_key` (moved there 2026-07-20, Registry's
`HashService` delegates) so it is never re-derived at a second call site.

**identity_hash caveat (from CASE-548 analysis, verified):** the hash is
namespace-independent and value-based — stable under merge — *except* when a
template's `identity_fields` include reference fields holding canonical IDs.
If such an ID maps, the identity values change and the hash must be
recomputed for that template's documents after the rewrite.

### 2.2 Pass 1 — definitions compatibility

**Contract:** before a single document moves, the archive's terminologies,
terms and templates must be *compatible* with the target's. Compatibility is
judged on **content, not UUID**.

**Compatible means:**

- **Template** — the target has one with the same value and identical
  content, OR has none with that value (it can be added without conflict).
- **Terminology** — the target has none with that value (addable whole), OR
  has one whose terms can be reconciled per the term rules below.
- **Terms** — extra terms on the archive side can be added; extra terms on
  the *target* side are harmless and compatible with no action (they simply
  are not referenced by the incoming documents).

**Incompatible is the leftover:** same value, different content. A template
with the same value and a different schema is the case that matters — merging
documents under it would validate one side's data against the other's schema.

**Default is verify-only.** The pass checks and refuses; it does not touch the
target's schema. This is deliberate (Peter, 2026-07-20): changing a live
namespace's definitions is an active decision, never a side effect of a
restore.

**Two opt-in reconciliation strategies**, each an explicit request:

- `add_missing_templates` — templates the target lacks are inserted.
- `extend_terminologies` — terms the target lacks are added to an existing
  terminology.

**No `upsert` for templates** (Peter, 2026-07-20). Importing the archive's
template as a new version of the target's is a schema mutation that fits
nowhere in the definition of compatible. The shipped `on_schema_clash=upsert`
is removed, and `on_schema_clash` disappears with it — schema entities have a
precondition, not a per-item resolution.

**Term differences where the value matches but the content does not** (label,
aliases): **the target wins**, and this MUST be reported — in the completed
merge's output and in the dry-run report. A silent overwrite of vocabulary
metadata is exactly the kind of thing an operator needs to see.

### 2.3 Pass 2 — documents

With a guaranteed-compatible schema and the mapping in hand, documents merge
per identity. Matching is by `(template_id, identity_hash)` after remapping,
falling back to `document_id` alone for identity-less (append-only)
templates, which have no logical identity to compare.

**Resolution policy (`on_clash`), documents only:**

- `skip` (default) — target wins; the archive's version is not imported.
- `overwrite` — the archive's **latest** version is appended as one new
  version on top of the target's head, adopting the target's `document_id`.
  Target history is preserved; archive history is **not** spliced in —
  interleaving two independent version chains has no defined order and would
  corrupt the `(document_id, version)` contract. On a `versioned: false`
  template (PoNIF #8) it replaces the single version in place, matching that
  template's own lifecycle.
- `newer` (proposed, not built) — see below.

**`newer`: what it can and cannot key on.** UUID7 is time-ordered and its
embedded timestamp is true UTC epoch millis — verified in
`registry/models/id_algorithm.py`, which deliberately uses
`datetime.now(UTC).timestamp()` and carries a comment about a naive
`utcnow()` silently skewing the ordering bits by the host's UTC offset. So
lexicographic UUID7 order IS chronological across hosts, with no timezone
hazard. Three reasons it is still the wrong key for `newer`:

1. **Wrong axis.** A UUID7 records when an entity was *created*, never when it
   last changed. A document created in January and edited yesterday loses to
   one created in June and untouched.
2. **Not universal.** `uuid7` is the default, but `algorithm` is configurable
   per namespace *and* per entity type (`uuid4`, `prefixed`, `nanoid`,
   `pattern`, `any`). A prefixed namespace's IDs carry a counter, not a clock.
3. **UTC fixes timezones, not skew.** Across installs the comparison is only
   as good as the two machines' clock discipline.

Recommendation: `newer` compares the head versions' `updated_at` (stored on
every version row, and the axis that actually means "fresher"), with the
UUID7 as a deterministic tiebreaker on equal timestamps. The clock-skew
caveat gets documented rather than hidden.

**Preconditions (both passes):**

- Namespace must exist (creating it would be a plain restore).
- Namespace config from the manifest is **compared, not applied** — drift
  (id_config, isolation_mode) is reported; `deletion_mode` untouched.
- Reporting precondition inverts: the schema is *expected* to exist;
  bookkeeping-usability is still checked; `drop_stale_reporting` is
  meaningless here and rejected.
- Structural gate and count parity run unchanged.

**Files** match by checksum: an existing checksum means the same bytes, so
metadata and blob are skipped and the target's `file_id` is reused.

**dry_run is the plan.** Both passes are computed before anything is written,
so the dry run reports exactly what a real run would do — per entity type:
insert / unchanged / clash / incompatible counts, the mapping size, every
term difference the target won, and the would-overwrite list (capped). It
fails on everything a real run would refuse, which is the point of asking
first.

### 2.4 Mode: `new-namespace` (deferred — see Part 4)

**Contract:** restore into a namespace that does not exist (or is empty),
under a *different* name than the source, with **new canonical IDs minted by
the Registry**. Re-minting is the mode's definition, not an implementation
choice within it: the mode exists precisely for the case where the source
entities remain live on the same install, and one canonical ID cannot denote
two things.

**Status (2026-07-20):** deferred. Both of its non-development justifications
dissolved on inspection:

- **Namespace rename.** There is no rename operation in the Registry
  (`api/namespaces.py` has upsert, archive, restore, export, delete — no
  rename), so restore-into-a-new-name was the only path to one. But that is
  acrobatics: it achieves a metadata change by severing identity. Peter's
  ruling: fix it with a real rename if it ever matters — and it likely will
  not, because **namespace names are cosmetic**. Filed as its own workstream,
  unprioritized. The non-obvious cost for whoever picks it up: the namespace
  name is baked into Registry composite keys, which are hashed into
  `primary_composite_key_hash`, so a rename must recompute every hash and
  rebuild every composite-key claim, update embedded synonym scopes, rename
  the postgres schema and its bookkeeping rows, and fix
  `allowed_external_refs` lists in *other* namespaces that name this one by
  string. Cross-namespace references survive untouched (they store canonical
  IDs, not names). It wants the namespace quiesced while it runs.
- **Consolidating installs.** Handled by §2.2-2.3 without minting anything.

What remains is parallel development copies on one install — real, and the
case where "which namespace is authoritative?" has no good answer.

**Lineage synonyms: removed from the design (Peter, 2026-07-20).** The
earlier draft proposed registering the source canonical ID as a qualified
synonym (`{"restored_from": "<old-id>"}`) on each re-minted entity. Do not
reintroduce this; it looks like an obvious nicety and is not:

1. **It asserts something false.** A Registry synonym means "this identifier
   denotes the same entity" (Vision.md, §"Term Aliases vs Registry
   Synonyms"). A restored copy in a parallel namespace is a *fork* — it
   diverges the moment either side takes a write. Encoding ancestry as
   identity puts a claim that is wrong on day two into the one subsystem
   whose job is being the authority on identity. WIP already refuses this
   blur one layer up: `migrate_documents` accepts an identity-preserving move
   and **rejects an identity-changing one as a fork** (PoNIF #2).
2. **It is redundant.** The document identity hash is namespace-independent
   and purely value-based, so a document and its re-minted copy carry the SAME
   identity hash and join on `(template value, identity_hash)` for free.
   Terminologies, terms and templates join on their `value`.
3. **Qualification did not buy what it was supposed to.**
   `RegistryEntry.rebuild_search_values` flattens every string value of every
   synonym's composite key into `search_values`, and `lookup_by_id` falls back
   to `{"search_values": <id>}` (`api/entries.py:909-916`). So a qualified
   lineage synonym still puts the old ID in the searchable values: once the
   source entity is deleted or deactivated, an unscoped lookup by the old ID
   starts returning the *copy*, and with two restored copies `find_one` picks
   arbitrarily.

**What replaces it.** Provenance belongs to the *namespace*, not to each of
its entities: one line appended to the restored namespace's `description`
("restored 2026-07-20 from an archive of `kb` taken <T> on host <X>"). There
is no `metadata` field on the `Namespace` model, and `description` is the
visible one. It must be written AFTER the namespace upsert, which sets
`description` from the manifest and would otherwise clobber it. A later backup
captures that description into its own manifest, so provenance travels forward
into future archives — desirable.

For the narrow case where nothing is derivable — identity-less (append-only)
documents have only a surrogate `document_id`, and templates whose identity
fields hold canonical IDs change hash under re-minting — the correlation, if
ever needed, is the mode's own mapping table, persisted ONCE as a job
artifact. One write, no participation in resolution, and a snapshot that
cannot drift. Unbuilt; available if a driving case appears.

### 2.5 Correction: the hole this restructure fixes

Recorded so the reasoning is not lost. The shipped Phase 2 claimed, in this
document and in `docs/deployment/backup-restore.md`:

> **Schema divergence is already handled.** If A's `PATIENT` v2 differs from
> B's, skip-if-exists would silently validate A's documents against B's
> schema. That is what `on_schema_clash=fail` (the shipped default) catches —
> the mode inherits it unchanged.

**That was false, and was verified false by probe**, not by reading. Target
holding `PATIENT` (`age: integer`), archive holding `PATIENT` under a
different ID with `age: string` plus an added `ssn`, `on_schema_clash` at its
`fail` default: the merge completed, dropped the archive's template silently,
and left any incoming documents sitting against an incompatible schema.

The cause is the collapse §2.1 describes: `on_schema_clash` inspected only
same-ID *clashes*, while a different-ID collision branched off earlier as a
*match* and never reached policy at all. Detection strategy had been fused to
resolution. The two-pass structure removes the hole by construction — schema
compatibility is a precondition that runs before any document write, so there
is no path where divergent definitions are silently skipped.


## Part 3 — Explicitly deferred or rejected

- **Merging an archive of namespace A into a differently-named namespace B**
  — raised 2026-07-20 as a consequence of making the mechanics generic
  ("you could merge two different, non-overlapping namespaces into one").
  Reachable in principle with IDs preserved, since canonical UUID7 IDs carry
  no namespace and the composite-key rewrite-and-rehash machinery exists.
  NOT designed, and two edges are unverified: what happens to
  `allowed_external_refs` and to cross-namespace references when entities
  change namespace, and what a `prefixed` namespace does with imported IDs
  minted against another namespace's counter (`IdCounter.counter_key` is
  `{namespace}:{entity_type}:{prefix}`). Treat as a fourth operation needing
  its own decision, not as something merge already does.
- **Namespace rename** — a real operation, not restore acrobatics. Filed,
  unprioritized: namespace names are cosmetic. Cost sketch in §2.4.
- **Contents-merge beyond terms** — §2.2 settles terms (extra either side is
  compatible; a differing label/alias means the target wins, reported).
  Anything richer needs its own design pass and a driving case.
- **Old-ID lineage synonyms** — REJECTED, not deferred. See §2.4; they assert
  a false identity claim, are redundant against derivable joins, and the
  qualification meant to make them safe does not.
- **Version-history splicing on overwrite** — rejected, see §2.3.
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

**Phases 1-2 — merge, first implementation: SHIPPED then SUPERSEDED**
(both 2026-07-20). Phase 1 delivered same-install merge
(`services/merge_plan.py`, `DirectRestoreEngine.run_merge`, `on_clash` for
documents, `on_schema_clash=fail|skip|upsert` for schema entities, inverted
preconditions, dry-run-as-plan, REST/MCP/`@wip/client` surface). Phase 2
added cross-install matching, reference rewriting, and the
`wip_auth.composite_key` consolidation.

Peter's review the same evening restructured the design (Part 2): matching
and resolution must be separate concerns, schema compatibility is a
precondition rather than a per-item policy, and the `cross_install` flag
should not exist. The shipped code therefore **diverges from this document**
in three specific ways, all pending the Phase 2b restructure below:

| Shipped | Design (Part 2) |
|---|---|
| `on_schema_clash=fail\|skip\|upsert`, applied per item during the write | No such parameter; a definitions precondition with two opt-in extend strategies |
| `cross_install` flag selecting a matching mode | No flag; matching is always by content |
| Schema divergence under a different ID skipped silently (§2.5) | Impossible by construction — the precondition runs first |

Retained from that work and still correct: the two-keyed planner, the
document policies, the claim rebuild, the `IDRemapper` extensions, and the
composite-key hashing move.

**Phase 2b — restructure to the two-pass design. NEXT.**

1. **Pass 1: definitions compatibility** (§2.2) — verify by content, emit the
   mapping table, refuse on incompatibility. Two opt-in strategies:
   `add_missing_templates`, `extend_terminologies`. Report every term
   difference the target won, in the job output and the dry run.
2. **Remove** `on_schema_clash` (including `upsert`) and `cross_install`.
3. **Pass 2: documents** (§2.3) — `skip` | `overwrite` against the mapping.
4. **Optional this pass or next:** `newer`, keyed on the head version's
   `updated_at` with a UUID7 tiebreaker (§2.3).

Still open regardless: the **clintrial-scale test**, and the fact that merge
has real-database tests but has never been run against two genuine installs.

**Phase 3 — new-namespace mode (§2.4), deferred.** Was Phase 2; demoted when
both its non-development justifications dissolved (rename should be a rename;
consolidation is merge). Remaining justification is parallel dev copies on one
install. Re-minting canonical IDs IS the mode — not an implementation choice
within it.

Prerequisites, both deferred out of Phase 0 and still open — verified
2026-07-20: `ArchiveReader.read_entities` still does
`self._zf.read(...).decode()` (whole JSONL in memory, `archive.py:273`), and
`/entries/provision|reserve|activate` still have **zero** callers outside the
Registry's own source and tests. The second is the sharper risk: this mode
would be the first production consumer of a three-endpoint lifecycle nothing
has ever driven, which is the never-tested-default trap (§1.4).

**Unphased, unprioritized:** namespace rename, and merge-into-a-different-
namespace-name (Part 3).

---

## Decisions (all four original questions resolved)

1. **Schema clash tier in merge.** Resolved 2026-07-19 as a per-action
   `on_schema_clash` choice, then **superseded 2026-07-20**: schema entities
   have a precondition, not a per-item resolution, and `upsert` for templates
   is removed outright. See §2.2.
2. **Multi-archive namespace mapping.** Resolved (Peter, 2026-07-20): every
   archive namespace must be explicitly mapped. No implicit suffixes, no
   partial-mapping shorthand — an unmapped namespace is an error.
3. **Lineage synonyms.** Resolved (Peter, 2026-07-20): **removed from the
   design.** A restore should leave a clean new state; restoring under a
   different namespace is a deliberate decision to keep the information and
   cut the historical context. Linking the copies is overly complex, only
   meaningful when the source namespace happens to still be intact on the
   same install, and invites the question the link cannot answer — which copy
   is authoritative, once they drift by upsert? Full reasoning in §2.4.
4. **Overwrite granularity.** Resolved (Peter, 2026-07-20): latest-only — the
   intended use is "bring this namespace up to the archive's state", not an
   audit replay.

**Open for the next pass:** whether `newer` (§2.3) ships with the Phase 2b
restructure or after it.
