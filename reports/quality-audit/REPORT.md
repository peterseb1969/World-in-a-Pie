# WIP Quality Audit Report
Generated: 2026-05-11 19:53 UTC | Commit: e5a3860 | Mode: quick

## Summary

| Dimension | Status | Issues | Baseline | Delta |
|-----------|--------|--------|----------|-------|
| Ruff (Python lint) | WARN | 61 | — | — |
| mypy (Python types) | WARN | 367 | — | — |
| Vulture (dead Python code) | WARN | 1 | — | — |
| ShellCheck | WARN | 76 | — | — |
| ESLint (Vue/TS lint) | WARN | 2 | — | — |
| vue-tsc (Vue types) | PASS | 0 | — | — |
| ts-prune (unused exports) | PASS | 0 | — | — |

## 1. Dead Code

### Python (vulture) — 1 issues

- `libs/wip-auth/src/wip_auth/identity.py:7: unused import 'Token' (90% confidence)`

### TypeScript (ts-prune) — 0 unused exports

No unused exports detected (or ts-prune not available).

## 2. Type Safety

### Python (mypy) — 367 errors

| Component | Errors |
|-----------|--------|
| components-def-store | 61 |
| components-document-store | 132 |
| components-ingest-gateway | 1 |
| components-registry | 39 |
| components-reporting-sync | 33 |
| components-template-store | 96 |
| libs-wip-auth | 5 |

**Top errors:**
- `components/def-store/src/def_store/services/registry_client.py:51: error: Dict entry 0 has incompatible type "str": "str | None"; expected "str": "str"  [dict-item]`
- `components/def-store/src/def_store/services/registry_client.py:121: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/def-store/src/def_store/services/registry_client.py:123: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/def-store/src/def_store/services/registry_client.py:182: error: Returning Any from function declared to return "str"  [no-any-return]`
- `components/def-store/src/def_store/services/registry_client.py:184: error: Returning Any from function declared to return "str"  [no-any-return]`
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

### Python (ruff) — 61 issues

| Rule | Count |
|------|-------|
| B904 | 13 |
| RUF012 | 11 |
| UP042 | 9 |
| SIM102 | 6 |
| I001 | 5 |
| SIM105 | 3 |
| SIM108 | 3 |
| RUF022 | 2 |
| RUF005 | 2 |
| B905 | 2 |
| SIM109 | 2 |
| F401 | 2 |
| SIM115 | 1 |

### Vue/TypeScript (eslint) — 2 issues

| Rule | Count |
|------|-------|
| @typescript-eslint/no-unused-vars | 2 |

### Shell (shellcheck) — 76 issues

| Code | Count |
|------|-------|
| SC1091 | 39 |
| SC2034 | 12 |
| SC2155 | 12 |
| SC2223 | 6 |
| SC2153 | 2 |
| SC2162 | 2 |
| SC1090 | 2 |
| SC2064 | 1 |

## 4. Test Coverage

*Skipped in quick mode. Run without `--quick` for coverage data.*

## 5. Complexity Hotspots

Top 20 functions by cyclomatic complexity (CC >= C):

| Rank | Function | CC | File:Line |
|------|----------|----|-----------|
| F | bulk_create | 63 | document-store/src/document_store/services/document_service.py:1806 |
| F | create_terms_bulk | 47 | def-store/src/def_store/services/terminology_service.py:658 |
| E | _validate_activation_set | 40 | template-store/src/template_store/services/template_service.py:1768 |
| E | register_keys | 37 | registry/src/registry/api/entries.py:299 |
| E | validate_template | 37 | template-store/src/template_store/services/template_service.py:1344 |
| E | import_ontology | 33 | def-store/src/def_store/services/import_export.py:759 |
| D | _template_has_changed | 29 | template-store/src/template_store/services/template_service.py:595 |
| D | update_template | 29 | template-store/src/template_store/services/template_service.py:768 |
| D | transform | 29 | reporting-sync/src/reporting_sync/transformer.py:349 |
| D | _normalize_field_references | 28 | template-store/src/template_store/services/template_service.py:2185 |
| D | traverse_relationships | 28 | document-store/src/document_store/services/document_service.py:1223 |
| D | _search_documents | 27 | reporting-sync/src/reporting_sync/search_service.py:465 |
| D | create_template | 26 | template-store/src/template_store/services/template_service.py:218 |
| D | check_alerts | 26 | reporting-sync/src/reporting_sync/metrics.py:239 |
| D | compute_template_compatibility | 25 | template-store/src/template_store/services/template_service.py:675 |
| D | _validate_field_references | 24 | template-store/src/template_store/services/template_service.py:2276 |
| D | _parse_obo_graph | 23 | def-store/src/def_store/services/import_export.py:609 |
| D | check_all_documents | 23 | document-store/src/document_store/services/integrity_service.py:229 |
| D | _resolve_permission | 22 | registry/src/registry/api/grants.py:51 |
| D | update_term | 22 | def-store/src/def_store/services/terminology_service.py:1015 |

## 6. API Consistency

All 125 endpoints across 19 files are compliant.

## 7. Dependency Health

### pip-audit — clean (or not installed)

### npm outdated — wip-client (6 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.4 | 4.1.6 |
| eslint | 9.39.4 | 9.39.4 | 10.3.0 |
| typescript | 5.9.3 | 5.9.3 | 6.0.3 |
| typescript-eslint | 8.57.1 | 8.59.2 | 8.59.2 |
| vitest | 3.2.4 | 3.2.4 | 4.1.6 |

### npm outdated — wip-react (11 packages)

| Package | Current | Wanted | Latest |
|---------|---------|--------|--------|
| @eslint/js | 9.39.4 | 9.39.4 | 10.0.1 |
| @tanstack/react-query | 5.90.21 | 5.100.10 | 5.100.10 |
| @types/react | 18.3.28 | 18.3.28 | 19.2.14 |
| @vitest/coverage-v8 | 3.2.4 | 3.2.4 | 4.1.6 |
| eslint | 9.39.4 | 9.39.4 | 10.3.0 |
| jsdom | 28.1.0 | 28.1.0 | 29.1.1 |
| react | 18.3.1 | 18.3.1 | 19.2.6 |
| react-dom | 18.3.1 | 18.3.1 | 19.2.6 |
| typescript | 5.9.3 | 5.9.3 | 6.0.3 |
| typescript-eslint | 8.57.1 | 8.59.2 | 8.59.2 |
| vitest | 3.2.4 | 3.2.4 | 4.1.6 |


## 8. Security Audit

> Planned as a separate dedicated session.

