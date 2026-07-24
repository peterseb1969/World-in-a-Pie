# Cell coverage mapping — CASE-773 Phase 2

Per the design doc §6: *"Implementation starts with a mapping pass: mark each
matrix cell already covered by a named existing test, and build ONLY the gaps."*
This is that pass. Each §5 cell is marked **COVERED** / **PARTIAL** / **GAP**
with the named evidence, established by reading every existing backup/restore
suite (doc-store `test_backup_*` / `test_merge_*` / `test_remap_*` /
`test_case_689_restore_phases`, `reporting-sync` restore tests, `wip-archive`
`test_archive`/`test_remap`, toolkit `test_exporter` + `test_round_trip` +
`test_convert_archive`).

Layer key (§5): **U** unit (mocked) · **C** component (in-process app + real
Mongo/Registry) · **L** live stack (the §7 runner — none exist yet as a scripted
suite).

Three design-doc reconciliations surfaced; they are listed at the end — read
them first, they change how several cells are interpreted.

## 5.1 Producer cells

| Cell | Status | Evidence / gap |
|---|---|---|
| B-01 P-SRV1 counts == EXPECTED_COUNTS, all classes incl registry_entries | **PARTIAL → L-GAP** | Structure asserted (`test_backup_engine::TestModuleStructure::*`, `test_backup_entity_order_covers_registry_entries`, `TestPreCount::test_returns_count_per_entity_type`) but **ArchiveWriter is mocked in every engine test** — no real archive is counted against a real seed. The real-count assertion is a Phase-3 L cell against the Phase-1 fixture's EXPECTED_COUNTS. |
| B-02 P-SRVN multi-ns subtrees/counts | **PARTIAL → L-GAP** | `test_backup_engine::TestRunBackupMultiNamespace::test_two_namespaces_manifest` asserts per-ns subtrees + `namespace_prefixes()`, but counts are all-zero (empty mock). Real per-ns counts = L. `wip-archive::TestMultiNamespaceArchive` covers the archive layout. |
| B-03 P-SRVALL all_namespaces incl wip; partial-grant refused | **GAP (L)** | No test sets `all_namespaces`; no admin-on-every-ns enforcement test. Runner cell, gated behind `--allow-instance-wide`. |
| B-04 P-CLI parity vs B-01 (diff class counts) | **PARTIAL** | `test_round_trip::test_golden_round_trip` does CLI export + **archive-count parity vs seed** (CASE-666). The *cross-producer diff* (CLI archive vs server archive, same seed) is not done → small L/C gap. |
| B-05 include_inactive off/on (E12 absent/present) | **COVERED (CLI) / see R1** | CLI: `test_exporter` exercises the export path; `test_round_trip` exports with `include_inactive`. **Server backup rejects `include_inactive`** (see Reconciliation R1) — so this cell is CLI-producer only. |
| B-06 latest_only (E6 1 version vs all) | **GAP / see R1** | `wip-archive::test_include_all_versions_manifest_field` covers the manifest flag. **Server backup rejects `latest_only`** (R1). CLI all-versions behaviour otherwise unexercised end-to-end → L. |
| B-07 skip_documents / template_prefixes / CLI skip_synonyms / skip_closure — each drops its class loudly | **MOSTLY COVERED / see R1** | skip_documents: `test_backup_engine::TestPreCount::test_skip_documents_zeros_doc_count_without_querying` + `test_skip_documents_omits_documents_phase`; CLI skip_synonyms/skip_closure/skip_documents/dry_run: `test_exporter::{test_skip_synonyms_no_registry_lookup, test_skip_closure_not_called, test_closure_called_by_default, test_skip_documents_no_docs_fetched, test_dry_run_still_fetches_entities}`. **`template_prefixes` is rejected on server backup** (R1) → not a supported drop. |
| ~~B-08 backup dry_run~~ | **DROPPED (Peter's ruling, CASE-782)** | Backup has no dry-run and won't get one: the server backup is a full copy (CASE-768), so predicted counts are just the namespace-stats read, honest size prediction needs reading the payload, and the write is non-destructive. Cell retired from the matrix; the design-doc §2 D1 + §5.1 edit is the sibling's `b782a13c` (pending push). CLI dry-run remains real: `test_exporter::test_dry_run_still_fetches_entities`. |
| B-09 P-BAD malformed family (no manifest / v2.0 unconverted / unknown entity / truncated zip → typed refusals) | **DONE (CASE-783 shape 1+2)** | wip-archive now raises typed `ArchiveError` subclasses (`NotAnArchiveError` / `MissingManifestError` / `ManifestParseError`), and the restore route 400s synchronously at upload — no doomed job (`test_archive::TestMalformedArchive`, `test_backup_api::test_restore_refuses_*`). v2.0-names-convert already worked (route version gate). Unknown/missing entity file is by-design (writer omits empty members). **Residual → runner:** the honest partial-damage check (some members lost after a valid write) is a manifest-count vs streamed-count cross-check — folded into the layer-L runner below (X-02), not a leftover on 783. |
| B-10 P-CONV convert v2.0→v3, already-v3 refused | **COVERED (U)** | `test_convert_archive::{test_convert_v2_to_v3, test_convert_rejects_already_v3}`. |

## 5.2 Restore-mode cells (dry-run/apply pairs)

| Cell | Status | Evidence / gap |
|---|---|---|
| R-01 P-SRV1 × R-ID into empty ns, full fidelity | **COVERED (C) → L BUILT** | `test_round_trip::test_golden_round_trip` (C). L: `run_matrix.py` R-01 (slice 2) — drop NS-B, `mode=restore` back; asserts every document id preserved verbatim, conserved counts match the pre-drop namespace, value-form resolution intact. |
| R-02 P-SRVN × R-ID both ns, cross-ns refs (E8) intact | **GAP (L)** | No multi-namespace real restore with cross-ns refs. |
| R-03 P-SRV1 × R-ID-X cross-instance DR | **GAP (L)** | Needs `--dr-install`; §6 confirms empty. |
| R-04 P-CLI × R-ID — the CASE-756 seam | **COVERED (C) → L** | `test_round_trip::test_golden_round_trip` IS this seam (CLI export → engine restore, resolution fidelity without caches, CASE-665/756). L version = Phase 3. |
| R-05 P-SRV1 × R-FR1 beside live original; LEAK on copy; original untouched | **PARTIAL → L-GAP** | Copy lands active in TARGET: `test_remap_integration::{test_ids_come_from_the_registry_and_are_active, test_documents_land_under_new_ids...}`. LEAK sweep: `test_remap_multi::test_reference_snapshots_carry_no_trace_of_the_source`. **"Original untouched" is never asserted** (source is a mocked reader). L cell + original-untouched diff. |
| R-06 P-SRVN × R-FRN cross-source refs both ways + template pin | **PARTIAL → L-GAP** | Cross-source template pin + one ref direction: `test_remap_multi::test_cross_source_template_pin_follows_the_new_id`. **Both directions** and real-stack = L. |
| R-07 P-SRVN × R-FRC collapse; same-valued **terminology** refused naming both sources; dry-run refuses the SAME cell | **PARTIAL → GAP** | Disjoint lands + N:1 refusal naming both sources: `test_remap_multi::{test_n_to_one_disjoint_content_lands_in_one_target, test_n_to_one_key_collision_refuses}` — but the collision is on **templates**, not terminologies, and `test_dry_run_placeholders_unique_across_sources` asserts the *opposite* (avoiding a false refusal). **DONE (C):** `test_remap_multi::test_n_to_one_terminology_collision_refuses` — two same-valued `MATRIX_STATUS` terminologies into one target refuse (apply path AND dry-run, since `_check_target_collisions` runs before provisioning). L real-stack cell remains for Phase 3. |
| R-08 P-SRV1 × R-MRG into drift; add_missing off/on; extend_terminologies | **COVERED (C) → L BUILT** | C: `test_merge_restore::TestDefinitionsPrecondition::*`, `test_merge_definitions::TestOptInStrategies::*`. L: `run_matrix.py` R-08 (slice 2) — hard-delete a document, `mode=merge`; the drifted-away document is re-inserted (on_clash=skip). add_missing/extend_terminologies variants still C-only. |
| R-09 R-MRG on_clash triple (skip / overwrite / newer) | **COVERED (C)** ← §6 was stale | `test_merge_restore::TestDocumentClashPolicy::*` (skip, overwrite, overwrite-in-place on versioned:false, adopts target id) and the whole `TestNewerPolicy` class (newer taken / older left / tie / naive-utc / unparseable-warns / missing / report-counts). **§6 lists R-09 `newer` as known-empty — it is in fact fully covered.** |
| R-10 R-MRG-T redirect (CASE-748), job record says so | **PARTIAL** | `test_merge_restore::TestMergeIntoADifferentNamespace::*` covers data landing in the target + rescoped/rehashed keys + id-collision refusals — **DONE (C):** `test_merge_restore::test_an_explicit_target_redirect_is_recorded_on_the_job` drives an explicit `target_namespace` differing from the archive manifest and asserts the job result records the redirect. Closing it added one production line — the merge result now records `source_namespace` per target (parity with remap). L cell remains for Phase 3. |
| R-11 R-JOB restore from a retained job, no re-upload; job independence | **GAP (L)** | Download/delete of retained archives covered (`test_backup_api::test_download_*`, `test_delete_*`) but not restore-from-retained-job. |
| R-12 E7 identity-less through R-ID/R-FR1/R-MRG, un-PATCHable, N:1 empty-key exemption | **PARTIAL → GAP** | Identity-less through remap: `test_remap_restore::{test_an_identity_less_document_gets_an_empty_key, test_distinct_documents_still_get_distinct_ids}` + `test_remap_integration::test_documents_land_under_new_ids...`; through merge-plan: `test_merge_plan::test_identity_less_documents_match_by_document_id_only`. **"still un-PATCHable after restore" DONE (C):** `test_remap_integration::test_an_identity_less_document_stays_append_only_after_restore` — restore preserves empty identity_fields + empty identity_hash (the exact conditions the append_only guard keys on); the rejection is pinned by `test_documents_patch::test_patch_no_identity_template_rejected_append_only`. Full R-ID/R-MRG end-to-end at L = Phase 3. |
| R-13 E4/E5 edge types through R-FR1; endpoints re-pointed; versioned:false overwrite post-restore | **COVERED (C) → L** | Component: `test_remap_integration::test_edge_type_endpoints_follow_the_restore_and_stay_addressable` — a real edge type (usage=relationship, versioned=false, identity_fields=[source_ref,target_ref]) fresh-restored: BOTH endpoints re-pointed to the restored docs' NEW ids, identity_hash recomputed over the pair, and the Registry claim carries it so a later write dedups (the re-addressability overwrite-in-place depends on). Live-validated on prod-test 20260724a via `probe_backup_restore.py` with NO `--drop-source`: version=1, count=1, no fork. L real-stack cell = Phase 3. |
| R-14 E9 blobs through R-ID/R-FR1 with include_files; skip_files loud | **GAP (L)** | Merge uploads blobs for *inserted* files only (`test_merge_restore::test_only_inserted_files_get_their_blobs_uploaded`); round-trip excludes files (no MinIO). Full blob round-trip + skip_files = L. |
| R-15 E11 prefixed id_config through R-FR1; re-minted follow TARGET config; next mint no collision | **FIXED (CASE-784) → L guard** | CASE-784 fixed (`e32d8c3f`, `preserve_id_config`): a fresh restore's target defaults to UUID7 instead of cloning the source prefix. Component regression: `test_backup_engine` (preserve_id_config). Live-validated on prod-test 20260724a — fresh-restore-beside-original completes, target id_config is UUID7 (prefix:null), no `entry_id` collision, no prefix leak. Build the L-cell as the real-stack guard in Phase 3. |
| R-16 E13 FTS + PL-REP full pass after R-FR1 | **GAP (L)** | Reporting/FTS after fresh restore not exercised (reporting is `None`/stubbed in merge+remap suites). L cell. |

## 5.3 Refusals & failure injection

| Cell | Status | Evidence / gap |
|---|---|---|
| F-01 R-ID into non-empty target refused, nothing created | **COVERED (C)** | `test_backup_engine::TestRunRestoreBasicFlow::test_restore_refuses_non_empty_namespace`, `test_merge_restore::TestRemapRestore::test_a_non_empty_target_is_refused`, round-trip phase 7. |
| F-02 R-ID target_namespace ≠ archive ns → "cannot re-namespace" | **COVERED (C)** | `test_backup_engine::TestRestoreRedirectGuard::*`, `test_merge_restore::test_a_plain_restore_still_refuses_to_re_namespace`, round-trip phase 7. |
| F-03 R-FRN map errors (unmapped / stranger / empty / multi-no-map) | **COVERED (U)** | `test_remap_multi::TestResolveRemapMapping::*` — all four, each names its problem. |
| F-04 stale reporting schema refused w/o drop_stale_reporting, proceeds with it | **COVERED (U)** | `test_case_689_restore_phases::TestPrecondition::{test_stale_schema_refuses_without_flag, test_stale_schema_drops_with_flag, test_failed_drop_refuses}`; merge rejects the flag: `test_backup_api::test_merge_rejects_drop_stale_reporting`. |
| F-05 kill engine mid-fresh-restore; reserved don't resolve; re-run converges | **GAP (L)** | Only end-state asserted (`test_remap_integration::test_ids_come_from_the_registry_and_are_active`); the docstring crash promise is never exercised by an interrupted run. |
| F-06 restore with reporting-sync stopped completes with warnings; PL-REP backfills after force | **PARTIAL → L-GAP** | Unreachable-reporting-warns: `test_case_689_restore_phases::TestPrecondition::test_unreachable_reporting_warns_and_disables`. Force-backfill (CASE-738) after a real restore = L. |
| F-07 permission: non-admin key refused per ns on backup / restore / download, nothing partial | **COVERED (C)** | `test_backup_api::test_non_admin_key_is_refused_on_backup_restore_and_download` — a non-admin key (scoped to `scoped-test-ns`, `none` on `wip`) gets 404 on all three doors; the two write doors mint no job (auth precedes job creation / archive read). |
| F-08 continue_on_error tombstone → loud 400 | **COVERED (C)** | `test_backup_api::test_restore_rejects_toolkit_era_params[continue_on_error]`. |

## 5.4 Cross-cutting invariant sweeps (harnesses)

| Cell | Status | Evidence / gap |
|---|---|---|
| X-01 dry-run parity harness — one parametrized runner over every R-* pair | **BUILT (L, fresh) / C per-mode** | `run_matrix.py` X-01 (slice 2): a fresh `dry_run` writes nothing, the apply then produces the source's conserved counts (plan-implied == outcome). Per-mode C parity: `test_merge_restore::TestMergeDryRun::*`, remap `test_a_dry_run_provisions_nothing_and_writes_nothing`. Generalizing X-01 over every R-* mode = later. |
| X-02 counts conservation harness — one table reused by every cell | **PARTIAL → build** | Seed side done: Phase-1 `FixtureBuilder.count()` → EXPECTED_COUNTS. CLI seed→archive→restore parity: `test_round_trip` (CASE-666). The reusable seed→archive→restore→API harness is Phase 3 (consumes the Phase-1 baseline). |
| X-03 leak sweep harness — serialized rows × forbidden tokens, every fresh cell | **PARTIAL → build** | One-off sweep exists: `test_remap_multi::test_reference_snapshots_carry_no_trace_of_the_source`. Generalise to a reusable harness. |
| X-04 job-plane sweep after any L run | **GAP (L)** | Job-plane asserted richly at component layer (`test_backup_service::TestFieldScopedJobWrites::*`, `TestValidationResultSurvives`, `TestPlanSurvivesOnTheJob`) but not as an L-layer post-run sweep. |
| X-05 double-restore idempotence | **BUILT (L, id-preserving)** | `run_matrix.py` X-05 (slice 2): a second id-preserving restore into the now-populated namespace is refused, nothing duplicated. The R-MRG all-unchanged re-run variant is still only compositional (C) — a later addition. |
| X-06 backup-of-a-restore, transitive fidelity | **GAP (L)** | Not exercised anywhere. |

## Gaps to build in Phase 3

**Component-layer (C) gaps — BUILT (BE-YAC-20260724-112448, Tier A):**
- R-07 dry-run genuinely *refuses* an N:1 same-valued **terminology** collision (the fixture's `MATRIX_STATUS` pair) — `test_remap_multi::test_n_to_one_terminology_collision_refuses` (apply + dry-run both refuse; the check precedes provisioning).
- R-10 merge with an explicit `target_namespace=` redirect + job-record assertion — `test_merge_restore::test_an_explicit_target_redirect_is_recorded_on_the_job`. Needed a one-line production add: the merge result now records `source_namespace` per target (parity with remap), so a redirect is legible on the job.
- R-12 identity-less docs **un-PATCHable after restore** — `test_remap_integration::test_an_identity_less_document_stays_append_only_after_restore` (restore preserves empty identity_fields + empty identity_hash; the append_only guard keys purely on those, and the rejection itself is pinned by `test_documents_patch::test_patch_no_identity_template_rejected_append_only`).
- R-13 edge-type **documents** re-pointed through a fresh restore + overwrite-in-place post-fresh-restore — `test_remap_integration::test_edge_type_endpoints_follow_the_restore_and_stay_addressable`. Live-validated on prod-test 20260724a.
- F-07 permission refusals on backup / restore / download for a non-admin key — `test_backup_api::test_non_admin_key_is_refused_on_backup_restore_and_download` (404 on all three, nothing partial minted).

**Fixed + live-validated (regression guard still wanted at L):**
- R-15 prefixed `id_config` through a fresh restore — **CASE-784 fixed** (`e32d8c3f`); component regression in `test_backup_engine` (preserve_id_config). Live-validated on prod-test 20260724a. Build the L-cell as the real-stack guard in Phase 3.

**Runner obligation folded in (no leftover case):**
- Partial-damage detection (the residual of B-09 after CASE-783 shape 1+2): the layer-L counts-conservation harness (X-02) must cross-check per-entity streamed counts against the manifest's declared counts and refuse on mismatch — a partial archive that reads as fewer entities with no error is the trap typed refusals cannot catch.

**Retired:** B-08 backup dry-run — dropped per Peter's ruling (CASE-782); design-doc edit is the sibling's `b782a13c`.

**Live-stack (L) runner — `run_matrix.py`.** The §7 runner: deployment-pointable, mints `<HHMMSS>-00a/b/c` namespaces, writes only inside them (`deletion_mode:full`), asserts each cell across the 7 planes, prints one cell×planes×pass/fail×wall table, non-zero exit on failure. `--keep`, `--verbose`, `--cleanup-only`. First slice **BUILT + green on prod-test 20260724a** (BE-YAC-20260724-112448):
- **B-01 / B-02 (BUILT):** real-archive counts — single-ns (NS-A) and multi-ns (NS-A+NS-B). Each asserts manifest-declared == streamed JSONL (PL-JOB, the B-09 partial-damage cross-check) AND == EXPECTED_COUNTS (PL-DATA). Verified live: NS-A 3 terminologies / 146 terms / 64 relations / 4 templates / 7 doc-versions / 1 file / 157 registry entries.
- **X-02 (BUILT):** counts conservation — a fresh restore reproduces the source's measured totals in the target (all 11 conserved classes).
- **R-05 / R-13 / R-15 (BUILT):** the fresh-restore spine — R-05 (copy lands + original untouched + PL-LEAK sweep + PL-REG value-form resolves), R-13 (edge endpoints re-pointed + versioned:false overwrite), R-15 (target UUID7 id_config, no prefix leak, ids disjoint from the live source).

Slice 2 **BUILT + green on prod-test 20260724b** (10/10 cells, ~49s):
- **R-01 (BUILT):** id-preserving restore into an emptied namespace (DR round-trip) — drop NS-B, `mode=restore` back into it; asserts every document id preserved verbatim, all conserved counts match the pre-drop namespace (PL-DATA), and value-form resolution intact (PL-REG, synonyms restored with the entries).
- **R-08 (BUILT):** merge into a drifted namespace — hard-delete a document, `mode=merge`; the drifted-away document is re-inserted, the rest left per `on_clash=skip` (PL-DATA).
- **X-01 (BUILT):** dry-run parity — a fresh `dry_run` writes nothing into the target, and the apply then produces exactly the source's conserved counts (the plan the dry-run implies == the outcome the apply delivers).
- **X-05 (BUILT):** double-restore idempotence — a second id-preserving restore into the now-populated namespace is refused (empty-target precondition), nothing duplicated.

**L cells still to build (later slices):**
- Real-archive: B-03 (`--allow-instance-wide`, partial-grant refused).
- Restore cells: R-02, R-03 (`--dr-install`), R-04, R-06 (both directions), R-07, R-11, R-14 (blobs + skip_files), R-16 (FTS/PL-REP).
- Failure injection: F-05 (crash mid-restore + re-run converges), F-06 (reporting-sync stopped + force backfill).
- Harnesses/sweeps: X-03 (leak sweep, generalized), X-04 (job-plane), X-06 (backup-of-a-restore).
- Cell zero (§4 of CASE-773): the CASE-766 inactive-version archive shape — the Phase-1 fixture builds it by construction (SPEC docs pinned to the deactivated v3).

**CONFIRMED BUG surfaced by the first L run — fresh restore drops value-form lookup synonyms.** A fresh restore reproduces every entity but its restored registry entries carry **0 synonyms where the source had 9** (`registry_synonyms 9 → 0`). Root-caused on prod-test 20260724a:
- Each source terminology/term/document carries one auto-synonym — the value-form key `{ns, type, value}` — distinct from the primary key `{ns, value, label}`. def-store/document-store auto-register it on original creation so the entity resolves by value alone (without its label). Templates never had one.
- The fresh-restore (remap) path re-provisions each entity via the registry provision/activate flow, which writes only the PRIMARY composite key; the secondary value-form synonym is never regenerated. The **merge**/id-preserving path preserves synonyms (`_rewrite_registry_entry`); only **fresh** restore drops them.
- **Functional impact (proven):** a value-form registry lookup (`/lookup/by-key` on `{ns,type,value}`) resolves `MATRIX_PRIORITY` on the source (found) and **fails on the restored copy** (not_found). A fresh-restored terminology can no longer be referenced by value — only by canonical id or the full label-bearing primary key. Violates Vision's "any valid synonym must behave identically to the canonical ID."
- **Gated in the runner** — `run_matrix.py` R-05 asserts value-form resolution survives (PL-REG), so the run now correctly reports this as a failure until the platform drops the synonym-regeneration gap. Needs a case + fix (regenerate the value-form synonyms on the remap-provision path, matching original registration).

## Confirmed bugs/gaps from the probe (`probe_backup_restore.py`, prod-test)

A targeted backup → fresh-restore probe against prod-test converted the two
suspected cells into verdicts:
- **R-15 → FIXED (CASE-784, `e32d8c3f`).** Was: fresh-restore of a prefixed-
  `id_config` namespace beside its live original 500s (target inherited the
  source prefix, re-minted a colliding sequence on the global `entry_id`
  index, leaked the prefix). Now: target defaults to UUID7. Re-probed on
  prod-test 20260724a — completes, UUID7 target, no collision, no leak.
- **R-13 → WORKS + COVERED.** Edge-type documents re-point their
  source_ref/target_ref through a fresh restore, and versioned:false
  overwrite-in-place holds post-restore. Re-probed on 20260724a WITHOUT
  `--drop-source` (R-15 fix unblocks the beside-original path). Component
  guard: `test_remap_integration::test_edge_type_endpoints_follow_the_restore_and_stay_addressable`.

Plus two confirmed by code-reading:
- **B-08 backup dry-run → CASE-782** (not implemented; restore's works).
- **B-09 malformed-archive refusals → CASE-783 (fixed, shape 1+2)** — typed `ArchiveError`s + synchronous 400 at upload; partial-damage count cross-check folded into the runner (X-02).

## Design-doc reconciliations (flag to Peter before building)

- **R1 — RESOLVED (sibling's `b782a13c`, pending push).** The §2 D1 producer-options list predated CASE-768 ("backups are full copies", commit `40d6b4c6`). `include_inactive`, `latest_only`, `template_prefixes`, and `dry_run` are **rejected** on the server `POST …/backup` path — not doc rot, a deliberate design: the direct engine takes a **full copy** (every namespace, version, and status; term-relations and synonyms embedded — `test_backup_engine::TestBuildQuery::test_query_is_namespace_alone`), and each retired flag's story is documented in the `BackupRequest` model field descriptions. Those filtering options live on the **CLI exporter** (P-CLI, `test_exporter`). Per Peter's CASE-782 ruling the sibling updated §2 D1 (producer split + server full-copy contract) and §5.1 (B-08 retired, B-05/B-06 marked P-CLI) in `b782a13c` — this mapping aligns with that; nothing further to change in the design doc from here.
- **R2 — §6 lists R-09 `newer` as known-empty; it is fully covered** by `test_merge_restore::TestNewerPolicy` (8 tests incl. tie / naive-utc / unparseable / missing timestamp). Drop R-09 from the gap list; keep only the L-layer real-stack version if wanted.
- **R3 — the "restore phases" of CASE-689 are the *reporting-verification* phases** (precondition → structural gate → count parity), not the entity-restore phases. When the matrix references restore phases, it means the reporting gate — already covered by `test_case_689_restore_phases` (U) and `reporting-sync/test_case_766_active_aware_ensure` (C).
