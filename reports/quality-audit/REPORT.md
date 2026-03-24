# WIP Quality Audit Report
Generated: 2026-03-24 07:31 UTC | Commit: 255a469 | Mode: full

## Summary

| Dimension | Status | Issues | Baseline | Delta |
|-----------|--------|--------|----------|-------|
| Ruff (Python lint) | WARN | 166 | 171 | -5 |
| mypy (Python types) | WARN | 256 | 276 | -20 |
| Vulture (dead Python code) | WARN | 1 | 1 | 0 |
| ShellCheck | WARN | 105 | 120 | -15 |
| ESLint (Vue/TS lint) | PASS | 0 | 0 | 0 |
| vue-tsc (Vue types) | PASS | 0 | 0 | 0 |
| ts-prune (unused exports) | PASS | 0 | 0 | 0 |

## 1. Dead Code

### Python (vulture) — 1 issues

- `components/document-store/src/document_store/services/replay_service.py:239: unreachable code after 'while' (100% confidence)`

### TypeScript (ts-prune) — 0 unused exports

No unused exports detected (or ts-prune not available).

## 2. Type Safety

### Python (mypy) — 256 errors

| Component | Errors |
|-----------|--------|
| components-def-store | 63 |
| components-document-store | 90 |
| components-ingest-gateway | 1 |
| components-registry | 30 |
| components-reporting-sync | 24 |
| components-template-store | 48 |
| libs-wip-auth | 0 |

**Top errors:**
- `components/def-store/src/def_store/services/registry_client.py:45: error: Dict entry 0 has incompatible type "str": "str | None"; expected "str": "str"  [dict-item]`
- `components/def-store/src/def_store/services/registry_client.py:100: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/def-store/src/def_store/services/registry_client.py:102: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/def-store/src/def_store/services/registry_client.py:154: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/def-store/src/def_store/services/registry_client.py:156: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/document-store/src/document_store/services/file_storage_client.py:105: error: Incompatible types in assignment (expression has type "dict[str, str]", target has type "str")  [assignment]`
- `components/document-store/src/document_store/services/file_storage_client.py:136: error: Returning Any from function declared to return "bytes"  [no-any-return]`
- `components/document-store/src/document_store/services/file_storage_client.py:266: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/document-store/src/document_store/services/import_service.py:65: error: Incompatible types in assignment (expression has type "Sequence[str]", variable has type "list[str]")  [assignment]`
- `components/document-store/src/document_store/services/template_store_client.py:61: error: Dict entry 0 has incompatible type "str": "str | None"; expected "str": "str"  [dict-item]`
- `components/ingest-gateway/src/ingest_gateway/http_client.py:65: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/registry/src/registry/models/id_algorithm.py:43: error: Missing named argument "prefix" for "IdAlgorithmConfig"  [call-arg]`
- `components/registry/src/registry/models/id_algorithm.py:43: error: Missing named argument "pattern" for "IdAlgorithmConfig"  [call-arg]`
- `components/registry/src/registry/models/id_algorithm.py:44: error: Missing named argument "prefix" for "IdAlgorithmConfig"  [call-arg]`
- `components/registry/src/registry/models/id_algorithm.py:44: error: Missing named argument "pattern" for "IdAlgorithmConfig"  [call-arg]`

### Vue/TypeScript (vue-tsc) — 0 errors

No vue-tsc errors (or not available).

## 3. Linting

### Python (ruff) — 166 issues

| Rule | Count |
|------|-------|
| B904 | 63 |
| UP042 | 21 |
| RUF012 | 19 |
| SIM102 | 12 |
| SIM105 | 10 |
| RUF005 | 7 |
| B905 | 7 |
| RUF013 | 7 |
| SIM108 | 6 |
| RUF022 | 3 |
| B007 | 3 |
| I001 | 2 |
| SIM109 | 2 |
| SIM118 | 1 |
| SIM113 | 1 |

### Vue/TypeScript (eslint) — 0 issues

No ESLint issues (or not available).

### Shell (shellcheck) — 105 issues

| Code | Count |
|------|-------|
| SC1091 | 39 |
| SC2155 | 18 |
| SC2034 | 15 |
| SC2162 | 9 |
| SC2223 | 6 |
| SC2012 | 5 |
| SC2329 | 4 |
| SC2153 | 2 |
| SC1090 | 2 |
| SC2115 | 1 |

## 4. Test Coverage

### Python

| Component | Stmts | Miss | Cover% |
|-----------|-------|------|--------|
| def-store | 2170 | 799 | 63.2% |
| document-store | 3680 | 1659 | 54.9% |
| ingest-gateway | 360 | 87 | 75.8% |
| registry | 1694 | 621 | 63.3% |
| reporting-sync | 2406 | 1297 | 46.1% |
| template-store | 1619 | 557 | 65.6% |

### TypeScript

No TypeScript coverage data available.

## 5. Complexity Hotspots

Top 20 functions by cyclomatic complexity (CC >= C):

| Rank | Function | CC | File:Line |
|------|----------|----|-----------|
| F | bulk_create | 57 | document-store/src/document_store/services/document_service.py:895 |
| F | create_terms_bulk | 41 | def-store/src/def_store/services/terminology_service.py:537 |
| E | _validate_activation_set | 40 | template-store/src/template_store/services/template_service.py:1156 |
| E | validate_template | 37 | template-store/src/template_store/services/template_service.py:736 |
| E | import_ontology | 33 | def-store/src/def_store/services/import_export.py:757 |
| D | _template_has_changed | 29 | template-store/src/template_store/services/template_service.py:366 |
| D | _validate_field_references | 26 | template-store/src/template_store/services/template_service.py:1685 |
| D | check_alerts | 26 | reporting-sync/src/reporting_sync/metrics.py:239 |
| D | update_template | 25 | template-store/src/template_store/services/template_service.py:447 |
| D | register_keys | 23 | registry/src/registry/api/entries.py:296 |
| D | _parse_obo_graph | 23 | def-store/src/def_store/services/import_export.py:607 |
| D | check_all_documents | 23 | document-store/src/document_store/services/integrity_service.py:229 |
| D | update_term | 22 | def-store/src/def_store/services/terminology_service.py:872 |
| D | create_template | 22 | template-store/src/template_store/services/template_service.py:36 |
| D | import_terminology | 21 | def-store/src/def_store/services/import_export.py:261 |
| D | import_documents | 21 | document-store/src/document_store/api/import_api.py:45 |
| D | get_template | 21 | document-store/src/document_store/services/template_store_client.py:81 |
| D | transform | 21 | reporting-sync/src/reporting_sync/transformer.py:278 |
| C | _import_relationships | 20 | def-store/src/def_store/services/import_export.py:451 |
| C | _flatten_object | 19 | reporting-sync/src/reporting_sync/transformer.py:100 |

## 6. API Consistency

**3 violations** found:

### document-store (3 violations)

- [bulk-first-request] `upload_file` (line 81): POST : write endpoint should accept List[...] body
- [bulk-first-request] `import_documents` (line 45): POST : write endpoint should accept List[...] body
- [bulk-first-request] `cancel_replay` (line 114): DELETE /{session_id}: write endpoint should accept List[...] body


## 7. Dependency Health

### pip-audit — clean (or not installed)

### npm outdated — wip-client (6 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.4 | 4.1.1 |
| eslint | 9.39.4 | 9.39.4 | 10.1.0 |
| typescript | 5.9.3 | 5.9.3 | 6.0.2 |
| typescript-eslint | 8.57.1 | 8.57.2 | 8.57.2 |
| vitest | 3.2.4 | 3.2.4 | 4.1.1 |

### npm outdated — wip-console (14 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @types/node | 25.1.0 | 25.5.0 | 25.5.0 |
| @vitejs/plugin-vue | 5.2.4 | 5.2.4 | 6.0.5 |
| axios | 1.13.5 | 1.13.6 | 1.13.6 |
| eslint | 9.39.4 | 9.39.4 | 10.1.0 |
| eslint-plugin-vue | 9.33.0 | 9.33.0 | 10.8.0 |
| oidc-client-ts | 3.4.1 | 3.5.0 | 3.5.0 |
| pinia | 2.3.1 | 2.3.1 | 3.0.4 |
| typescript | 5.6.3 | 5.6.3 | 6.0.2 |
| typescript-eslint | 8.57.1 | 8.57.2 | 8.57.2 |
| vite | 6.4.1 | 6.4.1 | 8.0.2 |
| vue | 3.5.27 | 3.5.30 | 3.5.30 |
| vue-router | 4.6.4 | 4.6.4 | 5.0.4 |
| vue-tsc | 2.2.12 | 2.2.12 | 3.2.6 |

### npm outdated — wip-react (11 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @tanstack/react-query | 5.90.21 | 5.95.2 | 5.95.2 |
| @types/react | 18.3.28 | 18.3.28 | 19.2.14 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.4 | 4.1.1 |
| eslint | 9.39.4 | 9.39.4 | 10.1.0 |
| jsdom | 28.1.0 | 28.1.0 | 29.0.1 |
| react | 18.3.1 | 18.3.1 | 19.2.4 |
| react-dom | 18.3.1 | 18.3.1 | 19.2.4 |
| typescript | 5.9.3 | 5.9.3 | 6.0.2 |
| typescript-eslint | 8.57.1 | 8.57.2 | 8.57.2 |
| vitest | 3.2.4 | 3.2.4 | 4.1.1 |


## 8. Security Audit

> Planned as a separate dedicated session.

