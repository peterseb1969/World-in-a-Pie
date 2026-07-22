# Backup/Restore Test Matrix — all modes, all seams

**Status:** test design, awaiting test-data decision (Peter)
**Scope:** every backup producer × every restore mode × the seams between
them, with named tests and their assertion planes. Test DATA (the concrete
seed entities) is deliberately abstract here — §3 states what each fixture
must contain; the concrete domain is Peter's call.

## 1. Why a matrix, and what it must prevent

The recent case chain is the argument. Every one of these shipped through
green suites because a *combination* was never exercised, not because a
unit was wrong:

- CLI-export → engine-restore produced unresolvable namespaces for the
  format's whole life (CASE-756) — both sides unit-green, seam never run.
- Array-of-reference payloads survived fresh restore with source ids
  (CASE-746) — the sweep fixture lacked the shape.
- N:1 collapse **dry runs** refused what apply allowed (dry-run
  placeholder collision) — the dry-run × N:1 cell was empty.
- Restore jobs recorded the wrong write targets (CASE-745) and clobbered
  their own fields (CASE-747/749/750) — data plane checked, job plane not.
- The exporter silently dropped every document id from its registry pass
  since the v3 layout — a count assert one seam later caught it.

Principles the matrix encodes:

1. **Producer × consumer cells, not per-side tests.** An archive format
   has two writers (engine, CLI) and one reader family (engine modes).
   Every writer × reader cell runs at least once.
2. **Every test asserts on every applicable PLANE (§4)** — data, registry,
   reporting, job record, automation, leak sweep, files. "The documents
   are back" is one plane of seven.
3. **Dry-run/apply parity is a first-class property.** For each mode ×
   topology: the dry run must refuse exactly what apply refuses and
   predict exactly what apply does. Tested as PAIRS.
4. **Refusals are tests, not error handling.** Every documented refusal
   has a test that proves it fires, fires EARLY (nothing half-created),
   and names its remediation.
5. **Counts are conserved, loudly.** Seed → archive → restore counts must
   match a declared expectation per entity class; a silent drop of any
   class is the CASE-666 failure shape.

## 2. The dimensions

### D1 — Producer (where the archive comes from)

| ID | Producer | Notes |
|---|---|---|
| P-SRV1 | Server backup, single namespace | `POST …/backup` / `start_backup` |
| P-SRVN | Server backup, `namespaces: [a, b]` | multi-namespace archive |
| P-SRVALL | Server backup, `all_namespaces: true` | includes `wip` |
| P-CLI | CLI export (`wip-toolkit export`) | 1-namespace v3; carries registry rows since CASE-756 |
| P-CONV | v2.0 flat archive → `convert_archive` | legacy pathway |
| P-BAD | Malformed: no manifest / v2.0 unconverted / unknown entity file / truncated zip | refusal family |

Producer options that multiply cells (exercise each at least once, on the
producer where it exists): `include_files`, `include_inactive`,
`skip_documents`, `latest_only`, `template_prefixes`, CLI
`skip_closure`/`skip_synonyms`, backup `dry_run`.

### D2 — Restore mode × topology

| ID | Mode | Topology |
|---|---|---|
| R-ID | `restore` (id-preserving) | into empty same-name namespace(s), same instance |
| R-ID-X | `restore` | into empty namespace on a DIFFERENT instance (the DR scenario — the only true "restore" use) |
| R-FR1 | `fresh` single (`target_namespace`) | beside the live original |
| R-FRN | `fresh` multi, 1:1 `namespace_map` | beside the live originals |
| R-FRC | `fresh` multi, N:1 collapse | two+ sources → one target |
| R-MRG | `merge` | into a NON-empty namespace |
| R-MRG-T | `merge` with `target_namespace` redirect | CASE-748 surface |
| R-JOB | restore-from-retained-job (`POST /backup/jobs/{id}/restore`) | no re-upload; restore/merge only |

Restore options that multiply cells: `dry_run` (paired with every R-*),
`skip_documents`, `skip_files`, `batch_size` (1, default, max — boundary),
`on_clash` (skip/overwrite/newer — merge only), `add_missing`,
`extend_terminologies` (merge only), `drop_stale_reporting`,
`continue_on_error`, `register_synonyms`.

### D3 — Content classes the fixtures must span (→ §3)

E1 terminology + terms + term ALIASES · E2 ontology term-relations ·
E3 entity template with MULTIPLE ACTIVE VERSIONS (and one inactive
version) · E4 edge type (`usage: relationship`, `versioned: false`) ·
E5 relationship documents · E6 multi-version documents (same identity,
2+ versions) · E7 identity-less (append-only) template + documents —
empty registry keys, dedup opt-out · E8 documents with scalar refs,
ARRAY-of-refs, and refs to BOTH an in-archive namespace and an
out-of-archive one (`wip` term) · E9 files: metadata + blobs, and a
document file-reference · E10 custom registry synonyms (beyond
auto-synonyms) · E11 a namespace with PREFIXED sequential `id_config` ·
E12 inactive entities of each type · E13 `full_text_indexed` string field
(reporting FTS) · E14 `metadata.custom` payloads · E15 namespace-config
variants: `isolation_mode: strict` + `allowed_external_refs`,
`deletion_mode: full`.

## 3. What the test data must contain (Peter's decision input)

Two fixture namespaces (call them **NS-A**, **NS-B**) plus the ambient
`wip` namespace, sized small enough to restore in seconds (the kb+library
archive proved ~5 s is achievable at 4.6k docs; the fixture can be far
smaller — tens of documents):

- **NS-A** (the rich one): E1, E2, E3, E6, E7, E8 (with an array-of-refs
  field pointing at NS-B documents AND a term ref into `wip`), E9, E10,
  E12, E13, E14.
- **NS-B** (the counterpart): E4, E5 (edges between NS-B docs), a
  same-VALUED terminology as one in NS-A (drives the N:1 collision
  refusal) plus a differently-valued one (drives the N:1 success), E11
  as its id_config.
- **NS-A ↔ NS-B**: at least one document reference each way (the
  cross-source rewrite tests), one template in NS-B used by an NS-A
  document (cross-source template pin).
- A declared **EXPECTED_COUNTS table per entity class per namespace** —
  the conservation assert every archive/restore test reuses.

The seed must be buildable through public APIs only (idempotent bootstrap
style), so every layer can rebuild it — no Mongo hand-writes.

## 4. Assertion planes

Every E2E test states which planes it asserts; a test that touches a plane
must assert it (silence ≠ pass):

| Plane | What is checked |
|---|---|
| PL-DATA | Entities via service APIs: presence, versions, field fidelity (incl. E14 metadata, E3 version pinning, E4 flags: `usage`, `versioned`, identity_fields) |
| PL-REG | Resolution: canonical id, value-form, custom synonym each resolve; composite-key CLAIMS recreated (a re-register of a restored key must NOT mint a duplicate); prefixed id_config continues its sequence |
| PL-REP | Reporting tables exist and count-match; per-version tables; FTS column live (query it); no fossil schema |
| PL-JOB | The job record: `namespaces` = real write targets, `options` faithful, `result` populated (incl. dry-run planned counts), `validation_job_ids`, archive fields — and no field clobbered by a concurrent writer (CASE-747/749/750 family) |
| PL-AUTO | The automation the job drives: validation jobs scoped to the written namespaces, healthy; reporting sync hit the right schemas |
| PL-LEAK | Fresh only: serialized restored rows contain NO source-namespace ids or names anywhere (the CASE-743/746 sweep, with E8's array shape in the fixture by construction) |
| PL-FILE | Blob bytes round-trip; file references resolve; `skip_files` leaves metadata but no blobs |

## 5. The named tests

Layer key: **U** unit (no services) · **C** component/in-process
(document-store suite or toolkit harness) · **L** live stack (scripted
against a dev install; the layer where CASE-745/747 class bugs live).

### 5.1 Producer tests

| ID | Layer | Test | Planes |
|---|---|---|---|
| B-01 | C | P-SRV1 of NS-A: archive counts == EXPECTED_COUNTS, all entity classes present incl. registry_entries | counts |
| B-02 | C | P-SRVN of NS-A+NS-B: per-namespace subtrees, per-ns counts, blobs flat | counts |
| B-03 | L | P-SRVALL: includes `wip`; admin required on every namespace (a partial-grant key is refused) | counts, perm |
| B-04 | C | P-CLI of NS-A: archive equivalent to B-01's for every entity class (the producer-parity assert — diff the two archives' class counts) | counts |
| B-05 | C | `include_inactive` off/on: E12 entities absent/present | counts |
| B-06 | C | `latest_only`: E6 docs carry 1 version; full: all versions | counts |
| B-07 | C | `skip_documents`, `template_prefixes`, CLI `skip_synonyms`/`skip_closure`: each drops exactly its class, LOUDLY (manifest counts reflect it) | counts |
| B-08 | C | backup `dry_run`: counts predicted, nothing written | PL-JOB |
| B-09 | U | P-BAD family: no manifest / v2.0 / truncated → typed refusals; v2.0 message names `convert_archive` | refusal |
| B-10 | U | P-CONV: convert v2.0 → v3, reader parity; already-v3 refused | counts |

### 5.2 Restore-mode cells (each is a dry-run/apply PAIR)

| ID | Layer | Cell | Planes |
|---|---|---|---|
| R-01 | C+L | P-SRV1 × R-ID into empty ns: full fidelity | ALL |
| R-02 | L | P-SRVN × R-ID both namespaces; cross-ns refs (E8) intact | ALL |
| R-03 | L | P-SRV1 × R-ID-X onto a second instance (or wiped stack): the DR drill — ids identical, PL-REG claims, PL-REP rebuilt | ALL |
| R-04 | C+L | P-CLI × R-ID: **the CASE-756 seam** — resolution fidelity from a CLI archive | ALL |
| R-05 | C+L | P-SRV1 × R-FR1 beside the live original: both copies fully functional; PL-LEAK on the copy; original untouched (diff before/after) | ALL |
| R-06 | L | P-SRVN × R-FRN: cross-source refs follow the map (E8 both directions, cross-source template pin) | ALL |
| R-07 | C+L | P-SRVN × R-FRC (collapse): disjoint content lands; same-valued terminology REFUSED at plan time with both sources named; dry-run refuses the SAME cell (the placeholder-uniqueness regression pin) | ALL + refusal |
| R-08 | C+L | P-SRV1 × R-MRG into NS-A-with-drift: identical defs unchanged, `add_missing` off→refusal on new template / on→added; `extend_terminologies` analog | PL-DATA, PL-JOB |
| R-09 | C | R-MRG `on_clash` triple: same doc identity changed on both sides → skip keeps target / overwrite takes archive / newer compares updated_at (fixture needs one older + one newer archive copy) | PL-DATA |
| R-10 | C | R-MRG-T redirect (CASE-748): merge lands in the named target; job record says so | PL-DATA, PL-JOB |
| R-11 | L | R-JOB: restore from a retained backup job without re-upload; job independence (deleting either job keeps the other's archive) | PL-JOB |
| R-12 | C | E7 identity-less docs through R-ID, R-FR1, R-MRG: every row restored (no dedup collapse), still un-PATCHable, N:1 empty-key exemption | PL-DATA, PL-REG |
| R-13 | C | E4/E5 edge types through R-FR1: endpoints re-pointed, `versioned:false` overwrite still works POST-restore (write to the restored edge) | PL-DATA |
| R-14 | L | E9 blobs through R-ID and R-FR1 with `include_files`; `skip_files` leaves references resolvable-but-blobless, LOUD in job warnings | PL-FILE, PL-JOB |
| R-15 | C | E11 prefixed id_config through R-FR1: re-minted ids follow the TARGET's config; next live mint continues without collision | PL-REG |
| R-16 | L | E13 FTS + PL-REP full pass after R-FR1: reporting search returns restored docs | PL-REP |

### 5.3 Refusals & failure injection

| ID | Layer | Test |
|---|---|---|
| F-01 | C | R-ID into non-empty target: refused naming the collections; NOTHING created (the CASE-668 shape) |
| F-02 | C | R-ID with `target_namespace` ≠ archive ns: "cannot re-namespace" refusal |
| F-03 | C | R-FRN map errors: unmapped ns / stranger ns / empty target / multi-ns without map — each refusal names the problem |
| F-04 | C | Stale reporting schema present: refused without `drop_stale_reporting`, proceeds with it |
| F-05 | L | Kill the engine mid-fresh-restore (or fault-inject provisioning): reserved entries do NOT resolve; target invisible; a re-run converges (the crash-behaviour promise in remap_restore's docstring) |
| F-06 | L | Restore with reporting-sync stopped: completes with warnings, never fails (operator ruling); PL-REP backfills after `force` rebuild (CASE-738) |
| F-07 | C | Permission: non-admin key refused per namespace on backup, restore, download — per-item/early, nothing partial |
| F-08 | C | `continue_on_error`: per-item failures collected, job completes with warnings; without it, first failure aborts |

### 5.4 Cross-cutting invariant sweeps

| ID | Layer | Test |
|---|---|---|
| X-01 | C | **Dry-run parity harness**: for every R-* pair above, dry-run's `planned`/refusal == apply's outcome/refusal. One parametrized runner, not per-test copies |
| X-02 | C | **Counts conservation harness**: seed → archive → restore → API counts, one table, reused by every cell |
| X-03 | C | **Leak sweep harness**: serialized restored rows × forbidden-token list (source ids, source ns names), reused by every fresh cell |
| X-04 | L | **Job-plane sweep**: after any L-layer run, assert the job record against a schema of field ownership (no nulled result, no clobbered back-links — the CASE-747/749/750 pin at the E2E level) |
| X-05 | L | **Double-restore idempotence**: R-ID re-run over its own completed target refuses (non-empty); R-MRG re-run of the same archive = all `unchanged`/`skip`, zero new versions |
| X-06 | L | **Backup-of-a-restore**: P-SRV1 of a freshly restored namespace, restore THAT — fidelity is transitive (catches anything the first restore quietly mangled) |

## 6. Existing coverage this maps onto (verified by suite, not by test)

Unit/component suites already present: `test_backup_engine` (47),
`test_merge_restore` (58), `test_merge_definitions`, `test_merge_plan`,
`test_remap_restore` (20), `test_remap_multi` (11), `test_remap_integration`
(5), `test_backup_service` (31), `test_backup_api` (31),
`test_case_689_restore_phases`, `test_backup_job_model`, and the toolkit
golden round-trip (P-CLI × R-ID at layer C). Implementation starts with a
mapping pass: mark each matrix cell already covered by a named existing
test, and build ONLY the gaps. The known-empty cells today: everything in
layer **L** as a scripted suite (the session's live checks were manual),
R-03 (cross-instance DR), R-09 `newer`, R-11, R-15, X-01 as a harness,
X-04, X-05, X-06.

## 7. Open decisions for Peter (besides the test data)

1. **Layer L home**: a `scripts/` E2E runner against the attached dev
   install (fast to build, uses the real stack) vs a compose-provisioned
   ephemeral stack in CI (hermetic, slower). Recommendation: start as a
   script against the dev install with guarded cleanup
   (`deletion_mode=full` targets, `00…`-prefixed namespaces), promote to
   CI later.
2. **R-03 (cross-instance)**: needs either a second install on this
   machine (`prod-test` exists) or a wipe-and-restore drill on a
   throwaway install. Which is acceptable to script against?
3. Whether `register_synonyms` (old→new id synonyms on fresh restore) is
   a supported surface to pin or a candidate for removal — it appears in
   job options today; the matrix pins whatever the ruling is.
