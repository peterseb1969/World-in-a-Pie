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

Direction (to be specified with the door team / a follow-up case):
**namespace-granular, structured, written by all three modes** —
restore job id, mode, archive manifest digest, and the full
`derived_from` chain with per-hop verify verdicts. Per-document
stamping is rejected: it would break restore mode's exact-copy
contract; if per-document granularity ever becomes real (post-merge
mixed namespaces), it goes in a sidecar collection keyed
`(document_id, version) → restore job`, never in the documents.

## 9. Open questions

1. `derived_from` hop schema (transform name, parameters, verify
   verdict, input manifest digest) — freeze before M3 writes the
   first hop; the door already tolerantly surfaces the key (v2.1.0).
2. The structured provenance record's home and schema (§8) —
   engine-side work, likely its own case.
3. Migration mapping-file format (M4): constants + copy-from-field +
   simple templates, escape hatch as jq-style/Python hook; no bespoke
   expression language.
4. Whether the restore door grows an opt-in full-schema validation
   mode for composed archives, or offline compose-validation plus
   post-restore integrity remains the contract.
5. `--json` schema versioning policy (frozen fields vs additive).

## 10. Non-goals

- Live-instance analysis of any kind (the collector stays dead).
- Toolkit-native encryption/compression (wrap the file with gpg/zstd).
- Offline identity re-minting (engine `fresh` mode owns identity).
- Archive concatenation (the engine's multi-namespace backup covers it).
