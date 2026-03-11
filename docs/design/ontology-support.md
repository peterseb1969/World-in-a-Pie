# Design: Ontology Support

**Status:** Proposed

## Motivation

Users may want to re-use existing ontologies (SNOMED CT, ICD, Gene Ontology, SKOS thesauri, etc.) within WIP. Today, WIP can import **terminologies** (flat or single-parent hierarchies) but cannot faithfully represent **ontologies**, which have:

- **Polyhierarchy** — a concept can have multiple parents (e.g., "Viral pneumonia" is_a "Pneumonia" AND is_a "Viral respiratory infection")
- **Typed relationships** — `is_a`, `part_of`, `finding_site`, `maps_to` are semantically distinct
- **Cross-terminology links** — concepts in one vocabulary relate to concepts in another

The single `parent_term_id` field on Term only supports one parent within one terminology. This makes ontology import lossy.

## Goals

1. Import standard ontologies (OWL, SKOS, OBO) without losing structural information
2. Support polyhierarchy (multiple parents per concept)
3. Support typed relationships between terms, including cross-terminology
4. Enable traversal queries (ancestors, descendants, transitive closure)
5. Leverage existing WIP primitives — no unnecessary duplication
6. Maintain backward compatibility — existing terminologies and `parent_term_id` continue to work

## What WIP Already Covers

| Ontology Concept | WIP Mechanism | Status |
|---|---|---|
| Concepts | Terms | ✅ Works today |
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
| Multi-parent hierarchy | — | ❌ Gap |
| Typed relationships | — | ❌ Gap |
| Cross-terminology links | — | ❌ Gap |
| Transitive traversal | — | ❌ Gap |

## Design

### New Model: TermRelationship

A lightweight edge model stored in a new `term_relationships` collection in Def-Store's MongoDB database.

```python
class TermRelationship(Document):
    """A typed, directed relationship between two terms."""

    namespace: str                          # Scoped like all WIP entities
    source_term_id: str                     # The subject term
    target_term_id: str                     # The object term
    relationship_type: str                  # Term ID from RELATIONSHIP_TYPES terminology
    relationship_value: Optional[str]       # Denormalized value (e.g., "is_a") for display

    # Optional enrichment
    source_terminology_id: Optional[str]    # Denormalized for query efficiency
    target_terminology_id: Optional[str]    # Denormalized for query efficiency
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
        ]
```

### Relationship Types as a Terminology

Relationship types are themselves a WIP terminology (`ONTOLOGY_RELATIONSHIP_TYPES`), auto-created on first ontology import or manually:

| Value | Label | Inverse | Transitive | Description |
|---|---|---|---|---|
| `is_a` | Is a | `has_subtype` | Yes | Subsumption (SKOS broader) |
| `part_of` | Part of | `has_part` | Yes | Mereological |
| `has_part` | Has part | `part_of` | Yes | Inverse of part_of |
| `maps_to` | Maps to | `mapped_from` | No | Cross-vocabulary mapping |
| `related_to` | Related to | `related_to` | No | Associative (SKOS related) |
| `finding_site` | Finding site | — | No | SNOMED-style attribute |
| `causative_agent` | Causative agent | — | No | SNOMED-style attribute |

Users can extend this terminology with domain-specific relationship types. The `transitive` flag in term metadata controls whether traversal queries follow chains.

### Registry Synonyms for Identity Mapping

External ontology codes are mapped via Registry synonyms — **not** via TermRelationship. This keeps a clean separation:

- **Synonyms** = "these are the same entity" (identity)
- **Relationships** = "these are related entities" (semantics)

Example: importing SNOMED concept 75570004 "Viral pneumonia":

```
Registry entry for WIP term T-000123:
  Primary key: {"namespace": "wip", "terminology_id": "SNOMED-CT", "value": "Viral pneumonia"}
  Synonym:     {"snomed_ct": "75570004"}
  Synonym:     {"icd10": "J12.9"}
```

This lets external systems look up WIP terms by their native codes, with O(1) hash-based lookup.

### Interaction with parent_term_id

`parent_term_id` remains as-is for simple single-parent terminologies. It is **not** deprecated — many terminologies are flat or single-hierarchy and don't need the relationship model.

For ontology imports with polyhierarchy, the importer will:
1. Leave `parent_term_id` empty (or set to the primary/preferred parent)
2. Create `is_a` TermRelationship records for ALL parents

Traversal queries always check both `parent_term_id` AND `is_a` relationships to give a unified view.

## API

All new endpoints live under `/api/def-store/ontology/`.

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
GET /api/def-store/ontology/relationships?term_id=T-000123&type=is_a

# Delete relationships (bulk)
DELETE /api/def-store/ontology/relationships
Body: [{"source_term_id": "T-000123", "target_term_id": "T-000456", "relationship_type": "is_a"}]
```

### Traversal Queries

```
# Ancestors: follow is_a (and parent_term_id) upward, transitively
GET /api/def-store/ontology/terms/{term_id}/ancestors?type=is_a&max_depth=10

Response: {
    "term_id": "T-000123",
    "ancestors": [
        {"term_id": "T-000456", "value": "Pneumonia", "depth": 1, "path": ["T-000123", "T-000456"]},
        {"term_id": "T-000789", "value": "Lung Disease", "depth": 2, "path": ["T-000123", "T-000456", "T-000789"]},
        ...
    ]
}

# Descendants: follow is_a downward
GET /api/def-store/ontology/terms/{term_id}/descendants?type=is_a&max_depth=5

# Direct parents only (immediate, non-transitive)
GET /api/def-store/ontology/terms/{term_id}/parents

# Direct children only
GET /api/def-store/ontology/terms/{term_id}/children
```

Traversal uses iterative breadth-first search with cycle detection. `max_depth` defaults to 10, capped at 50. Results are paginated for large ontologies.

### Ontology Import

Extends the existing import/export endpoints with a new format option.

```
# Import OWL/SKOS ontology
POST /api/def-store/import-export/import?format=owl
POST /api/def-store/import-export/import?format=skos
POST /api/def-store/import-export/import?format=obo
Body: <file upload>

Query parameters (in addition to existing ones):
  terminology_value: str        # Target terminology value (e.g., "SNOMED-CT")
  register_synonyms: bool       # Map external codes as Registry synonyms (default: true)
  relationship_batch_size: int  # Batch size for relationship creation (default: 500)
```

**Import Pipeline:**

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌─────────────────┐
│  Parse File │────▶│ Create Terms │────▶│  Create Rels   │────▶│ Register Syns   │
│ OWL/SKOS/OBO│     │ (bulk import)│     │ (bulk insert)  │     │ (Registry API)  │
└─────────────┘     └──────────────┘     └───────────────┘     └─────────────────┘
                          │                      │                       │
                    Existing import        New: batch insert       Existing Registry
                    pipeline (JSON)        TermRelationship        synonym API
```

1. **Parse** — Extract concepts, labels, relationships, external codes from source format
2. **Map to terms** — Each concept becomes a `CreateTermRequest` with value, aliases (alt labels), description (definition), translations, metadata (annotations)
3. **Bulk-create terms** — Uses existing import pipeline. Terms registered with Registry for stable IDs
4. **Bulk-create relationships** — New: insert `TermRelationship` documents in batches
5. **Register synonyms** — Existing: external codes (SNOMED ID, ICD code) become Registry synonyms

### Ontology Export

```
# Export terminology with relationships as OWL/SKOS
GET /api/def-store/import-export/export/{terminology_id}?format=owl
GET /api/def-store/import-export/export/{terminology_id}?format=skos

# Export includes:
# - Terms as concepts
# - TermRelationships as OWL object properties / SKOS semantic relations
# - Registry synonyms as skos:exactMatch / skos:notation
# - Translations as skos:prefLabel with language tags
```

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

## Namespace Considerations

Imported ontologies live in the same namespace as other terminologies. There is no need for a dedicated `wip-ontologies` namespace — the relationship layer is what distinguishes an ontology from a flat terminology.

However, users may choose to create a dedicated namespace (e.g., `clinical-ontologies`) to isolate imported ontologies from their working terminologies, using namespace isolation rules to control cross-referencing.

## Referential Integrity

The existing integrity service is extended to cover relationships:

- **Orphaned relationships:** source or target term_id no longer exists → severity: error
- **Inactive term in relationship:** source or target has status != "active" → severity: warning
- **Cross-namespace validation:** relationship endpoints respect namespace isolation mode
- **Cascade on term deactivation:** warn about relationships that reference the term

Relationship validation is opt-in during bulk import (skip for performance, run integrity check afterward).

## Reporting Sync

TermRelationships are synced to PostgreSQL for BI queries:

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

-- Indexes for traversal queries in SQL
CREATE INDEX idx_tr_source ON term_relationships (namespace, source_term_id, relationship_type);
CREATE INDEX idx_tr_target ON term_relationships (namespace, target_term_id, relationship_type);
```

This enables BI tools (Metabase) to visualize ontology structure and join relationships with document data.

## Implementation Phases

### Phase 1: Relationship Model + CRUD

- TermRelationship Beanie model with indexes
- ONTOLOGY_RELATIONSHIP_TYPES seed terminology
- Bulk create/delete/list endpoints under `/api/def-store/ontology/`
- Namespace-scoped validation
- Unit tests

### Phase 2: Traversal Queries

- Ancestors/descendants endpoints with BFS + cycle detection
- Unified traversal across `parent_term_id` and `is_a` relationships
- max_depth and pagination
- Integration tests with multi-parent hierarchies

### Phase 3: Ontology Import (OWL / SKOS)

- OWL parser (rdflib)
- SKOS parser (rdflib)
- OBO parser (fastobo or custom)
- Import pipeline: parse → terms → relationships → synonyms
- Batch tuning for large ontologies (SNOMED: ~350k concepts, ~800k relationships)

### Phase 4: Export + Reporting

- OWL/SKOS export format
- Reporting-sync support for term_relationships
- PostgreSQL table + NATS event handling
- Integrity service extension

### Phase 5: Console UI

- Relationship browser (tree/graph view for polyhierarchies)
- Ontology import wizard (upload OWL/SKOS file)
- Traversal visualization

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

## Scope of Ontology Support

**WIP provides full ontology support for data capture and annotation.** It can import standard ontologies (OWL, SKOS, OBO), preserve their complete structure including polyhierarchy and typed relationships, annotate documents against ontology terms with validated references, and export both data and ontology structure to downstream analysis platforms.

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

Full OWL-DL ontologies can contain logical axioms (class restrictions, cardinality constraints, disjointness, unions/intersections). These are stored in `term.metadata` and `relationship.metadata` for **round-trip preservation** but are not evaluated during data capture:

| OWL-DL Feature | Example | WIP Handling |
|---|---|---|
| Existential restrictions | `∃hasFindingSite.Lung` | Stored in metadata, exported on round-trip |
| Cardinality | `hasExactly 4 Chamber` | Stored in metadata |
| Disjointness | `Male ⊓ Female ≡ ∅` | Stored in metadata |
| Property characteristics | symmetric, functional | Stored on relationship type metadata |

This is consistent with WIP's architecture: **capture is upstream, reasoning is downstream.** WIP stores and serves ontology structure faithfully; inference and logical validation are delegated to specialized downstream tools (Protege, Ontoserver, OWL reasoners).

### Post-Coordination — Not Supported

SNOMED CT allows combining concepts at data entry time (e.g., "fracture" + "femur" → "fracture of femur" as a runtime expression). WIP terms are pre-coordinated: users select from existing terms. Post-coordinated expressions can be stored as free-text or structured data in document fields, but not as resolved term references. This is consistent with most terminology servers in practice.

## Success Criteria

- [ ] SNOMED CT core subset (~20k concepts) imports in under 60 seconds on Raspberry Pi 5
- [ ] ICD-10 imports with cross-map relationships to SNOMED via Registry synonyms
- [ ] Traversal of "all ancestors of Viral Pneumonia" returns correct multi-parent paths
- [ ] Existing terminologies with `parent_term_id` continue to work unchanged
- [ ] Round-trip: import SKOS → export SKOS produces equivalent output
