# Restore Modes: Merge, Cross-Install Merge, and New-Namespace

**Status:** Phases 0, 1 (same-install merge) and 2 (cross-install merge)
shipped 2026-07-20. The new-namespace (ID re-minting) mode was demoted to
Phase 3 the same day, after both of its non-development justifications
dissolved. See Part 4 for the phasing and Part 3 for what is deferred versus
rejected outright.
**Author:** BE-YAC-20260718-222350 (Phase 1 + 2026-07-20 revision:
BE-YAC-20260720-010210)
**Context:** Extends `backup-restore-redesign.md` (v3 multi-namespace archives,
direct-Mongo engine). Picks up the remap half deferred by CASE-542 and filed as
CASE-548. Two new restore modes were requested by Peter (2026-07-19); the
2026-07-20 review split the second in two and reversed their order:

1. **merge** (§2.2) — restore into an *existing, non-empty* namespace on the
   same install, with a skip / overwrite policy on identity clashes.
   **Shipped.**
2. **cross-install merge** (§2.3) — the same, where the two installs'
   canonical IDs differ: match by logical identity, skip what exists, rewrite
   references. The consolidation case. **Shipped.**
3. **new-namespace** (§2.4) — restore under a *different* namespace name,
   minting new canonical IDs. **Deferred** — its non-development
   justifications turned out to belong elsewhere.

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

## Part 2 — The new modes

All three modes extend the **direct-Mongo engine**. The toolkit HTTP engines
are not the substrate: they are single-namespace, per-item, and duplicate
logic the platform now owns. (Proposal: mark `import_/fresh.py` +
`import_/restore.py` as legacy pending retirement once the new modes land;
`remap.py` graduates into the engine's dependency set.)

They are not three engines but three ways of filling one plan's `id_map` —
identity, matching, or minting (§2.1). §2.2 is shipped; §2.3 adds one
matching path and one rewrite pass to it; §2.4 replaces the matching with
provisioning.

### 2.1 Shared machinery: the RemapPlan

Both modes need the same first step — decide, per archive entity, what it
maps to in the target. Introduce one concept:

```
RemapPlan
  per entity type:
    to_insert:    archive entities with no counterpart in target
    clashes:      archive entity ↔ existing target entity
    conflicts:    archive and target disagree about identity (same-install merge)
    id_map:       old_id → new_id
    hash_recompute: doc ids whose identity_hash must be recomputed
```

`id_map` has two possible sources, and which one applies is what separates
the modes:

- **Same-install merge (§2.2, shipped):** identity — IDs are preserved, so
  the map is empty and a mismatch between the two keys is a *conflict*, not
  something to remap.
- **Cross-install merge (§2.3):** populated from **matches** — where the
  target already holds an entity under a different ID, incoming references
  to the archive's ID are rewritten to the target's.
- **New-namespace (§2.4):** populated from **minting** — every entity gets a
  fresh canonical ID, so every reference is rewritten.

The second and third differ only in where the new ID comes from. That is
the whole reason this concept exists as one shared piece: `IDRemapper`
(`WIP-Toolkit/src/wip_toolkit/import_/remap.py`, complete and
engine-agnostic) consumes `id_map` without caring which source filled it.

Built by **bulk, indexed Mongo reads** (batched `$in`, 1000 keys per query),
never per-item HTTP. `IDRemapper` consumes `id_map` unchanged.

**Template matching never trusts archived composite-key hashes.** Registry
template entries carry an *empty* composite key in every pre-CASE-709
archive (`registry_client.py` registered templates with `composite_key={}`),
so hash comparison matches nothing. The RemapPlan constructs the template
key `{ns, type: "template", value}` from archive data it already has
(namespace + template value — no archive-format change), exactly as
CASE-709's restore ruling prescribes for plain restore. Terminologies,
terms, documents, and files have always carried real composite keys and
match by stored hash.

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
cross-install mode (§2.3), where the same evidence is read as a *match*
rather than a conflict. The two readings are opposite, which is why the
mode must be requested explicitly and never inferred from the data.

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

**Schema entities (terminologies, terms, templates) take their own clash
policy — an explicit operator decision on the restore action, defaulting
to fail.** Identical schema → skip (counted `unchanged`) under every
policy. On a *difference*, a per-request `on_schema_clash` parameter
decides:

- `fail` (**default**): per-item error listing the diff, merge refuses.
  Merging data into a namespace whose schema diverged from the archive is
  a schema migration someone should look at — loud is the safe default.
- `skip`: target schema wins; the archive's variant is not imported and
  the divergence is reported in the job result. Documents from the archive
  then validate against the *target's* schema semantics on the reporting
  side — the report must say so.
- `upsert`: the archive's template is imported as a **new version** of the
  target's template (matching platform create-as-upsert semantics,
  CASE-709 §5.1), loudly reported.

Peter's ruling (2026-07-19): the platform's create-as-upsert (CASE-709)
does NOT make upsert the merge default — "this must be a user decision" on
the restore action; WIP's write-path semantics and a restore action's
clash handling are separate contracts. Terminologies/terms follow the same
policy parameter with their `incompatible_config` diff shape.

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

### 2.3 Mode: cross-install merge

**Contract:** merge an archive taken on install A into the same-named
namespace on install B, where the two sides evolved independently and their
canonical IDs therefore differ. IDs are **not** re-minted: entities the
target already has are matched and skipped, entities it lacks are inserted
keeping their source IDs, and references are rewritten to whichever ID
survives. This is the consolidation case — folding two installs' copies of
one namespace together.

**Why UUIDs make this safe, and what still collides (Peter, 2026-07-20).**
With UUID7 canonical IDs, an *ID* collision between two installs is
effectively impossible, so nothing needs re-minting to avoid one. What DOES
collide is **logical identity**: install A's `GENDER` terminology and
install B's have different UUIDs and the same composite key
`{ns, type: terminology, value: GENDER}`, which hashes identically and hits
the Registry's unique index on
`(namespace, entity_type, primary_composite_key_hash)`. That is exactly the
conflict class §2.2's merge detects and refuses — here it is not an error
but the *normal* case, and the resolution is skip-if-exists.

**The non-obvious cost: skip requires reference rewriting.** Skipping A's
`GENDER` because B already has one leaves A's terms carrying
`terminology_id: <A's uuid>` and A's documents pointing at A's term IDs.
Inserting those unrewritten imports dangling references — the one thing
Vision.md's "References Must Resolve" forbids. So every skip records
`old_id → target_id` in `id_map`, and `IDRemapper` rewrites the incoming
entities before insert. This is the entire delta from §2.2's merge:
matching gains a by-composite-key path, and applying gains a rewrite pass.

**Schema divergence is already handled.** If A's `PATIENT` v2 differs from
B's, skip-if-exists would silently validate A's documents against B's
schema. That is what `on_schema_clash=fail` (the shipped default) catches —
the mode inherits it unchanged. Consolidation between installs running
different template versions should refuse until someone reconciles them.

**Preconditions and policies** are §2.2's, plus the by-composite-key
matching. `dry_run` remains the plan, and gains one line per entity type
for how many incoming references were rewritten.

**Deferred within this mode:** cross-install merge where the two sides hold
*different data under the same logical identity* (both have `GENDER` but
with different terms). Skip-if-exists takes the target's; a merge-the-
contents policy needs its own design pass and a driving case.

### 2.4 Mode: `new-namespace` (deferred — see Part 4)

**Contract:** restore into a namespace that does not exist (or is empty),
under a *different* name than the source, with **new canonical IDs minted by
the Registry**. Works same-install, with the source namespace still live.

**Status (2026-07-20):** deferred. Both of this mode's non-development
justifications dissolved on inspection:

- **Namespace rename.** There is no rename operation in the Registry
  (`api/namespaces.py` has upsert, archive, restore, export, delete — no
  rename), so restore-into-a-new-name was the only path to one. But that is
  acrobatics: it achieves a metadata change by severing identity. Peter's
  ruling: fix it with a real rename if it ever matters — and it likely
  will not, because **namespace names are cosmetic**. Filed as its own
  workstream, unprioritized. The non-obvious cost, for whoever picks it up:
  the namespace name is baked into Registry composite keys (`{ns, value}`,
  `{ns, terminology_id, value}`, `{ns, type, value}`), which are SHA-256
  hashed into `primary_composite_key_hash` — so a rename must recompute
  every hash and rebuild every composite-key claim, update embedded
  synonym scopes, rename the postgres schema and its bookkeeping rows, and
  fix `allowed_external_refs` lists in *other* namespaces that name this one
  by string. Cross-namespace references survive untouched (they store
  canonical IDs, not names). It wants the namespace quiesced while it runs.
- **Consolidating installs.** Handled by §2.3 without minting anything.

What remains is parallel development copies on one install — real, but the
case where "which namespace is authoritative?" has no good answer, and not
worth the mode's cost on its own.

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
   blur one layer up: `migrate_documents` accepts an identity-preserving
   move and **rejects an identity-changing one as a fork** (PoNIF #2).
2. **It is redundant.** The document identity hash is namespace-independent
   and purely value-based (`compute_identity_hash(data, identity_fields)`
   takes no namespace), so a document and its re-minted copy carry the SAME
   identity hash and join on `(template value, identity_hash)` for free.
   Terminologies, terms and templates join on their `value`. The
   correspondence is derivable at any time without storing it.
3. **Qualification did not buy what it was supposed to.**
   `RegistryEntry.rebuild_search_values` flattens every string value of
   every synonym's composite key into `search_values`, and
   `lookup_by_id` falls back to `{"search_values": <id>}`
   (`api/entries.py:909-916`). So a qualified lineage synonym still puts the
   old ID in the searchable values: once the source entity is deleted or
   deactivated, an unscoped lookup by the old ID starts returning the
   *copy*, and with two restored copies `find_one` picks arbitrarily. The
   ambiguity the qualification was chosen to prevent survives it.

**What replaces it.** Provenance belongs to the *namespace*, not to each of
its entities: one line appended to the restored namespace's `description`
("restored 2026-07-20 from an archive of `kb` taken <T> on host <X>").
There is no `metadata` field on the `Namespace` model, and `description` is
the visible one. It must be written AFTER the namespace upsert, which sets
`description` from the manifest and would otherwise clobber it. A later
backup captures that description into its own manifest, so provenance
travels forward into future archives — desirable.

For the narrow case where nothing is derivable — identity-less (append-only)
documents have only a surrogate `document_id`, and templates whose identity
fields hold canonical IDs change hash under re-minting — the correlation, if
ever needed, is the mode's own `id_map`, persisted ONCE as a job artifact.
One write, no participation in resolution, and a snapshot that cannot drift
because it describes a moment rather than asserting a live relationship.
Unbuilt; available if a driving case appears.

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
6. **Synonyms.** One kind only: the *identity-value auto-synonyms* the
   Registry mints during provision/registration exactly as normal creation
   would (edge-type skip rule honored) — free, no extra pass. Old-ID
   lineage synonyms are **removed from this design**; see above for why,
   and for the namespace-description provenance that replaces them.
7. **Reporting phases** — unchanged from plain restore.

**Why not draft/activate through service APIs (the old fresh-mode design):**
documents have no draft state to use; per-item HTTP creates are the exact
performance profile the redesign killed (validation + round-trip dominated);
and the reservation lifecycle gives the same not-visible-until-done property
at the layer that controls resolution. The three-pass HTTP engine survives
only as `remap.py`'s rewrite tables.

**Performance budget (qualitative, clintrial-scale 228k docs + ~229k
registry entries):** now dominated by ONE Registry-mediated step —
provisioning (~229 batched HTTP calls) — since dropping lineage synonyms
removed the second, which was the same order of magnitude again. It is
single-round-trip-per-batch after the §1.5 provision optimizations (block
counter allocation, claims via `insert_many`), which must land first.
Everything else is the existing insert_many path plus one streamed rewrite
pass. Expected same order of magnitude as plain restore; the mode must
*never* fall back to per-entity HTTP.

Note that §2.3's cross-install merge has NO Registry-mediated bulk step at
all — it matches by composite key against Mongo and rewrites references
locally — which is a large part of why it is now the cheaper mode to build
as well as the better-justified one.

---

## Part 3 — Explicitly deferred or rejected

- **Namespace rename** — a real operation, not restore acrobatics. Filed,
  unprioritized: namespace names are cosmetic. Cost sketch in §2.4.
- **Contents-merge on a logical-identity match (cross-install)** — when both
  installs hold `GENDER` with different terms. Skip-if-exists takes the
  target's; anything smarter needs its own design pass and a driving case.
- **Old-ID lineage synonyms** — REJECTED, not deferred. See §2.4; they
  assert a false identity claim, are redundant against derivable joins, and
  the qualification meant to make them safe does not.
- **Version-history splicing on overwrite** — rejected, see §2.2.
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

**Phase 1 — merge mode: SHIPPED** (2026-07-20). Delivered as planned:
the plan builder (`services/merge_plan.py` — same-ID matching, two-keyed with
identity-conflict detection), `DirectRestoreEngine.run_merge` with
`on_clash=skip|overwrite` for documents and `on_schema_clash=fail|skip|upsert`
for schema entities, precondition inversion (namespace must exist; reporting
schema expected; `drop_stale_reporting` rejected), namespace-config drift
reported not applied, dry-run-as-plan, and the REST / MCP / `@wip/client`
0.37.0 surface. Reporting structural gate and count parity run unchanged.

Two decisions made during implementation, beyond what this plan specified:

1. **`upsert` on terminologies and terms updates the row in place.** §2.2 gave
   them the same policy parameter as templates, but they have no version axis,
   so "the archive wins" has only one possible meaning. Reported explicitly in
   the job events.
2. **Blobs upload only for inserted files, and claims are rebuilt only for
   inserted registry entries.** Both follow from merge's delta nature; the
   claim rebuild also gained an "already claimed by the same entry" case so a
   merge into a namespace that already has claims does not cry wolf.

Also shipped alongside, from §1.6 item 1: **restore now recreates
composite-key claims** for every entry it writes — the verified live gap that
merge's clash detection depends on. It applies to plain restore too.

Still open from this phase: the **scale test against a clintrial-sized
namespace**. The read path is batched-by-archive-key (cost scales with the
delta, not the target), but that is a code-reading claim, not a measurement.

**Phase 2 — cross-install merge (§2.3): SHIPPED** (2026-07-20). Reordered
ahead of the new-namespace mode the same day; it is both better justified
(real consolidation use case vs. parallel dev copies) and cheaper to build
(no Registry-mediated bulk step at all). Delivered as scoped below, plus one
prerequisite the plan had not identified:

- **Composite-key hashing moved to `wip_auth.composite_key`** as its
  canonical home, with the Registry's `HashService` delegating. A
  cross-install merge rewrites the parent IDs embedded in a Registry
  composite key and must recompute its hash off the Registry's write path;
  re-deriving "sort, dump, sha256" at a second call site is the CASE-316 /
  CASE-401 drift class, and document identity hashing was consolidated for
  exactly this reason after CASE-402. Contract tests pin the digests and pin
  the facade to the canonical function.
- **`IDRemapper` gained `remap_term` and `remap_term_relation`** — the two
  rewrites it was missing. A term's parent terminology is its only outward
  reference and is load-bearing; term relations carry two endpoints, their
  denormalized terminologies, and a `relation_type` that may be a term ID or
  a plain value.
- **Planning interleaves rewrite → match → record** per entity type rather
  than reading everything up front, because the logical key a type matches on
  contains the IDs the previous types just resolved.

Still open: the same clintrial-scale test Phase 1 wants. Also unvalidated
live — the mode has real-database tests but has not yet been run against two
genuine installs.

Original scope, all delivered:

1. **Matching by composite key** — extend `MergePlanner` with a second
   matching path: where same-install merge treats "same logical key, different
   ID" as a conflict, cross-install treats it as a match and records
   `old_id → target_id` in `id_map`. A per-request mode flag selects which,
   because the two readings of the same evidence are opposite and must never
   be inferred.
2. **Reference rewriting on insert** — thread `IDRemapper` into the apply
   path so skipped-entity references resolve to the target's IDs. Without
   this the mode imports dangling references; it is not optional.
3. **Surface** — a mode value on the REST/MCP/client restore surface, plus
   dry-run reporting of how many references would be rewritten.
4. **Prerequisite:** none beyond what Phase 1 shipped. The `id_map` comes
   from Mongo reads, not from the Registry.

Inherits `on_schema_clash=fail` unchanged — consolidating installs whose
template versions have diverged should refuse until someone reconciles them.

**Phase 3 — new-namespace mode (§2.4), deferred.** Was Phase 2; demoted when
both its non-development justifications dissolved (rename should be a rename;
consolidation is Phase 2). Remaining justification is parallel dev copies on
one install. Scope if it is ever picked up: provision/activate integration,
remapper reuse + identity_hash recompute, explicit `{src: tgt}` mapping for
every archive namespace (open question #2), blob re-keying, and the
namespace-description provenance line. NO lineage synonyms. `mode=fresh`
400 message retires in favor of the real mode name.

Prerequisites, both deferred out of Phase 0 and still open — verified
2026-07-20: `ArchiveReader.read_entities` still does
`self._zf.read(...).decode()` (whole JSONL in memory, `archive.py:273`), and
`/entries/provision|reserve|activate` still have **zero** callers outside the
Registry's own source and tests. The second is the sharper risk: this mode
would be the first production consumer of a three-endpoint lifecycle nothing
has ever driven, which is the never-tested-default trap (§1.4).

**Unphased, unprioritized:** namespace rename (Part 3).

---

## Open questions for Peter

1. **Schema clash tier in merge:** ~~validate-or-error only (recommended),
   or an explicit template-overwrite (new version) escape hatch?~~
   **Resolved (Peter, 2026-07-19):** explicit per-action `on_schema_clash`
   choice — `fail` (default) | `skip` | `upsert` (new version, aligned
   with CASE-709 create-as-upsert). Upsert is deliberately NOT the
   default: the restore action's clash handling is the operator's
   decision, separate from WIP's write-path semantics. See §2.2.
2. ~~**New-namespace multi-archive UX:** explicit `{src: tgt}` map acceptable,
   or is a single-namespace-only v1 enough?~~
   **Resolved (Peter, 2026-07-20):** every archive namespace must be
   explicitly mapped. No implicit suffixes, no partial-mapping shorthand —
   an unmapped namespace is an error, not a default.
3. ~~**Lineage synonyms** (`restored_from` old-ID → new entity): default-on,
   default-off, or omitted in v1?~~
   **Resolved (Peter, 2026-07-20): removed from the design.** A restore
   should leave a clean new state; restoring under a different namespace is
   a deliberate decision to keep the information and cut the historical
   context. Linking the copies is overly complex, only meaningful when the
   source namespace happens to still be intact on the same install, and
   invites the question the link cannot answer — which copy is
   authoritative, once they drift by upsert? Full reasoning, including the
   two findings that make it a semantic error rather than a trade-off, in
   §2.4. Provenance instead goes on the restored namespace's `description`.
4. ~~**Overwrite granularity:** the proposed doc-level overwrite imports the
   archive's *latest* version only.~~
   **Resolved (Peter, 2026-07-20):** latest-only is correct — the intended
   use is "bring this namespace up to the archive's state", not an audit
   replay. This is what Phase 1 shipped, so no change follows.
