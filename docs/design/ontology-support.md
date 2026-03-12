# Design: Ontology Support

**Status:** Implemented

## Motivation

Users may want to re-use existing ontologies (SNOMED CT, ICD, Gene Ontology, SKOS thesauri, etc.) within WIP. Today, WIP can import **terminologies** (flat or single-parent hierarchies) but cannot faithfully represent **ontologies**, which have:

- **Polyhierarchy** — a concept can have multiple parents (e.g., "Viral pneumonia" is_a "Pneumonia" AND is_a "Viral respiratory infection")
- **Typed relationships** — `is_a`, `part_of`, `finding_site`, `maps_to` are semantically distinct
- **Cross-terminology links** — concepts in one vocabulary relate to concepts in another

The single `parent_term_id` field on Term only supports one parent within one terminology. This makes ontology import lossy.

## Goals

1. Import standard ontologies (OBO Graph JSON) without losing structural information
2. Support polyhierarchy (multiple parents per concept)
3. Support typed relationships between terms, including cross-terminology
4. Enable traversal queries (ancestors, descendants, transitive closure)
5. Leverage existing WIP primitives — no unnecessary duplication
6. Maintain backward compatibility — existing terminologies and `parent_term_id` continue to work

## What WIP Covers

| Ontology Concept | WIP Mechanism | Status |
|---|---|---|
| Concepts | Terms | ✅ |
| Preferred labels | `term.value` | ✅ |
| Alternative labels | `term.aliases` | ✅ |
| Definitions | `term.description` | ✅ |
| Multi-language labels | `term.translations` | ✅ |
| Custom annotations | `term.metadata` | ✅ |
| Single-parent hierarchy | `term.parent_term_id` | ✅ |
| Identity / exact match | Registry synonyms | ✅ |
| External code mapping | Registry synonyms | ✅ |
| Deprecation + replacement | `term.replaced_by_term_id` | ✅ |
| Bulk import | Import/export endpoints | ✅ |
| Multi-parent hierarchy | `TermRelationship` (is_a) | ✅ |
| Typed relationships | `TermRelationship` + `_ONTOLOGY_RELATIONSHIP_TYPES` | ✅ |
| Cross-terminology links | `TermRelationship` | ✅ |
| Transitive traversal | BFS with cycle detection | ✅ |
| OBO Graph JSON import | CLI script + API endpoint + UI | ✅ |
| Round-trip export/import | JSON with relationships + aliases | ✅ |

## Model: TermRelationship

A lightweight edge model stored in the `term_relationships` collection in Def-Store's MongoDB database.

```python
class TermRelationship(Document):
    """A typed, directed relationship between two terms."""

    namespace: str                          # Scoped like all WIP entities
    source_term_id: str                     # The subject term
    target_term_id: str                     # The object term
    relationship_type: str                  # Value from _ONTOLOGY_RELATIONSHIP_TYPES terminology
    relationship_value: Optional[str]       # Denormalized value for display

    # Denormalized for query efficiency
    source_terminology_id: Optional[str]
    target_terminology_id: Optional[str]
    metadata: dict[str, Any] = {}           # Provenance, confidence, source ontology
    status: str = "active"                  # active, inactive

    # Audit
    created_at: datetime
    created_by: Optional[str]

    class Settings:
        name = "term_relationships"
        indexes = [
            # Find all relationships FROM a term
            IndexModel([("namespace", 1), ("source_term_id", 1), ("relationship_type", 1)]),
            # Find all relationships TO a term (reverse lookup)
            IndexModel([("namespace", 1), ("target_term_id", 1), ("relationship_type", 1)]),
            # Uniqueness: one relationship of each type between two terms
            IndexModel(
                [("namespace", 1), ("source_term_id", 1), ("target_term_id", 1), ("relationship_type", 1)],
                unique=True,
            ),
            # By terminology (for ontology-wide queries)
            IndexModel([("namespace", 1), ("source_terminology_id", 1), ("relationship_type", 1)]),
            # Status filter
            IndexModel([("namespace", 1), ("status", 1)]),
        ]
```

## Relationship Types

Relationship types are validated against the system terminology `_ONTOLOGY_RELATIONSHIP_TYPES`, which is auto-created at Def-Store startup. New types are auto-created during ontology import.

### Built-in Types

| Value | Label | Inverse | Transitive | Description |
|---|---|---|---|---|
| `is_a` | Is a | `has_subtype` | Yes | Subsumption (SKOS broader) |
| `has_subtype` | Has subtype | `is_a` | Yes | Inverse of is_a |
| `part_of` | Part of | `has_part` | Yes | Mereological |
| `has_part` | Has part | `part_of` | Yes | Inverse of part_of |
| `maps_to` | Maps to | `mapped_from` | No | Cross-vocabulary mapping |
| `mapped_from` | Mapped from | `maps_to` | No | Inverse of maps_to |
| `related_to` | Related to | `related_to` | No | Associative (SKOS related) |
| `finding_site` | Finding site | — | No | SNOMED-style attribute |
| `causative_agent` | Causative agent | — | No | SNOMED-style attribute |

### Auto-Created Types (from OBO imports)

OBO ontologies bring additional relationship types that are auto-created as terms in `_ONTOLOGY_RELATIONSHIP_TYPES`:

- `regulates`, `positively_regulates`, `negatively_regulates` — Gene Ontology
- `capable_of`, `capable_of_part_of`, `has_input`, `has_output` — GO biological process
- `involved_in`, `regulates_activity_of` — GO molecular function
- Any unknown OBO predicate URI is converted to compact form (e.g., `BFO:0000066`, `RO:0002092`)

### Validation

`create_relationships` validates every `relationship_type` against terms in `_ONTOLOGY_RELATIONSHIP_TYPES`. Unknown types are rejected with a clear error listing valid types.

**Per-terminology restriction** of allowed relationship types is not enforced by WIP — this is delegated to client applications. The global terminology defines what types *exist*; clients decide which types to *use* per context.

## Registry Synonyms for Identity Mapping

External ontology codes are mapped via Registry synonyms — **not** via TermRelationship. This keeps a clean separation:

- **Synonyms** = "these are the same entity" (identity)
- **Relationships** = "these are related entities" (semantics)

## Interaction with parent_term_id

`parent_term_id` remains as-is for simple single-parent terminologies. It is **not** deprecated — many terminologies are flat or single-hierarchy and don't need the relationship model.

For ontology imports with polyhierarchy, the importer:
1. Leaves `parent_term_id` empty
2. Creates `is_a` TermRelationship records for ALL parents

Traversal queries always check both `parent_term_id` AND `is_a` relationships to give a unified view.

## API

All relationship endpoints live under `/api/def-store/ontology/`.

### Relationship CRUD

```
# Create relationships (bulk-first, as per WIP convention)
POST /api/def-store/ontology/relationships
Body: [
    {
        "source_term_id": "T-000123",
        "target_term_id": "T-000456",
        "relationship_type": "is_a"
    },
    ...
]
Response: BulkResponse

# List relationships for a term
GET /api/def-store/ontology/relationships?term_id=T-000123&direction=outgoing
GET /api/def-store/ontology/relationships?term_id=T-000123&direction=incoming
GET /api/def-store/ontology/relationships?term_id=T-000123&relationship_type=is_a

# List all relationships in namespace (paginated)
GET /api/def-store/ontology/relationships/all?namespace=wip&relationship_type=is_a

# Delete relationships (bulk)
DELETE /api/def-store/ontology/relationships
Body: [{"source_term_id": "T-000123", "target_term_id": "T-000456", "relationship_type": "is_a"}]
```

### Traversal Queries

```
# Ancestors: follow is_a (and parent_term_id) upward, transitively
GET /api/def-store/ontology/terms/{term_id}/ancestors?relationship_type=is_a&max_depth=10

Response: {
    "term_id": "T-000123",
    "relationship_type": "is_a",
    "direction": "ancestors",
    "nodes": [
        {"term_id": "T-000456", "value": "Pneumonia", "depth": 1, "path": ["T-000123", "T-000456"]},
        {"term_id": "T-000789", "value": "Lung Disease", "depth": 2, "path": ["T-000123", "T-000456", "T-000789"]},
        ...
    ],
    "total": 5,
    "max_depth_reached": false
}

# Descendants: follow is_a downward
GET /api/def-store/ontology/terms/{term_id}/descendants?relationship_type=is_a&max_depth=5

# Direct parents only (immediate, non-transitive)
GET /api/def-store/ontology/terms/{term_id}/parents

# Direct children only
GET /api/def-store/ontology/terms/{term_id}/children
```

Traversal uses iterative breadth-first search with cycle detection. `max_depth` defaults to 10, capped at 50.

### Ontology Import

Three import paths:

#### 1. OBO Graph JSON Import (API)

```
POST /api/def-store/import-export/import-ontology
Body: <OBO Graph JSON>
Query parameters:
  terminology_value: str        # e.g., "HPO", "GO" (auto-detected if not set)
  terminology_label: str        # Display label (auto-detected if not set)
  namespace: str                # Target namespace (default: "wip")
  prefix_filter: str            # Only import nodes with this OBO prefix
  include_deprecated: bool      # Import deprecated/obsolete nodes (default: false)
  max_synonyms: int             # Max aliases per term (default: 10)
  batch_size: int               # Terms per MongoDB batch (default: 1000)
  registry_batch_size: int      # Terms per registry HTTP call (default: 50)
  relationship_batch_size: int  # Relationships per batch (default: 500)
  skip_duplicates: bool         # Skip existing terms (default: true)
  update_existing: bool         # Update existing terms (default: false)
```

**Import pipeline:**

1. **Parse** — Extract CLASS nodes (prefix-filtered, deprecated-filtered) and edges (predicate-mapped)
2. **Auto-detect** — Prefix, title, version from graph metadata
3. **Create terminology** — Or find existing
4. **Bulk-create terms** — With aliases, descriptions, cross-references from OBO metadata
5. **Ensure relationship types** — Auto-create missing types in `_ONTOLOGY_RELATIONSHIP_TYPES`
6. **Bulk-create relationships** — Map OBO predicates to WIP relationship types

**OBO Predicate Mapping:**

| OBO Predicate | WIP Type |
|---|---|
| `is_a` | `is_a` |
| `BFO_0000050` | `part_of` |
| `BFO_0000051` | `has_part` |
| `RO_0002211` | `regulates` |
| `RO_0002212` | `negatively_regulates` |
| `RO_0002213` | `positively_regulates` |
| `RO_0002215` | `capable_of` |
| Unknown URI | Converted to compact form (e.g., `RO:0002500`) |

#### 2. CLI Script

```bash
source .venv/bin/activate
python scripts/import_obo_graph.py testdata/hp.json \
  --terminology-value HP \
  --terminology-label "Human Phenotype Ontology" \
  --prefix-filter HP \
  --dry-run  # Preview without importing
```

Same parsing logic as the API endpoint. Supports `--dry-run`, `--via-proxy`, batch size tuning.

#### 3. Console UI

Navigate to **Terminologies → Import Ontology** in the sidebar. Upload an OBO Graph JSON file, preview detected nodes/edges/predicates, configure options, and import.

### Export with Relationships

```
# Export terminology with relationships (JSON)
GET /api/def-store/import-export/export/{terminology_id}?include_relationships=true

Response includes:
{
  "terminology": { "value": "HP", "label": "Human Phenotype Ontology", ... },
  "terms": [
    { "value": "HP:0000001", "label": "All", "aliases": [...], ... },
    ...
  ],
  "relationships": [
    { "source_term_value": "HP:0000002", "target_term_value": "HP:0000001", "relationship_type": "is_a" },
    ...
  ]
}
```

The exported JSON can be re-imported via the standard `/import-export/import` endpoint — relationships are automatically processed using `source_term_value`/`target_term_value` to resolve term IDs.

The Console UI JSON export automatically includes relationships.

### Reporting Sync

TermRelationships are synced to PostgreSQL via NATS events:

```sql
CREATE TABLE term_relationships (
    id SERIAL PRIMARY KEY,
    namespace TEXT NOT NULL,
    source_term_id TEXT NOT NULL,
    target_term_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    relationship_value TEXT,
    source_terminology_id TEXT,
    target_terminology_id TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ,
    UNIQUE (namespace, source_term_id, target_term_id, relationship_type)
);

CREATE INDEX idx_tr_source ON term_relationships (namespace, source_term_id, relationship_type);
CREATE INDEX idx_tr_target ON term_relationships (namespace, target_term_id, relationship_type);
```

Events: `RELATIONSHIP_CREATED`, `RELATIONSHIP_DELETED`.

## Console UI

### Ontology Browser

Available at **Terminologies → Ontology Browser** (or via the `?tab=ontology` query parameter on the terminology list view). Shows all relationships in the current namespace with type filtering and pagination.

### Term Detail View

The term detail page includes:
- **Relationships tab** — outgoing and incoming relationships for the term
- **Hierarchy tab** — interactive tree showing ancestors and descendants via `is_a` traversal

### Relationship Management

Relationships can be created and deleted via the term detail view. The form requires term IDs (no autocomplete search — for large-scale ontology design, use the OBO import or CLI tools).

## OWL / SKOS Mapping

| WIP | OWL | SKOS |
|---|---|---|
| Terminology | `owl:Ontology` | `skos:ConceptScheme` |
| Term | `owl:Class` or `owl:NamedIndividual` | `skos:Concept` |
| `term.value` | `rdfs:label` | `skos:prefLabel` |
| `term.aliases` | — | `skos:altLabel` |
| `term.description` | `rdfs:comment` | `skos:definition` |
| `term.translations` | `rdfs:label@lang` | `skos:prefLabel@lang` |
| `is_a` relationship | `rdfs:subClassOf` | `skos:broader` |
| `related_to` relationship | — | `skos:related` |
| `maps_to` relationship | — | `skos:exactMatch` / `skos:closeMatch` |
| Registry synonym | `owl:sameAs` | `skos:notation` |

OWL/SKOS import/export parsers are not implemented. The current import path is OBO Graph JSON. OWL files can be converted to OBO Graph JSON using the [ROBOT](http://robot.obolibrary.org/) tool.

## Scope of Ontology Support

**WIP provides full ontology support for data capture and annotation.** It can import standard ontologies (OBO Graph JSON), preserve their complete structure including polyhierarchy and typed relationships, annotate documents against ontology terms with validated references, and export both data and ontology structure for round-trip fidelity.

### SKOS / Thesauri / Simple OWL — 100% Faithful

Classification schemes, controlled vocabularies, and simple ontologies map losslessly to WIP:

| Feature | WIP Storage | Fidelity |
|---|---|---|
| Concepts, labels, definitions | Terms (value, aliases, description) | Lossless |
| Polyhierarchy (broader/narrower) | TermRelationship `is_a` | Lossless |
| Associative links (related) | TermRelationship `related_to` | Lossless |
| External code mappings | Registry synonyms | Lossless |
| Multi-language labels | translations | Lossless |
| ConceptScheme metadata | Terminology fields + metadata | Lossless |

This covers ICD-10, ICD-11, MedDRA, LOINC, most of SNOMED CT's hierarchy, and any SKOS thesaurus.

### OWL Description Logic Axioms — Preserved, Not Interpreted

Full OWL-DL ontologies can contain logical axioms (class restrictions, cardinality constraints, disjointness, unions/intersections). These are stored in `term.metadata` and `relationship.metadata` for **round-trip preservation** but are not evaluated during data capture.

This is consistent with WIP's architecture: **capture is upstream, reasoning is downstream.** WIP stores and serves ontology structure faithfully; inference and logical validation are delegated to specialized downstream tools (Protege, Ontoserver, OWL reasoners).

### Post-Coordination — Not Supported

SNOMED CT allows combining concepts at data entry time. WIP terms are pre-coordinated: users select from existing terms. Post-coordinated expressions can be stored as free-text or structured data in document fields, but not as resolved term references.

## Verified Imports

| Ontology | Terms | Relationships | Time | Notes |
|---|---|---|---|---|
| HPO (Human Phenotype Ontology) | 19,389 | 23,677 (all is_a) | 53.5s | Mac localhost |
| GO (Gene Ontology) | 38,739 | 75,246 (9 types) | 128.5s | Mac localhost |

## Alternatives Considered

### Ontology as a separate service

A standalone `ontology-store` microservice with its own database and full OWL reasoning. Rejected because:
- Adds significant operational complexity (another service to deploy on Raspberry Pi)
- WIP's use case is storage and retrieval, not inference
- The relationship model in Def-Store is sufficient for import, browsing, and traversal

### Extend parent_term_id to a list

Making `parent_term_id` an array of strings. Rejected because:
- Only solves polyhierarchy, not typed relationships
- Breaks backward compatibility for all existing term queries and indexes
- Doesn't support cross-terminology links

### Store relationships in term.metadata

Using the existing `metadata` dict for ad-hoc relationship storage. Rejected because:
- No validation, no indexing, no traversal queries
- Relationships hidden inside individual terms instead of queryable as first-class entities
- Import/export would need custom conventions per ontology
