# World In a Pie (WIP)

**A universal, template-driven document storage and query system designed to run anywhere — even on a Raspberry Pi.**

---

## What is World In a Pie?

World In a Pie (WIP) is an extremely generic storage layer that can store and query *anything* that can be represented digitally. It achieves this through a layered architecture of definitions, templates, and validated documents.

The name reflects both the philosophy (containing the *world* of data) and the target deployment (a Raspberry *Pi*).

---

## Core Philosophy

> **Store anything, validate everything, query effortlessly.**

WIP is built on three principles:

1. **Universal Storage**: Any data structure can be stored, as long as it conforms to a defined template
2. **Enforced Consistency**: All data is validated against templates that reference a central ontology
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

**Core functionality complete** — All services operational with OIDC authentication, bulk operations, PostgreSQL reporting sync, binary file storage (MinIO), semantic types, and template draft mode.

Current focus: Namespace scoping improvements and data isolation.

---

## License

*TBD*

---

## Contributing

*TBD*
