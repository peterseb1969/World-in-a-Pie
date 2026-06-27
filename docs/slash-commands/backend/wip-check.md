Run the mechanical code checks — lint, type, dead-code, complexity, tests, security — over either the changed files (the pre-commit gate) or the whole codebase (the quality audit). Consolidates the former `/wip-pre-commit` and `/wip-quality`. For *judgment* on a diff (conventions, missing tests, design), use `/wip-review-changes` — that's the reasoning pass; this is the tooling pass.

**All commands run from the project root.** Start with `cd "$(git rev-parse --show-toplevel)"` so relative paths to `.venv/` and component dirs resolve. Invoke tools directly via `./.venv/bin/<tool>` — no `source .venv/bin/activate` (per `feedback_use_wip_test_sh.md` and CLAUDE.md §10).

### Modes

| Invocation | Scope | Default report |
|---|---|---|
| `/wip-check` (= `--changed`) | files changed vs HEAD + staged + untracked | go/no-go gate |
| `/wip-check --all` | whole codebase | audit summary + baseline compare |

`--fix` (auto-fix where supported) and `--ci` (fail if issue counts exceed the tracked baseline) compose with either scope.

---

### A. `--changed` (default) — the commit gate

#### 1. Identify changed files
```bash
cd "$(git rev-parse --show-toplevel)"
git diff --name-only HEAD; git diff --cached --name-only; git ls-files --others --exclude-standard
```
Categorize by type: Python (`.py`), Shell (`.sh`), TS/JS (`.ts/.js/.tsx/.jsx`), other.

#### 2. Lint / type the changed files
```bash
./.venv/bin/ruff check <file>            # Python — suggest `--fix` for auto-fixable
shellcheck <file>                        # Shell (system-installed, not venv)
cd components/<name> && PYTHONPATH=src ../../.venv/bin/mypy src/ --ignore-missing-imports   # where configured
npx eslint <file>                        # TS/JS — run from the dir with the relevant package.json
```

#### 3. Component tests (changed components only)
```bash
./scripts/wip-test.sh <component>
```
Do not hand-roll `cd && PYTHONPATH=src pytest` — that's the failure pattern the wrapper prevents (`feedback_use_wip_test_sh.md`). `wip-test.sh` already includes mypy where configured.

#### 4. Security scan (changed files)
- `dev_master_key_for_testing` in non-test files (fixture key, not for runtime)
- hardcoded passwords/secrets; `debug=True` in production paths
- newly-introduced env-var names not verified to exist in target code (`feedback_no_invented_config.md`)

#### 5. Report go/no-go
```
Pre-Commit Check:
  Ruff: PASS · Shellcheck: PASS · Mypy: PASS · Tests: PASS (registry 12/12) · ESLint: PASS · Security: PASS
  Result: GO — safe to commit
```
Or `NO-GO — <n> issues must be fixed:` with the specific file:line failures.

---

### B. `--all` — the codebase audit

#### 1. Run the audit script (single source for the linters — do NOT hand-roll)
```bash
./scripts/quality-audit.sh --quick        # ruff, shellcheck, vulture (dead code), radon (complexity), mypy, eslint — no services needed
```

#### 2. Summarize by severity
- **Errors** (must fix): ruff/mypy/eslint errors
- **Warnings** (should fix): complexity, dead-code candidates
- **Info** (nice to fix): style

#### 3. Fix / baseline (compose with the flags)
```bash
./scripts/quality-audit.sh --quick --fix   # ruff --fix + eslint --fix on applicable files
./scripts/quality-audit.sh --quick --ci    # fail if counts exceed the tracked baseline (pre-push sanity)
```

### Notes

- This command does NOT commit or push — it reports. Per CLAUDE.md §4.3, commits/pushes need explicit user approval; a check result is one input to that decision, not a license to push.
- Use `--changed` before a commit (fast, scoped, gate); `--all` after refactors / for quality-debt cleanup / as a pre-push baseline check.
- For *semantic* review of a diff (bulk-first/HTTP-200/auth conventions, missing tests, design judgment), use `/wip-review-changes` — this command runs tools; that one reasons.
