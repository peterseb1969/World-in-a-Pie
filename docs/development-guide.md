# WIP Development Guide

Day-to-day development reference for working on the WIP codebase.

---

## Running Tests

The canonical wrapper handles venv activation, `PYTHONPATH`, and exit codes. Use it:

```bash
# Run a component's tests
./scripts/wip-test.sh registry
./scripts/wip-test.sh document-store
./scripts/wip-test.sh all

# TypeScript libraries (use their own scripts)
cd libs/wip-client && npm test
cd libs/wip-react && npm test
```

If you need to run pytest directly (debugging a single test, attaching a debugger), the wrapper's source shows the equivalent invocation; do not hand-roll `cd && PYTHONPATH=src pytest` — that's the form CLAUDE.md §10 explicitly rejects.

For tests against a running container:

```bash
podman exec -it wip-registry pytest /app/tests -v
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

## Change Propagation

When adding or modifying fields/features, changes must propagate across multiple layers (API → client libs → UI → MCP → scripts → tests). See **[Change Propagation Checklist](change-propagation-checklist.md)** for the full list.

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

Provides: `/setup`, `/resume`, `/wip-status`, `/understand`, `/test`, `/quality`, `/review-changes`, `/pre-commit`, `/case`, `/lesson`, `/report`, `/doc-review`

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
| `docs/wip-guide.md` | Operator-facing reference: deploy, auth, networking, storage, apps, security |
| `docs/api-conventions.md` | Bulk-first convention, BulkResponse contract |
| `docs/uniqueness-and-identity.md` | ID generation, Registry synonyms, identity hashing |
| `docs/data-models.md` | Document, template, term data models |
| `docs/mcp-server.md` | MCP tools, resources, AI development workflow |
| `docs/semantic-types.md` | Semantic field types with validation rules |
| `docs/glossary.md` | A–Z terminology reference |
| `docs/install-test-guide.md` | Reproducible install procedure for the v1.0 install-test |
| `docs/WIP_AppSetup_Guide.md` | Setting up app projects that build on WIP |
| `docs/development-guide.md` | This file — tests, quality audit, seed data, agent modes |
| `docs/design/` | Surviving feature design documents (ontology, edge types, etc.) |
| `docs/release-checklist.md` | Pre-release verification checklist (code, tests, security, docs, deploy) |
| `docs/change-propagation-checklist.md` | Cross-layer propagation when adding or modifying a field/feature |
