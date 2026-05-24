Run CI-equivalent checks locally before committing. This catches issues that would fail in CI.

**All commands here run from the project root.** Start with `cd "$(git rev-parse --show-toplevel)"` to ensure relative paths to `.venv/` and component directories resolve correctly. Direct invocation via `./.venv/bin/<tool>` avoids the discipline trap of activating venv from the wrong directory (per `feedback_use_wip_test_sh.md` and CLAUDE.md §10).

### Steps

#### 1. Identify changed files

```bash
cd "$(git rev-parse --show-toplevel)"
git diff --name-only HEAD
git diff --cached --name-only
git ls-files --others --exclude-standard
```

Categorize by type: Python (`.py`), Shell (`.sh`), TypeScript/JavaScript (`.ts`, `.js`, `.tsx`, `.jsx`), other.

#### 2. Ruff check (Python)

For each changed Python file, invoke ruff via the venv's binary directly — no `source .venv/bin/activate` needed:

```bash
./.venv/bin/ruff check <file>
```

Report violations. Suggest `./.venv/bin/ruff check --fix <file>` for auto-fixable issues.

#### 3. Shellcheck (Shell)

For each changed shell script:

```bash
shellcheck <file>
```

(`shellcheck` is system-installed, not in the venv.)

#### 4. Mypy (Python)

For changed Python files in components with type checking configured:

```bash
cd "$(git rev-parse --show-toplevel)/components/{name}" && PYTHONPATH=src ../../.venv/bin/mypy src/ --ignore-missing-imports
```

Or simpler, run the tests via wrapper which already includes mypy where configured: `./scripts/wip-test.sh <component>`.

#### 5. Component tests

Identify which components have changed files and run their tests via the canonical wrapper:

```bash
cd "$(git rev-parse --show-toplevel)"
./scripts/wip-test.sh <component>
```

Do not hand-roll `cd && PYTHONPATH=src pytest` — that's the failure pattern the wrapper exists to prevent (full rule at `feedback_use_wip_test_sh.md`).

#### 6. ESLint (TypeScript/JavaScript)

For changed TS/JS files:

```bash
npx eslint <file>
```

Run from the directory containing the relevant `package.json` (e.g., `libs/wip-client/`, `ui/wip-console/`).

#### 7. Security scan

Check changed files for:

- `dev_master_key_for_testing` in non-test files (grep for it — it's a fixture key, not for runtime use)
- Hardcoded passwords or secrets
- `debug=True` in production code paths
- Newly-introduced env-var names that haven't been verified to exist in target code (per `feedback_no_invented_config.md`)

#### 8. Report go/no-go

```
Pre-Commit Check:

  Ruff:       PASS (0 violations)
  Shellcheck: PASS (0 warnings)
  Mypy:       PASS (no errors)
  Tests:      PASS (registry: 12/12, def-store: 18/18)
  ESLint:     PASS (0 errors)
  Security:   PASS (no hardcoded keys, no fabricated env names)

  Result: GO — safe to commit
```

Or:

```
  Result: NO-GO — 2 issues must be fixed:
  1. Ruff: unused import in components/registry/src/main.py:3
  2. Tests: 1 failure in test_entries.py::test_bulk_create
```

### Notes

- This command does NOT commit or push. It reports go/no-go. Per CLAUDE.md §4.3, commits and pushes require explicit user approval — pre-commit verification is one input to that decision, not a license to push.
