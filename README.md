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
│                         WEB UIs                             │
│  Ontology Editor │ Template Editor │ Admin │ Query Builder  │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                        │
│              (REST API + Pydantic Validation)               │
└────────────────────────────┬────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Def-Store   │    │  Template    │    │  Document    │
│ (Ontologies) │───►│    Store     │───►│    Store     │
└──────────────┘    └──────────────┘    └──────────────┘
                                               │
                             ┌─────────────────┴─────────────┐
                             ▼                               ▼
                    ┌──────────────┐                ┌──────────────┐
                    │   Registry   │                │  Reporting   │
                    │  (Identity)  │                │    Layer     │
                    └──────────────┘                └──────────────┘
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Philosophy & Vision](docs/philosophy.md) | Core principles and design rationale |
| [Architecture](docs/architecture.md) | Detailed system architecture |
| [Components](docs/components.md) | Deep dive into each component |
| [Technology Stack](docs/technology-stack.md) | Technology choices and rationale |
| [Project Structure](docs/project-structure.md) | Directory layout and component isolation |
| [Deployment](docs/deployment.md) | Deployment configurations and options |
| [Data Models](docs/data-models.md) | Conceptual data structures |
| [Glossary](docs/glossary.md) | Terms and definitions |

---

## Quick Start

*Coming soon — implementation in progress.*

```bash
# Clone the repository
git clone https://github.com/your-org/world-in-a-pie.git

# Start with Docker Compose
docker-compose up -d

# Access the UI
open http://localhost:8080
```

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + PrimeVue |
| Backend | Python 3.11+ / FastAPI |
| Auth | Authentik (or Authelia) |
| Document Store | MongoDB (pluggable) |
| Reporting Store | PostgreSQL (pluggable) |
| Message Queue | NATS |
| Deployment | Docker / MicroK8s |

---

## Project Status

🚧 **Work In Progress** — Architecture defined, implementation starting.

---

## License

*TBD*

---

## Contributing

*TBD*
