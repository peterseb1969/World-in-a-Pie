Analyze uncommitted work for correctness and convention compliance.

### Steps

#### 1. Get change overview
```bash
git diff --stat          # What files changed?
git diff --cached --stat # What's staged?
git status               # Any untracked files?
```

#### 2. Read the full diff
```bash
git diff                 # Unstaged changes
git diff --cached        # Staged changes
```

#### 3. Review each changed file for:

**Convention compliance:**
- **Bulk-first API:** Write endpoints must accept `List[ItemRequest]` and return `BulkResponse`. Never add single-entity write endpoints.
- **HTTP 200 always:** Write endpoints return 200 with per-item status, not 4xx for business errors.
- **Auth decorators:** All endpoints must have `Depends(verify_api_key)` or equivalent from wip-auth.
- **Pagination:** List endpoints must support `page` and `page_size` params (default 50, max 100).

**Security checks:**
- No hardcoded API keys (except `dev_master_key_for_testing` in test files)
- No `debug=True` or verbose error exposure in production paths
- No SQL injection vectors in reporting-sync queries
- File upload endpoints validate content types

**Code quality:**
- No unused imports
- No commented-out code blocks
- Type hints on function signatures (Python services)
- Error handling follows existing patterns in the component

#### 4. Check for missing tests
For each new or modified function/endpoint, check if corresponding tests exist in `tests/`. Suggest test cases for uncovered code.

#### 5. Flag CI risks
Identify anything that would likely fail in CI:
- Ruff violations in Python files
- Missing type annotations that mypy would catch
- Shell scripts without proper quoting

#### 6. Report
```
Change Review:

Files changed: 5 (3 Python, 1 shell, 1 YAML)
Lines: +127 / -34

Convention compliance: OK (all bulk-first, auth present)
Security: OK (no hardcoded keys)
Missing tests: 2 new functions in sync.py lack test coverage
CI risks: 1 ruff warning (unused import in models.py)

Suggestions:
1. Add tests for _process_template_event() in test_sync.py
2. Remove unused import on line 12 of models.py
```
