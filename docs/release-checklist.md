# WIP Release Checklist

Pre-release verification for World In a Pie. Run through this checklist before tagging a new version.

---

## 1. Code Quality

```bash
# Quick checks (no services needed): ruff, shellcheck, vulture, radon, mypy, eslint
./scripts/quality-audit.sh --quick --ci
```

- [ ] `quality-audit.sh --quick --ci` passes (zero regressions above baseline)
- [ ] No new ruff/mypy/eslint warnings introduced
- [ ] No new dead code detected by vulture

## 2. Tests

```bash
# Activate venv
source .venv/bin/activate

# Run all component tests
for svc in registry def-store template-store document-store reporting-sync; do
  echo "=== $svc ===" && cd components/$svc && PYTHONPATH=src pytest tests/ -v && cd ../..
done
```

- [ ] All component test suites pass
- [ ] WIP Console builds without errors: `cd ui/wip-console && npm run build`

## 3. Security

```bash
./scripts/security/production-check.sh
```

- [ ] `production-check.sh` passes on a `--prod` deployment
- [ ] No new security warnings from `pip-audit` or `bandit`
- [ ] Default dev API key (`dev_master_key_for_testing`) rejected in prod mode

## 4. API Consistency

```bash
source .venv/bin/activate
python scripts/api-consistency-check.py
```

- [ ] All endpoints follow bulk-first convention
- [ ] Response schemas match documented models

## 5. Documentation

This is the section that was missing before v1.0.0. Documentation errors compound — app-building AI instances follow docs literally.

### 5a. Curl examples work through Caddy

Pick 3-5 curl examples from each doc and run them against a live deployment (Pi or localhost). Every curl example in docs should target the Caddy proxy (`https://<host>:8443/api/...`), not direct service ports.

```bash
# Quick smoke test against a running instance
HOST="https://pi-poe-8gb.local:8443"  # or https://localhost:8443
API_KEY="your-api-key"

curl -sk "$HOST/api/registry/namespaces" -H "X-API-Key: $API_KEY"
curl -sk "$HOST/api/def-store/terminologies" -H "X-API-Key: $API_KEY"
curl -sk "$HOST/api/template-store/templates" -H "X-API-Key: $API_KEY"
curl -sk "$HOST/api/document-store/documents" -H "X-API-Key: $API_KEY"
curl -sk "$HOST/api/reporting-sync/status" -H "X-API-Key: $API_KEY"
```

- [ ] No docs use direct service ports (`:8001`–`:8005`) in curl examples meant for external clients
- [ ] Exception: component READMEs and MCP server docs may reference direct ports with a caveat note
- [ ] Exception: Dex token requests use `http://localhost:5556/dex/token` (OIDC issuer-specific)

### 5b. No references to non-existent files

```bash
# Extract markdown links and check targets exist
grep -roh '\[.*\]([^)]*\.md)' docs/ | grep -oP '\(([^)]+)\)' | tr -d '()' | sort -u | while read f; do
  # Resolve relative to docs/
  [ -f "docs/$f" ] || [ -f "$f" ] || echo "MISSING: $f"
done
```

- [ ] All inter-doc links resolve to existing files
- [ ] `docs/project-structure.md` directory listing matches reality

### 5c. Terminology consistency

- [ ] `podman-compose` used everywhere (not `docker-compose`) — exception: design docs for future features
- [ ] API field names match current code (`value`/`label`, not old `code`/`name`)
- [ ] Tool count matches reality (currently 68 `@mcp.tool` decorators)
- [ ] Service count and port assignments consistent across all docs

### 5d. Client library READMEs

- [ ] `libs/wip-client/README.md` — `baseUrl` guidance is correct (browser: `''`, Node.js: `'https://host:8443'`)
- [ ] `libs/wip-react/README.md` — Quick Start examples use `baseUrl: ''`
- [ ] No claims about automatic port routing (client does pure path concatenation)

### 5e. App setup guide

- [ ] `docs/WIP_AppSetup_Guide.md` — health check URLs work
- [ ] `scripts/create-app-project.sh` generates correct `.mcp.json` for both dev and prod modes
- [ ] Slash commands in `docs/slash-commands/` don't reference direct ports

## 6. Deployment Verification

Test on at least one real device (Pi or VM), not just localhost.

```bash
# Fresh deployment
./scripts/setup.sh --preset standard --hostname <host> --prod -y

# Verify all services respond
./scripts/security/production-check.sh
```

- [ ] `setup.sh --prod` completes without errors
- [ ] All services start and respond through Caddy
- [ ] WIP Console loads and can log in via OIDC
- [ ] Seed data script works: `python scripts/seed_comprehensive.py --profile minimal`

## 7. Git Hygiene

- [ ] All changes committed to `develop`
- [ ] `develop` is clean (`git status` shows no untracked/modified files)
- [ ] CI passes on `develop` (check Gitea Actions)
- [ ] Merge `develop` → `main`
- [ ] Tag with `vX.Y.Z` on `main`
- [ ] Push to both remotes: `git push gitea main --tags && git push github main --tags`

## 8. Post-Release

- [ ] Verify tag exists on both remotes
- [ ] Update `docs/roadmap.md` if applicable
- [ ] Deploy tagged version to Pi: `git pull && ./scripts/setup.sh --preset full --hostname <host> --prod -y`

---

## Known Gotchas

| Issue | What to check |
|-------|---------------|
| Reporting-sync `/health` not proxied | `/health` is on the app root, not under `/api/reporting-sync/`. Use `/api/reporting-sync/status` through Caddy. |
| OIDC issuer mismatch | `config/dex/config.yaml` `issuer`, `.env` `WIP_AUTH_JWT_ISSUER_URL`, `.env` `VITE_OIDC_AUTHORITY` must all match. |
| Env vars not reloaded | After `.env` changes, `podman-compose down && up -d` (not `restart`). |
| Client library tarballs stale | Rebuild with `npm pack` if READMEs were updated. |
