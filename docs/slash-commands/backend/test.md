Run component tests. Specify a component name, or "all" to run everything.

### Usage

- `/test registry` — run Registry tests
- `/test wip-client` — run @wip/client tests
- `/test all` — run all component tests sequentially

If no target is specified, detect from recent git changes which component was modified and test that.

### Steps

#### 1. Use the canonical test wrapper

`./scripts/wip-test.sh` is the canonical way to run tests. It handles venv activation, `PYTHONPATH`, and exit codes — do **not** hand-roll `cd … && PYTHONPATH=src pytest`. Manual activation also fails silently when run from a subdirectory (the relative `.venv/bin/activate` doesn't resolve correctly). Full rule: `feedback_use_wip_test_sh.md`.

```bash
cd "$(git rev-parse --show-toplevel)"
./scripts/wip-test.sh <component>
```

The wrapper accepts:
- A component name (`registry`, `def-store`, `template-store`, `document-store`, `reporting-sync`, `ingest-gateway`, `mcp-server`)
- A library name (`wip-auth`, `wip-client`, `wip-react`, `wip-proxy`)
- The literal `all` to run every component sequentially
- The literal `deployer` to run the deployer's 400+ tests

#### 2. For TypeScript libraries and the Vue console (which the wrapper doesn't yet cover)

```bash
cd "$(git rev-parse --show-toplevel)/libs/wip-client" && npm test
cd "$(git rev-parse --show-toplevel)/libs/wip-react" && npm test
cd "$(git rev-parse --show-toplevel)/libs/wip-proxy" && npm test
cd "$(git rev-parse --show-toplevel)/ui/wip-console" && npm test
```

#### 3. If "all": run each sequentially

Use `./scripts/wip-test.sh all` for Python components first (they share the venv), then the npm-based libraries listed above. Report pass/fail for each.

#### 4. Report summary

```
Test Results:
  registry:       12 passed, 0 failed
  def-store:      18 passed, 0 failed
  wip-client:    111 passed, 0 failed
  ...

  Total: X passed, Y failed
```

If any tests fail, show the failure output and suggest fixes. Do not propose source changes from a single failure without checking whether the failure is reproducible (per `feedback_reproduce_bugs_first.md`).

### Notes

- Some component tests require MongoDB to be running (integration tests). Run `/wip-status` first if you suspect infrastructure issues.
- The CI equivalent runs via `.gitea/workflows/test.yaml` on `wip-pi.local` — use `/pre-commit` to run an equivalent locally before pushing.
- Pass `pytest` flags via `./scripts/wip-test.sh <component> -- -x` to stop at the first failure or `./scripts/wip-test.sh <component> -- -k "test_name"` to run one specific test.
