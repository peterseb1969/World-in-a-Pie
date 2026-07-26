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
| B-01 P-SRV1 counts == EXPECTED_COUNTS, all classes incl registry_entries | **BUILT (L, slice 1)** — row corrected; §7 had it built while this table still read PARTIAL | Structure asserted (`test_backup_engine::TestModuleStructure::*`, `test_backup_entity_order_covers_registry_entries`, `TestPreCount::test_returns_count_per_entity_type`) but **ArchiveWriter is mocked in every engine test** — no real archive is counted against a real seed. The real-count assertion is a Phase-3 L cell against the Phase-1 fixture's EXPECTED_COUNTS. |
| B-02 P-SRVN multi-ns subtrees/counts | **BUILT (L, slice 1)** — row corrected; §7 had it built while this table still read PARTIAL | `test_backup_engine::TestRunBackupMultiNamespace::test_two_namespaces_manifest` asserts per-ns subtrees + `namespace_prefixes()`, but counts are all-zero (empty mock). Real per-ns counts = L. `wip-archive::TestMultiNamespaceArchive` covers the archive layout. |
| B-03 P-SRVALL all_namespaces incl wip; partial-grant refused | **BUILT (L, gated) — live-validated** | `run_matrix.py` B-03 (slice 3), behind `--allow-instance-wide`. Validated end-to-end on prod-test 20260725a (14/14 cells green, B-03 88/88). **Permission half:** `b03_partial_grant_refused()` mints a key with admin on one namespace only, waits for it to go live (see the key-sync gotcha below), attempts an `all_namespaces` backup and gets **404** — refused, and 404 rather than 403 per the don't-leak-existence convention. Key revoked in a `finally`. **Instance-wide half:** the archive spans every namespace — `wip` present, 12 namespaces in the manifest, both runner namespaces present, and the job's `namespaces` matching the archive's. The archive job is deleted afterwards so nothing instance-sized is retained. Without the flag the cell reports **SKIPPED**, never silently absent. **Reads only the manifest** — see CASE-803 below. |

**B-03 reads only the archive's manifest (CASE-803).** The first version
downloaded the whole instance-wide archive — 853 MB on prod-test, 826 s of the
run's 887 s — to inspect one small member. That pushed a gigabyte through the
same service the run was measuring, and the degradation it caused landed in the
run's own health telemetry: the single unhealthy sample of that run fell
*outside* the compression window and inside the download. A cell cannot measure
a service it is saturating. It now reads a bounded prefix
(`MANIFEST_PREFIX_BYTES`, 512 KB) and parses `manifest.json` out of it —
possible because `ArchiveWriter.write` emits the manifest as the archive's
FIRST member, ahead of every entity file and blob. HTTP Range is not usable
here (the download route is a bare `StreamingResponse`, which ignores it), so
the transfer is stopped client-side via `WipClient.get_prefix`. **The trade-off,
deliberate:** the per-entity `declared == streamed` cross-check cannot survive —
counting JSONL lines means inflating every member — so B-03 gives it up. B-01
and B-02 already assert it over the runner's own namespaces on every run, and
repeating it across every namespace on the instance was never what made this
cell distinct. B-03's four surviving checks (the partial-grant refusal, `wip`
present, the runner's namespaces present, job-vs-manifest namespace parity) are
the ones only it can make, and all four passed in the full-download run before
the change.

**Gotcha the B-03 build surfaced (not a bug — documented behaviour).** A newly
created runtime API key is usable against the **Registry** immediately (it owns
the key store) but 401s on every other service until `KeySyncService` picks it
up — it polls the Registry every **30 s** by default
(`libs/wip-auth/src/wip_auth/key_sync.py`). A permission assertion made against
a not-yet-propagated key returns 401, which is not a refusal: it means the
check never ran. The first version of this cell read that 401 as its answer.
`b03_partial_grant_refused` now waits for the key to be recognised before
asserting, and treats a 401 as an explicit failure ("the check did not run")
rather than folding it into the accepted-refusal set.
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
| R-02 P-SRVN × R-ID both ns, cross-ns refs (E8) intact | **BUILT (L)** | `run_matrix.py` R-02 (slice 5) — the runner's first MULTI-namespace id-preserving restore, the shape a real DR takes. Drops BOTH namespaces and restores them from the archive B-02 took while they were pristine; asserts the job names both write targets, ids come back verbatim on both sides, NS-A's `primary_sample`/`linked_samples` refs into NS-B survive unchanged, and — the distinctive half — each ref still **resolves** to a document in the restored NS-B. Counts cannot see this: a dangling reference is a well-formed string in a document whose class totals all reconcile. Note refs are stored in QUALIFIED VALUE form (`<ns>:<value>`), not as the target's UUID, so resolution is a real lookup on the value (split on the first colon), not set-membership against document_ids. Runs last of the data cells because it destroys both sources. |
| R-03 P-SRV1 × R-ID-X cross-instance DR | **GAP (L)** | Needs `--dr-install`; §6 confirms empty. |
| R-04 P-CLI × R-ID — the CASE-756 seam | **COVERED (C) → L** | `test_round_trip::test_golden_round_trip` IS this seam (CLI export → engine restore, resolution fidelity without caches, CASE-665/756). L version = Phase 3. |
| R-05 P-SRV1 × R-FR1 beside live original; LEAK on copy; original untouched | **BUILT (L, slice 1)** — row corrected; §7 had it built while this table still read PARTIAL | Copy lands active in TARGET: `test_remap_integration::{test_ids_come_from_the_registry_and_are_active, test_documents_land_under_new_ids...}`. LEAK sweep: `test_remap_multi::test_reference_snapshots_carry_no_trace_of_the_source`. **"Original untouched" is never asserted** (source is a mocked reader). L cell + original-untouched diff. |
| R-06 P-SRVN × R-FRN cross-source refs both ways + template pin | **BUILT (L, slice 5) — GREEN, 8/8 on prod-test 20260726b** | Cross-source template pin + one ref direction: `test_remap_multi::test_cross_source_template_pin_follows_the_new_id`. The cell shipped red on 20260726a: qualified data refs (`<ns>:<id>`) passed through `IDRemapper._remap_data_ids` unrewritten (exact-match maps key on bare ids), so the copy's refs pointed verbatim at the ORIGINAL namespace while the `resolved` snapshots were rewritten. Fixed — the remapper now rewrites the qualified form through the namespace and id maps, and the C sweep fixture gained the qualified shape it was missing (CASE-814, `e1065a94`). Live-verified green on the 20260726b roll. |
| R-07 P-SRVN × R-FRC collapse; same-valued **terminology** refused naming both sources; dry-run refuses the SAME cell | **PARTIAL → GAP** | Disjoint lands + N:1 refusal naming both sources: `test_remap_multi::{test_n_to_one_disjoint_content_lands_in_one_target, test_n_to_one_key_collision_refuses}` — but the collision is on **templates**, not terminologies, and `test_dry_run_placeholders_unique_across_sources` asserts the *opposite* (avoiding a false refusal). **DONE (C):** `test_remap_multi::test_n_to_one_terminology_collision_refuses` — two same-valued `MATRIX_STATUS` terminologies into one target refuse (apply path AND dry-run, since `_check_target_collisions` runs before provisioning). **L cell BUILT (slice 5) and RED — the C claim does NOT generalize (CASE-815):** the check compares the full Registry key `{ns, value, label}`, and the C fixture's labels are identical while the live fixture's differ ("Matrix Status (NS-A)" vs "(NS-B)"), so live neither the dry run nor the apply refuses — the dry run previews `complete` and the apply dies mid-write on def-store's `(namespace, value)` unique index, leaving a half-restored target. **CASE-815 fixed** (`2b21a71f` — collision check now projects to store uniqueness; `test_n_to_one_terminology_collision_refuses_across_labels` pins the differently-labeled shape). **Live-verified GREEN, 4/4 on prod-test 20260726b** — dry run and apply both refuse, nothing partial lands. |
| R-08 P-SRV1 × R-MRG into drift; add_missing off/on; extend_terminologies | **COVERED (C) → L BUILT** | C: `test_merge_restore::TestDefinitionsPrecondition::*`, `test_merge_definitions::TestOptInStrategies::*`. L: `run_matrix.py` R-08 (slice 2) — hard-delete a document, `mode=merge`; the drifted-away document is re-inserted (on_clash=skip). add_missing/extend_terminologies variants still C-only. |
| R-09 R-MRG on_clash triple (skip / overwrite / newer) | **COVERED (C)** ← §6 was stale | `test_merge_restore::TestDocumentClashPolicy::*` (skip, overwrite, overwrite-in-place on versioned:false, adopts target id) and the whole `TestNewerPolicy` class (newer taken / older left / tie / naive-utc / unparseable-warns / missing / report-counts). **§6 lists R-09 `newer` as known-empty — it is in fact fully covered.** |
| R-10 R-MRG-T redirect (CASE-748), job record says so | **PARTIAL** | `test_merge_restore::TestMergeIntoADifferentNamespace::*` covers data landing in the target + rescoped/rehashed keys + id-collision refusals — **DONE (C):** `test_merge_restore::test_an_explicit_target_redirect_is_recorded_on_the_job` drives an explicit `target_namespace` differing from the archive manifest and asserts the job result records the redirect. Closing it added one production line — the merge result now records `source_namespace` per target (parity with remap). L cell remains for Phase 3. |
| R-11 R-JOB restore from a retained job, no re-upload; job independence | **BUILT (L)** | `run_matrix.py` R-11 (slice 4) — backs up NS-A, then `POST /backup/jobs/{id}/restore` with **no re-upload**. Merge mode, not restore: `RestoreFromJobRequest` is strict and carries no `target_namespace`, so each archived namespace returns to itself, and an id-preserving restore would need an empty target NS-A is not. Asserts the restore minted its OWN job, and that deleting the SOURCE backup job leaves the restore job intact — the route's stated archive-copy independence, which a shared handle would only break later, at cleanup. The cell deletes a job on purpose and therefore retracts it from X-04's end-of-run sweep itself. |
| R-12 E7 identity-less through R-ID/R-FR1/R-MRG, un-PATCHable, N:1 empty-key exemption | **PARTIAL → GAP** | Identity-less through remap: `test_remap_restore::{test_an_identity_less_document_gets_an_empty_key, test_distinct_documents_still_get_distinct_ids}` + `test_remap_integration::test_documents_land_under_new_ids...`; through merge-plan: `test_merge_plan::test_identity_less_documents_match_by_document_id_only`. **"still un-PATCHable after restore" DONE (C):** `test_remap_integration::test_an_identity_less_document_stays_append_only_after_restore` — restore preserves empty identity_fields + empty identity_hash (the exact conditions the append_only guard keys on); the rejection is pinned by `test_documents_patch::test_patch_no_identity_template_rejected_append_only`. Full R-ID/R-MRG end-to-end at L = Phase 3. |
| R-13 E4/E5 edge types through R-FR1; endpoints re-pointed; versioned:false overwrite post-restore | **COVERED (C) → L** | Component: `test_remap_integration::test_edge_type_endpoints_follow_the_restore_and_stay_addressable` — a real edge type (usage=relationship, versioned=false, identity_fields=[source_ref,target_ref]) fresh-restored: BOTH endpoints re-pointed to the restored docs' NEW ids, identity_hash recomputed over the pair, and the Registry claim carries it so a later write dedups (the re-addressability overwrite-in-place depends on). Live-validated on prod-test 20260724a via `probe_backup_restore.py` with NO `--drop-source`: version=1, count=1, no fork. L real-stack cell = Phase 3. |
| R-14 E9 blobs through R-ID/R-FR1 with include_files; skip_files loud | **BUILT (L)** | `run_matrix.py` R-14 (slice 4) — the archive carries a blob per file, a fresh restore conserves the file records, and **every restored blob is byte-identical to its source**, compared through `/files/{id}/content` (the raw-bytes route; `/download` returns a pre-signed URL and would compare per-request JSON that can never match). Ids are re-minted, so the compare is on content. `skip_files=true` into a second target leaves no file records — loudly absent. The byte compare is the point: a file row restored with the right id and a missing or truncated blob passes every count assertion in the matrix. |
| R-15 E11 prefixed id_config through R-FR1; re-minted follow TARGET config; next mint no collision | **FIXED (CASE-784) → L guard** | CASE-784 fixed (`e32d8c3f`, `preserve_id_config`): a fresh restore's target defaults to UUID7 instead of cloning the source prefix. Component regression: `test_backup_engine` (preserve_id_config). Live-validated on prod-test 20260724a — fresh-restore-beside-original completes, target id_config is UUID7 (prefix:null), no `entry_id` collision, no prefix leak. Build the L-cell as the real-stack guard in Phase 3. |
| R-16 E13 FTS + PL-REP full pass after R-FR1 | **BUILT (L, both halves)** | **PL-REP half:** `run_matrix.py` R-16 polls `GET /api/reporting-sync/parity` for the fresh-restored target and asserts the real `NamespaceParityResult` contract: `schema_present`, `table_count > 0`, `structural_issues == 0`, `count_mismatches == 0`, `ok`. Reporting is `None`/stubbed in every merge and remap unit suite, so whether PostgreSQL reflects a restored namespace was previously unknown. Reporting-sync unreachable (a `core`-preset target, or one mid-redeploy) reports **SKIPPED** with its reason rather than passing or failing on a precondition the invocation did not supply. **E13/FTS half (slice 5):** searches the restored copy of NS-A (`ns_g`, where the fixture's `full_text_indexed` field lives) for a known restored document and demands a hit — parity compares counts and table shape and says nothing about whether the tsvector columns FTS matches on were built. Separately demands `unmatched_template is None`: that assertion alone would have caught **CASE-810**, where a type-filtered search returned zero hits on every post-split install because it resolved `doc_<template>` as a BASE TABLE when post-split that name is a VIEW. A zero-hit result cannot distinguish "this type has nothing" from "this filter matched no table" — which is why 810 survived on two live instances until CASE-811 added the signal. Leak plane: every hit id must be a document of the restored namespace (matched on id, because `SearchResult` carries no namespace field and asserting one yields a vacuous None). |

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
| X-02 counts conservation harness — one table reused by every cell | **BUILT (L, slice 1)** — row corrected; §7 had it built while this table still read PARTIAL | Seed side done: Phase-1 `FixtureBuilder.count()` → EXPECTED_COUNTS. CLI seed→archive→restore parity: `test_round_trip` (CASE-666). The reusable seed→archive→restore→API harness is Phase 3 (consumes the Phase-1 baseline). |
| X-03 leak sweep harness — serialized rows × forbidden tokens, every fresh cell | **BUILT (L) / C one-off** | C: `test_remap_multi::test_reference_snapshots_carry_no_trace_of_the_source`. L: `run_matrix.py` `leak_sweep()` (slice 3) — every restore-written surface (documents, templates, terminologies, terms, registry-entry detail incl. synonyms/`search_values`/`source_info`) crossed with every source identifier (namespace name, document/template/terminology/term/registry-entry ids). Reused: R-05 asserts the document slice, X-06 runs it on the second hop. The target's namespace *description* is excluded by design — a fresh restore writes source provenance there deliberately. |
| X-04 job-plane sweep after any L run | **BUILT (L)** | Job-plane asserted richly at component layer (`test_backup_service::TestFieldScopedJobWrites::*`, `TestValidationResultSurvives`, `TestPlanSurvivesOnTheJob`). L: `run_matrix.py` X-04 (slice 3) — every job the run drove is **re-read** after the race window and asserted against a field-ownership schema: `namespaces` == real write targets (the CASE-745 pin: a fresh restore names its target, not its source), `options` echoed, `archive_size` set, `result` populated exactly where that kind's terminal event carries details (fresh/merge/any dry run — a backup and an id-preserving restore correctly have none), `validation_job_ids` surviving, and each validation scoped to a written namespace, completed, carrying its `namespace_integrity` findings (PL-AUTO). The re-read is the point: the back-link and archive-lifecycle writers land *after* the job reports terminal, which is where CASE-747/749/750 lived. |
| X-05 double-restore idempotence | **BUILT (L, id-preserving)** | `run_matrix.py` X-05 (slice 2): a second id-preserving restore into the now-populated namespace is refused, nothing duplicated. The R-MRG all-unchanged re-run variant is still only compositional (C) — a later addition. |
| X-06 backup-of-a-restore, transitive fidelity | **BUILT (L)** | `run_matrix.py` X-06 (slice 3) — back up the fresh copy in `-00c`, fresh-restore THAT into `-00f`: the second-hop archive is internally consistent (declared == streamed), the second hop conserves the first copy's counts, the `sample_code` identity values survive both hops, value-form resolution still works, and the leak sweep finds no trace of the first copy in the second. Hop one compares against a fixture the runner built, so a self-consistently mangled row survives it; hop two makes such a row either reproduce exactly or diverge visibly. |

## Gaps to build in Phase 3

**Component-layer (C) gaps — BUILT (BE-YAC-20260724-112448, Tier A):**
- R-07 dry-run genuinely *refuses* an N:1 same-valued **terminology** collision (the fixture's `MATRIX_STATUS` pair) — `test_remap_multi::test_n_to_one_terminology_collision_refuses` (apply + dry-run both refuse; the check precedes provisioning). *Caveat added by the slice-5 L run, then resolved: the check keyed on the full `{ns, value, label}` key, so the live differently-labeled pair sailed through and crashed the apply mid-write (CASE-815). Fixed — the check now projects to store uniqueness `(ns, value)`; `test_n_to_one_terminology_collision_refuses_across_labels` pins the differently-labeled shape.*
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

Slice 3 **BUILT + green on prod-test 20260724b** (14 cells, 13 pass / 0 fail /
1 skip in the default set; 65 s wall):
- **X-03 (BUILT):** the leak-sweep harness the design doc asks every fresh cell to reuse. Every restore-written surface (documents, templates, terminologies, terms, and registry-entry *detail* — the only place synonyms / `search_values` / `source_info` are visible) crossed with every source identifier (namespace name plus document/template/terminology/term/registry-entry ids). The sweep asserts its own coverage first (rows > 0 per surface, tokens > 1), because a sweep over nothing finds nothing and proves nothing. R-05 now asserts the document slice of this same sweep instead of its own narrower inline check; X-06 runs it again on the second hop. The target namespace's *description* is excluded on purpose — `_record_provenance` writes "restored from an archive of '<source>'" there by design, and sweeping it would flag a documented feature as a leak.
- **X-04 (BUILT):** the job-plane sweep, 91 checks over the 10 jobs a default run drives. Every job is **re-read at the end of the run** and asserted against a field-ownership schema: `namespaces` == the real write targets (the CASE-745 pin — a fresh restore must name its target, not the source it read), `options` echoed, `created_by` recorded, `archive_size` populated, and `result` populated *exactly* where that job kind's terminal event carries details (fresh, merge, and every dry run do; a backup and an id-preserving restore correctly carry none — asserting it both ways keeps the sweep from passing on a result that appeared where none belongs). PL-AUTO: `validation_job_ids` survived, and each validation is scoped to a written namespace, completed, and carries its `namespace_integrity` findings. The re-read is the whole point — the validation back-link and the archive-lifecycle hook are detached writers that land *after* the job reports terminal, which is exactly where the CASE-747/749/750 lost-update class lived; an inline assert would pass straight over the race.
- **X-06 (BUILT):** backup-of-a-restore. Back up the fresh copy in `-00c`, fresh-restore THAT into `-00f`, and assert the second-hop archive is internally consistent (declared == streamed), the second hop conserves the first copy's counts, the `sample_code` identity values survive both hops, value-form resolution still works, and the leak sweep finds no trace of the first copy in the second. Hop one is compared against a fixture the runner itself built, so a row mangled into a self-consistent state survives it; hop two forces such a row to either reproduce exactly or diverge visibly.
- **B-03 (BUILT, gated; permission half live-validated, instance-wide half not):** see §5.1.

**Two bugs surfaced by the slice-3 runs.** Both were found by the new cells
doing what the matrix exists for — exercising combinations no unit suite runs:
- **CASE-800** — archive download truncation (below).
- **CASE-801** — an `all_namespaces` backup builds its archive (853 MB on prod-test, 12 namespaces) synchronously on the document-store's event loop, so `/health` stops answering inside its 5 s probe timeout: readiness failed 5× over 2m29s, liveness once, and every caller got 503 from the ingress for ~2.5 minutes. The pod did not restart, but two more consecutive liveness failures would have killed it mid-backup. This is why B-03 is gated, and the gate is now documented as "interrupts the target", not merely "reaches outside our namespaces".

Slice 4 **BUILT + green on prod-test** (17 cells, 16 pass / 0 fail / 1 skip,
174s). The two entity classes the matrix carried through backup counts but
never through a restore, plus the retained-job door:
- **R-14 (BUILT):** blobs survive a fresh restore byte-identically, and
  `skip_files` drops them loudly. Compared through `/files/{id}/content` —
  `/download` returns a pre-signed URL, so comparing it compares per-request
  JSON and never matches. (Cost one red run to learn.)
- **R-16 (BUILT, slice 4 = PL-REP half; slice 5 = E13/FTS half):** parity
  against the real `NamespaceParityResult` fields, then a full-text search for
  a known restored document plus `unmatched_template is None`. The two halves
  fail independently — parity was perfectly happy on the installs where
  CASE-810 made every type-filtered search return zero hits. SKIPs when
  reporting-sync is absent.
- **R-11 (BUILT):** restore from a retained job with no re-upload, plus the
  archive-copy independence the route promises.

Slice 5 **BUILT; shipped with R-06 and R-07 deliberately RED on prod-test
20260726a** — the first slice whose reds were attributed platform defects,
not cell bugs (each settled by reading the decisive evidence, not the cell's
own output). Both fixed same-day (CASE-814 `e1065a94`, CASE-815 `2b21a71f`)
and **live-verified on the 20260726b roll: 20 cells, 19 pass, 0 fail, 1 skip
(B-03, gated)** — the full board green for the first time since the slice
landed:
- **R-06 (BUILT, RED — CASE-814):** multi-source fresh restore, cross-source
  refs both ways. The copy's stored ref strings still name the ORIGINAL
  namespace and ids verbatim (`110821-00b:110821-00b-D000001`) — decided by
  reading the restored rows, not the cell's resolution counters.
- **R-07 (BUILT, RED — CASE-815):** N:1 collapse refusal. Job records settle
  attribution: the dry run wrote nothing but previewed `complete`; the apply
  was no refusal — it restored source A fully, then died on B's terminology
  bulk-insert (Mongo E11000 on `(namespace, value)`), leaving a half-restored
  target.
- Ordering constraint the slice added: R-06/R-07 read both pristine sources,
  so they run before R-02, which drops them.

**L cells still to build (later slices):**
- Restore cells: R-03 (`--dr-install`), R-04.
- Failure injection: F-05 (crash mid-restore + re-run converges), F-06 (reporting-sync stopped + force backfill).
- Sweep generalizations: X-01 over every R-* mode (currently fresh only), X-05's R-MRG all-unchanged re-run variant.
- Cell zero (§4 of CASE-773): the CASE-766 inactive-version archive shape — the Phase-1 fixture builds it by construction (SPEC docs pinned to the deactivated v3).

**Bug surfaced by the slice-3 runs — CASE-800, archive download truncation.**
A `GET /backup/jobs/{id}/download` issued seconds after its backup completed
returned 200 + `Content-Length: 5786` + a **zero-byte body**. The archive was
undamaged: the same job downloaded whole (5786 bytes, valid zip) a minute
later, and the identical run sequence passed before and after. Leading
hypothesis, from code reading and **not** verified by instrumenting the server:
`download_archive` checks `archive_exists(job)` and streams from the same
in-memory job record, while `archive_lifecycle_hook` concurrently uploads the
scratch file to the bucket, unlinks it, and only *then* flips
`archive_backend`/`archive_path` on the record — so a download inside that
window passes the exists check and its generator's `open()` raises after the
headers are already on the wire. The runner now validates every download
against the job's `archive_size` and the zip magic and retries loudly
(`_download_archive`), so a run survives the flake without absorbing it.

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
