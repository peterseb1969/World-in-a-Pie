Run the quality audit. This checks code quality across the entire codebase using ruff, shellcheck, vulture, radon, mypy, and eslint.

### Steps

#### 1. Run quick audit
```bash
./scripts/quality-audit.sh --quick
```

This runs without needing services to be up: ruff (Python linting), shellcheck (shell scripts), vulture (dead code), radon (complexity), mypy (type checking), eslint (TypeScript/JavaScript).

#### 2. Parse and summarize results
Read the output and group issues by category:
- **Errors** — must fix (ruff errors, mypy errors, eslint errors)
- **Warnings** — should fix (complexity warnings, dead code candidates)
- **Info** — nice to fix (style suggestions)

#### 3. Suggest fixes
For auto-fixable issues, suggest:
```bash
./scripts/quality-audit.sh --quick --fix
```

This runs `ruff --fix` and `eslint --fix` on applicable files.

#### 4. Compare against baseline (optional)
If the user wants CI-mode checking:
```bash
./scripts/quality-audit.sh --quick --ci
```

This fails if issue counts exceed the tracked baseline. Useful before pushing.

### When to use
- Before committing significant changes
- After refactoring
- When cleaning up code quality debt
- As a pre-push sanity check (lighter than `/pre-commit`)
