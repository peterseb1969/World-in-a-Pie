# Glossary

This glossary defines key terms and concepts used throughout the World In a Pie (WIP) documentation.

---

## A

### Active (Status)
The default status for entities in WIP. Active items are visible in queries and available for use. Contrast with [Inactive](#inactive-status) and [Archived](#archived-status).

### API Key
A secret token used for system-to-system authentication, primarily by the [Registry](#registry). Format: `wip_sk_live_abc123...`

### Archive (Store)
Optional secondary storage for old [versions](#version) of documents, terminologies, and templates. Enabled via configuration and governed by [archive policies](#archive-policy).

### Archive Policy
Rules that determine when inactive versions are moved to the archive store. Types:
- **Age-based**: Move after N days
- **Volume-based**: Move when storage exceeds threshold
- **Template-based**: Different rules per template

### Authentik
The default self-hosted authentication provider for WIP. Python-based, full-featured OIDC/SAML provider. See also [Authelia](#authelia).

### Authelia
Lightweight alternative authentication provider for resource-constrained deployments (e.g., Raspberry Pi). Go-based, ~30MB RAM footprint.

---

## B

### Bootstrap
The process of manually seeding the [Def-Store](#def-store) with foundational terminologies before the system can validate its own data. Required because the Def-Store cannot validate against templates that don't yet exist.

---

## C

### Composite Key
A combination of multiple fields that together uniquely identify a document. Defined in the template's `identity_fields` array. Example: `["first_name", "last_name", "birth_date"]`.

### Conditional Rule
A [validation rule](#validation-rule) that applies only when certain conditions are met. Example: "Tax ID is required if country is Germany."

---

## D

### Deactivate
The action of marking an entity as [inactive](#inactive-status). In WIP, nothing is ever truly deleted—only deactivated. This preserves audit trails and enables recovery.

### Def-Store
The foundational persistence layer containing [ontologies](#ontology) and [terminologies](#terminology). Provides the vocabulary that [templates](#template) reference. Must be [bootstrapped](#bootstrap) manually.

### Document
The primary data entity in WIP. A JSON object that:
1. References a [template](#template)
2. Contains data conforming to that template
3. Has an [identity hash](#identity-hash) for versioning
4. Is [validated](#validation) on ingestion

### Document Store
The persistence layer that holds [documents](#document). Default implementation uses MongoDB.

---

## E

### Event
A message published to the [message queue](#message-queue) when something happens in WIP. Types include `document.created`, `document.updated`, `template.created`, etc. Used for [sync](#sync) and notifications.

### Extends
Template inheritance. A template can extend another template, inheriting its fields and rules while adding or overriding its own.

---

## F

### FastAPI
The Python web framework used for WIP's backend. Provides automatic OpenAPI documentation, Pydantic validation, and async support.

### Field
A single data element within a [template](#template). Has a name, type, and optional validation rules. Types include: `string`, `number`, `date`, `term`, `object`, `array`.

---

## G

### (No entries)

---

## H

### Hash
See [Identity Hash](#identity-hash).

---

## I

### Identity
The unique "fingerprint" of a document, computed from its [identity fields](#identity-fields). Two documents with the same identity are considered versions of the same entity.

### ID-as-Synonym
A technique for resolving duplicate registrations. When the same entity was accidentally registered twice with different [Registry](#registry) IDs, one ID can be made a synonym of the other. Both IDs continue to work, but all queries return the [preferred ID](#preferred-id). Avoids the need to fix downstream systems that propagated the duplicate.

### Identity Fields
The template-defined fields that form the [composite key](#composite-key). Specified in the template's `identity_fields` array.

### Identity Hash
A SHA-256 hash computed from the [identity fields](#identity-fields). Algorithm:
1. Sort field names alphanumerically
2. Concatenate as `field1=value1|field2=value2|...`
3. Hash with SHA-256

### Inactive (Status)
Status of a deactivated entity. Inactive items are retained for history but excluded from normal queries. Can optionally be moved to [archive](#archive-store).

---

## J

### JetStream
NATS's persistence layer for guaranteed message delivery. Optionally enabled for critical events.

### JWT
JSON Web Token. Used for user authentication. Contains claims like user ID, email, and roles.

---

## K

### (No entries)

---

## L

### Lookup
A [Registry](#registry) query mode that returns the [source system](#source-system) for a given ID, without fetching the actual document.

---

## M

### Mandatory
A field property indicating the field must be present in a document. Validation fails if a mandatory field is missing.

### Message Queue
Asynchronous communication layer for events. WIP uses [NATS](#nats) by default.

### MicroK8s
Lightweight Kubernetes distribution used for demo and production deployments. Single-node capable.

### MongoDB
Default [document store](#document-store) implementation. Native JSON storage, flexible queries.

---

## N

### Namespace
A logical partition in the [Registry](#registry) that prevents ID collisions. The same ID value can exist in different namespaces as different entities. The **default namespace** is managed by the Registry (ID generation and uniqueness). **Custom namespaces** represent external systems (e.g., "vendor1", "vendor2") with their own ID formats.

### NATS
Lightweight message queue used by WIP. ~10-20MB RAM footprint. Supports pub/sub and request/reply patterns.

---

## O

### Ontology
A formal representation of knowledge as a set of concepts and relationships. In WIP, ontologies are implemented as collections of [terminologies](#terminology).

---

## P

### PostgreSQL
Default [reporting store](#reporting-store) implementation. Provides SQL querying capabilities.

### Preferred ID
When an entity has multiple IDs (due to [synonyms](#synonym) or [ID-as-synonym](#id-as-synonym)), the preferred ID is the canonical identifier returned in query results. Other IDs are returned as "additional IDs." Any ID can be promoted to preferred status.

### PrimeVue
Vue.js component library used for WIP's web UIs. Provides TreeTable, DataTable, and form components.

### Proxy (Query Mode)
A [Registry](#registry) query mode that forwards the request to the [source system](#source-system) and returns the actual document.

### Pydantic
Python data validation library used throughout WIP. Provides declarative schema definition and automatic validation.

---

## Q

### Query Builder
Web UI tool for constructing and executing ad-hoc queries against the [document store](#document-store).

---

## R

### RBAC
Role-Based Access Control. WIP uses roles like `admin`, `architect`, `editor`, `viewer` to control access to different components.

### Registry
Standalone service providing federated identity management. Maps [composite keys](#composite-key) to standardized IDs and tracks which [source system](#source-system) owns each identity.

### Reporting Layer
Optional relational database ([PostgreSQL](#postgresql) by default) that mirrors document data for SQL-based querying. Not the source of truth—a projection for convenience.

### Reporting Store
The database backing the [reporting layer](#reporting-layer). Receives data via [sync](#sync).

### Rule
See [Validation Rule](#validation-rule).

---

## S

### Source System
A WIP instance registered with the [Registry](#registry). Identified by a system ID and API endpoint.

### Status
Lifecycle state of an entity. Values: `active`, `inactive`, `archived`.

### Sync
The process of copying document data to the [reporting store](#reporting-store). Modes:
- **Batch**: Scheduled periodic sync
- **Event**: React to document changes
- **Queue**: Near real-time via message queue

### Synonym
In the [Registry](#registry), a synonym is an alternative [composite key](#composite-key) that resolves to the same entity. Synonyms enable cross-system identity matching. Example: Product "XY" in your system, "AB" at Vendor 1, and "CD" at Vendor 2 can all be synonyms of the same Registry ID. Synonyms can span different [namespaces](#namespace) and have different key structures.

---

## T

### Template
A schema definition that documents must conform to. Contains:
- Field definitions
- Validation rules
- Identity field declarations
- Optional inheritance (`extends`)

### Template Editor
Web UI for creating and managing templates.

### Template Store
Persistence layer for [templates](#template). Templates reference the [Def-Store](#def-store).

### Term
A single concept within a [terminology](#terminology). Has a code (e.g., "M") and label (e.g., "Male"). Can be hierarchical via `parent_id`.

### Terminology
A controlled vocabulary containing related [terms](#term). Example: "Gender" terminology containing terms "Male", "Female", "Other".

### Terminology Reference
A field property linking a [term](#term) type field to a specific [terminology](#terminology). Validation ensures the field value is a valid term from that terminology.

### Traefik
Reverse proxy used in WIP deployments. Provides automatic HTTPS and service discovery.

---

## U

### Upsert
The combined create-or-update behavior based on [identity](#identity). If a document with the same identity hash exists, it's updated (new version created). Otherwise, a new document is created.

---

## V

### Validation
The process of checking a document against its [template](#template) before storage. Includes:
1. Structural validation (valid JSON)
2. Template resolution
3. Field validation (types, mandatory)
4. Rule evaluation (conditional logic)
5. Identity computation

### Validation Engine
Core component that performs [validation](#validation). Returns success/failure with detailed error messages.

### Validation Rule
A constraint that spans multiple fields. Types:
- `conditional_required`: Field required if condition met
- `conditional_value`: Value constrained by condition
- `mutual_exclusion`: Only one of listed fields can have value
- `dependency`: Field requires another field

### Version
A snapshot of an entity at a point in time. When updated, a new version is created and the previous version is [deactivated](#deactivate). Versions share the same [identity hash](#identity-hash).

### Vue
JavaScript framework used for WIP's frontend. Version 3 with Composition API.

---

## W

### WIP
Acronym for **W**orld **I**n a **P**ie. Also a happy coincidence with "Work In Progress."

### World In a Pie
The full name of this system. Reflects:
1. The goal of storing "the world" of data
2. The target deployment on a Raspberry **Pi**

---

## X-Z

### (No entries)

---

## Symbols & Abbreviations

| Abbreviation | Meaning |
|--------------|---------|
| API | Application Programming Interface |
| CRUD | Create, Read, Update, Delete |
| JSON | JavaScript Object Notation |
| JWT | JSON Web Token |
| MQ | Message Queue |
| OIDC | OpenID Connect |
| RBAC | Role-Based Access Control |
| REST | Representational State Transfer |
| SAML | Security Assertion Markup Language |
| SHA | Secure Hash Algorithm |
| SQL | Structured Query Language |
| UI | User Interface |
| UUID | Universally Unique Identifier |
