# Toolkit Archive Transforms — Design

**Status:** draft v1 — architecture + the analysis core + `inspect` in
depth; transforms, ramps, and surgery are scoped but deliberately
sketched, to be detailed in later versions.
**Date:** 2026-07-29
**Tracking:** CASE-827 (decisions: FIRESIDE-27, FIRESIDE-28,
CASE-827#1, CASE-827#2; groundwork shipped: CASE-823, CASE-824)

---

## 1. Context and inherited decisions

The platform has one collector and one import write-path: the server
backup engine reads the databases directly and produces complete,
restorable archives; the server restore engine is the only way data
enters (CASE-744 lineage). The wip-toolkit's client-side collector
(`export`'s HTTP fan-out) is being retired — its defect record
(silent version-history loss, per-page dedup leaks, registry-id gaps
that went unnoticed for years) is the cost of maintaining a second
definition of "what a namespace contains" (CASE-819 audit).

The toolkit's future shape is a three-step pipeline:

```
1. backup   (server engine — mechanical, complete, trusted)
2. modify   (the toolkit — everything selective, clever, or blocked live)
3. restore  (server engine — the validating door)
```

Steps 1 and 3 exist. Step 2 is this design.

Decisions this document builds on, restated in full:

- **The door principle.** An archive is outside WIP's jurisdiction —
  anyone can edit the JSONL today. Constraining transforms is
  therefore pointless; the guarantee lives where it always has: the
  restore door validates everything that enters (identity-row
  refusal, post-restore integrity validation mirrored onto the
  restore job, `derived_from` surfacing — all shipped in v2.1.0). The
  toolkit's obligation flips: modifications are made **correct**
  (derived state recomputed) and **declared** (consequences stated,
  provenance stamped) — never forbidden.
- **Power asymmetry.** The CLI being more powerful than the engine is
  its value. The engine stays mechanical and minimal: complete
  backups, three restore modes, a hardening door. It gains no
  selection parameters and no subset selectors; all cleverness
  accrues toolkit-side.
- **Three transform classes.** *Shaping* (invariant-preserving
  selection: filter, definitions-only, strip-blobs, latest-only,
  split, convert), *ramps* (zip→CSV/report off-ramp; `compose`
  on-ramp: external data → archive → fresh restore), *surgery*
  (modifications live WIP refuses by design: migration with filled
  values, version squash, bulk repair, redaction).
- **Provenance is mandatory.** Operators must be able to tell that
  data arrived via restore and what happened to it on the way
  (CASE-827#2). Archives chain `derived_from` hops; the door persists
  the chain namespace-granularly (see §8).
- **Dry-run is not a code path.** Every transform's dry-run is an
  `inspect` view of its plan; there is no parallel preview code to
  drift. *(As built, 2026-11-01: `--dry-run` on each transform verb runs
  `run_transform` itself on scratch space, the copy and the verify of the
  would-be output included, and skips only the final write. It reports
  the counts and the verdict the real run would write.)*

## 2. Architecture

Two layers, one new and one grown:

```
libs/wip-archive                      WIP-Toolkit (CLI verbs)
┌───────────────────────────┐        ┌──────────────────────────────┐
│ ArchiveReader / Writer     │◄──────┤ backup / restore  (thin HTTP) │
│ IDRemapper                 │       │ inspect           (M1)        │
│ NEW: ArchiveModel          │◄──────┤ verify            (M2)        │
│   entity index + graph     │       │ filter, slim, split, … (M3)   │
│ NEW: offline validate /    │◄──────┤ export-csv        (M3)        │
│   recompute (M2)           │       │ compose, surgery  (M4+)       │
└───────────────────────────┘        └──────────────────────────────┘
```

- **`ArchiveModel`** (new, in `libs/wip-archive`): loads an archive
  into an entity index plus a dependency graph, and computes the
  analyses of §4. It is the shared core: `inspect` is its read-only
  face, `filter`'s closure walks it, `verify` gates on its findings,
  `diff` compares two of them.
- **Offline validate/recompute** (new, M2): validates documents
  against the archived templates and terminologies, recomputes
  identity hashes, resolves term values. Powers migratability
  assessment, `verify`'s deeper checks, and later `compose` and
  surgery. Not required for M1.
- The toolkit verbs stay thin; anything two verbs need lives in the
  library.

**Discipline line:** the model reads **archives only**. "Analyze my
instance" = `backup` + `inspect`. An `inspect --live` would be the
second collector reborn; it must not exist.

## 3. The archive model

### 3.1 Entity index

From a v3 archive (`namespaces/<ns>/*.jsonl`, `manifest.json`,
synonyms file, blobs), indexed per namespace:

| Entity | Key | Notes |
|---|---|---|
| Template versions | `(template_id, version)` | schema nodes are per-version |
| Terminologies | `terminology_id` | |
| Terms | `term_id` | + term_relations (ontology edges) |
| Document versions | `(document_id, version)` | data edges are per-version |
| Files | `file_id` | blob presence tracked separately |
| Registry rows | `entry_id` | identity coverage check input |

### 3.2 Edges

**Declared (schema) edges** — extracted per template *version* from
the same field set the retired closure walker used (`extends`,
`terminology_ref`, `array_terminology_ref`, `template_ref`,
`array_template_ref`, `target_templates[]`,
`target_terminologies[]`), **plus the template-level
`source_templates`/`target_templates` lists on relationship
templates, which the old walker never read** — a known gap the new
model closes.

**Actual (data) edges** — extracted per document *version*:

- doc → doc: the `references[]` snapshots (resolved document ids)
- doc → term: resolved term ids in stored data
- doc → file: file-field references
- doc → template version: the pin (`template_id`, `template_version`)

**Classification:** every edge target is `internal` (in the archive),
or `external` with a reason (`other-namespace-not-in-archive`,
typically the shared `wip` vocabulary). External edges are labeled,
never followed — there is nothing to follow offline, and the door
resolves them on the target instance exactly as live cross-namespace
references resolve today.

### 3.3 Granularity and roll-up

The graph is computed at `(template, version)` / `(document,
version)` granularity — declared references change across template
versions, and old document versions carry their own reference
snapshots. Reporting rolls up to template granularity with version
annotations. Two closure variants are always distinguishable:
**with-history** (all document versions contribute edges) and
**latest-only** (only each document's highest version does) — because
the `latest-only` transform changes the graph, and the operator
deciding on it needs both numbers.

Version *status* is irrelevant to closure; **pinning decides**: every
template version any included document pins travels, active or not
(the platform's frozen-version pattern, asserted live by the backup
matrix).

### 3.4 Memory model

Definitions (templates, terminologies, terms, registry rows) load
into memory — they are small. Documents stream in two passes: pass 1
collects ids and pins, pass 2 extracts edges. This mirrors the
engine's own streaming discipline and keeps the model O(definitions +
doc-ids), not O(archive).

## 4. Analyses

All computed by `ArchiveModel`, consumed by `inspect` (and later by
`verify`/`filter`/`diff`):

1. **Degrees and closures per template.** In-degree (who declares or
   holds references to me — the *deletability* question), out-degree
   (what I need — the *extractability* question), and two closure
   previews: **schema-closed** (declared targets + terminologies) and
   **data-closed** (actually-referenced documents and their pins).
   Extraction needs both closures satisfied; deletion needs only
   in-degree zero. The two questions are asymmetric and the report
   keeps them apart.
2. **Islands.** Connected components over the combined
   declared+actual graph = minimal self-contained extraction units.
   The archive summary leads with them. Terminologies shared across
   islands are flagged (extraction duplicates them; a later
   merge-on-return reconciles by identity).
3. **Edge-type connectivity.** Per relationship template: declared
   endpoint lists, actual edge count, distinct endpoints touched, and
   the disconnection quantification ("dropping these 340 edges
   disconnects 12% of MONSTER docs from any SPELL") — because
   `/relationships` and `/traverse` are app-visible features, an edge
   decision changes application behavior, not just completeness.
   `versioned: false` is surfaced per edge type (overwrite-in-place
   changes merge semantics).
4. **Health stats.** Per template: document count, version spread
   (docs pinned per template version — migration pressure made
   visible). Per terminology: term count, terms actually referenced
   by documents vs unused, deprecated-but-still-referenced terms.
   Files: blob count/bytes, orphan blobs (metadata without any
   referencing document). Identity: entities without registry rows —
   the exact condition the restore door refuses on (CASE-824),
   visible before upload.
5. **Findings.** Analyses that indicate a problem emit structured
   findings `{severity, class, subject, detail, count, samples}` —
   dangling internal references, missing identity rows, orphan blobs,
   external-reference inventory. `inspect` *reports* findings;
   `verify` (M2) *gates* on them with an exit code. Same objects, two
   consumers — verify is inspect's findings filtered to
   restorability.
6. **Migratability assessment** (M2, needs the offline validator):
   for a template and target version, validate every pinned document
   offline and report failure classes ("214 docs lack new-mandatory
   `region`; 3 have string where v2 wants integer"). The offline
   value over live `migrate_documents --dry-run`: no instance needed,
   works on archives from anywhere, feeds fill-value migration
   planning (M4).

## 5. The `inspect` verb

Grown from the existing verb (which shows ids and references); the
old flags remain as shallow projections. No second `info` verb.

```
wip-toolkit inspect <archive>                      # summary
wip-toolkit inspect <archive> TEMPLATE [T2 ...]    # deep dive
wip-toolkit inspect <archive> --terminology LOV    # vocabulary dive
wip-toolkit inspect <archive> --json               # machine output
wip-toolkit inspect <archive> --target-version N TEMPLATE   # M2
```

**Summary view** (in order): manifest header (source, taken-at,
counts, honesty flags) and the **`derived_from` provenance chain** if
present; islands; per-template table (docs, versions pinned,
in/out-degree, island); per-terminology table (terms, used/unused);
edge-type table (connectivity); findings, most severe first.

**Deep dive** (per template): fields and declared references; version
list with per-version doc-pin histogram; in-edges and out-edges with
actual counts; both closure previews — which is *literally the filter
plan*: `inspect A TEMPLATE` prints what `filter A TEMPLATE` would
produce. Per terminology: term list with per-term usage counts,
alias/ontology structure, unused and deprecated-but-referenced terms.

**`--json`**: the full model output with a versioned top-level
`schema` field. Tables are for humans; JSON is the API — agents and
future tooling (UI, scripted fleet maintenance) build on it, so its
stability is a contract from day one.

## 6. Milestones

| | Contents | Needs |
|---|---|---|
| **M1** (done) | `ArchiveModel` (index, edges, closures, islands, stats, findings) + grown `inspect` (summary, deep dive, `--json`) | nothing new beyond wip-archive |
| **M2** (done, §6.2) | offline validate/recompute library; `verify` verb (findings → exit code); `inspect --target-version` migratability | identity-hash + term-resolution logic shared with wip-auth's canonical implementations |
| **M3** (done: M3a §7.2, M3b §7.3) | shaping transforms (`filter` with boundary policy `strict/drop/stubs/closure`, definitions-only, `prune`, strip-blobs, latest-only, split), `export-csv` off-ramp, thin `backup`/`restore` verbs, `diff` | M1 model; M2 for filter's auto-verify postcondition |
| **M4** (done, §6.4; plan §6.3) | surgery: fill-value migration via mapping file, redaction | M2 library |
| **M5** (closed, not built for now, §6.5) | `compose` on-ramp: not built, the live write path is the bulk-load path; `export` still removed with the next release | M4; separate design iteration |

Every transform (M3+) runs `verify` on its output automatically and
stamps the verdict into the `derived_from` hop it writes — a
transform that produces a broken archive says so at production time,
not restore time.

**Testing:** unit tests against hand-built archives (`ArchiveWriter`
is importable — no instance needed); integration via the existing
in-process toolkit harness (real backup → model → assertions, and
later transform → real engine restore); golden `--json` outputs pin
the machine contract.

### 6.1 The M2 plan (agreed 2026-10-31)

**What the live platform offers.** Everything that accepts a document
lives in document-store's `ValidationService`. Its checks are pure
logic, but they are wrapped in async code that fetches templates over
HTTP, resolves terms through def-store, and resolves references through
MongoDB and the Registry; nothing imports as is. Template inheritance
is resolved in template-store, and archives store templates
unresolved. The identity feed (`wip_auth/document_identity.py`) is pure
standard library, but importing it runs `wip_auth/__init__.py`, which
pulls in FastAPI and httpx, while `wip-archive` depends only on
pydantic. Term resolution is a short pure algorithm over a term list:
active terms only, a term's value before any term's names, case per
terminology, the first-created bearer on ambiguity.

**Decisions (Peter, 2026-10-31).**

1. **Reimplement, guarded by comparison tests.** The checks are
   reimplemented in `wip-archive`. Comparison tests send the same
   documents through the live validator (`POST /validation/validate`
   in the in-process toolkit stack) and through the offline one, which
   must agree. Extracting a shared module out of the live write path
   stays a later option if the tests show drift.
2. **Dependencies outside the archive are a warning.** Terminologies,
   parent templates or documents whose content the archive does not
   carry (only their identity rows, in `referents.jsonl`) produce an
   `unverifiable_dependency` warning that lists them. `verify` says
   `warn`, never a silent `pass`.
3. **Live quirks are reproduced exactly, for now.** The offline check
   must agree with what the server accepts. The questionable quirks are
   filed as CASE-1009; whoever fixes one there updates the offline
   validator too.
4. **`verify` exits 1 on errors, 0 otherwise;** `--strict` also fails
   on warnings.

**Two checks, not one.**

- **`verify` asks whether the archive is consistent and restorable.**
  Each document is checked against the template version it pins:
  field structure, stored term and reference ids present in the
  archive or listed as outside it, recomputed identity hash equal to
  the stored one, cross-field rules, plus M1's findings. It does not
  re-resolve stored values as new writes: existing data keeps resolving
  terms retired since (PoNIF #1).
- **Migratability (`inspect --target-version`) asks whether a document
  would pass as a write against version N,** as live
  `migrate_documents` does. Before it is built, `_migrate_one` is read
  to settle how live migration treats terms deprecated since.

**Slices, one commit each.**

| # | Slice |
|---|---|
| 0 | CASE-1008 (server): the restore door refuses a failed verdict unless the request accepts it, in all three modes. |
| 1 | Definition index: full template rows, `extends` resolved as template-store does, term `names` read (M1 reads a nonexistent `aliases` key), `referents.jsonl` read; the identity feed copied into `wip-archive` with a byte-parity test against `wip_auth`. |
| 2 | Validation library: term resolver, field types and constraints, semantic types, cross-field rules, live quirks included. |
| 3 | `verify` pass in `ArchiveModel`: a third streaming pass keeping counters and capped samples only; new finding classes (`document_invalid`, `identity_hash_mismatch`, `unverifiable_dependency`, …); `--json` schema 1.1. |
| 4 | Toolkit: the `verify` verb and `restore`'s pre-upload gate with `--force`. |
| 5 | Migratability: `inspect --target-version`. |
| 6 | Wrap-up: milestone status here (§6.2), CASE-827 comment. Versions, goldens and release notes shipped with each slice. |

Every slice carries hand-built-archive tests, at least one assertion
against a real engine backup, and, from slice 2 on, comparison tests
against the live validator.

### 6.2 M2 delivered (2026-11-01)

| Slice | Commit | What shipped |
|---|---|---|
| 0 | `192031d2` | CASE-1008: the restore door refuses an archive whose newest hop says `verdict: "fail"`, in all three modes, unless the request accepts it (`allow_failed_verify`; the toolkit's `--force`). |
| 1 | `03330c84` | `wip_archive.definitions` (full rows, template-store's inheritance, referents) and `wip_archive.identity` (a verbatim copy guarded by a source-and-output parity test in document-store). wip-archive 0.7.0. |
| 2 | `3182d6f1` | `wip_archive.validation`: the offline validator, held to the live one by an integration comparison suite. wip-archive 0.8.0. CASE-1009 later changed both sides together (`aa44e0de`, 0.10.0). |
| 3 | `4fd631e9` | `wip_archive.document_check` behind `ArchiveModel.load(check_documents=True)`: `template_version_missing` and `missing_term_reference` (errors), `document_invalid`, `identity_hash_mismatch` and `unverifiable_dependency` (warnings). `--json` schema 1.1. wip-archive 0.9.0. |
| 4 | `fb4673dd` | `wip-toolkit verify` and `restore`'s pre-upload gate; `--force` passes both gates. WIP-Toolkit 0.8.0. |
| 5 | `ca86a65e` | `wip_archive.migratability` and `inspect --target-version`, held to the live migrate dry run by an integration comparison test. `--json` schema 1.2. wip-archive 0.11.0, WIP-Toolkit 0.9.0. |

**Measured on a real archive** (pbs-clintrial, 72 MB, 240,474 document rows,
taken 2026-10-13): `inspect` 3.3 s, `verify` 6.0 s, both about 232 MB peak
memory. The document check adds about 2.7 s and no memory. Verdict `pass`:
no invalid document, every identity hash recomputed offline equals the stored
one, nothing unverifiable. A synthetic 270,834-row archive measured the same
rate (about 38,000 rows/s for the check).

**What the M2 work changed or found elsewhere:**

- M1 read term aliases from an `aliases` key engine archives do not carry;
  terms are read from `names` now.
- Live inheritance resolution dropped template properties and suppressed
  resolution errors (CASE-1010, fixed `b030cb80`).
- Five live validation gaps and quirks (CASE-1009, fixed in both validators).
- Live migration re-resolves terms, so a term deprecated since a document was
  written fails its migration; `verify` deliberately does not re-resolve.

**Known limits:**

- Reference and file checks are not reproduced offline; `verify` checks
  stored term references for existence and leaves document references to M1's
  `dangling_document_reference`. Migratability reports
  `references_checked: false`.
- For an ambiguous name on a non-identity field, the recorded bearer can
  differ from live when two bearers were created in the same millisecond
  (the archive has no insertion order). Validity is unaffected; both warn.

### 6.3 The M4 plan (agreed 2026-11-01)

Shaping (M3) never edits a row it keeps. Surgery is the class that does,
so it carries an obligation shaping does not: whatever it changes, it
recomputes everything derived from it. One fact shapes all of it: a
document's Registry row records its identity hash in its primary key, and
its identity values in clear in a synonym (checked on a real engine
archive). An edit to an identity value would therefore have to rewrite
the Registry row as well, and changing identity is a fork, which the
platform refuses.

**Decisions (Peter, 2026-11-01).**

1. **The surgery contract.** Surgery edits rows, never changes a
   `document_id`, never renumbers versions. It recomputes what depends on
   what it changed: the identity hash (`wip_archive.identity`), term and
   reference snapshots, file references. The Registry is not touched,
   because surgery refuses identity changes. On surgery output an
   `identity_hash_mismatch` is an error, not a warning: it would mean the
   recomputation failed.
2. **Order:** fill-value migration first (M2's migratability already
   says what a migration lacks), redaction second. `compose` becomes M5;
   removing `export` goes with the next release cut, not a slice.
3. **Migration mapping file (open question 3).** JSON or YAML naming the
   template, its source versions and the target version, with one rule
   per field to fill: a constant, `{"copy": "<path>"}` from another
   field, or `{"format": "{a}-{b}"}`. No Python hook in v1: a hook
   cannot be recorded reproducibly in a hop, and it would run arbitrary
   code against archives from anywhere. Semantics are live
   `migrate_documents`' own: a new version row pinned to the target, the
   old version retired, the same `document_id` and identity hash; an
   identity-changing migration is refused as a fork. Each migrated
   document must pass the offline write validation against the target,
   and a comparison test holds the result to the live migration.
4. **Redaction.** Identity fields are refused (their values live in the
   Registry's synonyms, and changing them is a fork). Non-identity fields
   are named by path per template and cleared to null in every version;
   a term or reference field drops its snapshot, a file field its file
   reference (the file leaves when nothing else references it). The hop
   records the paths, never the values, with `params_redacted: true`.
5. **No version squash.** Keeping each document's newest version is
   `latest-only`; renumbering history to version 1 would break the
   pinned references the platform guards, and the stable-version contract.
6. **Open question 4: no opt-in full-schema validation at the door for
   now.** Offline verify plus post-restore integrity validation stays
   the contract. Revisited with `compose`, the first producer of rows no
   install ever held.

**Slices, one commit each.**

| # | Slice |
|---|---|
| M4-1 | Surgery core in `wip-archive`: row edits through the transform core, identity recomputation, identity-change refusal, `identity_hash_mismatch` as an error on surgery output. |
| M4-2 | `migrate`: mapping file, live migration semantics, the live comparison test. |
| M4-3 | `redact`: field paths per template, snapshot and file-reference clean-up, `params_redacted`. |

### 6.4 M4 delivered (2026-11-01)

| Slice | Commit | What shipped |
|---|---|---|
| M4-1 | `94670878` | The surgery core: `run_transform(rewrite_row=…, strict_identity=True)`, `TransformResult.findings`, and `wip_archive.surgery` (identity recomputed with the platform's feed and a change refused, snapshot paths under renames, stamp and actor). wip-archive 0.22.0. |
| M4-2 | `113fece8` | `migrate`: a JSON mapping with constant, copy, format and value fills; live migration's cohort and rows (old version retired, next written, `versioned: false` in place); refusal unless `--skip-failing`. WIP-Toolkit 0.22.0. |
| M4-3 | `b4b55e36` | `redact`: non-identity paths nulled in every version, snapshots and unshared files with them, `params_redacted` with paths only. WIP-Toolkit 0.23.0. |

**The guards against live:**

- `migrate`: an integration test applies a live migration between two
  engine backups and requires the offline migration of the first to equal
  the second row for row, in every field but `updated_at`, `updated_by` and
  `created_by`. The document live refuses migrates offline with a fill and
  restores fresh at version 2.
- `redact`: a two-version document with an email and a term, redacted and
  searched byte for byte: the value is in no row, Registry entry or hop;
  restored fresh, neither version holds it.

**Choices made while building, not in the plan:**

- `migrate` refuses when any document would fail, unless `--skip-failing`.
- `migrate` first read JSON mapping files only; Peter then approved PyYAML
  as a WIP-Toolkit dependency, and `.yaml` / `.yml` files are read as YAML
  (WIP-Toolkit 0.24.0).
- Rows a surgery writes record `wip-toolkit:<verb>` as their writer;
  `redact` keeps the rows' own timestamps and writers, since it rewrites
  history in place rather than writing a version.
- A reference or file field cannot be filled by `migrate`: offline, its
  snapshot cannot be resolved.

### 6.5 The M5 plan (decisions 2026-11-02; measurement first)

`compose` is the first producer of rows no install ever held. Every
earlier transform starts from rows a live install validated; compose
builds them from outside data.

**What a composed archive must hold** (read from `run_remap` in
`backup_engine.py`). A fresh restore re-mints every ID through the
Registry and rewrites references, so compose may write placeholder ids.
It still reads the archive's `registry_entries`: `_restore_synonyms`
carries each archived synonym onto the re-minted entity, a document's
identity-values synonym included. So compose writes Registry rows as well
as document rows. Each document row carries what a live write derives:
`identity_hash`, the `term_references` and `references` snapshots, and
`template_version`. The M2 library already computes all of these
(`identity`, `document_check`, `validation`), as `migrate` does.

**Decisions (Peter, 2026-11-02).**

1. **Use cases:** migrating a legacy system (a one-off large load into a
   new namespace), and bulk loads faster than the live write path.
   Seeding app data and test fixtures are not drivers.
2. **Definitions come from an existing archive.** Templates and
   terminologies are authored on a live install, where they are fully
   validated, and taken from its backup. Compose adds documents and their
   Registry rows only. It never creates a schema, so no offline template
   validation is needed.
3. **Compose validates every row; the door is unchanged.** Each composed
   row must pass the offline write validation against its template, the
   same check `migrate` runs. A failure refuses the run unless
   `--skip-failing`. Open question 4 stays closed: no full-schema
   validation at the door. The known gap: an archive edited after compose
   is checked again only by `verify` and the post-restore integrity
   validation, which covers references, not schemas.
4. **Staged: v1 targets fresh restore only.** A merge keeps the archive's
   canonical IDs (the docstring of `_check_ids_are_free` in
   `backup_engine.py`: "Canonical IDs are preserved by a merge"). Merging a composed archive would
   make compose's offline placeholder IDs canonical, which bypasses the
   Registry as the identity authority and breaks namespaces with prefixed
   ID formats. A fresh restore re-mints every ID through the Registry, so
   v1 serves the legacy load into an empty namespace. Recurring loads into
   a populated namespace need a merge variant that re-mints new documents'
   IDs. That is engine work, decided on the measurement below.
5. **Measure first.** The speed claim behind the second use case is
   unmeasured. Before building compose, measure a fresh restore against
   live bulk create of the same documents. If the restore is not clearly
   faster, the bulk-load use case is better served by speeding up the
   live write path, and the merge variant is dropped.

**The measurement (2026-11-02, Raspberry Pi 5, dev install at
`4de37267`).** The pbs-clintrial archive (240,474 document rows, 222,026
documents, two namespaces) loaded both ways into fresh namespaces, one
client each, no concurrency:

| Path | Time | Result |
|---|---|---|
| Fresh restore (server job) | 71 min 44 s | 240,474 rows, 222,026 latest; post-restore validation healthy |
| Live bulk create: definitions-only fresh restore, then `POST /documents` in batches of 500 through Caddy | 2 min 23 s + 70 min 28 s | 240,474 rows, 222,026 latest; 222,026 created, 18,448 updated, 0 errors |
| Fresh restore of an archive whose identity synonyms are already qualified | 43 min 53 s | 240,474 rows, 222,026 latest; validation healthy; nothing re-qualified |

Where the restore's time went: activating 222,119 document identities
17 min 42 s; restoring 222,113 synonyms 16 min 12 s; re-qualifying the
archive's bare identity synonyms about 33 min (a lookup pass, then each
synonym added again in the qualified form and the bare one removed). The
archive predates qualified identity synonyms, so this restore paid the
requalify pass; a composed archive writes the qualified form and would not.
The third row measures exactly that shape: a backup of the restored
namespace (its synonyms now qualified), restored fresh again. Its phases:
activation 18 min 10 s, synonyms 17 min 19 s, and about 5 min for the
requalify pass, which still reads every entry back to find nothing to do.

The live path ran at 27 to 133 rows a second depending on the template.

**Reading.** For the archive shape compose would write, a fresh restore
took 43 min 53 s against 72 min 51 s for live bulk create: about 1.7 times
faster. Nearly all of the restore's time is two per-item Registry passes
(finding 1); nearly all of the live path's is per-document validation and
Registry work. Both paths have the fixable costs listed below, so the
ratio is a property of today's code, not of the two designs. Whether 1.7
times counts as "clearly faster" for decision 5 is open, for Peter.

**Repeated on the Mac Studio dev install (2026-11-02, `059b854a`),
archive in the current format only:** fresh restore 12 min 55 s
(activation ~6 min 10 s, synonyms ~4 min 36 s; validation healthy); live
bulk create 23 min 5 s (44 s definitions, 22 min 21 s documents; 0 errors).
About 1.8 times, the same ratio as the Pi at about a third of the time.

**Peter's direction (2026-11-02).** The old (bare) identity-synonym format
goes away; there is no use case for it. Investment goes into improvements
that benefit every restore path and the live path alike: missing bulk
support and avoidable round trips. Filed as CASE-1017 to CASE-1023.

**Findings from the measurement** (each tied to a measured phase; none is
fixed by this plan):

1. Keyed activation (`registry/api/entries.py`, `activate_entries`) and
   `/synonyms/add` (`registry/api/synonyms.py`) handle one item at a time:
   a full-document read, a pending claim, a full-document save and a
   confirm, about 4 to 6 MongoDB round trips each. Keyless activation
   (terms) is bulk: 18,054 identities took 15 s. Under both sits a claim
   protocol with no bulk form, which `/entries/register` (every live
   create) also pays. CASE-1017 (claims), CASE-1018 (activation),
   CASE-1019 (synonyms); CASE-1020: a fresh restore writes each document's
   entry twice, once per pass.
2. A fresh restore adds an archive's bare identity synonyms, then
   `requalify_identity_synonyms` re-adds every one qualified and removes the
   bare one; on an archive in the current format the pass still reads every
   entry back to find nothing. CASE-1021 retires the format and the pass.
3. The live path spends its time in an untimed stage of `bulk_create`:
   the namespace-isolation check on each document's references, run one
   document at a time with its own Registry `lookup/by-id`
   (`document_service.py`, `_check_new_references`;
   `reference_validator.py`, `lookup_entry_namespaces`). For a namespace
   that is not `strict` the check cannot fail. Measured from the bulk
   response's own `timing`: a batch of 500 rows with term fields took
   4,118 ms, of which the timed stages account for about 130 ms; a batch
   without term fields took 56 ms. CASE-1022. (This replaces an earlier
   reading that blamed the inbound gate's terminology lookup during
   validation; all of validation took 60 ms for that batch.)
4. `wip-toolkit restore` follows the job for 3600 s, then prints the last
   snapshot and exits 0 while the job is still running. `backup` exits 1 on
   the same timeout. CASE-1023.

**The retest (2026-11-03), after CASE-1017 to CASE-1027.** Same archive,
the current (qualified) format, same runner on both machines: a fresh
restore into one pair of empty namespaces, then a definitions-only fresh
restore into a second pair followed by `POST /documents` in batches of 500
through Caddy, one client, no concurrency. Every run ended 222,026
documents latest, 0 errors, post-restore validation healthy.

| Machine, commit | Fresh restore | Live bulk create (definitions + documents) |
|---|---|---|
| Mac, `059b854a` (2026-11-02, above) | 12 min 55 s | 23 min 5 s |
| Mac, `01d21196` | 1 min 16 s (server job; 1 min 25 s client, upload included) | 5 min 28 s (19 s + 5 min 9 s) |
| Mac, `ba609931` (adds CASE-1026/1027) | not re-run; neither commit touches the restore path | 3 min 48 s (18 s + 3 min 30 s) |
| Pi, `4de37267` (2026-11-02, above) | 43 min 53 s | 72 min 51 s |
| Pi, `01d21196` | 6 min 22 s (client) | not used: code changed mid-run |
| Pi, `ba609931` | 5 min 49 s (server job; 6 min 18 s client) | 14 min 28 s (1 min 52 s + 12 min 36 s; 324 rows/s) |

What moved the live path, per 500-row batch on the Mac (the bulk
response's own `timing`): server total 602 → 225 ms; the stage-4 writes
280 → 51 ms (CASE-1026: one retire `update_many`, one `insert_many`);
the Registry register 262 → 99 ms (CASE-1027: ids minted from the
namespace config already read). Before those, CASE-1022 had taken a
term-bearing batch from 4.1 s to about 0.15 s (reference-gate lookups only
in strict namespaces, once per batch) and CASE-1024 the term-heavy
MDMS batches from about 0.95 s to 0.17 s (term values resolved once per
batch). CASE-1025, found on the way, is a guardrail fix rather than a
speed-up: the term inbound gate had never refused anything.

**The live path is now bound by reporting, by design.** Of the Mac
`ba609931` run's 206 s document phase, the server worked about 110 s; the
rest is document-store's backpressure throttle (`get_throttle_delay`:
50/100/500 ms after each write while reporting-sync has more than
1k/10k/50k events pending). A probe batch's client-minus-server gap was
115 ms during the load and 12–13 ms once reporting had caught up.

**The two numbers do not stop at the same point.** A fresh restore
reports done when its job completes; its reporting sync starts afterwards
(the job's own reporting verdict: "a fresh restore does not wait for
reporting"). The live figure includes the throttle, that is, it already
pays part of the reporting catch-up. Time until reporting holds the
restored namespace was not measured. Comparing server work only, restore
76 s against live about 110 s plus 18 s of definitions.

**Decision (Peter, 2026-11-03): M5 is closed, `compose` not built for
now.** Restore is still the faster path (live against the restore's
server job: Mac 3 times, Pi 2.5 times), but the gap that motivated compose is gone: minutes, not an hour,
for 222,026 documents, and about 1.7 times on server work on the Mac. Under decision 5 the bulk-load use case is served
by the live write path, and the merge variant is dropped. A legacy load
goes through `POST /documents` (or the CSV/XLSX import), validated by the
server's own rules, with no offline twin to keep in step. Removing `export`
with the next release is unaffected. Reopen compose if a load needs
offline shaping the live path cannot do, not for speed alone.

**Not yet decided** (proposed defaults, for the plan after the
measurement): input as CSV and JSONL rows; a per-template mapping file in
`migrate`'s format; references named by identity values, synonym or term
value and resolved offline against the archive; the hop recording the
input files and their digests.

## 7. Boundary policy (M3 preview, recorded here because inspect quantifies it)

When a selection cuts document-reference edges (plain reference
fields and relationship documents alike — one policy, edge types are
merely the guaranteed-same-namespace case):

- `strict` — refuse if anything crosses (for when you expected an island)
- `drop` — default; cut and report exactly what was cut
- `stubs` — include foreign endpoint documents one hop out, declaring
  their onward references dangle
- `closure` — follow until data-closed; prints its blast radius
  before proceeding (this can legitimately pull the whole archive,
  which is why the retired collector refused to follow document refs)

`inspect`'s deep dive shows the consequences of each policy for a
given selection — that is its filter-dry-run role.

### 7.1 The M3 plan (agreed 2026-11-01)

**The principle: shaping never edits a row it keeps.** A transform
selects which rows travel. It does not rewrite, null or re-anchor a
reference inside a row it keeps. When an entity is cut, its Registry row
moves from `registry_entries.jsonl` to `referents.jsonl`, the table in
which an archive records what it references but does not carry. On
restore, the door resolves each referent on the target by its portable
keys (`{ns, type, value}`, a document's template-qualified identity
synonym), never by its old id. When nothing or several entities match,
the reference keeps its archived id and the namespace validation job
flags it. This is the constraint from FIRESIDE-29: a reference crossing
a cut must still carry something the target can resolve. It also means:

- identity hashes cannot change, because no kept row changes;
- `verify` treats a reference to a referent as resolvable by design, as
  it already treats a term known only as a referent: a document cut
  alone verifies as `pass`, and the hop's `summary.referents` counts
  what was cut. (This plan first said "ends at `warn`"; that claim was
  not checked against `verify`, which never warned for referents.
  Corrected in M3a-1. M1's dangling check had not consulted the
  referents table at all; M3a-1 fixes that too.);
- a change to the rows themselves is surgery (M4), not shaping.

**Decisions (Peter, 2026-11-01).**

1. **`drop` keeps a cut reference and moves its target into the
   referents table.** Nulling the field was rejected: that edits rows,
   and it can break mandatory or identity fields. Dropping every
   document that holds a cut reference was rejected too: it cascades
   and can empty the selection.
2. **Relationship documents whose source is selected come along by
   default.** Closure follows outgoing references, and a relationship
   document references its source, so without this rule selecting
   MONSTER would leave its MONSTER→SPELL relationships behind.
   `/relationships` and `/traverse` are visible to apps, so losing them
   changes how an app behaves. Their targets then pass through the
   boundary policy like any other reference. `--no-relationships` opts
   out.
3. **The boundary policy covers documents only.** Schema closure is
   mandatory: every template version a kept document pins, its
   `extends` chain, and every terminology those templates draw from
   travel, and a terminology travels whole. Carrying only the terms in
   use would change how later writes validate. Definitions are small.
   References into namespaces the input archive itself does not carry
   (typically `wip`) stay as they are: already referents.
4. **"Remove the templates nothing uses" is its own verb, `prune`.** It
   answers the deletion question (in-degree zero), which is the opposite
   of `filter`'s extraction question (out-degree and closure). M1 keeps
   the two apart.
5. **The slice order below**, split into M3a and M3b with a check-in
   between them.
6. **`strip-blobs` cuts file rows to referents** (decided 2026-11-01,
   during M3a-5). A fresh restore of a file row whose blob is missing
   still inserts the file: a record that lists but has no content.
   Keeping metadata without bytes was rejected for that reason. Files
   are content-addressed (their Registry key is the checksum, within
   the namespace), so a cut file resolves to an install's own copy of
   the same bytes.
7. **`split` takes `--by namespace|island`** (decided 2026-11-01, before
   M3b), namespace the default. Each part is written through the
   transform core and verified with its own hop; a reference from one
   part into another becomes a referent, as under `filter --boundary
   drop`.
8. **`export-csv` writes one CSV per template** (decided 2026-11-01):
   `<dir>/<ns>/<TEMPLATE>.csv`, each document's latest version, columns
   `document_id`, `version`, `status`, then the data fields flattened by
   dot path; arrays as JSON text; a term field as its submitted value
   plus `<field>__term_id`. `--all-versions` writes every version row.

**Defaults (proposed by the implementer, not objected to).**

- **`stubs`:** an endpoint document one hop out travels with its pinned
  template version and that template's terminologies. Its own outgoing
  references become referents.
- **`closure`:** prints how much it would pull in before writing, and
  needs `--yes` when that is more than half the archive's documents.
  The threshold is a choice, not a measurement.
- **`latest-only`:** keeps every template version. A reference with
  `version_strategy: "pinned"` can name an older document version than
  the latest one; that version travels anyway, and the summary counts
  how many did. Slice 4 confirms how a pinned reference is stored
  before relying on this.
- **Output:** every transform takes `IN OUT`, and refuses to overwrite
  `OUT` without `--force`. The dry run is `inspect` on the plan (§1).
- **What leaves with a cut entity:** its synonym rows, and any blob only
  cut documents reference. The manifest's provenance rows (§8) travel
  unchanged.
- **Tests:** hand-built archives for each policy, and in every slice at
  least one transform of a real engine backup restored through the
  real engine.

**Slices, one commit each.**

| # | Slice |
|---|---|
| M3a-1 | Transform core in `wip-archive`: the input digest, appending the `derived_from` hop (§9.1), moving cut entities' rows into referents, the verify postcondition, writing the output. |
| M3a-2 | `filter`: selection by template, the four boundary policies, relationship documents with their source, mandatory schema closure, `--definitions-only`. |
| M3a-3 | `prune`: templates with in-degree zero, and the terminologies only they used. |
| M3a-4 | `latest-only`. |
| M3a-5 | `strip-blobs`. |
| M3a-6 | The thin `backup` verb, and `export`'s deprecation warning (§9.2). |
| — | Check-in with Peter. |
| M3b-1 | `diff`. |
| M3b-2 | `split`. |
| M3b-3 | `export-csv`. |

Each slice ships its wheel versions, golden re-captures and release
notes in the same commit.

### 7.2 M3a delivered (2026-11-01)

| Slice | Commit | What shipped |
|---|---|---|
| M3a-1 | `d6d4d29c` | `wip_archive.transform.run_transform`: kept rows copied verbatim, cut entities moved into `referents.jsonl`, the raw manifest kept with counts recomputed, one `derived_from` hop, verify before the final write. Fix: a reference to a recorded referent is no longer `dangling_document_reference`. wip-archive 0.12.0. |
| M3a-2 | `b1c80008` | `filter`: selection by template (id, value, `ns:VALUE`; an ambiguous bare value refused), relationships with their source, the four boundary policies, mandatory definitions closure, `--definitions-only`. WIP-Toolkit 0.11.0. |
| — | `03ec7420` | `inspect` and the model's name lookup refuse a bare value several namespaces carry (it picked the first). WIP-Toolkit 0.12.0. |
| M3a-3 | `929906a3` | `prune`: templates without documents that no schema of a template with documents needs, and the terminologies only they declared. WIP-Toolkit 0.13.0. |
| M3a-4 | `67c617e3` | `latest-only`: newest version per document, plus the versions `pinned` references name (the hard-delete guard treats `pinned` + `resolved.version` as a version reference). `include_all_versions: false`. WIP-Toolkit 0.14.0. |
| M3a-5 | `adaea2cf` | `strip-blobs`: file rows leave with their blobs; referenced files become referents, resolved by checksum (decision 6). WIP-Toolkit 0.15.0. |
| M3a-6 | `68d33295` | `backup`: the server engine's archive, followed and downloaded. `export` prints its deprecation warning (§9.2). WIP-Toolkit 0.16.0. |

**Measured on the real pbs-clintrial archive** (72 MB, 240,474 document
rows):

| Verb | Time | Peak memory | Verdict | What it did |
|---|---|---|---|---|
| `filter PCT_TRIAL_ELIGIBILITY` (drop) | 8.9 s | 231 MB | pass | 1,777 references cut to 1,777 referents |
| same, `--boundary stubs` | 8.9 s | 223 MB | pass | the 1,777 PCT_STUDY targets kept instead |
| same, `--boundary strict` | 5.7 s | 216 MB | — | refused, nothing written |
| `prune` | 17.8 s | 238 MB | pass | 3 templates without documents, 2 empty terminologies |
| `latest-only` | 17.4 s | 286 MB | pass | 18,448 older version rows dropped |

The archive holds one document-reference class and no files, so `closure`
and `strip-blobs` were measured only on seeded data.

**Every verb has a real-engine test** (`WIP-Toolkit/tests/integration/
test_transform_core.py`): a seeded namespace backed up by the engine,
transformed through the CLI, restored fresh by the engine, and the
restored result checked against what the verb promised.

**Known limits:**

- Dry run added after the M3a check-in: `--dry-run` on every transform
  verb (§1). wip-archive 0.18.0, WIP-Toolkit 0.17.0.
- The engine's `run_backup(latest_only=True)` still exists in-process and
  only stamps the manifest; the API refuses it and nothing calls it.
- Each verb loads the archive model twice (plan, then verify). Not
  measured how much of the time that is.

### 7.3 M3b delivered (2026-11-01)

| Slice | Commit | What shipped |
|---|---|---|
| — | `eecb525f` | `--dry-run` on every transform: `run_transform` on scratch space, verify included, only the final write skipped (§1). WIP-Toolkit 0.17.0. |
| M3b-1 | `d259d21f` | `diff`: rows added, removed and changed per namespace and entity type, the differing fields named for a sample; referents, blobs, both chains. Exit 0 / 1 / 2. WIP-Toolkit 0.18.0. |
| M3b-2 | `534b9241` | `split --by namespace|island` (decision 7): each part through the core, cross-part references as referents, an `unattached` part for terminologies no island uses and documents whose template is not carried. WIP-Toolkit 0.19.0. |
| M3b-3 | `883121be` | `export-csv` (decision 8): one CSV per template, latest versions, flattened, terms as value plus `__term_id`. WIP-Toolkit 0.20.0. |

**Measured on the real pbs-clintrial archive:**

| Verb | Time | Peak memory | Result |
|---|---|---|---|
| `diff` (archive vs its `latest-only` output) | 9.5 s | 97 MB | exactly the 18,448 removed version rows |
| `split --by namespace` | 20.3 s | 277 MB | 2 parts, 1,482 referents into the reference namespace, both pass |
| `split --by island` | 37.1 s | 279 MB | 9 islands + `unattached`, 240,474 documents across them, no referents, all pass |
| `export-csv` | 8.1 s | 218 MB | 12 files, 222,026 rows, 116 MB |

**Real-engine tests** for each: two backups around an update (`diff`
names exactly `status` and `updated_at` on the retired version); a
two-namespace backup split and one part restored fresh, its
cross-namespace reference resolving; a CSV row held field by field to the
live API's document, term id included.

**Known limits:** `split --by island` re-reads the input once per part.
`export-csv` writes no hop (it produces CSV files, not an archive).

## 8. Restore provenance (door-side requirement, tracked here)

Operators must be able to tell restored data from live-written data
(CASE-827#2: mandatory). Current state, verified: fresh/remap
restores append a prose note to the namespace description
("Restored <date> from an archive of '<source>' …"); id-preserving
restore writes nothing (byte-faithful by design); `BackupJob` records
are the instance-level log. Gaps: not machine-readable, no transform
chain, silent id-preserving mode.

**Built (2026-10-25):** namespace-granular, structured, written by
all three modes. Per-document stamping is rejected: it would break
restore mode's exact-copy contract. If per-document granularity ever
becomes real (post-merge mixed namespaces), it goes in a sidecar
collection keyed `(document_id, version) → restore job`, never in the
documents.

**Where records live.** Collection `restore_provenance` in
document-store's database, one row per (namespace, restore). Rows are
inserted, never updated, except that the restore's own validation job
id is filled in once that job starts. Namespace deletion removes them.
A field on the namespace record was rejected for three reasons:
- the Registry owns that record;
- appending to a list through the namespace `PUT` is a read-modify-write
  that can lose a record;
- the writer, the backup engine, lives in document-store.

**The record.**

| Field | Meaning |
|---|---|
| `namespace` | The namespace the row belongs to (queries and deletion use it). |
| `restored_into` | The namespace the restore wrote into. Equals `namespace` for the namespace's own restores; an inherited row keeps the name its restore happened under. |
| `restore_job_id` | The restore's `BackupJob`. |
| `mode` | `restore`, `remap` or `merge`. |
| `at`, `actor` | When, and the identity that started the job. |
| `archive` | `sha256` of the whole archive file (null when there is no file), `format_version`, `tool_version`. |
| `source` | The archived namespace, `host`, `exported_at`, and `install` (the manifest's `source_install`, verbatim). |
| `derived_from` | The manifest's chain, verbatim, oldest hop first (§9.1). A single object becomes a one-hop list. Empty for a full backup. |
| `merge` | Merge only: `on_clash`, `older_versions`, `add_missing`, `extend_terminologies`, and the merge plan's `definitions` and per-entity-type `entities` summaries. These are planned counts: under `on_clash: newer` the clashes actually written are a subset. |
| `validation_job_id` | The validation job this restore started for the namespace. CASE-837's reference re-resolution reports there. |
| `inherited` | True when the row arrived inside an archive. |

**How each mode words it.**
- **restore:** writes a record. The restored entities stay exact copies,
  because the record is a separate row.
- **remap:** writes a record and keeps the description sentence as its
  human-readable echo.
- **merge:** records what arrived and under which policy. The namespace
  is now mixed, so the record does not claim the namespace came from
  the archive.

**Travelling forward.** A backup puts the namespace's rows into its
manifest entry (`NamespaceEntry.provenance`, an optional field). The
field is additive, so `format_version` stays 3.1. A restore of that
archive inserts those rows first, marked `inherited`, then its own
record. A row the target already holds, matched by `(restore_job_id,
restored_into)`, is not copied twice.

**Read surface.** `GET /api/document-store/backup/provenance?namespace=<prefix>`
returns the history oldest first, paginated, and requires read
permission on the namespace. An empty list means no restore has
written into the namespace.

## 9. Open questions

1. ~~`derived_from` hop schema~~ — **settled 2026-10-25, frozen in §9.1.**
2. ~~The structured provenance record's home and schema~~ —
   **settled and built 2026-10-25, §8.**
3. ~~Migration mapping-file format~~ — **settled 2026-11-01, §6.3
   item 3** (constants, copy, format string; no hook in v1).
4. ~~Opt-in full-schema validation at the door~~ — **settled for now
   2026-11-01, §6.3 item 6** (no; revisited with `compose`).
5. ~~`--json` schema versioning policy~~ — **settled 2026-10-31, §9.2.**

### 9.2 Settled: verbs, gates, `--json`, bandwidth, `export` (2026-10-31)

Decided with Peter on CASE-827.

- **Verb naming: flat.** Every transform is a top-level verb beside
  `inspect` and `restore`: `filter`, `latest-only`, `strip-blobs`,
  `split`, `verify`, `diff`, and later the surgery and ramp verbs. No
  `transform` group, no per-class groups. The `transform` field of a
  `derived_from` hop is the verb's name.
- **The CLI's verify gate: refuse, with `--force`.** `wip-toolkit
  restore` runs `verify` on the archive before uploading it. An
  `error` finding refuses the upload, and `--force` sends it anyway.
  Warnings are printed, and the upload continues.
- **The door's policy for a failed verdict: refuse unless forced.**
  When the newest `derived_from` hop says `verdict: "fail"`, the
  engine refuses the restore unless the request explicitly accepts it
  (a restore option; name decided when built). `pass` and `warn`
  verdicts keep today's warning for any derived archive. This gives
  callers that bypass the toolkit, the REST API and MCP, the same
  guardrail as the CLI.
- **`--json` versioning: the `derived_from` rule.** Machine output
  carries `schema: "<major>.<minor>"`. Frozen fields keep their
  meaning; additions are new optional fields and bump the minor.
  Removing a field or changing its meaning bumps the major and gets a
  release note. Consumers check the major. One policy for every verb's
  JSON (`inspect`, `verify`, `diff`).
- **Bandwidth: keep the trade.** Selecting a slice means a full engine
  backup, downloaded, then filtered locally. The engine keeps its
  invariant that every engine archive is complete and restorable;
  `skip_files` stays the one transfer knob. A coarse engine-side
  filter is reconsidered only on a measured case where the download is
  the bottleneck.
- **`export`'s exit: warn, then remove.** The release that ships the
  thin `backup` verb makes `export` print a deprecation warning that
  names `backup` (and `filter`, once M3 has it). The next release
  removes `export`. At removal, the backup matrix's R-04 cell
  (CLI-produced archive → engine restore) moves to `backup` plus a
  transform.

### 9.1 Settled: the `derived_from` marker (frozen 2026-10-25)

Every archive a transform writes carries a `derived_from` key in its
`manifest.json`. An archive without the key is a full engine backup.
Decided with Peter on CASE-827: the digest covers the whole input
file, parameters are recorded verbatim, and a failed verify still
writes the archive.

```json
"derived_from": [
  {
    "transform": "filter",
    "toolkit_version": "0.7.0",
    "at": "2026-10-25T14:03:11Z",
    "params": {"templates": ["MONSTER"], "boundary": "drop"},
    "params_redacted": false,
    "input": {
      "sha256": "9f2c…",
      "format_version": "3.1",
      "derived": false
    },
    "verify": {
      "verdict": "pass",
      "findings": {"error": 0, "warning": 2, "info": 5}
    },
    "summary": {"documents_kept": 412, "documents_dropped": 9188}
  }
]
```

**The chain.**

- `derived_from` is a **list of hops, oldest first**.
- Transforming an archive that is already derived **appends** a hop;
  earlier hops are copied unchanged, never rewritten.
- Producers always write the list. Readers keep accepting a single
  object as a one-hop chain; `inspect` already does.

**The fields of a hop.**

| Field | Required | Meaning |
|---|---|---|
| `transform` | yes | Transform name, as the CLI verb names it. |
| `toolkit_version` | yes | Version of the toolkit that wrote the hop. |
| `at` | yes | UTC timestamp, ISO 8601 with `Z`. |
| `params` | yes | The transform's parameters, verbatim. |
| `params_redacted` | yes | `true` when a surgery transform whose parameters would reveal what it removed (redaction) records a redacted form instead. |
| `input.sha256` | yes | SHA-256 of the **whole input archive file** — definitive, at the cost of one extra streaming read of the input. |
| `input.format_version` | yes | The input manifest's `format_version`. |
| `input.derived` | yes | Whether the input itself carried a `derived_from` chain. |
| `verify.verdict` | yes | `pass` (no findings above `info`), `warn` (warnings but no errors) or `fail` (at least one error). |
| `verify.findings` | yes | Count of findings per severity (`error` / `warning` / `info`) — the same severities as `inspect`'s findings. |
| `summary` | no | Per-transform counts of what was kept, dropped or changed. |

**A failed verify.**

- Every transform verifies its own output (§6).
- On `fail`, the archive is **still written**, the hop records
  `verdict: "fail"`, and the command exits non-zero.
- The archive thereby declares itself broken. The restore door refuses
  it unless the request explicitly accepts a failed verdict (§9.2); it
  warns on every other derived archive.
- Repairing a broken archive is a separate function, defined later,
  outside the transforms.

**Evolution.**

- The fields above are frozen.
- Additions are allowed only as new optional fields. Readers must
  tolerate keys they do not know: the restore door only checks that the
  marker is present, and `inspect` displays every hop key it finds.
- Changing a frozen field's meaning would need a new key, not a
  reinterpretation.
- The engine's restore copies the chain into the namespace's
  provenance record (§8), so lineage survives a restore and travels
  into later backups.

## 10. Non-goals

- Live-instance analysis of any kind (the collector stays dead).
- Toolkit-native encryption/compression (wrap the file with gpg/zstd).
- Offline identity re-minting (engine `fresh` mode owns identity).
- Archive concatenation (the engine's multi-namespace backup covers it).
