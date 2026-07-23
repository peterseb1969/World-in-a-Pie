# Backup/Restore Test Matrix — tooling

Implements `docs/design/backup-restore-test-matrix.md` (CASE-773). The design
doc is authoritative; this directory is the code.

The matrix exists because every recent backup/restore bug shipped through green
unit suites — a *combination* was never exercised, not a unit wrong. The tooling
here builds a small, fully-shaped fixture and (Phase 3) drives every
producer × restore-mode × seam cell against a real deployment.

## Layout

| File | Purpose |
|---|---|
| `wip_http.py` | Deployment-pointable HTTP client + target resolution. A target is stated explicitly — `--install <name>` (reads `~/.wip-deploy/<name>/`) or `--base-url` + `--key-file`. Reused by the fixture builder and the Phase 3 runner. |
| `fixtures.py` | `FixtureBuilder`: provisions NS-A (rich) / NS-B (counterpart) covering the §3 entity checklist E1–E15, through public APIs only, idempotently; counts every class back into an EXPECTED_COUNTS table; tears the namespaces down. |
| `provision_fixtures.py` | CLI over `FixtureBuilder` — build, `--count-only`, `--teardown`. |
| `expected_counts.sample.json` | A reference EXPECTED_COUNTS from a `default`-install build. Illustrative — real counts are measured per run. |

## Phase 1 — fixtures (done)

Build the fixture into a deployment and capture its EXPECTED_COUNTS:

```bash
# against a named install (self-signed dev/prod-test certs → --no-verify-tls)
.venv/bin/python tools/backup-matrix/provision_fixtures.py \
    --install default --no-verify-tls \
    --ns-a 143022-00a --ns-b 143022-00b \
    --counts-out /tmp/expected.json

# tear it down (namespaces are deletion_mode:full, so delete cascades)
.venv/bin/python tools/backup-matrix/provision_fixtures.py \
    --install default --no-verify-tls --ns-a 143022-00a --ns-b 143022-00b --teardown
```

Namespace names are parameters: standalone runs default to `MTXA`/`MTXB`; the
Phase 3 runner passes its own `<HHMMSS>-00a` / `-00b` names.

### What the fixture contains (E1-E15 → §3)

- **NS-A (strict isolation, allow-list `[wip, NS-B]`, E15):** `MATRIX_COLOR`
  terminology with aliases + a deprecated term (E1, E12); `MATRIX_ONTOLOGY`
  imported from an OBO Graph JSON as real term-relations (E2, folds in
  CASE-658); `MATRIX_SPECIMEN` entity template with v1/v2 active + v3 inactive
  (E3), an FTS-indexed field (E13), a scalar + array-of-refs into NS-B and a
  term ref into `wip` (E8); `MATRIX_EVENT_LOG` identity-less append-only
  template + docs (E7); a multi-version document with `metadata.custom` (E6,
  E14); a file blob linked to a document (E9); a custom registry synonym (E10);
  an archived document (E12).
- **NS-B (open, prefixed sequential `id_config`, E11):** `MATRIX_SAMPLE` entity
  template (the cross-source pin target); `MATRIX_LINKED_TO` edge type,
  `versioned:false` (E4) with a relationship document (E5); a terminology whose
  value collides with NS-A's (the N:1-collapse refusal driver) plus a distinct
  one (the N:1 success driver).
- **Cross-namespace:** a document reference each way (NS-A → NS-B primary/linked
  samples; NS-B `SAMP-1` → NS-A `SPEC-1` back-ref).
- **Cell zero (CASE-766):** the SPEC documents pin to `MATRIX_SPECIMEN` v3,
  which is then deactivated with `force` — an archive whose documents sit on an
  inactive template version, the originally-failing restore-gate shape.

Notes learned building it, that later phases rely on:
- Cross-namespace references need the **qualified `NS:VALUE`** form or a
  canonical id — bare business keys never cross a namespace boundary, and
  prefixed document_ids aren't UUID7 so they don't hit the direct-id path.
- Prefixed `id_config` must use a **per-namespace prefix**: the registry
  `entry_id` index is global while the counter is per-namespace, so a shared
  prefix collides across the fresh namespaces each run mints.
- E9 files require `WIP_FILE_STORAGE_ENABLED` on the target; the builder skips
  E9 **loudly** (a warning, not silence) when storage is disabled.

## Phase 2 — mapping pass (next)

Before writing cells: mark each matrix cell already covered by a named existing
suite (`test_backup_engine`, `test_merge_restore`, `test_remap_*`, … per §6) and
build only the gaps. Known-empty today: all of layer L as a scripted runner,
R-03 (cross-instance DR), R-09 `newer`, R-11, R-15, X-01 as a harness, X-04/05/06.

## Phase 3 — the layer-L runner (§7)

Manual, on-demand, deliberately outside `wip-test.sh all` and CI. Deployment-
pointable; mints `<HHMMSS>-00x` namespaces; every cell a dry-run/apply pair;
seven assertion planes with silence ≠ pass; counts conserved against
EXPECTED_COUNTS; one table (cell × planes × pass/fail × wall time) as the
artifact; cleanup on success, `--keep`, `--cleanup-only`.
