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
  drift.

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
| **M1** | `ArchiveModel` (index, edges, closures, islands, stats, findings) + grown `inspect` (summary, deep dive, `--json`) | nothing new beyond wip-archive |
| **M2** | offline validate/recompute library; `verify` verb (findings → exit code); `inspect --target-version` migratability | identity-hash + term-resolution logic shared with wip-auth's canonical implementations |
| **M3** | shaping transforms (`filter` with boundary policy `strict/drop/stubs/closure`, definitions-only, strip-blobs, latest-only, split), `export-csv` off-ramp, thin `backup`/`restore` verbs, `diff` | M1 model; M2 for filter's auto-verify postcondition |
| **M4+** | surgery (fill-value migration via mapping file, version squash, redaction design), `compose` on-ramp, `export` deprecation | M2 library; separate design iterations |

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
| 6 | wip-archive 0.7.0, WIP-Toolkit 0.8.0, golden re-capture, release notes. |

Every slice carries hand-built-archive tests, at least one assertion
against a real engine backup, and, from slice 2 on, comparison tests
against the live validator.

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
3. Migration mapping-file format (M4): constants + copy-from-field +
   simple templates, escape hatch as jq-style/Python hook; no bespoke
   expression language.
4. Whether the restore door grows an opt-in full-schema validation
   mode for composed archives, or offline compose-validation plus
   post-restore integrity remains the contract.
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
