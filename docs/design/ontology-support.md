# Design: Ontology Support

**Status:** Implemented

## Motivation

Users may want to re-use existing ontologies (SNOMED CT, ICD, Gene Ontology, SKOS thesauri, etc.) within WIP. Today, WIP can import **terminologies** (flat or single-parent hierarchies) but cannot faithfully represent **ontologies**, which have:

- **Polyhierarchy** — a concept can have multiple parents (e.g., "Viral pneumonia" is_a "Pneumonia" AND is_a "Viral respiratory infection")
- **Typed relations** — `is_a`, `part_of`, `finding_site`, `maps_to` are semantically distinct
- **Cross-terminology links** — concepts in one vocabulary relate to concepts in another

The single `parent_term_id` field on Term only supports one parent within one terminology. This makes ontology import lossy.

## Goals

1. Import standard ontologies (OBO Graph JSON) without losing structural information
2. Support polyhierarchy (multiple parents per concept)
3. Support typed relations between terms, including cross-terminology
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
| Multi-parent hierarchy | `TermRelation` (is_a) | ✅ |
| Typed relations | `TermRelation` + `_ONTOLOGY_RELATIONSHIP_TYPES` | ✅ |
| Cross-terminology links | `TermRelation` | ✅ |
| Transitive traversal | BFS with cycle detection | ✅ |
| OBO Graph JSON import | CLI script + API endpoint + UI | ✅ |
| Round-trip export/import | JSON with relations + aliases | ✅ |

## Model: TermRelation

A lightweight edge model stored in the `term_relations` collection in Def-Store's MongoDB database.

```python
class TermRelation(Document):
    """A typed, directed relation between two terms."""

    namespace: str                          # Scoped like all WIP entities
    source_term_id: str                     # The subject term
    target_term_id: str                     # The object term
    relation_type: str                  # Value from _ONTOLOGY_RELATIONSHIP_TYPES terminology
    relation_value: Optional[str]       # Denormalized value for display

    # Denormalized for query efficiency
    source_terminology_id: Optional[str]
    target_terminology_id: Optional[str]
    metadata: dict[str, Any] = {}           # Provenance, confidence, source ontology
    status: str = "active"                  # active, inactive

    # Audit
    created_at: datetime
    created_by: Optional[str]

    class Settings:
        name = "term_relations"
        indexes = [
            # Find all relations FROM a term
            IndexModel([("namespace", 1), ("source_term_id", 1), ("relation_type", 1)]),
            # Find all relations TO a term (reverse lookup)
            IndexModel([("namespace", 1), ("target_term_id", 1), ("relation_type", 1)]),
            # Uniqueness: one relation of each type between two terms
            IndexModel(
                [("namespace", 1), ("source_term_id", 1), ("target_term_id", 1), ("relation_type", 1)],
                unique=True,
            ),
            # By terminology (for ontology-wide queries)
            IndexModel([("namespace", 1), ("source_terminology_id", 1), ("relation_type", 1)]),
            # Status filter
            IndexModel([("namespace", 1), ("status", 1)]),
        ]
```

## Relation Types

Relation types are validated against the system terminology `_ONTOLOGY_RELATIONSHIP_TYPES`, which is auto-created at Def-Store startup. New types are auto-created during ontology import.

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

OBO ontologies bring additional relation types that are auto-created as terms in `_ONTOLOGY_RELATIONSHIP_TYPES`:

- `regulates`, `positively_regulates`, `negatively_regulates` — Gene Ontology
- `capable_of`, `capable_of_part_of`, `has_input`, `has_output` — GO biological process
- `involved_in`, `regulates_activity_of` — GO molecular function
- Any unknown OBO predicate URI is converted to compact form (e.g., `BFO:0000066`, `RO:0002092`)

### Validation

`create_relations` validates every `relation_type` against terms in `_ONTOLOGY_RELATIONSHIP_TYPES`. Unknown types are rejected with a clear error listing valid types.

**Per-terminology restriction** of allowed relation types is not enforced by WIP — this is delegated to client applications. The global terminology defines what types *exist*; clients decide which types to *use* per context.

## Registry Synonyms for Identity Mapping

External ontology codes are mapped via Registry synonyms — **not** via TermRelation. This keeps a clean separation:

- **Synonyms** = "these are the same entity" (identity)
- **Relations** = "these are related entities" (semantics)

## Interaction with parent_term_id

`parent_term_id` remains as-is for simple single-parent terminologies. It is **not** deprecated — many terminologies are flat or single-hierarchy and don't need the relation model.

For ontology imports with polyhierarchy, the importer:
1. Leaves `parent_term_id` empty
2. Creates `is_a` TermRelation records for ALL parents

Traversal queries always check both `parent_term_id` AND `is_a` relations to give a unified view.

## API

All relation endpoints live under `/api/def-store/ontology/`.

### Relation CRUD

```
# Create relations (bulk-first, as per WIP convention)
POST /api/def-store/ontology/term-relations
Body: [
    {
        "source_term_id": "019abc01-def3-7abc-8def-100000000123",
        "target_term_id": "019abc01-def3-7abc-8def-100000000456",
        "relation_type": "is_a"
    },
    ...
]
Response: BulkResponse

# List relations for a term
GET /api/def-store/ontology/term-relations?term_id=019abc01-def3-7abc-8def-100000000123&direction=outgoing
GET /api/def-store/ontology/term-relations?term_id=019abc01-def3-7abc-8def-100000000123&direction=incoming
GET /api/def-store/ontology/term-relations?term_id=019abc01-def3-7abc-8def-100000000123&relation_type=is_a

# List all relations in namespace (paginated)
GET /api/def-store/ontology/term-relations/all?namespace=wip&relation_type=is_a

# Delete relations (bulk)
DELETE /api/def-store/ontology/term-relations
Body: [{"source_term_id": "019abc01-def3-7abc-8def-100000000123", "target_term_id": "019abc01-def3-7abc-8def-100000000456", "relation_type": "is_a"}]
```

### Traversal Queries

```
# Ancestors: follow is_a (and parent_term_id) upward, transitively
GET /api/def-store/ontology/terms/{term_id}/ancestors?relation_type=is_a&max_depth=10

Response: {
    "term_id": "019abc01-def3-7abc-8def-100000000123",
    "relation_type": "is_a",
    "direction": "ancestors",
    "nodes": [
        {"term_id": "019abc01-def3-7abc-8def-100000000456", "value": "Pneumonia", "depth": 1, "path": ["019abc01-def3-7abc-8def-100000000123", "019abc01-def3-7abc-8def-100000000456"]},
        {"term_id": "019abc01-def3-7abc-8def-100000000789", "value": "Lung Disease", "depth": 2, "path": ["019abc01-def3-7abc-8def-100000000123", "019abc01-def3-7abc-8def-100000000456", "019abc01-def3-7abc-8def-100000000789"]},
        ...
    ],
    "total": 5,
    "max_depth_reached": false
}

# Descendants: follow is_a downward
GET /api/def-store/ontology/terms/{term_id}/descendants?relation_type=is_a&max_depth=5

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
  relation_batch_size: int  # Relations per batch (default: 500)
  skip_duplicates: bool         # Skip existing terms (default: true)
  update_existing: bool         # Update existing terms (default: false)
```

**Import pipeline:**

1. **Parse** — Extract CLASS nodes (prefix-filtered, deprecated-filtered) and edges (predicate-mapped)
2. **Auto-detect** — Prefix, title, version from graph metadata
3. **Create terminology** — Or find existing
4. **Bulk-create terms** — With aliases, descriptions, cross-references from OBO metadata
5. **Ensure relation types** — Auto-create missing types in `_ONTOLOGY_RELATIONSHIP_TYPES`
6. **Bulk-create relations** — Map OBO predicates to WIP relation types

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

### Export with Relations

```
# Export terminology with relations (JSON)
GET /api/def-store/import-export/export/{terminology_id}?include_relations=true

Response includes:
{
  "terminology": { "value": "HP", "label": "Human Phenotype Ontology", ... },
  "terms": [
    { "value": "HP:0000001", "label": "All", "aliases": [...], ... },
    ...
  ],
  "relations": [
    { "source_term_value": "HP:0000002", "target_term_value": "HP:0000001", "relation_type": "is_a" },
    ...
  ]
}
```

The exported JSON can be re-imported via the standard `/import-export/import` endpoint — relations are automatically processed using `source_term_value`/`target_term_value` to resolve term IDs.

The Console UI JSON export automatically includes relations.

### Reporting Sync

TermRelations are synced to PostgreSQL via NATS events:

```sql
CREATE TABLE term_relations (
    id SERIAL PRIMARY KEY,
    namespace TEXT NOT NULL,
    source_term_id TEXT NOT NULL,
    target_term_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    relation_value TEXT,
    source_terminology_id TEXT,
    target_terminology_id TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ,
    UNIQUE (namespace, source_term_id, target_term_id, relation_type)
);

CREATE INDEX idx_tr_source ON term_relations (namespace, source_term_id, relation_type);
CREATE INDEX idx_tr_target ON term_relations (namespace, target_term_id, relation_type);
```

Events: `TERM_RELATION_CREATED`, `TERM_RELATION_DELETED`.

## Console UI

### Ontology Browser

Available at **Terminologies → Ontology Browser** (or via the `?tab=ontology` query parameter on the terminology list view). Shows all relations in the current namespace with type filtering and pagination.

### Term Detail View

The term detail page includes:
- **Relations tab** — outgoing and incoming relations for the term
- **Hierarchy tab** — interactive tree showing ancestors and descendants via `is_a` traversal

### Relation Management

Relations can be created and deleted via the term detail view. The form requires term IDs (no autocomplete search — for large-scale ontology design, use the OBO import or CLI tools).

## OWL / SKOS Mapping

| WIP | OWL | SKOS |
|---|---|---|
| Terminology | `owl:Ontology` | `skos:ConceptScheme` |
| Term | `owl:Class` or `owl:NamedIndividual` | `skos:Concept` |
| `term.value` | `rdfs:label` | `skos:prefLabel` |
| `term.aliases` | — | `skos:altLabel` |
| `term.description` | `rdfs:comment` | `skos:definition` |
| `term.translations` | `rdfs:label@lang` | `skos:prefLabel@lang` |
| `is_a` relation | `rdfs:subClassOf` | `skos:broader` |
| `related_to` relation | — | `skos:related` |
| `maps_to` relation | — | `skos:exactMatch` / `skos:closeMatch` |
| Registry synonym | `owl:sameAs` | `skos:notation` |

OWL/SKOS import/export parsers are not implemented. The current import path is OBO Graph JSON. OWL files can be converted to OBO Graph JSON using the [ROBOT](http://robot.obolibrary.org/) tool.

## Scope of Ontology Support

**WIP provides full ontology support for data capture and annotation.** It can import standard ontologies (OBO Graph JSON), preserve their complete structure including polyhierarchy and typed relations, annotate documents against ontology terms with validated references, and export both data and ontology structure for round-trip fidelity.

### SKOS / Thesauri / Simple OWL — 100% Faithful

Classification schemes, controlled vocabularies, and simple ontologies map losslessly to WIP:

| Feature | WIP Storage | Fidelity |
|---|---|---|
| Concepts, labels, definitions | Terms (value, aliases, description) | Lossless |
| Polyhierarchy (broader/narrower) | TermRelation `is_a` | Lossless |
| Associative links (related) | TermRelation `related_to` | Lossless |
| External code mappings | Registry synonyms | Lossless |
| Multi-language labels | translations | Lossless |
| ConceptScheme metadata | Terminology fields + metadata | Lossless |

This covers ICD-10, ICD-11, MedDRA, LOINC, most of SNOMED CT's hierarchy, and any SKOS thesaurus.

### OWL Description Logic Axioms — Preserved, Not Interpreted

Full OWL-DL ontologies can contain logical axioms (class restrictions, cardinality constraints, disjointness, unions/intersections). These are stored in `term.metadata` and `relation.metadata` for **round-trip preservation** but are not evaluated during data capture.

This is consistent with WIP's architecture: **capture is upstream, reasoning is downstream.** WIP stores and serves ontology structure faithfully; inference and logical validation are delegated to specialized downstream tools (Protege, Ontoserver, OWL reasoners).

### Post-Coordination — Not Supported

SNOMED CT allows combining concepts at data entry time. WIP terms are pre-coordinated: users select from existing terms. Post-coordinated expressions can be stored as free-text or structured data in document fields, but not as resolved term references.

## Verified Imports

| Ontology | Terms | Relations | Time | Notes |
|---|---|---|---|---|
| HPO (Human Phenotype Ontology) | 19,389 | 23,677 (all is_a) | 53.5s | Mac localhost |
| GO (Gene Ontology) | 38,739 | 75,246 (9 types) | 128.5s | Mac localhost |

## Alternatives Considered

### Ontology as a separate service

A standalone `ontology-store` microservice with its own database and full OWL reasoning. Rejected because:
- Adds significant operational complexity (another service to deploy on Raspberry Pi)
- WIP's use case is storage and retrieval, not inference
- The relation model in Def-Store is sufficient for import, browsing, and traversal

### Extend parent_term_id to a list

Making `parent_term_id` an array of strings. Rejected because:
- Only solves polyhierarchy, not typed relations
- Breaks backward compatibility for all existing term queries and indexes
- Doesn't support cross-terminology links

### Store relations in term.metadata

Using the existing `metadata` dict for ad-hoc relation storage. Rejected because:
- No validation, no indexing, no traversal queries
- Relations hidden inside individual terms instead of queryable as first-class entities
- Import/export would need custom conventions per ontology
