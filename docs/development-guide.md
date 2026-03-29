# WIP Development Guide

Day-to-day development reference for working on the WIP codebase.

---

## Running Tests

```bash
# Activate venv first
source .venv/bin/activate

# Run a component's tests locally
cd components/registry && PYTHONPATH=src pytest tests/ -v

# Run inside container
podman exec -it wip-registry pytest /app/tests -v

# TypeScript libraries
cd libs/wip-client && npm test
cd libs/wip-react && npm test
```

CI runs all component tests via `.gitea/workflows/test.yaml`.

---

## Quality Audit

```bash
# Quick check (no services needed): ruff, shellcheck, vulture, radon, mypy, eslint
./scripts/quality-audit.sh --quick

# Full check (services running): adds pytest coverage, API consistency
./scripts/quality-audit.sh

# CI mode (fails if issues exceed baseline)
./scripts/quality-audit.sh --quick --ci

# Auto-fix what can be fixed
./scripts/quality-audit.sh --quick --fix
```

---

## Security Checks

```bash
# Validate production hardening
./scripts/security/production-check.sh

# Generate a new API key
./scripts/security/generate-api-key.sh
```

---

## Seed Data

```bash
source .venv/bin/activate
pip install faker requests
python scripts/seed_comprehensive.py --profile standard
```

| Profile | Documents | Use case |
|---------|-----------|----------|
| `minimal` | 50 | Quick smoke test |
| `standard` | 500 | Normal development |
| `full` | 2000 | Integration testing |
| `performance` | 100k | Load testing, bulk import tuning |

---

## Agent Modes

WIP supports two agent configurations, each with role-specific CLAUDE.md, slash commands, and MCP connectivity:

### Backend Developer Agent

For working ON WIP itself — modifying services, libraries, infrastructure.

```bash
./scripts/setup-backend-agent.sh [--target local|ssh|http] [--host HOST]
```

Provides: `/setup`, `/resume`, `/wip-status`, `/understand`, `/test`, `/quality`, `/review-changes`, `/pre-commit`, `/roadmap`

### App Builder Agent

For building applications ON TOP of WIP — using MCP tools to create terminologies, templates, documents, and building React/TypeScript frontends.

```bash
./scripts/create-app-project.sh /path/to/my-app --name "My App"
```

Provides: `/explore`, `/design-model`, `/implement`, `/build-app`, `/improve`, `/document`, `/export-model`, `/bootstrap`, `/add-app`, `/resume`, `/wip-status`, `/analyst`

See `docs/WIP_AppSetup_Guide.md` for the full app builder guide.

---

## Documentation Index

| Document | What it covers |
|----------|---------------|
| `docs/architecture.md` | System architecture, service interactions |
| `docs/api-conventions.md` | Bulk-first convention, BulkResponse contract |
| `docs/uniqueness-and-identity.md` | ID generation, Registry synonyms, identity hashing |
| `docs/data-models.md` | Document, template, term data models |
| `docs/authentication.md` | Auth modes, API keys, JWT/OIDC configuration |
| `docs/network-configuration.md` | 4 deployment scenarios, OIDC setup, critical gotchas |
| `docs/production-deployment.md` | Production hardening guide |
| `docs/mcp-server.md` | MCP tools, resources, AI development workflow |
| `docs/reporting-layer.md` | MongoDB → PostgreSQL sync architecture |
| `docs/semantic-types.md` | 7 semantic field types with validation rules |
| `docs/bulk-import-tuning.md` | Tuning batch sizes for 100k+ imports |
| `docs/WIP_AppSetup_Guide.md` | Setting up app projects that build on WIP |
| `docs/development-guide.md` | This file — tests, quality audit, seed data, agent modes |
| `docs/roadmap.md` | Future plans, pending features, design docs |
| `docs/security/` | Key rotation, encryption at rest |
| `docs/design/` | Feature design documents (ontology, replay, draft mode, etc.) |
| `docs/release-checklist.md` | Pre-release verification checklist (code, tests, security, docs, deploy) |
