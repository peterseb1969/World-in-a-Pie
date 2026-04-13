<p align="center">
  <img src="docs/images/WIP_logo_blue_small.png" alt="World In a Pie" width="200">
</p>

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
| **Auditability** | Full version history; data is soft-deleted by default — configurable per namespace |
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
| [API Conventions](docs/api-conventions.md) | Bulk-first API, BulkResponse contract |
| [Authentication](docs/authentication.md) | API keys, JWT/OIDC, Dex configuration |
| [Network Configuration](docs/network-configuration.md) | Hostnames, TLS, OIDC setup, critical gotchas |
| [Reporting Layer](docs/reporting-layer.md) | PostgreSQL sync for analytics |
| [Development Guide](docs/development-guide.md) | Running tests, quality audit, seed data, agent modes |
| [Production Deployment](docs/production-deployment.md) | Secure production setup guide |
| [App Setup Guide](docs/WIP_AppSetup_Guide.md) | Setting up app projects that build on WIP |
| [Namespace Implementation](docs/namespace-implementation.md) | Namespace scoping and data isolation |
| [Roadmap](docs/roadmap.md) | Future plans, pending features, design docs |
| [FAQ](docs/faq.md) | Common issues and solutions |

---

## Quick Start

### Development Setup (Mac/Linux)

**Prerequisites:** Python 3.11+, Podman (or Docker), and Git.

```bash
# Clone the repository
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie

# Run setup (auto-detects platform)
./scripts/setup.sh --preset standard --hostname localhost --localhost

# Access the UI
open https://localhost:8443
```

### AI Agent Setup

WIP supports two agent roles, each with role-specific instructions, slash commands, and MCP connectivity:

```bash
# Backend developer agent — for working ON WIP itself
./scripts/setup-backend-agent.sh                                    # local MCP
./scripts/setup-backend-agent.sh --target ssh --host pi-poe.local   # SSH proxy
./scripts/setup-backend-agent.sh --target http --host wip-kubi.local # HTTP transport

# App builder agent — for building apps ON TOP of WIP
./scripts/create-app-project.sh /path/to/my-app --name "My App"
```

See [Development Guide](docs/development-guide.md) for details on both modes.

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
- Ontology support — OBO Graph JSON import, typed relationships, polyhierarchy, traversal queries, unified import with auto-format detection

See [Roadmap](docs/roadmap.md) for current priorities and design documents.

---

## One More Thing...

### Streaming Ingestion Gateway

External systems don't need to call REST APIs directly. The **Ingest Gateway** consumes messages from NATS JetStream and routes them to the right service — terminologies, templates, or documents. Fire-and-forget with correlation-based result tracking.

```
External System → NATS (wip.ingest.*) → Ingest Gateway → REST APIs
                                              ↓
                              NATS (wip.ingest.results.{correlation_id})
```

- Batched pull consumption with configurable batch sizes
- Automatic retry with backpressure (explicit ack/nak, max 3 attempts)
- Wrapped or direct payload formats
- Per-message correlation IDs for result tracking
- Health, status, and metrics endpoints

This decouples producers from the WIP API surface — useful for IoT pipelines, ETL jobs, or any system that speaks NATS.

### Business Intelligence with Metabase

WIP's **Reporting-Sync** service streams changes from MongoDB to PostgreSQL in real time via NATS events. Every template becomes a SQL table with flattened fields, term references, and version history. Deploy Metabase on top for instant dashboards:

```bash
# One command to add BI
podman-compose -f deploy/optional/metabase/docker-compose.yml up -d
# → Metabase at http://localhost:3030, pre-connected to wip_reporting
```

No ETL pipelines, no schema management — tables evolve automatically as templates change. Term-aware columns enable cross-template joins through shared vocabularies.

### The Registry: Foreign IDs as First-Class Citizens

The Registry isn't just an ID generator — it's an **identity federation hub**. Any entry can have multiple composite keys (synonyms), each pointing to the same canonical ID. This makes external system integration trivial:

```
WIP Entry (019-uuid-42)
├── Primary:  {"namespace": "wip", "value": "ASPIRIN"}
├── Synonym:  {"vendor": "SAP",  "material_id": "MAT-4291"}
├── Synonym:  {"system": "FDA",  "ndc": "0573-0150-20"}
└── Synonym:  {"legacy_db": "pharma-v1", "drug_code": "ASP-001"}
```

Any of these keys resolves to the same entry — instantly, via indexed hash lookup. When you import data from SAP, the FDA, or a legacy database, you don't force ID remapping. Each system keeps its native identifiers, and the Registry links them:

- **Synonym resolution** — look up by any vendor/external ID, get the canonical WIP ID
- **Source tracking** — each synonym carries `source_info` (system ID, endpoint URL) for provenance
- **ID merging** — discover two entries are the same entity? Merge them; the old ID becomes a synonym, all existing synonyms migrate, nothing breaks
- **Federated search** — search across all namespaces and all synonym keys in one query, with human-readable resolution paths

This means WIP can sit at the center of a multi-vendor environment where every system has its own ID scheme, and act as the universal translator between them — without ever losing track of where each ID came from.

### MCP Server: AI-Native Development

WIP ships with a **Model Context Protocol (MCP) server** that exposes the full platform to AI coding assistants. An AI building an application on top of WIP can discover templates, query documents, manage terminologies, and import data — all through tool calls, without reading WIP source code.

```json
{
  "mcpServers": {
    "wip": {
      "command": "python",
      "args": ["-m", "wip_mcp"],
      "env": { "WIP_BASE_URL": "http://localhost:8001" }
    }
  }
}
```

70+ tools covering all CRUD operations, plus 5 resources for API conventions, data model documentation, and non-obvious behaviours. Supports stdio, SSE, and HTTP streamable transports — validated on local, SSH proxy, and Kubernetes deployments.

> [!CAUTION]
> **Cloud AI + your data: three channels of exposure.**
>
> WIP stores your data locally — your Pi, your server, your laptop. That sovereignty is real *at rest*. But if you use a cloud AI (Claude, ChatGPT, etc.) anywhere in the workflow, your data can leave your machine through three channels:
>
> 1. **Development context** — When an AI reads your sample files to write parsers or understand data formats, your real data (account numbers, transactions, IBANs) enters the AI provider's context window. This happens before MCP is even involved.
> 2. **MCP queries** — When an AI calls WIP tools to query, import, or verify documents, your stored data is sent to the AI provider's servers.
> 3. **Conversational queries** — The "talk to your data" use case. Same exposure as above, but now it's the feature, not a side effect.
>
> Channel 1 is the one people miss. You don't need the MCP server to expose data to a cloud AI. You just need to develop against real data — which every developer does.
>
> **The structural fix exists:** local models (via Ollama or similar) speak the same MCP protocol. When they are capable enough for multi-tool reasoning, your data never leaves your network. WIP's architecture is ready for that today. Until then, **understand the tradeoff and make it a conscious choice.**

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
