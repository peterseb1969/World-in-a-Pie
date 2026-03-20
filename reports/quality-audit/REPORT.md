# WIP Quality Audit Report
Generated: 2026-03-20 13:03 UTC | Commit: 2f24669 | Mode: full

## Summary

| Dimension | Status | Issues | Baseline | Delta |
|-----------|--------|--------|----------|-------|
| Ruff (Python lint) | WARN | 171 | — | — |
| mypy (Python types) | WARN | 276 | — | — |
| Vulture (dead Python code) | WARN | 1 | — | — |
| ShellCheck | WARN | 120 | — | — |
| ESLint (Vue/TS lint) | PASS | 0 | — | — |
| vue-tsc (Vue types) | PASS | 0 | — | — |
| ts-prune (unused exports) | PASS | 0 | — | — |

## 1. Dead Code

### Python (vulture) — 1 issues

- `components/document-store/src/document_store/services/replay_service.py:239: unreachable code after 'while' (100% confidence)`

### TypeScript (ts-prune) — 0 unused exports

No unused exports detected (or ts-prune not available).

## 2. Type Safety

### Python (mypy) — 276 errors

| Component | Errors |
|-----------|--------|
| components-def-store | 63 |
| components-document-store | 91 |
| components-ingest-gateway | 1 |
| components-registry | 31 |
| components-reporting-sync | 24 |
| components-template-store | 48 |
| libs-wip-auth | 18 |

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

### Python (ruff) — 171 issues

| Rule | Count |
|------|-------|
| B904 | 68 |
| UP042 | 21 |
| RUF012 | 19 |
| SIM102 | 12 |
| SIM105 | 10 |
| RUF013 | 9 |
| RUF005 | 7 |
| B905 | 7 |
| SIM108 | 6 |
| RUF022 | 3 |
| B007 | 3 |
| SIM109 | 2 |
| SIM118 | 1 |
| SIM113 | 1 |
| RUF034 | 1 |

### Vue/TypeScript (eslint) — 0 issues

No ESLint issues (or not available).

### Shell (shellcheck) — 120 issues

| Code | Count |
|------|-------|
| SC1091 | 39 |
| SC2155 | 24 |
| SC2034 | 15 |
| SC2162 | 9 |
| SC2223 | 6 |
| SC2016 | 6 |
| SC2329 | 4 |
| SC2076 | 4 |
| SC1090 | 3 |
| SC2153 | 2 |

## 4. Test Coverage

### Python

| Component | Stmts | Miss | Cover% |
|-----------|-------|------|--------|
| def-store | 2166 | 796 | 63.3% |
| document-store | 3620 | 1648 | 54.5% |
| registry | 1686 | 1136 | 32.6% |
| reporting-sync | 2401 | 1296 | 46.0% |
| template-store | 1615 | 554 | 65.7% |

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

### npm outdated — wip-client (4 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.4 | 4.1.0 |
| eslint | 9.39.4 | 9.39.4 | 10.0.3 |
| vitest | 3.2.4 | 3.2.4 | 4.1.0 |

### npm outdated — wip-console (13 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @types/node | 25.1.0 | 25.5.0 | 25.5.0 |
| @vitejs/plugin-vue | 5.2.4 | 5.2.4 | 6.0.5 |
| axios | 1.13.5 | 1.13.6 | 1.13.6 |
| eslint | 9.39.4 | 9.39.4 | 10.0.3 |
| eslint-plugin-vue | 9.33.0 | 9.33.0 | 10.8.0 |
| oidc-client-ts | 3.4.1 | 3.5.0 | 3.5.0 |
| pinia | 2.3.1 | 2.3.1 | 3.0.4 |
| typescript | 5.6.3 | 5.6.3 | 5.9.3 |
| vite | 6.4.1 | 6.4.1 | 8.0.1 |
| vue | 3.5.27 | 3.5.30 | 3.5.30 |
| vue-router | 4.6.4 | 4.6.4 | 5.0.4 |
| vue-tsc | 2.2.12 | 2.2.12 | 3.2.6 |

### npm outdated — wip-react (9 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @tanstack/react-query | 5.90.21 | 5.91.3 | 5.91.3 |
| @types/react | 18.3.28 | 18.3.28 | 19.2.14 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.4 | 4.1.0 |
| eslint | 9.39.4 | 9.39.4 | 10.0.3 |
| jsdom | 28.1.0 | 28.1.0 | 29.0.1 |
| react | 18.3.1 | 18.3.1 | 19.2.4 |
| react-dom | 18.3.1 | 18.3.1 | 19.2.4 |
| vitest | 3.2.4 | 3.2.4 | 4.1.0 |


## 8. Security Audit

> Planned as a separate dedicated session.

