# World In a Pie (WIP)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![Vue 3](https://img.shields.io/badge/Vue-3-brightgreen.svg)](https://vuejs.org/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7-green.svg)](https://www.mongodb.com/)
[![Built with Claude Code](https://img.shields.io/badge/Built_with-Claude_Code-blueviolet.svg)](https://claude.ai/claude-code)

**A universal, template-driven document storage and query system designed to run anywhere — from a Raspberry Pi to the cloud.**

> **This project is an experiment in agentic coding.** The entire platform — six microservices, a Vue 3 console, CI pipeline, deployment automation — was designed and built with AI assistance (Claude Code). Judge for yourself whether the result holds up.

---

## Why WIP Exists

Agentic and vibe-coded apps are cheap to create and easy to throw away. Each one invents its own data model and backend. The apps are disposable — but **the data often isn't**. When the app is retired, the data dies with it or needs painful migration. When you have 20 vibe-coded apps, you have 20 incompatible data silos that can't talk to each other.

**WIP solves this by decoupling data from applications.**

Any app — whether carefully engineered or quickly vibe-coded — can use WIP as its backend. The app defines templates (its schema), stores validated documents, and moves on. When the app is decommissioned, the data stays: validated, versioned, and queryable. When another app needs the same data, it's already there with a consistent structure.

| Without WIP | With WIP |
|---|---|
| Each app invents its own schema | Apps share a common schema layer (templates) |
| App retirement = data loss or migration | App retirement = nothing happens to data |
| Cross-app analysis requires ETL | Cross-app queries work out of the box |
| Data standards diverge over time | Controlled vocabularies enforce consistency |

WIP doesn't force apps into the same data model — it provides a **common backend** where integration is possible where it makes sense, without getting in the way where it doesn't.

---

## What is World In a Pie?

World In a Pie (WIP) is a generic storage layer that can store and query *anything* that can be represented digitally. It achieves this through a layered architecture of definitions, templates, and validated documents.

The name reflects both the philosophy (containing the *world* of data) and the target deployment (a Raspberry *Pi*).

---

## Core Philosophy

> **Store anything, validate everything, query effortlessly.**

WIP is built on three principles:

1. **Universal Storage**: Any data structure can be stored, as long as it conforms to a defined template
2. **Enforced Consistency**: All data is validated against templates that reference controlled vocabularies and ontologies
3. **Federated Identity**: A standalone registry provides identity resolution across distributed instances

---

## Key Benefits

| Benefit | Description |
|---------|-------------|
| **Flexibility** | Store any data structure without schema migrations |
| **Consistency** | Templates enforce data quality at ingestion |
| **Portability** | Runs on a Raspberry Pi or scales to the cloud |
| **Federation** | Registry enables cross-instance identity resolution |
| **Auditability** | Full version history; nothing is ever deleted |
| **Extensibility** | Pluggable storage backends and reporting layers |

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│                     WIP Console (Vue 3 + PrimeVue)          │
│  Terminologies │ Templates │ Documents │ Files │ Reporting  │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS via Caddy (:8443)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Microservices                      │
│              (REST API + Pydantic Validation)                │
├──────────┬──────────┬──────────┬──────────┬────────────────┤
│ Registry │Def-Store │ Template │ Document │ Reporting-Sync │
│  :8001   │  :8002   │  Store   │  Store   │     :8005      │
│          │          │  :8003   │  :8004   │                │
│ ID gen   │ Terms    │ Schemas  │ Storage  │ MongoDB→PgSQL  │
│ Synonyms │ Aliases  │ Rules    │ Files    │ via NATS       │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴───────┬────────┘
     │          │          │          │             │
     └──────────┴──────────┴──────────┴─────────────┘
                             │
        ┌────────────┬───────┼───────┬──────────────┐
        ▼            ▼       ▼       ▼              ▼
   ┌─────────┐ ┌─────────┐ ┌────┐ ┌─────┐    ┌─────────┐
   │ MongoDB │ │PostgreSQL│ │NATS│ │MinIO│    │   Dex   │
   │ :27017  │ │  :5432   │ │    │ │     │    │  (OIDC) │
   └─────────┘ └─────────┘ └────┘ └─────┘    └─────────┘
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Vision](docs/Vision.md) | Philosophy, design principles, and use cases |
| [Architecture](docs/architecture.md) | Detailed system architecture |
| [Data Models](docs/data-models.md) | Conceptual data structures |
| [Authentication](docs/authentication.md) | API keys, JWT/OIDC, Dex configuration |
| [Network Configuration](docs/network-configuration.md) | Hostnames, TLS, and OIDC setup |
| [Reporting Layer](docs/reporting-layer.md) | PostgreSQL sync for analytics |
| [Production Deployment](docs/production-deployment.md) | Secure production setup guide |
| [Namespace Implementation](docs/namespace-implementation.md) | Namespace scoping and data isolation |
| [FAQ](docs/faq.md) | Common issues and solutions |

---

## Quick Start

### Development Setup (Mac/Linux)

```bash
# Clone the repository
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie

# Run setup (auto-detects platform)
./scripts/setup.sh --preset standard --hostname localhost --localhost

# Access the UI
open https://localhost:8443
```

### Production Deployment

```bash
# Deploy with production security (generates random secrets, enables auth)
./scripts/setup.sh --preset standard --hostname wip-pi.local --prod -y

# Validate production readiness
./scripts/security/production-check.sh

# View generated credentials (store securely, then delete)
cat data/secrets/credentials.txt
```

For internet-exposed deployments with Let's Encrypt TLS:
```bash
./scripts/setup.sh --preset standard --hostname wip.example.com --prod \
  --email admin@example.com -y
```

See [Production Deployment Guide](docs/production-deployment.md) for complete instructions.

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + PrimeVue |
| Backend | Python 3.11+ / FastAPI |
| Auth | Dex OIDC (pluggable — any OIDC provider) |
| Document Store | MongoDB |
| Reporting Store | PostgreSQL |
| Object Storage | MinIO (S3-compatible) |
| Message Queue | NATS JetStream |
| Deployment | Podman Compose (primary) / Kubernetes |

---

## Project Status

**Core platform complete and operational.** All services running with:

- OIDC authentication (Dex) + API key dual mode
- Bulk-first API convention across all write endpoints
- PostgreSQL reporting sync via NATS JetStream
- Binary file storage (MinIO) with reference tracking
- Semantic types (email, URL, geo_point, duration, etc.)
- Template draft mode with cascading activation
- Template inheritance with version pinning
- Streaming import/export with cursor pagination
- Namespace-scoped referential integrity
- Ontology support — typed relationships, polyhierarchy, traversal queries

Current focus: Ontology import (OWL/SKOS), file upload (CSV/XLSX), and BI dashboards.

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

---

## Contributing

Contributions are welcome! Please open an issue to discuss proposed changes before submitting a pull request.

Key conventions:
- **Bulk-first API** — all write endpoints accept arrays and return `BulkResponse`
- **Soft-delete** — data is never hard-deleted (except file storage reclamation)
- **Namespace-scoped** — all entities are scoped to a namespace
- See [API Conventions](docs/api-conventions.md) and [Data Models](docs/data-models.md) for details
