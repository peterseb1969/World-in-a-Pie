<p align="center">
  <img src="docs/images/WIP_logo_blue_small.png" alt="World In a Pie" width="200">
</p>

# World In a Pie (WIP)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/UI-React-61dafb.svg)](https://react.dev/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7-green.svg)](https://www.mongodb.com/)
[![Built with Claude Code](https://img.shields.io/badge/Built_with-Claude_Code-blueviolet.svg)](https://claude.ai/claude-code)

**A universal, template-driven document storage and query system designed to run anywhere — from a Raspberry Pi to the cloud.**

> **This project is an experiment in agentic coding.** The entire platform — eight microservices, a web console, CI pipeline, deployment automation — was designed and built with AI assistance (Claude Code). Judge for yourself whether the result holds up.
>
> The AI agents building WIP keep their own working memory in **WIP-KB** — a knowledge-base app built *on top of* WIP (cross-agent cases, session history, design decisions, lessons). So WIP is developed *through* an app that runs on WIP: the platform's agentic memory both proves the model and is essential to the work.

> ### ▶ Try it in ~5 minutes
> **[Single-host quickstart → podman + public GHCR images](docs/deploy/podman/README.md)** — one command brings up the backend **and** the React Console UI on your machine. (There's also a [hot-reload dev guide](docs/deploy/dev/README.md) and a [Kubernetes guide](docs/deploy/k8s/README.md).)

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

WIP is a generic storage layer that can store and query *anything* representable digitally, through a layered architecture of definitions, templates, and validated documents. The name reflects both the philosophy (containing the *world* of data) and the target deployment (a Raspberry *Pi*).

> **Store anything, validate everything, query effortlessly.**

Three principles:

1. **Universal storage** — any data structure, as long as it conforms to a defined template
2. **Enforced consistency** — every document is validated against templates that reference controlled vocabularies and ontologies
3. **Federated identity** — a standalone registry resolves identity across systems and (eventually) distributed instances

| Benefit | Description |
|---------|-------------|
| **Flexibility** | Store any data structure without schema migrations |
| **Consistency** | Templates enforce data quality at ingestion |
| **Portability** | Runs on a Raspberry Pi or scales to the cloud |
| **Federation** | Registry enables cross-system / cross-instance identity resolution |
| **Auditability** | Full version history; soft-delete by default (configurable per namespace) |

---

## Architecture at a Glance

```
┌───────────────────────────────────────────────────────────────┐
│   Web apps on top of WIP   (React Console · WIP-KB · your app) │
└───────────────────────────────┬───────────────────────────────┘
                                 │  HTTPS via Caddy (:8443)
                                 ▼
┌───────────────────────────────────────────────────────────────┐
│   Auth-Gateway   — API-key + OIDC/JWT in front of every call   │
└───────────────────────────────┬───────────────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────┐
│                     FastAPI microservices                      │
│   Registry · Def-Store · Template-Store · Document-Store       │
│   Reporting-Sync · Ingest-Gateway · MCP Server                 │
└───────────────────────────────┬───────────────────────────────┘
                                 ▼
    MongoDB   ·   PostgreSQL   ·   NATS JetStream   ·   MinIO   ·   Dex (OIDC)
```

Eight WIP services (Auth-Gateway + the seven above), fronted by Caddy, over standard infrastructure. The admin UI is the **React Console**, deployed as an app on top of the platform — not baked into the backend.

---

## Get Started

| I want to… | Go to |
|---|---|
| **Run WIP on one machine, fast** | **[Single-host quickstart (podman + GHCR)](docs/deploy/podman/README.md)** ← start here |
| Full operator reference (deploy, auth, networking, storage, security) | [WIP Guide](docs/wip-guide.md) |
| Develop WIP or an app (hot-reload) | [Dev deployment guide](docs/deploy/dev/README.md) |
| Deploy to Kubernetes | [Kubernetes guide](docs/deploy/k8s/README.md) |

**Set up an AI agent** (WIP supports two roles — working *on* WIP, and building apps *on top of* it):

```bash
# Backend developer agent — for working ON WIP itself
./scripts/setup-backend-agent.sh                                     # local MCP
./scripts/setup-backend-agent.sh --target ssh  --host pi-poe.local   # SSH proxy
./scripts/setup-backend-agent.sh --target http --host wip-kubi.local # HTTP transport

# App builder agent — for building apps ON TOP of WIP
./scripts/create-app-project.sh /path/to/my-app --name "My App"
```

See the [Development Guide](docs/development-guide.md) for both modes.

---

## Documentation

| Document | Description |
|----------|-------------|
| **[Single-host quickstart](docs/deploy/podman/README.md)** | **Fastest path: podman + GHCR images on one host** |
| [Vision](docs/Vision.md) | Philosophy, design principles, and use cases |
| [WIP Guide](docs/wip-guide.md) | Operator reference — install, auth, networking, storage, apps, security |
| [Data Models](docs/data-models.md) | Conceptual data structures |
| [API Conventions](docs/api-conventions.md) | Bulk-first API, `BulkResponse` contract |
| [MCP Server](docs/mcp-server.md) | The AI-facing tool surface |
| [Glossary](docs/glossary.md) | A–Z terminology reference |
| [Development Guide](docs/development-guide.md) | Tests, quality audit, seed data, agent modes |
| [App Setup Guide](docs/WIP_AppSetup_Guide.md) | Setting up app projects that build on WIP |

---

## Hardware

WIP runs anywhere from a Raspberry Pi to a cloud VM. Rough floor: **Raspberry Pi 5 (8 GB+)** — Pi 4 is not supported; **macOS 16 GB+** (a 4 GB Podman machine is comfortable); **Linux VM ~8 GB+** with real local disk. On a Pi, an **SSD is mandatory** — SD-card storage guts MongoDB/NATS/PostgreSQL/MinIO throughput.

Host-prep specifics (Pi SSD + `fstab`, Podman-machine sizing, VM sizing) live in the **[quickstart's Host prep appendix](docs/deploy/podman/README.md#appendix-host-prep-pi-ssd-podman-sizing)** and the [WIP Guide](docs/wip-guide.md) (storage & backup).

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| UI | React (the React Console app) |
| Backend | Python 3.11+ / FastAPI |
| Auth | Dex OIDC (pluggable — any OIDC provider) + API keys |
| Document Store | MongoDB |
| Reporting Store | PostgreSQL |
| Object Storage | MinIO (S3-compatible) |
| Message Queue | NATS JetStream |
| Deployment | Podman Compose (primary) / Kubernetes |

---

## Project Status

**Core platform complete and operational.** All services running with:

- OIDC authentication (Dex) + API-key dual mode
- Bulk-first API convention across all write endpoints
- PostgreSQL reporting sync via NATS JetStream
- Binary file storage (MinIO) with reference tracking
- Semantic types (email, URL, geo_point, duration, …)
- Template draft mode with cascading activation; inheritance with version pinning
- Streaming import/export with cursor pagination
- Namespace-scoped referential integrity
- Ontology support — OBO Graph JSON import, typed relationships, polyhierarchy, traversal queries

See the [WIP Guide](docs/wip-guide.md) for the canonical operator reference and `git log` for current priorities.

---

## One More Thing…

### Streaming Ingestion Gateway

External systems don't need to call REST APIs directly. The **Ingest Gateway** consumes messages from NATS JetStream and routes them to the right service — terminologies, templates, or documents. Fire-and-forget with correlation-based result tracking, batched pull consumption, automatic retry with backpressure, and per-message correlation IDs. This decouples producers from the WIP API surface — useful for IoT pipelines, ETL jobs, or anything that speaks NATS.

### Business Intelligence on PostgreSQL

**Reporting-Sync** streams changes from MongoDB to PostgreSQL in real time via NATS events. Every template becomes a SQL table with flattened fields, term references, and version history — point any BI tool that speaks PostgreSQL at the `wip_reporting` database for instant dashboards. No ETL pipelines, no schema management; tables evolve automatically as templates change, and term-aware columns enable cross-template joins through shared vocabularies.

### The Registry: Foreign IDs as First-Class Citizens

The Registry isn't just an ID generator — it's an **identity federation hub**. Any entry can have multiple composite keys (synonyms), each resolving to the same canonical ID:

```
WIP Entry (019-uuid-42)
├── Primary:  {"namespace": "wip", "value": "ASPIRIN"}
├── Synonym:  {"vendor": "SAP",  "material_id": "MAT-4291"}
├── Synonym:  {"system": "FDA",  "ndc": "0573-0150-20"}
└── Synonym:  {"legacy_db": "pharma-v1", "drug_code": "ASP-001"}
```

Import from SAP, the FDA, or a legacy database without forcing ID remapping — each system keeps its native identifiers and the Registry links them (synonym resolution, `source_info` provenance, one-way ID merging, federated search across namespaces and synonym keys). WIP can sit at the center of a multi-vendor environment as the universal translator between ID schemes — without ever losing track of where each ID came from.

### MCP Server: AI-Native Development

WIP ships a **Model Context Protocol (MCP) server** exposing the full platform to AI coding assistants — **94 tools + 5 resources** covering CRUD, terminologies, import, and non-obvious-behaviour docs, so an AI can build on WIP through tool calls without reading source. Transports: stdio, SSE, and HTTP streamable (validated on local, SSH-proxy, and Kubernetes deployments). Wire it up with `./scripts/setup-backend-agent.sh`; see [docs/mcp-server.md](docs/mcp-server.md) for transports and configuration.

> [!CAUTION]
> **Cloud AI + your data: three channels of exposure.**
>
> WIP stores your data locally — your Pi, your server, your laptop. That sovereignty is real *at rest*. But if you use a cloud AI (Claude, ChatGPT, etc.) anywhere in the workflow, your data can leave your machine through three channels:
>
> 1. **Development context** — when an AI reads your sample files to write parsers or understand formats, your real data (account numbers, transactions, IBANs) enters the provider's context window. This happens before MCP is even involved.
> 2. **MCP queries** — when an AI calls WIP tools to query, import, or verify documents, your stored data is sent to the provider's servers.
> 3. **Conversational queries** — the "talk to your data" use case. Same exposure, but now it's the feature, not a side effect.
>
> Channel 1 is the one people miss. You don't need the MCP server to expose data to a cloud AI — you just need to develop against real data, which every developer does.
>
> **The structural fix exists:** local models (via Ollama or similar) speak the same MCP protocol. When they're capable enough for multi-tool reasoning, your data never leaves your network. Until then, **make the tradeoff a conscious choice.**

---

## Common Questions

### Who is WIP for right now?

Honestly: technically curious people who want to watch an experiment unfold, and professionals in regulated data domains — particularly clinical trial operations — who recognise the data-interoperability problems WIP is designed to solve. WIP is currently a working experiment, not a packaged product.

### What are the best use cases for WIP?

- **Multiple data sources with different ID schemes** that need to coexist and interoperate
- **Regulated or audit-sensitive data** where full history and provenance matter
- **Multiple applications sharing the same underlying data**, where consistency matters more than raw speed
- **AI-generated application development**, where enforcing schema discipline before any data is written is valuable
- **Long-lived data** that must outlive the apps that created it
- **Controlled-vocabulary requirements** — anywhere "list of values" problems have burned you

Concrete domains: clinical trial data management, configuration management, master data management, compliance records, IoT collection, research repositories, multi-tenant SaaS backends.

### What are bad use cases for WIP?

- **You need raw write throughput above all** — WIP validates and registers every document; that's overhead by design
- **Your data model is truly simple and stable** — five columns don't need a generic engine
- **You need a workflow engine** — WIP stores and validates data; it doesn't orchestrate processes
- **You need a general-purpose event bus** — WIP receives events via the Ingest Gateway, but isn't your app's event hub
- **You want a managed cloud service** — WIP is self-hosted by design
- **You're building something purely throwaway** — if the data doesn't matter after the app, the structure is unnecessary overhead

### Is WIP ready for enterprise use?

The architecture is enterprise-grade *in its design principles* — OIDC auth, namespace isolation, audit trails, controlled vocabularies, referential integrity, federation-ready identity. But WIP is currently maintained by a single developer as an experiment: no commercial support, no SLA, no ops team. The blueprint is sound; the productisation is not there yet.

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

## Contributing

Contributions welcome — please open an issue to discuss proposed changes before a PR. Key conventions:

- **Bulk-first API** — all write endpoints accept arrays and return `BulkResponse`
- **Soft-delete** — data is never hard-deleted (except file-storage reclamation)
- **Namespace-scoped** — all entities are scoped to a namespace
- See [API Conventions](docs/api-conventions.md) and [Data Models](docs/data-models.md) for details
</content>
