# WIP Quality Audit Report
Generated: 2026-07-26 18:11 UTC | Commit: be71a103 | Mode: full

## Summary

| Dimension | Status | Issues | Baseline | Delta |
|-----------|--------|--------|----------|-------|
| Ruff (Python lint) | WARN | 36 | — | — |
| mypy (Python types) | WARN | 47 | — | — |
| Vulture (dead Python code) | WARN | 1 | — | — |
| ShellCheck | WARN | 4 | — | — |
| ESLint (Vue/TS lint) | WARN | 1 | — | — |
| vue-tsc (Vue types) | PASS | 0 | — | — |
| ts-prune (unused exports) | PASS | 0 | — | — |

## 1. Dead Code

### Python (vulture) — 1 issues

- `libs/wip-auth/src/wip_auth/identity.py:7: unused import 'Token' (90% confidence)`

### TypeScript (ts-prune) — 0 unused exports

No unused exports detected (or ts-prune not available).

## 2. Type Safety

### Python (mypy) — 47 errors

| Component | Errors |
|-----------|--------|
| components-def-store | 3 |
| components-document-store | 18 |
| components-ingest-gateway | 0 |
| components-mcp-server | 0 |
| components-registry | 1 |
| components-reporting-sync | 2 |
| components-template-store | 22 |
| libs-wip-auth | 1 |

**Top errors:**
- `components/def-store/src/def_store/api/terms.py:455: error: Argument 1 to "find_by_value" of "TerminologyService" has incompatible type "str | None"; expected "str"  [arg-type]`
- `components/def-store/src/def_store/services/import_export.py:660: error: Returning Any from function declared to return "str | None"  [no-any-return]`
- `components/def-store/src/def_store/services/import_export.py:881: error: Need type annotation for "observed"  [var-annotated]`
- `components/document-store/src/document_store/services/remap_restore.py:152: error: Argument 1 to "get" of "dict" has incompatible type "Any | None"; expected "str"  [arg-type]`
- `components/document-store/src/document_store/services/remap_restore.py:165: error: Argument 1 to "get" of "dict" has incompatible type "Any | None"; expected "str"  [arg-type]`
- `components/document-store/src/document_store/services/remap_restore.py:342: error: Returning Any from function declared to return "dict[str, Any]"  [no-any-return]`
- `components/document-store/src/document_store/services/merge_plan.py:402: error: List comprehension has incompatible type List[dict[str, Any]]; expected List[str]  [misc]`
- `components/document-store/src/document_store/services/reporting_client.py:36: error: Dict entry 0 has incompatible type "str": "str | None"; expected "str": "str"  [dict-item]`
- `components/registry/src/registry/api/api_keys.py:90: error: Argument "grants" to "APIKeyResponse" has incompatible type "dict[str, Literal['read', 'write', 'admin']] | None"; expected "dict[str, str] | None"  [arg-type]`
- `components/reporting-sync/src/reporting_sync/batch_sync.py:491: error: Incompatible types in assignment (expression has type "None", variable has type "dict[str, Any]")  [assignment]`
- `components/reporting-sync/src/reporting_sync/main.py:1707: error: Returning Any from function declared to return "list[Any]"  [no-any-return]`
- `components/template-store/src/template_store/services/template_service.py:1639: error: Incompatible types in assignment (expression has type "Template | None", variable has type "list[Template]")  [assignment]`
- `components/template-store/src/template_store/services/template_service.py:1644: error: Incompatible types in assignment (expression has type "Template | None", variable has type "list[Template]")  [assignment]`
- `components/template-store/src/template_store/services/template_service.py:1650: error: "list[Template]" has no attribute "usage"  [attr-defined]`
- `components/template-store/src/template_store/services/template_service.py:1652: error: "list[Template]" has no attribute "value"  [attr-defined]`

### Vue/TypeScript (vue-tsc) — 0 errors

No vue-tsc errors (or not available).

## 3. Linting

### Python (ruff) — 36 issues

| Rule | Count |
|------|-------|
| UP042 | 7 |
| I001 | 6 |
| RUF012 | 6 |
| B904 | 6 |
| SIM102 | 4 |
| SIM108 | 2 |
| SIM105 | 2 |
| F401 | 1 |
| SIM115 | 1 |
| RUF022 | 1 |

### Vue/TypeScript (eslint) — 1 issues

| Rule | Count |
|------|-------|
| @typescript-eslint/no-unused-vars | 1 |

### Shell (shellcheck) — 4 issues

| Code | Count |
|------|-------|
| SC2034 | 2 |
| SC2001 | 1 |
| SC2162 | 1 |

## 4. Test Coverage

### Python

| Component | Stmts | Miss | Cover% |
|-----------|-------|------|--------|
| def-store | 2508 | 512 | 79.6% |
| document-store | 7284 | 1705 | 76.6% |
| ingest-gateway | 383 | 87 | 77.3% |
| registry | 2990 | 502 | 83.2% |
| reporting-sync | 3636 | 1272 | 65.0% |
| template-store | 2248 | 473 | 79.0% |

### TypeScript

| wip-client | Stmts: 71.0% | Branches: 83.2% |

## 5. Complexity Hotspots

Top 20 functions by cyclomatic complexity (CC >= C):

| Rank | Function | CC | File:Line |
|------|----------|----|-----------|
| F | _validate_activation_set | 46 | template-store/src/template_store/services/template_service.py:2610 |
| F | run_merge | 44 | document-store/src/document_store/services/backup_engine.py:1336 |
| F | import_ontology | 41 | def-store/src/def_store/services/import_export.py:831 |
| F | run_remap | 41 | document-store/src/document_store/services/backup_engine.py:691 |
| E | register_keys | 38 | registry/src/registry/api/entries.py:344 |
| E | start_restore | 38 | document-store/src/document_store/api/backup.py:333 |
| E | validate_template | 37 | template-store/src/template_store/services/template_service.py:2181 |
| E | activate_entries | 36 | registry/src/registry/api/entries.py:845 |
| E | _decorate_with_peers | 36 | document-store/src/document_store/services/document_service.py:1350 |
| E | transform | 36 | reporting-sync/src/reporting_sync/transformer.py:349 |
| E | _run_batch_sync | 35 | reporting-sync/src/reporting_sync/batch_sync.py:380 |
| E | ensure_views_for_template | 35 | reporting-sync/src/reporting_sync/schema_manager.py:1130 |
| E | update_template | 34 | template-store/src/template_store/services/template_service.py:1205 |
| E | bulk_create | 34 | document-store/src/document_store/services/document_service.py:2541 |
| E | _template_has_changed | 33 | template-store/src/template_store/services/template_service.py:857 |
| E | run_restore | 32 | document-store/src/document_store/services/backup_engine.py:463 |
| D | _search_documents | 30 | reporting-sync/src/reporting_sync/search_service.py:670 |
| D | _normalize_field_references | 28 | template-store/src/template_store/services/template_service.py:3090 |
| D | traverse_relationships | 28 | document-store/src/document_store/services/document_service.py:1535 |
| D | check_all_documents | 28 | document-store/src/document_store/services/integrity_service.py:436 |

## 6. API Consistency

**3 violations** found:

### document-store (1 violations)

- [bulk-first-request] `migrate_documents` (line 212): POST /migrate: write endpoint should accept List[...] body

### template-store (2 violations)

- [permission-enforcement] `get_namespace_template_stamp` (line 136): GET /stamp: endpoint does not call any namespace permission helper (['_enforce_replay_admin', '_is_superadmin', 'check_namespace_permission', 'resolve_accessible_namespaces', 'resolve_namespace_filter']). Add a check or extend the exemption list with rationale.
- [bulk-first-request] `add_edge_type_endpoints` (line 642): POST /{template_id}/endpoints: write endpoint should accept List[...] body


## 7. Dependency Health

### pip-audit — clean (or not installed)

### npm outdated — wip-client (7 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.5 | 10.0.1 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.7 | 4.1.10 |
| eslint | 9.39.4 | 9.39.5 | 10.8.0 |
| tsx | 4.21.0 | 4.23.1 | 4.23.1 |
| typescript | 5.9.3 | 5.9.3 | 7.0.2 |
| typescript-eslint | 8.57.1 | 8.65.0 | 8.65.0 |
| vitest | 3.2.4 | 3.2.7 | 4.1.10 |


## 8. Security Audit

> Planned as a separate dedicated session.

