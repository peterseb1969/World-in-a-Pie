Run component tests. Specify a component name, or "all" to run everything.

### Usage

- `/test registry` — run Registry tests
- `/test wip-client` — run @wip/client tests
- `/test all` — run all component tests sequentially

If no target is specified, detect from recent git changes which component was modified and test that.

### Steps

#### 1. Activate venv
```bash
source .venv/bin/activate
```

#### 2. Run tests for the specified target

**Python components:**
```bash
cd components/{name} && PYTHONPATH=src pytest tests/ -v
```

Components: `registry`, `def-store`, `template-store`, `document-store`, `reporting-sync`, `ingest-gateway`, `mcp-server`

**Python libraries:**
```bash
cd libs/wip-auth && PYTHONPATH=src pytest tests/ -v
```

**TypeScript libraries:**
```bash
cd libs/wip-client && npm test
cd libs/wip-react && npm test
cd libs/wip-proxy && npm test
```

**Console (Vue):**
```bash
cd ui/wip-console && npm test
```

#### 3. If "all": run each sequentially
Run Python components first (they share the venv), then TypeScript libraries. Report pass/fail for each.

#### 4. Report summary
```
Test Results:
  registry:       12 passed, 0 failed
  def-store:      18 passed, 0 failed
  wip-client:    111 passed, 0 failed
  ...

  Total: X passed, Y failed
```

If any tests fail, show the failure output and suggest fixes.

### Notes
- Some component tests require MongoDB to be running (integration tests)
- The CI equivalent runs via `.gitea/workflows/test.yaml`
- Use `pytest -x` to stop at the first failure for faster debugging
- Use `pytest -k "test_name"` to run a specific test
