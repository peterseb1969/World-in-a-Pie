Run CI-equivalent checks locally before committing. This catches issues that would fail in CI.

### Steps

#### 1. Identify changed files
```bash
git diff --name-only HEAD
git diff --cached --name-only
git ls-files --others --exclude-standard
```

Categorize by type: Python (.py), Shell (.sh), TypeScript/JavaScript (.ts, .js, .tsx, .jsx), other.

#### 2. Ruff check (Python)
For each changed Python file:
```bash
source .venv/bin/activate
ruff check <file>
```
Report violations. Suggest `ruff check --fix <file>` for auto-fixable issues.

#### 3. Shellcheck (Shell)
For each changed shell script:
```bash
shellcheck <file>
```

#### 4. Mypy (Python)
For changed Python files in components with type checking configured:
```bash
cd components/{name} && PYTHONPATH=src mypy src/ --ignore-missing-imports
```

#### 5. Component tests
Identify which components have changed files and run their tests:
```bash
cd components/{name} && PYTHONPATH=src pytest tests/ -v
```

#### 6. ESLint (TypeScript/JavaScript)
For changed TS/JS files:
```bash
npx eslint <file>
```

#### 7. Security scan
Check changed files for:
- `dev_master_key_for_testing` in non-test files (grep for it)
- Hardcoded passwords or secrets
- `debug=True` in production code paths

#### 8. Report go/no-go
```
Pre-Commit Check:

  Ruff:       PASS (0 violations)
  Shellcheck: PASS (0 warnings)
  Mypy:       PASS (no errors)
  Tests:      PASS (registry: 12/12, def-store: 18/18)
  ESLint:     PASS (0 errors)
  Security:   PASS (no hardcoded keys)

  Result: GO — safe to commit
```

Or:

```
  Result: NO-GO — 2 issues must be fixed:
  1. Ruff: unused import in components/registry/src/main.py:3
  2. Tests: 1 failure in test_entries.py::test_bulk_create
```
